"""Motor de vehículos exportados desde Automation (dispositivo ``camsoEngine``).

Fuente de la semántica: ``lua/powertrain/camsoEngine.lua`` INCLUIDO en la muestra
(se cita la línea). Lo que depende de código de BeamNG que no está en la muestra
(``createCurve``, fusión de piezas, embrague) se marca como hipótesis o queda
pendiente. Ver docs/research/motor.md.

Tres capas, separadas en :class:`EngineModel`:

* ``raw``      — valores leídos tal cual (expresiones evaluadas).
* derivados    — calculados con reglas del Lua de la muestra (curva neta, freno
                 motor, techo de rpm, daño por sobrerrégimen…).
* ``status``   — estado de cada magnitud: confirmado / derivado / hipótesis /
                 pendiente.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.jbeam import ResolvedConfig, evaluate, parse_table
from src.jbeam.merge import MergedSection, merge_section

WOT_THROTTLE = (
    99  # camsoEngine.lua:728 throttleLookup ≤ 99; :2047 torqueCurve = map[99]
)
# Fila 100 del mapa: la que Automation usa a gas a fondo (sus par y potencia declarados). BeamNG NUNCA la
# lee (``throttleLookup`` ≤ 99); solo sirve de referencia para validar contra ``info_*.json``.
REFERENCE_THROTTLE = 100
CLOSED_THROTTLE = 1  # camsoEngine.lua:728 throttleLookup ≥ 1 (versión 47437d5c…)

# Versiones conocidas de camsoEngine.lua (sha256 del archivo del vehículo) y la fila del
# mapa de par que usa a gas cerrado: ``throttleLookup = max(N, min(99, …))``. Solo
# cambia eso entre ellas (diff completo: 1 línea; el resto se desplaza 3 líneas).
# Una versión no registrada deja el freno motor PENDIENTE: no se supone su fila.
CAMSO_ENGINE_VERSIONS: dict[str, dict[str, Any]] = {
    "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed": {
        "closed_throttle": 1,
        "evidence": "camsoEngine.lua:728 max(1, …) (Ibiza 35b84, Porsche GT3 RS)",
    },
    "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb": {
        "closed_throttle": 0,
        "evidence": "camsoEngine.lua:725 max(0, …) (samples_beamng, exportador 40340)",
    },
}
CAMSO_ENGINE_LUA = "lua/powertrain/camsoEngine.lua"
NEGATIVE_TORQUE_FACTOR = 1.35  # camsoEngine.lua:773 "if torque < 0 then torque*1.35"
HP_W = 745.69987158227  # caballo mecánico (hp) en vatios
RPM_TO_RAD_S = 2 * math.pi / 60

# Claves de mainEngine que usa este adaptador; un conflicto u operador en ellas
# impide continuar (salvo hasRevLimiter, ver _limiter).
USED_KEYS = (
    "torque",
    "frictionTorque",
    "idleRPM",
    "maxRPM",
    "maxPhysicalRPM",
    "inertia",
    "revLimiterRPM",
    "revLimiterType",
    "revLimiterRPMDrop",
    "turbocharger",
)

CONFIRMED = "confirmado"
DERIVED = "derivado"
HYPOTHESIS = "hipótesis"
PENDING = "pendiente"


class EngineDataError(ValueError):
    """Datos de motor incompletos, no soportados o con conflictos sin resolver."""


@dataclass(frozen=True)
class TorqueRow:
    throttle: float
    rpm: float
    torque: float
    fuel_used: float | None
    pressure: float | None


@dataclass(frozen=True)
class CurvePoint:
    rpm: float
    gross: float  # τ[WOT] leído (incluye boost)
    friction: float  # frictionTorque interpolado
    net: float  # gross − friction
    pressure: float | None  # bar absolutos (mapa)
    power_w: float  # net · ω

    @property
    def na_equivalent(self) -> float | None:
        """(τ − f) / Pressure: curva "NA" que calcula el Lua (líneas 63-70)."""
        if self.pressure in (None, 0):
            return None
        return self.net / self.pressure


@dataclass
class RawEngine:
    device_type: str
    device_name: str
    source_parts: list[str]
    torque_rows: list[TorqueRow]
    friction_rows: list[tuple[float, float]]
    idle_rpm: float
    max_rpm: float
    max_physical_rpm: float | None
    inertia: float
    rev_limiter_candidates: list[tuple[str, Any]]  # (pieza, hasRevLimiter)
    rev_limiter_rpm: float | None
    rev_limiter_type: str | None
    rev_limiter_rpm_drop: float | None
    turbo_section_name: str | None
    turbo: dict[str, Any]  # sección del turbo, expresiones evaluadas
    other_keys: list[str]  # claves de mainEngine no usadas por el adaptador
    # G2.8: otros dispositivos que camsoEngine.lua crea desde la sección del motor
    # (l. 2132-2154: solo si la clave nombra una sección existente). Se DETECTAN; su
    # efecto sobre el par no está analizado (``status``).
    supercharger_section_name: str | None = None
    nitrous_section_name: str | None = None


@dataclass
class EngineModel:
    raw: RawEngine
    wot_throttle: int
    curve: list[CurvePoint]
    duplicate_rpms: list[
        tuple[float, list[float]]
    ]  # rpm -> torques en orden de aparición
    peak_torque: CurvePoint
    peak_power: CurvePoint
    rpm_ceiling: float
    rpm_ceiling_basis: str
    has_cut_limiter: bool | None  # None = depende de un conflicto sin resolver
    overrev_damage_rpm: float
    coast: list[tuple[float, float]]  # (rpm, par de freno motor > 0) para rpm ≥ ralentí
    max_boost_bar: float | None  # presión manométrica máxima del mapa WOT
    full_boost_rpm: float | None  # primer rpm con ≥ 99 % de la presión máxima
    # curva y picos con la fila 100 (referencia de Automation); ``None`` si el mapa no la tiene
    reference_curve: list[CurvePoint] | None = None
    reference_peak_torque: CurvePoint | None = None
    reference_peak_power: CurvePoint | None = None
    status: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EngineDataError(f"{what}: se esperaba un número, no {value!r}")
    if not math.isfinite(value):
        raise EngineDataError(f"{what}: valor no finito {value!r}")
    return float(value)


def interp_curve(points: list[tuple[float, float]], x: float) -> float:
    """Interpolación lineal; por encima del último punto, último valor.

    [HIPÓTESIS para la forma entre puntos: ``createCurve`` de BeamNG no está en la
    muestra. El valor más allá del final sí sigue el Lua: línea 731 usa el último
    elemento de la tabla de fricción.]
    """
    if not points:
        raise EngineDataError("curva vacía")
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def find_engine_device(resolved: ResolvedConfig) -> tuple[str, str]:
    """(tipo, nombre) del motor, a partir de las tablas ``powertrain`` activas."""
    engines = []
    for active in resolved.active:
        for row in parse_table(active.part.data.get("powertrain", [])).rows:
            dtype = row.get("type")
            if isinstance(dtype, str) and dtype.lower().endswith("engine"):
                engines.append((dtype, row.get("name")))
    if len(engines) != 1:
        raise EngineDataError(
            f"se esperaba un único motor en powertrain, hay {engines}"
        )
    return engines[0]


def _check_merge(merged: MergedSection) -> None:
    used_conflicts = [k for k in merged.conflicts if k in USED_KEYS]
    if used_conflicts:
        raise EngineDataError(
            f"conflicto sin regla verificada entre piezas en {used_conflicts}: "
            f"{dict((k, merged.conflicts[k]) for k in used_conflicts)}"
        )
    used_ops = [(p, k) for p, k, _ in merged.operators if k[2:] in USED_KEYS]
    if used_ops:
        raise EngineDataError(
            f"operadores $+/$* sobre claves usadas {used_ops}: semántica no verificada"
        )
    if merged.non_dict:
        raise EngineDataError(
            f"contribuciones no-objeto a {merged.name}: {merged.non_dict}"
        )


def read_raw_engine(resolved: ResolvedConfig) -> RawEngine:
    dtype, name = find_engine_device(resolved)
    if dtype != "camsoEngine":
        raise EngineDataError(
            f"motor '{dtype}': solo está implementado el adaptador camsoEngine"
        )
    merged = merge_section(resolved, name)
    _check_merge(merged)
    variables = resolved.variable_values()

    def ev(key: str, required: bool = True) -> Any:
        if key not in merged:
            if required:
                raise EngineDataError(f"{name}.{key} no está definido")
            return None
        return evaluate(merged.get(key), variables)

    torque = parse_table(merged.get("torque"))
    expected = ("throttle", "rpm", "torque")
    if torque.header[:3] != expected:
        raise EngineDataError(f"cabecera de torque inesperada {torque.header}")
    rows = []
    for row in torque.rows:
        rows.append(
            TorqueRow(
                throttle=_num(evaluate(row.get("throttle"), variables), "throttle"),
                rpm=_num(evaluate(row.get("rpm"), variables), "rpm"),
                torque=_num(evaluate(row.get("torque"), variables), "torque"),
                fuel_used=(
                    None
                    if row.get("FuelUsed") is None
                    else _num(evaluate(row.get("FuelUsed"), variables), "FuelUsed")
                ),
                pressure=(
                    None
                    if row.get("Pressure") is None
                    else _num(evaluate(row.get("Pressure"), variables), "Pressure")
                ),
            )
        )
    friction_table = parse_table(merged.get("frictionTorque"))
    if friction_table.header[:2] != ("rpm", "torque"):
        raise EngineDataError(
            f"cabecera de frictionTorque inesperada {friction_table.header}"
        )
    friction = [
        (
            _num(evaluate(r.get("rpm"), variables), "friction rpm"),
            _num(evaluate(r.get("torque"), variables), "friction torque"),
        )
        for r in friction_table.rows
    ]
    turbo_name = ev("turbocharger", required=False)
    turbo: dict[str, Any] = {}
    if turbo_name:
        turbo_merged = merge_section(resolved, turbo_name)
        if turbo_merged.conflicts or turbo_merged.operators:
            raise EngineDataError(f"sección {turbo_name} con conflictos u operadores")
        for key, value in turbo_merged.values.items():
            try:
                turbo[key] = evaluate(value, variables)
            except ValueError:
                turbo[key] = value
    supercharger_name = _existing_section(resolved, ev("supercharger", False))
    nitrous_name = _existing_section(resolved, ev("nitrousOxideInjection", False))
    limiter_candidates = [
        (c.part, c.value) for c in merged.conflicts.get("hasRevLimiter", [])
    ]
    if not limiter_candidates and "hasRevLimiter" in merged:
        limiter_candidates = [
            (merged.sources["hasRevLimiter"][0], merged.get("hasRevLimiter"))
        ]
    max_physical = ev("maxPhysicalRPM", required=False)
    rpm_limit = ev("revLimiterRPM", required=False)
    drop = ev("revLimiterRPMDrop", required=False)
    return RawEngine(
        device_type=dtype,
        device_name=name,
        source_parts=merged.parts,
        torque_rows=rows,
        friction_rows=friction,
        idle_rpm=_num(ev("idleRPM"), "idleRPM"),
        max_rpm=_num(ev("maxRPM"), "maxRPM"),
        max_physical_rpm=(
            None if max_physical is None else _num(max_physical, "maxPhysicalRPM")
        ),
        inertia=_num(ev("inertia"), "inertia"),
        rev_limiter_candidates=limiter_candidates,
        rev_limiter_rpm=None if rpm_limit is None else _num(rpm_limit, "revLimiterRPM"),
        rev_limiter_type=ev("revLimiterType", required=False),
        rev_limiter_rpm_drop=None if drop is None else _num(drop, "revLimiterRPMDrop"),
        turbo_section_name=turbo_name,
        turbo=turbo,
        other_keys=sorted(k for k in merged.values if k not in USED_KEYS),
        supercharger_section_name=supercharger_name,
        nitrous_section_name=nitrous_name,
    )


def _existing_section(resolved: ResolvedConfig, name: Any) -> str | None:
    """``name`` si nombra una sección aportada por alguna pieza activa, como exige
    camsoEngine.lua (``jbeamData.x and v.data[jbeamData.x]``, l. 2132, 2140, 2148)."""
    if not isinstance(name, str) or not name:
        return None
    return name if resolved.section_contributions(name) else None


def _limiter(raw: RawEngine, max_available: float) -> tuple[float, str, bool | None]:
    """Techo de rpm según camsoEngine.lua:1997 y 2003-2007.

    * sin limitador: maxRPM_eff = min(maxRPM, rpm máx. de la tabla); por encima de
      la tabla el par vale 0 (línea 732, ``or 0``);
    * con limitador: maxRPM_eff = min(rpm máx. de la tabla, revLimiterRPM o maxRPM).

    ``hasRevLimiter`` puede estar en conflicto entre piezas (sin regla de fusión
    verificada). Se evalúan todos los candidatos y solo se acepta el resultado si
    no depende del candidato.
    """
    candidates = [v for _, v in raw.rev_limiter_candidates] or [True]  # Lua :2003
    outcomes: dict[float, set[bool]] = {}
    for value in candidates:
        if value is True:
            ceiling = min(max_available, raw.rev_limiter_rpm or raw.max_rpm)
        elif value is False:
            ceiling = min(raw.max_rpm, max_available)
        else:
            raise EngineDataError(f"hasRevLimiter con valor no booleano {value!r}")
        outcomes.setdefault(ceiling, set()).add(value)
    if len(outcomes) != 1:
        raise EngineDataError(
            f"el techo de rpm depende de qué pieza gane en hasRevLimiter: {outcomes}"
        )
    ceiling, values = next(iter(outcomes.items()))
    has_cut = values.pop() if len(values) == 1 else None
    if has_cut is None:
        basis = (
            "independiente del conflicto hasRevLimiter "
            f"{raw.rev_limiter_candidates}: con limitador, revLimiterRPM; sin él, "
            "fin de la tabla de par (par = 0 por encima)"
        )
    elif has_cut:
        basis = "revLimiterRPM (limitador activo), acotado por el fin de la tabla"
    else:
        basis = "fin de la tabla de par (sin limitador; par = 0 por encima)"
    return ceiling, basis, has_cut


def build_engine_model(
    resolved: ResolvedConfig, lua_sha256: str | None = None
) -> EngineModel:
    """``lua_sha256``: huella del ``camsoEngine.lua`` del vehículo. Con ella se elige
    la fila de gas cerrado de :data:`CAMSO_ENGINE_VERSIONS`; una versión no
    registrada deja el freno motor pendiente. Sin ella (llamadas antiguas) se usa la
    fila 1 de la versión original y el estado lo dice."""
    raw = read_raw_engine(resolved)
    issues: list[str] = []

    if lua_sha256 is None:
        closed_throttle: int | None = CLOSED_THROTTLE
        coast_basis = "versión de camsoEngine.lua no comprobada; fila 1 (Lua:728)"
    elif lua_sha256 in CAMSO_ENGINE_VERSIONS:
        version = CAMSO_ENGINE_VERSIONS[lua_sha256]
        closed_throttle = version["closed_throttle"]
        coast_basis = f"fila {closed_throttle}: {version['evidence']}"
    else:
        closed_throttle = None
        coast_basis = (
            f"camsoEngine.lua desconocido (sha256 {lua_sha256[:12]}…)"
            if lua_sha256
            else "camsoEngine.lua ausente del vehículo"
        )
        issues.append(
            f"{coast_basis}: la fila de gas cerrado no está verificada; freno motor "
            "sin calcular (registrar la versión en CAMSO_ENGINE_VERSIONS)"
        )

    wot_rows = [r for r in raw.torque_rows if r.throttle == WOT_THROTTLE]
    closed_rows = (
        [r for r in raw.torque_rows if r.throttle == closed_throttle]
        if closed_throttle is not None
        else []
    )
    if not wot_rows:
        raise EngineDataError(f"no hay filas con throttle = {WOT_THROTTLE}")
    if closed_throttle is not None and not closed_rows:
        raise EngineDataError(f"no hay filas con throttle = {closed_throttle}")
    max_available = max(r.rpm for r in raw.torque_rows)  # camsoEngine.lua:1964

    # Curva de fricción: el Lua antepone (0, 0) (línea 1975).
    friction_points = [(0.0, 0.0)] + sorted(raw.friction_rows)

    # rpm duplicados: se conservan todos en el informe; para la curva se usa la
    # ÚLTIMA fila (hipótesis: createCurve no está en la muestra).
    by_rpm: dict[float, list[TorqueRow]] = {}
    for row in wot_rows:
        by_rpm.setdefault(row.rpm, []).append(row)
    duplicates = [
        (rpm, [r.torque for r in rows]) for rpm, rows in by_rpm.items() if len(rows) > 1
    ]
    for rpm, torques in duplicates:
        issues.append(
            f"rpm {rpm:g} repetido en throttle {WOT_THROTTLE} con pares {torques}; "
            "se usa el último (hipótesis)"
        )

    curve = []
    for rpm in sorted(by_rpm):
        row = by_rpm[rpm][-1]
        f = interp_curve(friction_points, rpm)
        net = row.torque - f
        curve.append(
            CurvePoint(rpm, row.torque, f, net, row.pressure, net * rpm * RPM_TO_RAD_S)
        )

    ceiling, basis, has_cut = _limiter(raw, max_available)
    in_range = [p for p in curve if p.rpm <= ceiling]
    peak_torque = max(in_range, key=lambda p: p.net)
    peak_power = max(in_range, key=lambda p: p.power_w)

    # Referencia de Automation: misma construcción con la fila 100 (misma fricción y mismo techo)
    ref_by_rpm: dict[float, TorqueRow] = {}
    for row in raw.torque_rows:
        if row.throttle == REFERENCE_THROTTLE:
            ref_by_rpm[row.rpm] = row  # última fila por rpm, como en la curva principal
    reference_curve = None
    ref_peak_torque = ref_peak_power = None
    if ref_by_rpm:
        reference_curve = []
        for rpm in sorted(ref_by_rpm):
            row = ref_by_rpm[rpm]
            f = interp_curve(friction_points, rpm)
            net = row.torque - f
            reference_curve.append(
                CurvePoint(
                    rpm, row.torque, f, net, row.pressure, net * rpm * RPM_TO_RAD_S
                )
            )
        ref_in = [p for p in reference_curve if p.rpm <= ceiling]
        if ref_in:
            ref_peak_torque = max(ref_in, key=lambda p: p.net)
            ref_peak_power = max(ref_in, key=lambda p: p.power_w)

    # Freno motor a gas cerrado (fila según la versión, ×1.35 si negativo), desde el
    # ralentí.
    coast = []
    closed = {}
    for row in closed_rows:
        closed[row.rpm] = row.torque  # última fila por rpm
    for rpm in sorted(closed):
        if rpm < raw.idle_rpm or rpm > ceiling:
            continue
        torque = closed[rpm] - interp_curve(friction_points, rpm)
        if torque < 0:
            torque *= NEGATIVE_TORQUE_FACTOR
        coast.append((rpm, -torque))

    pressures = [
        p.pressure for p in curve if p.pressure is not None and p.rpm <= ceiling
    ]
    max_boost = full_boost_rpm = None
    if pressures:
        p_max = max(pressures)
        max_boost = p_max - 1.0
        full_boost_rpm = next(
            p.rpm
            for p in curve
            if p.pressure is not None and p.pressure >= 0.99 * p_max
        )
        if max_boost <= 0:
            max_boost = full_boost_rpm = None

    overrev = raw.max_physical_rpm or raw.max_rpm * 1.05  # camsoEngine.lua:1944

    status = {
        "wot_throttle_99": CONFIRMED + " (camsoEngine.lua:728, 2047)",
        "net_curve": CONFIRMED + " (Lua:749; reproduce info_<config>.json)",
        "boost_included_in_table": CONFIRMED
        + " (Lua:776-834: el mapa es el máximo; el turbo solo lo reduce)",
        "friction_interpolation": HYPOTHESIS
        + " (lineal; createCurve no está en la muestra)",
        "duplicate_rpm_policy": HYPOTHESIS + " (última fila)",
        "rpm_ceiling": DERIVED + " (" + basis + ")",
        "coast_curve": (
            DERIVED + f" (gas cerrado − fricción, ×1.35; {coast_basis})"
            if closed_throttle is not None
            else PENDING + f" ({coast_basis})"
        ),
        "overrev_damage_rpm": DERIVED
        + " (maxPhysicalRPM o maxRPM·1.05; Lua:1944, 510)",
        "inertia": DERIVED
        + " (valor del motor; FALTA additionalEngineInertia del embrague, Lua:1001)",
        "air_density_coef": HYPOTHESIS + " (= 1; Lua:613 lo toma del entorno)",
        "turbo_lag": PENDING + " (pressureSmoother no reproducido; fuera de alcance)",
    }
    if raw.supercharger_section_name:
        status["supercharger"] = PENDING + (
            f" (sección '{raw.supercharger_section_name}': camsoSupercharger no "
            "analizado; Lua:782-790 puede anular la presión del mapa con el embrague "
            "del compresor; el par de power.lut no está verificado)"
        )
    if raw.nitrous_section_name:
        status["nitrous"] = PENDING + (
            f" (sección '{raw.nitrous_section_name}': par añadido por "
            "nitrousOxideInjection, Lua:204-211; NO incluido en la curva)"
        )
    if raw.turbo:
        status["turbo_section"] = (
            "leído; NO gobierna el par en camsoEngine "
            "(Lua:787: la presión sale del mapa)"
        )
    return EngineModel(
        raw=raw,
        wot_throttle=WOT_THROTTLE,
        curve=curve,
        duplicate_rpms=duplicates,
        peak_torque=peak_torque,
        peak_power=peak_power,
        rpm_ceiling=ceiling,
        rpm_ceiling_basis=basis,
        has_cut_limiter=has_cut,
        overrev_damage_rpm=overrev,
        coast=coast,
        max_boost_bar=max_boost,
        full_boost_rpm=full_boost_rpm,
        reference_curve=reference_curve,
        reference_peak_torque=ref_peak_torque,
        reference_peak_power=ref_peak_power,
        status=status,
        issues=issues,
    )


@dataclass(frozen=True)
class InfoCheck:
    quantity: str
    reference: float
    computed: float
    difference: float
    explanation: str


def compare_with_info(model: EngineModel, info: dict[str, Any]) -> list[InfoCheck]:
    """Contraste con info_<config>.json (datos de UI generados por el exportador)."""
    checks: list[InfoCheck] = []
    t, p = model.peak_torque, model.peak_power
    hp = p.power_w / HP_W

    def add(name, ref, value, tolerance, unit):
        if not isinstance(ref, (int, float)) or isinstance(ref, bool):
            return
        diff = value - ref
        ok = abs(diff) <= tolerance
        head = f"|Δ| = {abs(diff):.3g} {unit}"
        if ok:
            why = f"{head} ≤ {tolerance:g}: compatible (referencia redondeada)"
        else:
            why = f"{head} > {tolerance:g}: DISCREPANCIA a explicar"
        checks.append(InfoCheck(name, float(ref), value, diff, why))

    add("Torque (Nm)", info.get("Torque"), t.net, 0.5, "Nm")
    add("TorquePeakRPM", info.get("TorquePeakRPM"), t.rpm, 0, "rpm")
    add("Power (hp)", info.get("Power"), hp, 0.5, "hp")
    add("PowerPeakRPM", info.get("PowerPeakRPM"), p.rpm, 0, "rpm")
    weight, ratio = info.get("Weight"), info.get("Weight/Power")
    if isinstance(weight, (int, float)) and isinstance(ratio, (int, float)) and ratio:
        add("Power implícita Weight/(Weight/Power) (hp)", weight / ratio, hp, 0.5, "hp")
    return checks
