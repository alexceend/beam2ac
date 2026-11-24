"""Motor ``combustionEngine`` (coches oficiales de BeamNG): adaptador de SOLO LECTURA.

Reproduce, sin ejecutar el juego, la curva de par neta que BeamNG calcula y muestra
(``getTorqueData``), a partir del JBeam resuelto. No escribe nada ni produce física de
AC: devuelve hechos con su origen para el modelo de atributos.

Semántica (``lua/vehicle/powertrain/combustionEngine.lua`` del juego, solo lectura):

* Curva base: ``createCurve`` sobre la tabla ``torque`` (``rpm``, ``torque``)
  (l. 1886-1894).
* ``createCurve`` (``lua/vehicle/ve_utils.lua:264``): con 2 puntos, recta; con más,
  ``CatMullRomSpline`` (l. 234-262), evaluada en cada rpm ENTERA con el paso acumulado
  ``t += 1/(x2-x1)``. Se porta tal cual (no es interpolación lineal).
* Combinada: ``base·torqueModMult + torqueModIntake + torqueModExhaust`` en cada rpm
  entera; fuera de su tabla, ``torqueModMult`` vale 1 y los otros su último valor
  (l. 1896-1935).
* ``maxRPM = min(maxRPM_jbeam, máx. rpm de las tablas)`` (l. 1957-1958). Con limitador
  (``hasRevLimiter`` vale ``true`` si no se declara, l. 1964),
  ``maxRPM = revLimiterRPM`` (o ``maxRPM`` si no hay ``revLimiterRPM``; l. 1968-1971).
* Par 0 por debajo de ``0,3·idleRPM`` (l. 1995-1997); puntos en cada rpm entera hasta
  el ``maxRPM`` previo al limitador, más tres puntos de caída
  (``redlineTorqueDropOffRange``, 500 por defecto) y un punto (``maxRPM``, 0) si hace
  falta; segunda ``createCurve`` (l. 2000-2024).
* Neta (``getTorqueData``, l. 41-49), para cada rpm k < ``maxRPM``:
  ``T_neto(k) = curva(k) − friction − dynamicFriction · k · π/30`` (coeficientes de
  desgaste y daño = 1 en un motor nuevo, l. 855-871).
* Tablas externas: un valor ``"$=include('<ruta>.csv')"`` se lee con ``csvlib.decode``
  (``lua/common/csvlib.lua``, RFC 4180), igual que ``includeExternalResource``
  (``lua/common/jbeam/expressionParser.lua:29``). Solo CSV y solo con un
  ``resource_reader`` (p. ej. ``zip_resource_reader``); ``.xlsx`` y otros argumentos
  se rechazan.

Turbo (G2.9, 2026-11-02): :func:`estimate_turbo_wot` porta la estimación de régimen
estable que el propio juego usa para su curva «Turbo» (``turbocharger.lua``
``getTorqueCoefs`` l. 527-547 e ``init`` l. 345-500; ``combustionEngine.lua``
``getTorqueData`` l. 82-103). Es una ESTIMACIÓN del juego (la válvula de descarga se
supone en el punto medio de su rango), no una medida en marcha.

Freno motor (G2.10, D-G29c): :func:`_coast` porta el par resistente a gas cerrado de
``updateTorque`` (l. 744-783): con gas 0 el mapa de gas da 0 (``smoothmin(0, 1, 0.8)``
= 0, mathlib.lua l. 1216-1220), el par de combustión es 0 y ``instantLoad`` = 0, así que
el freno es ``friction + dynamicFriction·ω + engineBrakeTorque`` (``engineBrakeTorque``
por defecto ``friction·2``, l. 1858). Solo por encima del ralentí más la mitad del
error de lectura (``idleRPMRoughness``, l. 620, 1705, 716), donde el gas de ralentí es
0. Con los controladores ``compressionBrake`` o ``antiLag`` (que cambian ese par) no se
calcula.

Fuera del alcance (se informa, no se supone): compresor (``supercharger.lua``), óxido
nitroso y daño.

Versión del Lua portado (instalación local, solo lectura): ``combustionEngine.lua``
sha256 ``c3aa4463…``, ``turbocharger.lua`` ``004810d6…``, ``ve_utils.lua``
``23f2026d…`` (:data:`GAME_LUA`). Las exportaciones de Automation 200409 no traen
Lua del motor: usan el del juego instalado.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
import zipfile
from pathlib import Path
from typing import Any, Callable

from src.jbeam import ResolvedConfig, evaluate, parse_table
from src.jbeam.unify import UnifiedSection, unify_dict_section

LUA = "lua/vehicle/powertrain/combustionEngine.lua"
TURBO_LUA = "lua/vehicle/powertrain/turbocharger.lua"
# Versiones del Lua del juego portadas en este módulo (procedencia; no se comprueban en
# tiempo de ejecución porque la instalación del juego no es una entrada del conversor).
GAME_LUA = {
    LUA: "c3aa446393cc4af240fb71ddab4880afb857090a97f18d1d89b904e44728a03d",
    TURBO_LUA: "004810d6fb991b086288c1a2802f30419922eed0221632efe923d4d87ad76f29",
    "lua/vehicle/ve_utils.lua": (
        "23f2026d27de0092ddb36a78eaa3018a164cb7493c32889e872807d889bf6ac1"
    ),
}
USED_KEYS = (
    "torque",
    "torqueModMult",
    "torqueModIntake",
    "torqueModExhaust",
    "idleRPM",
    "maxRPM",
    "revLimiterRPM",
    "hasRevLimiter",
    "revLimiterType",
    "friction",
    "dynamicFriction",
    "inertia",
    "redlineTorqueDropOffRange",
    "engineBrakeTorque",
    "maxPhysicalRPM",
    "idleRPMRoughness",
)
# G2.10: controladores que modifican el par a gas cerrado (combustionEngine.lua
# l. 575-586, 777-780; controller/braking/compressionBrake.lua,
# controller/powertrainControl/antiLag.lua)
COAST_CONTROLLERS = ("compressionBrake", "antiLag")
FORCED_INDUCTION_KEYS = ("turbocharger", "supercharger", "nitrousOxideInjection")
HP_W = 745.69987158227
RPM_TO_AV = math.pi / 30


class CombustionEngineError(ValueError):
    """Datos insuficientes o semántica no soportada; nunca se suple con un supuesto."""


# --- port de ve_utils.lua ------------------------------------------------------------


def catmull_rom_spline(points: list[tuple[float, float]]) -> dict[int, float]:
    """``CatMullRomSpline`` (ve_utils.lua:234-262), evaluada en cada entero."""
    res: dict[int, float] = {}
    n = len(points)
    for i in range(n - 1):  # Lua: for i = 1, pointSize - 1 (índices desde 1)
        p0, p1 = points[max(i - 1, 0)], points[i]
        p2, p3 = points[i + 1], points[min(i + 2, n - 1)]
        step = 1 / (p2[0] - p1[0])
        t = 0.0
        y0, y1, y2, y3 = p0[1], p1[1], p2[1], p3[1]
        c1 = 3 * (y1 - y2) + y3 - y0
        c2 = 2 * y0 - 5 * y1 + 4 * y2 - y3
        c3 = y2 - y0
        for x in range(math.floor(p1[0]), math.floor(p2[0]) + 1):
            res[x] = y1 + 0.5 * t * (c3 + (c1 * t + c2) * t)
            t += step
    return res


def create_curve(points: list[tuple[float, float]]) -> dict[int, float]:
    """``createCurve`` (ve_utils.lua:264-287).

    G2.9: dos puntos consecutivos con la misma rpm dan ``1/0`` en el Lua (``inf``, sin
    error); aquí se rechazan con un error explícito en vez de portar ese caso."""
    if len(points) < 2:
        raise CombustionEngineError("createCurve necesita al menos 2 puntos")
    for a, b in zip(points, points[1:]):
        if b[0] == a[0]:
            raise CombustionEngineError(
                f"createCurve: dos puntos consecutivos en x = {a[0]:g} (en el Lua, "
                "1/0 = inf; caso no portado)"
            )
    if len(points) == 2:
        (x1, y1), (x2, y2) = points
        res: dict[int, float] = {}
        step = 1 / (x2 - x1)
        t = 0.0
        for x in range(math.floor(x1), math.floor(x2) + 1):
            res[x] = y1 + t * (y2 - y1)
            t += step
        return res
    return catmull_rom_spline(points)


# --- lectura -----------------------------------------------------------------------


@dataclass
class CombustionEngine:
    device_name: str
    source_parts: list[str]
    idle_rpm: float
    max_rpm_jbeam: float
    max_available_rpm: int
    max_rpm: float  # tras el limitador
    has_rev_limiter: bool
    rev_limiter_type: str | None
    friction: float
    dynamic_friction: float
    inertia: float
    net_curve: list[tuple[int, float]]  # rpm entera, N·m
    peak_torque: tuple[int, float]
    peak_power: tuple[int, float]  # rpm, W
    forced_induction: list[str] = field(default_factory=list)
    supported: bool = True  # la curva NA (``net_curve``) es la curva a plena carga
    issues: list[str] = field(default_factory=list)
    # G2.9: claves ausentes del JBeam que toman el valor por defecto del Lua
    # (clave -> (valor, línea del Lua)); nunca un supuesto propio.
    defaults_used: dict[str, tuple[Any, str]] = field(default_factory=dict)
    # G2.9: curva a plena carga con la sobrealimentación que se sabe calcular.
    wot: "WotCurve | None" = None
    wot_blockers: list[str] = field(default_factory=list)
    # G2.9: rpm de los puntos de la tabla ``torque`` base (nudos de la curva)
    table_rpms: list[float] = field(default_factory=list)
    # G2.10 (D-G29c): par de freno motor (> 0) a gas cerrado, (rpm, N·m), o motivos
    engine_brake_torque: float | None = None
    idle_rpm_roughness: float | None = None
    coast: list[tuple[float, float]] = field(default_factory=list)
    coast_blockers: list[str] = field(default_factory=list)


def find_combustion_engine(resolved: ResolvedConfig) -> str:
    engines = []
    for active in resolved.active:
        for row in parse_table(active.part.data.get("powertrain", [])).rows:
            if row.get("type") == "combustionEngine":
                engines.append(row.get("name"))
    if len(engines) != 1:
        raise CombustionEngineError(f"se esperaba un combustionEngine, hay {engines}")
    return engines[0]


def _check(merged: UnifiedSection) -> None:
    left = [k for k in merged.values if k[:1] == "$" and k[2:] in USED_KEYS]
    if left:
        raise CombustionEngineError(
            f"modificadores sin valor base tras la fusión: {left}"
        )


ResourceReader = Callable[[str], bytes]


def zip_resource_reader(*zip_paths: str | Path) -> ResourceReader:
    """Lee una ruta virtual del juego (``vehicles/...``) del primer zip que la contenga.

    No extrae nada: abre cada zip en modo lectura. ``KeyError`` si ningún zip la tiene.
    """
    paths = [Path(p) for p in zip_paths]

    def read(uri: str) -> bytes:
        name = uri.replace("\\", "/").lstrip("/")
        for zp in paths:
            with zipfile.ZipFile(zp, "r") as zf:
                try:
                    return zf.read(name)
                except KeyError:
                    continue
        raise KeyError(uri)

    return read


def _lua_tonumber(text: str) -> Any:
    """``tonumber`` de Lua: admite espacios y hexadecimal; no ``_`` ni inf/nan."""
    s = text.strip()
    body = s.lstrip("+-").lower()
    if not s or "_" in s or body.startswith(("inf", "nan")):
        return text
    try:
        return int(s, 16) if body.startswith("0x") else int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return text


def decode_csv(s: str, sep: str = ",") -> list[list[Any]]:
    """Port de ``csvlib.decode`` (``lua/common/csvlib.lua:99-161``).

    Campos entre comillas con ``""`` escapadas; los no entrecomillados que empiezan por
    dígito, ``+`` o ``-`` se convierten con ``tonumber`` (si falla, quedan como texto);
    se quitan los campos vacíos al final de cada línea y las líneas vacías del final.
    """
    rows: list[list[Any]] = []
    i, n = 0, len(s)
    line: list[Any] = []
    while True:
        while i < n and s[i] in " \t":
            i += 1
        c = s[i] if i < n else None
        val: Any
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if s[j] == '"' and j + 1 < n and s[j + 1] == '"':
                    buf.append('"')
                    j += 2
                elif s[j] == '"':
                    break
                else:
                    buf.append(s[j])
                    j += 1
            val = "".join(buf)
            i = j + 1
            while i < n and s[i] in " \t":
                i += 1
        else:
            j = i
            while j < n and s[j] not in (sep, "\r", "\n"):
                j += 1
            raw = s[i:j]
            numeric = c is not None and (c.isdigit() or c in "+-")
            val = _lua_tonumber(raw) if numeric else raw
            i = j
        line.append(val)
        c = s[i] if i < n else None
        i += 1
        if c == sep:
            continue
        while line and line[-1] == "":
            line.pop()
        rows.append(line)
        line = []
        if c == "\r" and i < n and s[i] == "\n":
            i += 1
        if c is None or i >= n:
            while rows and rows[-1] in ([], [""]):
                rows.pop()
            return rows


def _include_target(raw: str) -> str | None:
    """Ruta de ``$=include('<ruta>')`` si el valor es solo eso; si no, ``None``."""
    expr = raw.strip()
    if not expr.startswith("$="):
        return None
    expr = expr[2:].strip()
    if not (expr.startswith("include(") and expr.endswith(")")):
        return None
    arg = expr[len("include(") : -1].strip()
    quoted = len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "'\""
    if quoted and arg[0] not in arg[1:-1]:
        return arg[1:-1]
    return None


def _resolve_include(raw: Any, reader: ResourceReader | None, what: str) -> Any:
    if not isinstance(raw, str):
        return raw
    target = _include_target(raw)
    if target is None:
        return raw  # parse_table informará del tipo
    if not target.lower().endswith(".csv"):
        raise CombustionEngineError(f"{what}: include de {target!r} (solo CSV)")
    if reader is None:
        raise CombustionEngineError(f"{what}: include de {target!r} sin lector")
    try:
        data = reader(target)
    except KeyError as exc:
        raise CombustionEngineError(f"{what}: {target!r} no encontrado") from exc
    return decode_csv(data.decode("utf-8-sig"))


def _table(
    raw: Any, variables: dict, what: str, reader: ResourceReader | None = None
) -> list[tuple[float, float]]:
    """Tabla con cabecera ``["rpm", "torque"]`` (tableFromHeaderTable)."""
    t = parse_table(_resolve_include(raw, reader, what))
    if "rpm" not in t.header or "torque" not in t.header:
        raise CombustionEngineError(f"{what}: cabecera {t.header} sin rpm/torque")
    pts = []
    for row in t.rows:
        rpm, tq = evaluate(row.get("rpm"), variables), evaluate(
            row.get("torque"), variables
        )
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in (rpm, tq)
        ):
            raise CombustionEngineError(f"{what}: fila no numérica {row.values}")
        pts.append((float(rpm), float(tq)))
    if len(pts) < 2:
        raise CombustionEngineError(f"{what}: menos de 2 puntos")
    return pts


def read_combustion_engine(
    resolved: ResolvedConfig, resource_reader: ResourceReader | None = None
) -> CombustionEngine:
    name = find_combustion_engine(resolved)
    # fusión con la semántica del juego (slotSystem.lua unifyParts; src/jbeam/unify.py)
    merged = unify_dict_section(resolved, name)
    _check(merged)
    var = resolved.variable_values()
    defaults: dict[str, tuple[Any, str]] = {}

    def num(key: str, default: float | None = None) -> float:
        if key not in merged:
            if default is None:
                raise CombustionEngineError(f"{name}.{key} no está definido")
            return default
        v = evaluate(merged.get(key), var)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise CombustionEngineError(f"{name}.{key} no numérico: {v!r}")
        return float(v)

    if "torque" not in merged:
        raise CombustionEngineError(f"{name}: sin tabla torque")
    base_pts = _table(merged.get("torque"), var, "torque", resource_reader)
    max_avail = max(p[0] for p in base_pts)
    base = create_curve(base_pts)
    mult = intake = exhaust = None
    last_intake = last_exhaust = 0.0
    if "torqueModMult" in merged:
        pts = _table(merged.get("torqueModMult"), var, "torqueModMult", resource_reader)
        max_avail = max(max_avail, max(p[0] for p in pts))
        mult = create_curve(pts)
    if "torqueModIntake" in merged:
        pts = _table(
            merged.get("torqueModIntake"), var, "torqueModIntake", resource_reader
        )
        max_avail = max(max_avail, max(p[0] for p in pts))
        intake = create_curve(pts)
        last_intake = intake[max(intake)]  # rawIntakeCurve[#rawIntakeCurve]
    if "torqueModExhaust" in merged:
        pts = _table(
            merged.get("torqueModExhaust"), var, "torqueModExhaust", resource_reader
        )
        max_avail = max(max_avail, max(p[0] for p in pts))
        exhaust = create_curve(pts)
        last_exhaust = exhaust[max(exhaust)]
    max_avail_i = int(max_avail)
    combined = {}
    for i in range(0, max_avail_i + 1):
        b = base.get(i, 0.0)
        m = mult.get(i, 1.0) if mult is not None else 1.0
        it = intake.get(i, last_intake) if intake is not None else last_intake
        ex = exhaust.get(i, last_exhaust) if exhaust is not None else last_exhaust
        combined[i] = b * m + it + ex

    idle = num("idleRPM")
    max_rpm_jbeam = num("maxRPM")
    pre_limiter_max = min(max_rpm_jbeam, max_avail_i)  # l. 1957
    has_lim_raw = (
        evaluate(merged.get("hasRevLimiter"), var)
        if "hasRevLimiter" in merged
        else None
    )
    # l. 1964: ``x == nil and true or x``; en Lua solo nil y false son falsos (un 0
    # numérico es verdadero). G2.9: antes se usaba ``bool`` de Python.
    has_lim = True if has_lim_raw is None else has_lim_raw is not False
    lim_type = None
    max_rpm = pre_limiter_max
    if has_lim:
        if "revLimiterType" not in merged:
            defaults["revLimiterType"] = ("rpmDrop", f"{LUA}:1966")
        raw_type = merged.get("revLimiterType")
        # ``jbeamData.revLimiterType or "rpmDrop"``: en Lua "" es verdadero
        lim_type = "rpmDrop" if raw_type is None or raw_type is False else raw_type
        if "revLimiterRPM" not in merged:
            defaults["revLimiterRPM"] = (pre_limiter_max, f"{LUA}:1968")
        max_rpm = num("revLimiterRPM", pre_limiter_max)  # l. 1968-1971
        if lim_type not in ("rpmDrop", "timeBased", "soft"):
            # l. 1985-1988: un tipo desconocido desactiva el limitador, pero
            # ``device.maxRPM`` YA vale ``revLimiterRPM`` (l. 1971) y no se restaura.
            # G2.9: antes se volvía a ``pre_limiter_max`` (desviación del Lua).
            has_lim = False
    for i in range(0, math.floor(idle * 0.3) + 1):
        combined[i] = 0.0
    pts = [
        (float(i), combined.get(i, 0.0))
        for i in range(0, math.floor(pre_limiter_max) + 1)
    ]
    if "redlineTorqueDropOffRange" not in merged:
        defaults["redlineTorqueDropOffRange"] = (500.0, f"{LUA}:2008")
    drop = min(
        max(num("redlineTorqueDropOffRange", 500.0), 10.0), pre_limiter_max
    )  # clamp, l. 2008
    raw_max_rpm_torque = combined.get(math.floor(pre_limiter_max), 0.0)
    pts.append((pre_limiter_max + drop * 0.5, raw_max_rpm_torque * 0.7))
    pts.append((pre_limiter_max + drop, raw_max_rpm_torque / 5))
    pts.append((pre_limiter_max + drop * 2, 0.0))
    if pre_limiter_max + drop * 2 < max_rpm:
        pts.append((max_rpm, 0.0))
    curve = create_curve(pts)

    for key, line in (("friction", 1680), ("dynamicFriction", 1699)):
        if key not in merged:
            defaults[key] = (0.0, f"{LUA}:{line}")
    friction = num("friction", 0.0)
    dyn = num("dynamicFriction", 0.0)
    net: list[tuple[int, float]] = []
    for k in sorted(curve):
        if k < max_rpm:
            net.append((k, curve[k] - friction - dyn * k * RPM_TO_AV))
    peak_t = max(net, key=lambda p: (p[1], -p[0]))
    powers = [(k, t * k * RPM_TO_AV) for k, t in net]
    peak_p = max(powers, key=lambda p: (p[1], -p[0]))
    # Sobrealimentación activa solo si el motor nombra la sección Y una pieza activa la
    # aporta (combustionEngine.lua:2105, ``jbeamData.turbocharger and v.data[...]``).
    forced = []
    for key in FORCED_INDUCTION_KEYS:
        ref = merged.get(key)
        if isinstance(ref, str) and resolved.section_contributions(ref):
            forced.append(f"{key}={ref}")
    issues = []
    supported = True
    if forced:
        supported = False
        issues.append(
            f"sobrealimentación {forced}: los coeficientes salen de otros dispositivos "
            "(turbocharger.lua/supercharger.lua); la curva es la atmosférica"
        )
    if "inertia" not in merged:
        defaults["inertia"] = (0.1, f"{LUA}:1717")
    # G2.10: freno motor (l. 1858) y error de lectura del ralentí (l. 1705)
    if "engineBrakeTorque" not in merged:
        defaults["engineBrakeTorque"] = (friction * 2, f"{LUA}:1858")
    if "idleRPMRoughness" not in merged:
        defaults["idleRPMRoughness"] = (50.0, f"{LUA}:1705")
    engine = CombustionEngine(
        device_name=name,
        source_parts=list(merged.parts),
        idle_rpm=idle,
        max_rpm_jbeam=max_rpm_jbeam,
        max_available_rpm=max_avail_i,
        max_rpm=max_rpm,
        has_rev_limiter=has_lim,
        rev_limiter_type=lim_type,
        friction=friction,
        dynamic_friction=dyn,
        inertia=num("inertia", 0.1),  # l. 1717: 0,1 por defecto
        net_curve=net,
        peak_torque=peak_t,
        peak_power=peak_p,
        forced_induction=forced,
        supported=supported,
        issues=issues,
        defaults_used=defaults,
        table_rpms=sorted({p[0] for p in base_pts}),
        engine_brake_torque=num("engineBrakeTorque", friction * 2),
        idle_rpm_roughness=num("idleRPMRoughness", 50.0),
    )
    engine.wot, engine.wot_blockers = _wot(
        resolved, merged, engine, curve, var, resource_reader
    )
    engine.coast, engine.coast_blockers = _coast(resolved, engine)
    return engine


def _coast(
    resolved: ResolvedConfig, e: CombustionEngine
) -> tuple[list[tuple[float, float]], list[str]]:
    """Par de freno motor (> 0, N·m) a gas cerrado, motor nuevo, en régimen estable.

    ``updateTorque`` (combustionEngine.lua l. 731-800) con gas 0 y sin gas de ralentí:
    ``throttleMap = smoothmin(0, 1, 0.8) = 0`` → par de combustión 0 → ``instantLoad``
    = 0 → ``frictionTorque = friction + dynamicFriction·|ω| + engineBrakeTorque`` (l.
    780-781), limitado a ``|ω|·inertia·2000`` (l. 783). El gas de ralentí es 0 cuando
    ``ω ≥ idleAV + idleRPMRoughness/2`` (l. 620, 716). Puntos: ese límite inferior, las
    rpm de la tabla ``torque`` en el intervalo y el techo de rpm (la función es afín en
    ω: la interpolación lineal entre puntos es exacta)."""
    blockers = []
    for active in resolved.active:
        for row in parse_table(active.part.data.get("controller", [])).rows:
            name = str(row.get("fileName") or "")
            if name.rsplit("/", 1)[-1] in COAST_CONTROLLERS:
                blockers.append(
                    f"controlador '{name}' (pieza {active.part.name}): modifica el "
                    "par a gas cerrado (combustionEngine.lua l. 575-586, 777-780); "
                    "no portado"
                )
    if e.engine_brake_torque is None or e.idle_rpm_roughness is None:
        blockers.append("engineBrakeTorque o idleRPMRoughness sin valor")
    if blockers:
        return [], blockers
    low = e.idle_rpm + e.idle_rpm_roughness / 2
    rpms = sorted({r for r in e.table_rpms if low <= r <= e.max_rpm} | {low, e.max_rpm})
    out = []
    for r in rpms:
        av = r * RPM_TO_AV
        torque = e.friction + e.dynamic_friction * av + e.engine_brake_torque
        if torque > av * e.inertia * 2000:
            # l. 783: el tope de estabilidad actuaría (solo a rpm muy bajas); no portado
            return [], [
                f"tope |ω|·inertia·2000 activo a {r:g} rpm (l. 783); no portado"
            ]
        out.append((r, torque))
    return out, []


# --- curva a plena carga con turbo (G2.9) ------------------------------------------

# Constantes LITERALES de turbocharger.lua (l. 12-17): no son exactamente pi/30 ni
# 30/pi; se conservan para reproducir el redondeo del juego.
TURBO_RPM_TO_AV = 0.10471971768
TURBO_AV_TO_RPM = 9.5493
INV_PASCAL_TO_PSI = 0.00014503773773
PSI_TO_PASCAL = 6894.757293178
# combustionEngine.lua l. 19: rpmToAV de getTorqueData (fricción dinámica)
ENGINE_RPM_TO_AV = 0.104719755
# l. 21: torqueToPower (hp) de getTorqueData
TORQUE_TO_POWER_HP = 0.0001404345295653085
# turbocharger.lua l. 544-546: «1 psi = 6 % más potencia»; 1 Pa = 0,00087 %
BOOST_PER_PASCAL = 0.0000087


def _f32(x: float) -> float:
    """Valor tras guardarlo en ``ffi.new("float[?]")`` (``createCurve(…, true)``)."""
    return struct.unpack("f", struct.pack("f", x))[0]


@dataclass
class TurboEstimate:
    """Parámetros del turbo leídos del JBeam y curvas de ``turbocharger.init``."""

    section: str
    source_parts: list[str]
    max_turbo_av: float
    estimated_wastegate_limit_psi: float
    max_exhaust_power: float
    back_pressure_coef: float
    friction_coef: float
    pressure_curve: dict[int, float]  # rpm de la turbina -> psi (float32)
    exhaust_curve: dict[int, float]
    efficiency_curve: dict[int, float]
    inv_eng_max_av: float
    defaults_used: dict[str, tuple[Any, str]] = field(default_factory=dict)

    def coef(self, rpm: int) -> float:
        """Coeficiente de par a ``rpm`` (cuerpo del bucle de ``getTorqueCoefs``,
        l. 536-545; daño y desgaste = 1, motor nuevo)."""
        drive = (
            0.9
            * rpm
            * TURBO_RPM_TO_AV
            * self.inv_eng_max_av
            * self.exhaust_curve.get(rpm, 1.0)  # ``or 1`` (tabla Lua)
            * self.max_exhaust_power
            - self.friction_coef
        )
        turbo_av = min(
            math.sqrt(max(drive, 0.0) / self.back_pressure_coef), self.max_turbo_av
        )
        turbo_rpm = math.floor(turbo_av * TURBO_AV_TO_RPM)
        if turbo_rpm not in self.pressure_curve:
            # el array FFI va de 0 al máximo de la tabla; fuera, el Lua leería fuera
            # de rango: no se supone nada
            raise CombustionEngineError(
                f"turbo: rpm de turbina {turbo_rpm} fuera de la tabla pressurePSI"
            )
        pressure = min(
            self.pressure_curve[turbo_rpm], self.estimated_wastegate_limit_psi
        )
        eff = self.efficiency_curve.get(rpm, 0.0)  # ``or 0``
        return 1 + BOOST_PER_PASCAL * pressure * PSI_TO_PASCAL * eff


@dataclass
class WotCurve:
    """Par neto a plena carga en cada rpm entera (estado estable, motor nuevo)."""

    kind: str  # "na" (exacta según el Lua) | "turbo_estimate" (estimación del juego)
    curve: dict[int, float]  # rpm entera 0..max_rpm -> N·m (coeficiente a esa rpm)
    peak_torque: tuple[int, float]  # sobre k < max_rpm, como getTorqueData
    peak_power: tuple[int, float]  # (rpm, W)
    basis: str
    turbo: TurboEstimate | None = None
    # Curva tal como la muestra el juego (getTorqueData l. 89): en la rpm k usa
    # ``turboCoefs[k]``, que es el coeficiente calculado para k - 1 (desfase de 1 rpm)
    display_peak_torque: tuple[int, float] | None = None
    display_peak_power_hp: tuple[int, float] | None = None
    display_max_abs_delta: float | None = None


def _rows(raw: Any, what: str, width: int, var: dict) -> list[list[float]]:
    """Filas numéricas de una tabla sin cabecera (``pressurePSI``, ``engineDef``)."""
    if not isinstance(raw, list) or not raw:
        raise CombustionEngineError(f"turbo: {what} ausente o vacía")
    out = []
    for row in raw:
        if not isinstance(row, list) or len(row) < width:
            raise CombustionEngineError(f"turbo: fila de {what} no válida: {row!r}")
        vals = [evaluate(v, var) for v in row[:width]]
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals
        ):
            raise CombustionEngineError(f"turbo: fila de {what} no numérica: {row!r}")
        out.append([float(v) for v in vals])
    if len(out) < 2:
        raise CombustionEngineError(f"turbo: {what} con menos de 2 puntos")
    return out


def read_turbocharger(
    resolved: ResolvedConfig, section: str, engine_max_rpm: float, var: dict
) -> TurboEstimate:
    """``turbocharger.init`` (l. 345-500) en lo que afecta a ``getTorqueCoefs``.

    Solo admite ``wastegateStart``/``wastegateLimit`` numéricos (o el límite ausente):
    por marcha (tabla) la estimación usa la ÚLTIMA entrada de ``pairs``, cuyo orden no
    está definido en Lua; se rechaza."""
    merged = unify_dict_section(resolved, section)
    left = [k for k in merged.values if k[:1] == "$"]
    if left:
        raise CombustionEngineError(f"turbo: modificadores sin valor base {left}")
    defaults: dict[str, tuple[Any, str]] = {}

    def num(key: str, default: float, line: int) -> float:
        if key not in merged:
            defaults[key] = (default, f"{TURBO_LUA}:{line}")
            return default
        v = evaluate(merged.get(key), var)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise CombustionEngineError(f"turbo: {section}.{key} no numérico: {v!r}")
        return float(v)

    tp = _rows(merged.get("pressurePSI"), "pressurePSI", 2, var)
    ed = _rows(merged.get("engineDef"), "engineDef", 3, var)
    max_turbo_av = 1.0
    for x, _ in tp:
        max_turbo_av = max(x * TURBO_RPM_TO_AV, max_turbo_av)
    pressure = {k: _f32(v) for k, v in create_curve([(x, y) for x, y in tp]).items()}
    exhaust = create_curve([(r[0], min(r[2], 1.0)) for r in ed])
    efficiency = create_curve([(r[0], r[1]) for r in ed])
    for key in ("wastegateStart", "wastegateLimit"):
        if isinstance(merged.get(key), (dict, list)):
            raise CombustionEngineError(
                f"turbo: {key} por marcha no soportado (orden de pairs indefinido)"
            )
    start = num("wastegateStart", 0.0, 426) * PSI_TO_PASCAL
    if "wastegateLimit" in merged:
        limit = num("wastegateLimit", 0.0, 437) * PSI_TO_PASCAL
    else:
        defaults["wastegateLimit"] = ("wastegateStart + 0,01 psi", f"{TURBO_LUA}:441")
        limit = start + 0.01 * PSI_TO_PASCAL
    rng = limit - start
    bpc = num("backPressureCoef", 0.0005, 457)
    if bpc <= 0:
        raise CombustionEngineError(f"turbo: backPressureCoef {bpc} no positivo")
    if engine_max_rpm <= 0:
        raise CombustionEngineError("turbo: maxRPM del motor no positivo")
    return TurboEstimate(
        section=section,
        source_parts=list(merged.parts),
        max_turbo_av=max_turbo_av,
        # l. 532: (maxWastegateStart + maxWastegateRange · 0,5) · invPascalToPSI
        estimated_wastegate_limit_psi=(start + rng * 0.5) * INV_PASCAL_TO_PSI,
        max_exhaust_power=num("maxExhaustPower", 1.0, 455),
        back_pressure_coef=bpc,
        friction_coef=num("frictionCoef", 0.01, 458),
        pressure_curve=pressure,
        exhaust_curve=exhaust,
        efficiency_curve=efficiency,
        # l. 475: assignedEngine.maxRPM ya es el del limitador (init en l. 2105)
        inv_eng_max_av=1 / (engine_max_rpm * TURBO_RPM_TO_AV),
        defaults_used=defaults,
    )


def _peaks(
    curve: dict[int, float], max_rpm: float
) -> tuple[tuple[int, float], tuple[int, float]]:
    pts = [(k, t) for k, t in sorted(curve.items()) if k < max_rpm]
    peak_t = max(pts, key=lambda p: (p[1], -p[0]))
    powers = [(k, t * k * RPM_TO_AV) for k, t in pts]
    peak_p = max(powers, key=lambda p: (p[1], -p[0]))
    return peak_t, peak_p


def _wot(
    resolved: ResolvedConfig,
    merged: UnifiedSection,
    e: CombustionEngine,
    curve: dict[int, float],
    var: dict,
    reader: ResourceReader | None,
) -> tuple[WotCurve | None, list[str]]:
    """Curva a plena carga, o los motivos por los que no se puede dar."""
    active = {f.split("=", 1)[0] for f in e.forced_induction}
    blockers = [
        f"{k}: sobrealimentación no portada ({k}.lua)"
        for k in ("supercharger", "nitrousOxideInjection")
        if k in active
    ]
    if blockers:
        return None, blockers
    top = math.floor(e.max_rpm)

    def friction(k: int) -> float:
        return e.friction + e.dynamic_friction * k * RPM_TO_AV

    if "turbocharger" not in active:
        wc = {k: curve[k] - friction(k) for k in range(0, top + 1) if k in curve}
        pt, pp = _peaks(wc, e.max_rpm)
        return WotCurve("na", wc, pt, pp, "getTorqueData l. 41-49 (curva NA)"), []
    try:
        turbo = read_turbocharger(resolved, merged.get("turbocharger"), e.max_rpm, var)
        wc = {
            k: curve[k] * turbo.coef(k) - friction(k)
            for k in range(0, top + 1)
            if k in curve
        }
        # tal como lo muestra el juego: coeficiente de k - 1 en k (``or 0`` en k = 0)
        disp = {
            k: curve[k] * (turbo.coef(k - 1) if k >= 1 else 0.0)
            - e.friction
            - e.dynamic_friction * k * ENGINE_RPM_TO_AV
            for k in range(0, top + 1)
            if k in curve and k < e.max_rpm
        }
    except CombustionEngineError as exc:
        return None, [f"turbocharger: {exc}"]
    pt, pp = _peaks(wc, e.max_rpm)
    dt_ = max(disp.items(), key=lambda p: (p[1], -p[0]))
    dp_ = max(
        ((k, t * k * TORQUE_TO_POWER_HP) for k, t in disp.items()),
        key=lambda p: (p[1], -p[0]),
    )
    return (
        WotCurve(
            "turbo_estimate",
            wc,
            pt,
            pp,
            "estimación de régimen estable del juego: turbocharger.lua getTorqueCoefs "
            "(l. 527-547; válvula de descarga en start + rango/2) × curva NA - "
            "fricción (combustionEngine.lua getTorqueData l. 82-103)",
            turbo=turbo,
            # el juego numera la rpm del pico como k + 1 (l. 45-51)
            display_peak_torque=(dt_[0] + 1, dt_[1]),
            display_peak_power_hp=(dp_[0] + 1, dp_[1]),
            display_max_abs_delta=max(abs(disp[k] - wc[k]) for k in disp),
        ),
        [],
    )
