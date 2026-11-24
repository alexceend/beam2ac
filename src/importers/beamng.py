"""Importador BeamNG → IR (``src.ir``). No genera nada de Assetto Corsa.

Reutiliza los extractores existentes (``src.jbeam``, ``src.extract``) a través de
``src.pipeline.physics_preview`` y traduce sus resultados a rutas neutras del IR,
conservando la procedencia exacta (archivo, localizador, código que lo obtuvo).

Transición (G1): la vista previa calcula también borradores de AC, que este importador
NO usa. La tracción, el radio de la rueda motriz y el papel de cada diferencial
(antes ``analyse``/``awd_axles`` de ``src/ac/drivetrain.py``) viven desde G2.5c en
``src/extract/traction.py`` y se leen de ``pv.drivetrain.layout``. Aún se leen de la
vista previa de AC ``pv.drivetrain.shift_controller_raw`` (``vehicleController``) y el
objeto ``Drivetrain`` de ``src/extract`` que transporta (G2.6).

Lo que este importador deja FUERA del IR a propósito (es del destino): el ajuste
``COAST_REF`` lineal y el tipo de suspensión de AC. Se calculan en
``src.ir.legacy_facts`` a partir del IR.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields
from pathlib import Path
from typing import Any

from src.attrs import schema as S
from src.ir.param import Param
from src.ir.vehicle import (
    AxleSuspension,
    BrakeAxle,
    Differential,
    TyreAxle,
    UnknownItem,
    VehicleIR,
    ensure_entry,
)

FORMAT = "beamng.jbeam"


def _code(path: str, locator: str) -> S.EvidenceRef:
    return S.EvidenceRef(type="code", path=path, locator=locator)


def _same(values: list[Any]) -> Any:
    """Valor común de las ruedas de un eje; ``None`` si difieren o falta alguno."""
    if (
        not values
        or any(v is None for v in values)
        or any(v != values[0] for v in values)
    ):
        return None
    return values[0]


def _clutch_additional_inertia(pv: Any, engine: str) -> float | None:
    """Σ ``additionalEngineInertia`` de los hijos del motor.

    camsoEngine.lua l. 995-1001: solo los ``clutchlike``. Solo si todos son
    ``frictionClutch`` o ``torqueConverter`` (2026-11-21: ambos son ``clutchlike`` y su valor por
    defecto, 0, está verificado: frictionClutch.lua l. 242, torqueConverter.lua l. 8 y 146); si no,
    ``None``. Sin los convertidores, el INERTIA del motor de las automáticas y CVT quedaba sin
    resolver y AC lo leía como 0: par no finito, 0 rpm y el motor en rojo."""
    from src.extract.drivetrain import DrivetrainDataError, read_devices

    if pv.resolved is None:
        return None
    try:
        devices = read_devices(pv.resolved)
    except DrivetrainDataError:
        return None
    children = [d for d in devices.values() if d.input_name == engine]
    if not children or any(
        # dctGearbox: clutchlike, additionalEngineInertia = ... or 0 (dctGearbox.lua l. 8 y 387)
        d.type not in ("frictionClutch", "torqueConverter", "dctGearbox")
        for d in children
    ):
        return None
    total = 0.0
    for d in children:
        v = d.params.get("additionalEngineInertia", 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        total += float(v)
    return total


# G2.10 (D-G29a): dispositivos ``clutchlike`` del juego instalado (BeamNG 0.38.6) y de
# dónde sale su ``additionalEngineInertia`` (Lua leído, solo lectura). ``None``: el
# módulo no define el campo y combustionEngine.lua:938 usa ``or 0``.
CLUTCHLIKE_ADDITIONAL_INERTIA = {
    "frictionClutch": (
        "frictionClutch.lua l. 242: jbeamData.additionalEngineInertia or 0"
    ),
    "centrifugalClutch": (
        "centrifugalClutch.lua l. 242: jbeamData.additionalEngineInertia or 0"
    ),
    "dctGearbox": "dctGearbox.lua l. 387: jbeamData.additionalEngineInertia or 0",
    "torqueConverter": (
        "torqueConverter.lua l. 146: jbeamData.additionalEngineInertia or 0"
    ),
    "viscousClutch": "viscousClutch.lua l. 99: jbeamData.additionalEngineInertia or 0",
    "compressor": None,
    "hydraulicPump": None,
}


# G2.10 (D-G29b): error máximo de la interpolación lineal (la de AC, curva de AC en
# AC) de los puntos de engine.wot_torque_curve frente a la curva del motor en cada
# rpm entera. Es una cota de fidelidad del muestreo, no un factor de corrección.
WOT_KNOT_TOLERANCE_NM = 0.1


def refine_knots(
    curve: dict[int, float], knots: list[int], tolerance: float
) -> list[int]:
    """Nudos + las rpm enteras necesarias para que la interpolación lineal entre nudos
    consecutivos quede a ≤ ``tolerance`` de ``curve`` en cada rpm entera intermedia.

    Inserción recursiva del punto de mayor error en cada tramo (como Ramer-Douglas-
    Peucker con error vertical). Determinista; los nudos de partida se conservan y deben
    estar en ``curve``."""
    if any(k not in curve for k in knots):
        raise ValueError("refine_knots: nudo fuera de la curva")
    out = set(knots)

    def split(a: int, b: int) -> None:
        ya, yb = curve[a], curve[b]
        worst, at = 0.0, None
        for k in range(a + 1, b):
            if k not in curve:
                continue
            d = abs(ya + (yb - ya) * (k - a) / (b - a) - curve[k])
            if d > worst:
                worst, at = d, k
        if at is not None and worst > tolerance:
            out.add(at)
            split(a, at)
            split(at, b)

    ordered = sorted(out)
    for a, b in zip(ordered, ordered[1:]):
        split(a, b)
    return sorted(out)


def _combustion_clutch_additional_inertia(
    pv: Any, engine: str
) -> tuple[float | None, str, list[str]]:
    """Σ ``additionalEngineInertia`` de los hijos de un ``combustionEngine`` (G2.10).

    combustionEngine.lua l. 932-941 (``validate``): para cada hijo ``clutchlike`` suma
    ``child.additionalEngineInertia or 0``; un hijo que no es ``clutchlike`` invalida el
    tren motriz. Solo los tipos de :data:`CLUTCHLIKE_ADDITIONAL_INERTIA` (Lua leído); un
    tipo desconocido o un valor no numérico → ``None`` con el motivo. Sin hijos, el Lua
    no suma nada (0). Devuelve (valor, motivo si ``None``, descripción de los hijos)."""
    from src.extract.drivetrain import DrivetrainDataError, read_devices

    if pv.resolved is None:
        return None, "configuración sin resolver", []
    try:
        devices = read_devices(pv.resolved)
    except DrivetrainDataError as exc:
        return None, f"tren motriz ilegible: {exc}", []
    total = 0.0
    seen: list[str] = []
    for d in (d for d in devices.values() if d.input_name == engine):
        if d.type not in CLUTCHLIKE_ADDITIONAL_INERTIA:
            return None, f"hijo '{d.name}' de tipo '{d.type}' sin analizar", seen
        v = d.params.get("additionalEngineInertia", 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, f"'{d.name}'.additionalEngineInertia no numérico: {v!r}", seen
        src = CLUTCHLIKE_ADDITIONAL_INERTIA[d.type] or (
            f"{d.type}.lua sin el campo: combustionEngine.lua:938 'or 0'"
        )
        seen.append(f"{d.name} ({d.type}) = {float(v):g} [{src}]")
        total += float(v)
    return total, "", seen


def _mass_note(g: Any) -> str:
    """Nota de ``mass.total``; nombra las filas de nodo reemplazadas (G2.13)."""
    note = "Σ nodeWeight + masa generada de las ruedas (pressureWheels)"
    overrides = getattr(g, "node_overrides", None) or []
    if overrides:
        kg = sum(o.replaced_mass for o in overrides)
        ids = ", ".join(sorted({o.id for o in overrides}))
        note += (
            f"; {len(overrides)} filas de nodo con id repetido en la misma pieza "
            f"reemplazadas por la última (regla del juego, tableSchema.lua): "
            f"no se cuentan {kg:g} kg ({ids})"
        )
    return note


def import_preview(pv: Any, driver_mass_kg: float | None = None) -> VehicleIR:
    """IR a partir de una ``PhysicsPreview`` (vehículo cargado en solo lectura)."""
    src = pv.source
    ir = VehicleIR()
    data = S.EvidenceRef(
        type="file", path=src.description, locator=f"config {src.variant}.pc"
    )

    def put(path: str, value: Any, unit: str, kind: str, *refs, note="", key=""):
        """Dato de la configuración (lleva la referencia a la .pc delante)."""
        ir.set(path, Param.of(value, unit, kind, [data, *refs], note, key))

    def put_raw(path: str, value: Any, unit: str, kind: str, refs, note="", key=""):
        ir.set(path, Param.of(value, unit, kind, list(refs), note, key))

    # --- identidad, formatos y valores declarados --------------------------------
    info_ref = S.EvidenceRef(type="file", path=src.description, locator="info.json")
    cfg_ref = S.EvidenceRef(
        type="file", path=src.description, locator=f"info_{src.variant}.json"
    )
    ir.identity.vehicle_id = Param.of(src.vehicle_id, "text", "extracted")
    ir.identity.variant = Param.of(src.variant, "text", "extracted")
    ir.formats.source_format = Param.of(FORMAT, "text", "extracted")
    for key, path in (
        ("Name", "identity.name"),
        ("Type", "identity.source_tool"),
        ("Author", "identity.author"),
    ):
        if key in src.model_info:
            put_raw(path, src.model_info[key], "text", "extracted", [info_ref], key=key)
        else:  # G2.8: ausente con motivo
            ir.set(path, Param.missing("text", f"info.json sin '{key}'", key))
    declared_note = "valor declarado por el exportador (UI), no medido"
    for key, path, unit in (
        ("Weight", "declared.weight", "kg"),
        ("Power", "declared.power", "hp"),
        ("Torque", "declared.torque", "N·m"),
        ("PowerPeakRPM", "declared.power_peak_speed", "rpm"),
        ("TorquePeakRPM", "declared.torque_peak_speed", "rpm"),
        ("Drivetrain", "declared.drivetrain", "enum"),
        ("Transmission", "declared.transmission", "enum"),
        ("Top Speed", "declared.top_speed", "m/s"),
        ("0-100 km/h", "declared.accel_0_100", "s"),
    ):
        if key in src.info:
            put_raw(
                path,
                src.info[key],
                unit,
                "extracted",
                [cfg_ref],
                note=declared_note,
                key=key,
            )
        else:  # G2.8: ausente con motivo (el exportador 200409 no trae el archivo)
            ir.set(
                path,
                Param.missing(
                    unit,
                    f"info_{src.variant}.json "
                    + (f"sin '{key}'" if src.info else "inexistente o vacío"),
                    key,
                ),
            )
    w, wp = src.info.get("Weight"), src.info.get("Weight/Power")
    if isinstance(w, (int, float)) and isinstance(wp, (int, float)) and wp:
        put_raw(
            "declared.implied_power",
            w / wp,
            "hp",
            "computed",
            [cfg_ref],
            note="Weight / (Weight/Power): potencia declarada "
            "sin el truncado del entero 'Power'",
        )
    for rel, digest in sorted(src.lua_sha256.items()):
        name = rel.rsplit("/", 1)[-1].removesuffix(".lua")
        ir.formats.code_versions[name] = Param.of(
            digest,
            "text",
            "extracted",
            [S.EvidenceRef(type="code", path=f"{src.description}{rel}", sha256=digest)],
            source_key=rel,
        )

    # --- motor ---------------------------------------------------------------------
    e = pv.engine
    if e is not None:
        ec = "src/extract/engine_camso.py"
        put(
            "engine.device_type",
            e.raw.device_type,
            "enum",
            "extracted",
            _code(ec, "find_engine_device"),
        )
        put(
            "engine.idle_speed",
            e.raw.idle_rpm,
            "rpm",
            "extracted",
            _code(ec, "read_raw_engine: idleRPM"),
            key=f"{e.raw.device_name}.idleRPM",
        )
        put(
            "engine.inertia",
            e.raw.inertia,
            "kg·m²",
            "extracted",
            _code(ec, "read_raw_engine: inertia"),
            note="solo el motor; falta additionalEngineInertia "
            "del embrague (camsoEngine.lua:1001)",
            key=f"{e.raw.device_name}.inertia",
        )
        extra = _clutch_additional_inertia(pv, e.raw.device_name)
        put(
            "engine.clutch_additional_inertia",
            extra,
            "kg·m²",
            "computed",
            _code(ec, "camsoEngine.lua:995-1001 (validate)"),
            S.EvidenceRef(
                type="code",
                path="lua/vehicle/powertrain/frictionClutch.lua",
                locator="l. 242: additionalEngineInertia = jbeamData.… or 0",
            ),
            note="Σ additionalEngineInertia de los hijos frictionClutch del "
            "motor (el Lua la suma a device.inertia); ausente = 0",
        )
        put(
            "engine.rev_limiter_mode",
            {
                True: "activo",
                False: "sin_limitador",
                None: "independiente_del_conflicto",
            }[e.has_cut_limiter],
            "enum",
            "computed",
            _code(
                ec,
                "_limiter (Lua 47437d5c l. 2003-2007; 91392d70 l. 2000-2004)",
            ),
            note=e.rpm_ceiling_basis,
        )
        put(
            "engine.speed_ceiling",
            e.rpm_ceiling,
            "rpm",
            "computed",
            _code(ec, "_limiter"),
            note=e.rpm_ceiling_basis,
        )
        put(
            "engine.wot_torque_curve",
            [[p.rpm, round(p.net, 2)] for p in e.curve if p.rpm <= e.rpm_ceiling],
            "table:rpm->N·m",
            "computed",
            _code(ec, "build_engine_model: fila 99 − fricción"),
            note="par neto WOT con boost incluido; 2 decimales como render_power_lut",
        )
        ref_refs = _code(
            ec, "build_engine_model: fila 100 − fricción (referencia de Automation)"
        )
        ref_note = (
            "fila 100 del mapa: la que Automation usa a gas a fondo; BeamNG nunca la lee "
            "(throttleLookup ≤ 99). Solo valida los info_*.json"
        )
        if e.reference_curve and e.reference_peak_torque and e.reference_peak_power:
            put(
                "engine.reference_wot_torque_curve",
                [
                    [p.rpm, round(p.net, 2)]
                    for p in e.reference_curve
                    if p.rpm <= e.rpm_ceiling
                ],
                "table:rpm->N·m",
                "computed",
                ref_refs,
                note=ref_note,
            )
            put(
                "engine.reference_peak_torque",
                e.reference_peak_torque.net,
                "N·m",
                "computed",
                ref_refs,
                note=ref_note,
            )
            put(
                "engine.reference_peak_power",
                e.reference_peak_power.power_w,
                "W",
                "computed",
                ref_refs,
                note=ref_note,
            )
        put(
            "engine.peak_torque",
            e.peak_torque.net,
            "N·m",
            "computed",
            _code(ec, "peak_torque"),
        )
        put(
            "engine.peak_torque_speed",
            e.peak_torque.rpm,
            "rpm",
            "computed",
            _code(ec, "peak_torque"),
        )
        put(
            "engine.peak_power",
            e.peak_power.power_w,
            "W",
            "computed",
            _code(ec, "peak_power"),
        )
        put(
            "engine.peak_power_speed",
            e.peak_power.rpm,
            "rpm",
            "computed",
            _code(ec, "peak_power"),
        )
        # G2.8: camsoEngine.lua crea turbo y compresor desde la sección del motor
        # (l. 2132-2146); antes solo se miraba el turbo y un motor con compresor
        # quedaba como «sin sobrealimentación».
        put(
            "engine.forced_induction",
            e.raw.turbo_section_name is not None
            or e.raw.supercharger_section_name is not None,
            "enum",
            "extracted",
            _code(ec, "read_raw_engine: turbocharger, supercharger"),
        )
        if e.max_boost_bar is not None:
            boost_refs = _code(ec, "build_engine_model: columna Pressure de la fila 99")
            kind = (
                "both"
                if e.raw.turbo_section_name and e.raw.supercharger_section_name
                else "supercharger" if e.raw.supercharger_section_name else "turbo"
            )
            put("engine.boost_kind", kind, "enum", "computed", boost_refs)
            put(
                "engine.boost_pressure_curve",
                [
                    [p.rpm, round(p.pressure, 4)]
                    for p in e.curve
                    if p.pressure is not None and p.rpm <= e.rpm_ceiling
                ],
                "table:rpm->bar",
                "computed",
                boost_refs,
                note="presión absoluta del mapa a gas a fondo; para el [TURBO_n] con lag que se escribe al exportar (src/ac/adjustable_turbo.py)",
            )
            put("engine.boost_max", e.max_boost_bar, "bar", "computed", boost_refs)
            if e.full_boost_rpm is not None:
                put(
                    "engine.boost_full_rpm",
                    e.full_boost_rpm,
                    "rpm",
                    "computed",
                    boost_refs,
                )
        for name, what in (
            (e.raw.supercharger_section_name, "compresor (supercharger)"),
            (e.raw.nitrous_section_name, "óxido nitroso (nitrousOxideInjection)"),
        ):
            if name is not None:
                ir.unknown.append(
                    UnknownItem(
                        e.raw.device_name,
                        name,
                        None,
                        f"{what} detectado y NO interpretado: su efecto sobre el par "
                        "no está analizado (estado del modelo de motor)",
                    )
                )
        basis = e.status.get("coast_curve", "")
        put("engine.coast_basis", basis or None, "text", "computed")
        if e.coast:
            put(
                "engine.coast_curve",
                [[r, t] for r, t in e.coast],
                "table:rpm->N·m",
                "computed",
                _code(ec, "build_engine_model: freno motor"),
                note=basis,
            )
        else:
            ir.engine.coast_curve = Param.missing(
                "table:rpm->N·m", f"freno motor sin calcular: {basis}"
            )

    elif getattr(pv, "combustion_engine", None) is not None:
        # G2.9: motor ``combustionEngine`` (exportador 200409 de Automation)
        _import_combustion_engine(ir, pv, put)

    # --- transmisión ---------------------------------------------------------------
    if pv.drivetrain is not None:
        _import_drivetrain(ir, pv, put, e)

    # --- masas y geometría ---------------------------------------------------------
    g = pv.geometry
    if g is not None:
        mg = "src/extract/mass_geometry.py"
        put(
            "mass.total",
            g.total_mass,
            "kg",
            "measured",
            _code(mg, "compute_mass_geometry"),
            note=_mass_note(g),
        )
        # G2.26: tensor del vehículo entero (diagonal, ejes AC) para [EXPLICIT_INERTIA]
        put(
            "mass.inertia_about_cog",
            [g.inertia_ac[i][i] for i in range(3)],
            "kg·m²",
            "measured",
            _code(mg, "_inertia"),
            note="diagonal [X cabeceo, Y guiñada, Z balanceo] respecto al CdG del "
            "vehículo entero; masas puntuales en los nodos y masa generada de las "
            "ruedas en su centro",
        )
        put(
            "mass.wheel_assembly",
            g.wheel_assembly_mass,
            "kg",
            "measured",
            _code(mg, "wheel_assembly_mass"),
            note="cota inferior de la masa no suspendida",
        )
        put(
            "dimensions.wheelbase",
            g.wheelbase,
            "m",
            "measured",
            _code(mg, "_group_axles"),
        )
        put(
            "dimensions.front_axle_fraction",
            g.front_axle_fraction,
            "1",
            "measured",
            _code(mg, "front_axle_fraction"),
        )
        if len(g.axles) == 2:
            put(
                "dimensions.track_front",
                g.axles[0].track,
                "m",
                "measured",
                _code(mg, "_group_axles"),
            )
            put(
                "dimensions.track_rear",
                g.axles[1].track,
                "m",
                "measured",
                _code(mg, "_group_axles"),
            )
            # G2.18: y_centro_rueda(eje) − y_CdG (AC, + arriba), para BASEY
            centers = {w.name: w.center_ac for w in g.wheels}
            for pos, axle in zip(("front", "rear"), g.axles):
                ys = [centers[n][1] for n in axle.wheels if n in centers]
                path = f"dimensions.wheel_center_height_rel_cog_{pos}"
                if not ys or len(ys) != len(axle.wheels):
                    ir.set(path, Param.missing("m", "ruedas del eje sin centro medido"))
                    continue
                put(
                    path,
                    sum(ys) / len(ys) - g.cog_ac[1],
                    "m",
                    "measured",
                    _code(mg, "compute_mass_geometry"),
                    note="media de los centros de rueda del eje − CdG del vehículo "
                    "entero (nodos; independiente del suelo)",
                )
        put(
            "dimensions.cog_height",
            g.cog_height,
            "m",
            "measured",
            _code(mg, "cog_height"),
            note="hipótesis: suelo = neumático sin carga",
        )

    # --- chasis: suspensión, neumáticos, frenos, aerodinámica ----------------------
    if pv.chassis is not None:
        _import_chassis(ir, pv.chassis, put)
        # G2.19: tablas medidas en el juego (solo aerodinámica por triángulos)
        _import_aero_bench(ir, pv, getattr(pv.chassis, "aero_model", None))
        # G2.20: neumático medido en el banco del juego
        _import_tyre_bench(ir, pv)

    if pv.fuel.get("ok"):
        fp = _code("src/pipeline/physics_preview.py", "fuel_values")
        put("mass.fuel_capacity", pv.fuel["fuelCapacity"], "L", "extracted", fp)
        put("mass.fuel_start", pv.fuel["startingFuelCapacity"], "L", "extracted", fp)
    elif pv.fuel.get("reason"):
        for path in ("mass.fuel_capacity", "mass.fuel_start"):
            ir.set(path, Param.missing("L", f"fuel_values: {pv.fuel['reason']}"))
    if driver_mass_kg is not None:
        put_raw(
            "mass.driver",
            driver_mass_kg,
            "kg",
            "manual",
            [
                S.EvidenceRef(
                    type="doc",
                    path="CLAUDE.md",
                    locator="Decisiones del proyecto, 2 (piloto)",
                )
            ],
            note="decisión del proyecto: 70 kg configurables; Kunos usa 75 kg",
        )
    if g is not None and len(g.axles) == 2 and pv.resolved is not None:
        # G2.45: posición ESTIMADA del piloto (el origen no la declara)
        from src.extract import driver_position as dp
        from src.extract.nodes import collect_nodes

        zs = sorted(ax.longitudinal_ac for ax in g.axles)
        try:
            pos = dp.analyse(pv.resolved, collect_nodes(pv.resolved, []), zs[0], zs[1])
        except ValueError as exc:
            ir.set("mass.driver_position", Param.missing("m", str(exc)))
        else:
            rel = [c - o for c, o in zip(pos.com, g.cog_ac)]
            put(
                "mass.driver_position",
                rel,
                "m",
                "estimated",
                _code("src/extract/driver_position.py", "estimate"),
                note=f"centro de masas del piloto sentado, ESTIMADO ({pos.source}): "
                f"{pos.note}; ojos → caderas {list(dp.EYES_TO_HIPS)} (mediana de 107 "
                f"coches de AC) + caderas → centro de masas {list(dp.HIPS_TO_COM)} "
                f"(hipótesis antropométrica); ± {dp.UNCERTAINTY} m por eje; relativo "
                "al CdG del vehículo entero; ejes AC",
            )
    elif g is not None:
        ir.set(
            "mass.driver_position",
            Param.missing(
                "m", "vehículo sin dos ejes: sin batalla para situar al piloto"
            ),
        )

    from . import automation

    # ExporterVersion es un dato declarado: se registra siempre que exista (también si
    # el origen no cumple la procedencia de Automation; la familia lo decide).
    comb = getattr(pv, "combustion_engine", None) is not None
    automation.enrich_metadata(ir, src, comb)

    # --- datos del origen no interpretados -----------------------------------------
    if pv.resolved is not None:
        for entry in pv.resolved.unreachable_pc:
            ir.unknown.append(
                UnknownItem(
                    f"{src.variant}.pc",
                    entry,
                    src.pc.parts.get(entry) if hasattr(src.pc, "parts") else None,
                    "la .pc elige una pieza para un slot que no se recorre (BeamNG "
                    "la ignora)",
                )
            )
    _explain_missing(ir, pv, automation.is_automation(src, comb))
    return ir


def _import_combustion_engine(ir: VehicleIR, pv: Any, put) -> None:
    """Motor ``combustionEngine`` → IR (G2.9).

    Lo que el adaptador lee del JBeam es ``extracted``; lo que aplica la semántica del
    Lua (techo, picos, curva NA) es ``computed``; la curva con turbo es la ESTIMACIÓN
    de régimen estable del propio juego (``getTorqueCoefs``) y queda ``estimated``.
    Una clave ausente que el Lua sustituye por su valor por defecto queda ``computed``
    con la línea del Lua. Lo que no se sabe calcular (compresor, N2O, freno motor)
    queda ausente con su motivo; nunca se usa la curva atmosférica como si fuera la de
    plena carga de un motor sobrealimentado."""
    from src.extract.engine_combustion import GAME_LUA, LUA, TURBO_LUA

    c = pv.combustion_engine
    xc = "src/extract/engine_combustion.py"
    name = c.device_name

    def lua_at(locator: str, path: str = LUA) -> S.EvidenceRef:
        return S.EvidenceRef(
            type="code", path=path, locator=locator, sha256=GAME_LUA[path]
        )

    def jbeam_or_default(path, key, value, unit, locator, note=""):
        """Valor del JBeam, o el valor por defecto del Lua si la clave falta."""
        if key in c.defaults_used:
            default, line = c.defaults_used[key]
            put(
                path,
                value,
                unit,
                "computed",
                _code(xc, locator),
                lua_at(line.rsplit(":", 1)[1]),
                note=(note + "; " if note else "")
                + f"{name}.{key} ausente: valor por defecto del juego ({line})",
                key=f"{name}.{key}",
            )
        else:
            put(
                path,
                value,
                unit,
                "extracted",
                _code(xc, locator),
                note=note,
                key=f"{name}.{key}",
            )

    put(
        "engine.device_type",
        "combustionEngine",
        "enum",
        "extracted",
        _code(xc, "find_combustion_engine"),
        key=name,
    )
    put(
        "engine.idle_speed",
        c.idle_rpm,
        "rpm",
        "extracted",
        _code(xc, "read_combustion_engine: idleRPM"),
        key=f"{name}.idleRPM",
    )
    jbeam_or_default(
        "engine.inertia",
        "inertia",
        c.inertia,
        "kg·m²",
        "read_combustion_engine: inertia",
        "solo el motor; el Lua suma additionalEngineInertia de los hijos "
        "clutchlike (combustionEngine.lua:938)",
    )
    # G2.10 (D-G29a): todos los hijos clutchlike cuyo Lua se ha leído (G2.9: solo
    # frictionClutch). El camino camsoEngine sigue con _clutch_additional_inertia.
    extra, why_extra, kids = _combustion_clutch_additional_inertia(pv, name)
    if extra is None:
        ir.set(
            "engine.clutch_additional_inertia",
            Param.missing("kg·m²", f"{why_extra} (combustionEngine.lua:932-941)"),
        )
    else:
        put(
            "engine.clutch_additional_inertia",
            extra,
            "kg·m²",
            "computed",
            _code("src/importers/beamng.py", "_combustion_clutch_additional_inertia"),
            lua_at("l. 932-941 (validate: + child.additionalEngineInertia or 0)"),
            note="Σ additionalEngineInertia de los hijos clutchlike del motor: "
            + ("; ".join(kids) if kids else "sin hijos (no se suma nada)"),
        )
    basis = (
        f"min(maxRPM {c.max_rpm_jbeam:g}, tabla {c.max_available_rpm}); "
        + (
            f"limitador {c.rev_limiter_type}: maxRPM = revLimiterRPM"
            if c.has_rev_limiter
            else "sin limitador"
        )
        + " (combustionEngine.lua l. 1957-1989)"
    )
    put(
        "engine.rev_limiter_mode",
        "activo" if c.has_rev_limiter else "sin_limitador",
        "enum",
        "computed",
        _code(xc, "read_combustion_engine: hasRevLimiter/revLimiterType"),
        lua_at("l. 1964-1989"),
        note=basis,
    )
    put(
        "engine.speed_ceiling",
        c.max_rpm,
        "rpm",
        "computed",
        _code(xc, "read_combustion_engine: maxRPM"),
        lua_at("l. 1957-1971"),
        note=basis
        + "".join(
            f"; {k} ausente: {v[0]!r} ({v[1]})"
            for k, v in sorted(c.defaults_used.items())
            if k in ("revLimiterRPM", "revLimiterType")
        ),
    )
    active = [f.split("=", 1)[0] for f in c.forced_induction]
    put(
        "engine.forced_induction",
        bool(active),
        "enum",
        "extracted",
        _code(xc, "read_combustion_engine: turbocharger/supercharger/N2O"),
        lua_at("l. 2105 (sección nombrada Y aportada por una pieza activa)"),
        note=", ".join(c.forced_induction),
    )
    for what in ("supercharger", "nitrousOxideInjection"):
        ref = next((f for f in c.forced_induction if f.startswith(what + "=")), None)
        if ref is not None:
            ir.unknown.append(
                UnknownItem(
                    name,
                    ref.split("=", 1)[1],
                    None,
                    f"{what} detectado y NO interpretado: {what}.lua sin portar "
                    "(la curva a plena carga queda ausente)",
                )
            )

    w = c.wot
    curve_paths = (
        ("engine.wot_torque_curve", "table:rpm->N·m"),
        ("engine.peak_torque", "N·m"),
        ("engine.peak_torque_speed", "rpm"),
        ("engine.peak_power", "W"),
        ("engine.peak_power_speed", "rpm"),
    )
    if w is None:
        why = "curva a plena carga no calculable: " + "; ".join(c.wot_blockers)
        for path, unit in curve_paths:
            ir.set(path, Param.missing(unit, why, name))
    else:
        if w.kind == "na":
            kind = "computed"
            refs = [_code(xc, "_wot (na)"), lua_at("getTorqueData l. 41-49")]
            note = (
                "par neto NA: createCurve(torque, mods) − friction − "
                "dynamicFriction·ω (motor nuevo)"
            )
        else:
            kind = "estimated"
            refs = [
                _code(xc, "_wot (turbo_estimate)"),
                lua_at("getTorqueData l. 82-103"),
                lua_at("getTorqueCoefs l. 527-547", TURBO_LUA),
            ]
            note = (
                "ESTIMACIÓN de régimen estable del propio juego (getTorqueCoefs: "
                "válvula de descarga en start + rango/2 = "
                f"{w.turbo.estimated_wastegate_limit_psi:.3f} psi), no medida en "
                "marcha; coeficiente a la misma rpm (el gráfico del juego lo desfasa "
                f"1 rpm: |Δ| máx. {w.display_max_abs_delta:.3f} N·m)"
            )
        top = w.curve
        knots = sorted(
            {int(r) for r in c.table_rpms if float(r).is_integer() and r <= c.max_rpm}
            | {max(top)}
        )
        # G2.10 (D-G29b): la curva es por rpm entera y el coeficiente del turbo no es
        # lineal entre los nudos de la tabla; con solo esos nudos, la interpolación
        # lineal (la de AC) se separaba hasta 5,4 % cerca del ralentí. Se añaden rpm
        # hasta que la interpolación lineal quede a ≤ WOT_KNOT_TOLERANCE_NM de la curva
        # en cada rpm entera (src.importers.beamng.refine_knots).
        rpms = refine_knots(top, [r for r in knots if r in top], WOT_KNOT_TOLERANCE_NM)
        put(
            "engine.wot_torque_curve",
            [[r, round(top[r], 2)] for r in rpms],
            "table:rpm->N·m",
            kind,
            *refs,
            note=note
            + "; en las rpm de la tabla torque, en el techo y en las rpm añadidas para "
            f"que la interpolación lineal quede a ≤ {WOT_KNOT_TOLERANCE_NM:g} N·m "
            "de la curva en cada rpm entera; 2 decimales",
        )
        peak_note = note
        if w.kind == "na":
            peak_note += (
                "; contraste con info_*.json oficiales: par ~−2,7 N·m sin explicar "
                "(G2.9 §3), no validado"
            )
        put("engine.peak_torque", w.peak_torque[1], "N·m", kind, *refs, note=peak_note)
        put("engine.peak_torque_speed", w.peak_torque[0], "rpm", kind, *refs)
        put("engine.peak_power", w.peak_power[1], "W", kind, *refs, note=note)
        put("engine.peak_power_speed", w.peak_power[0], "rpm", kind, *refs)
    # G2.10 (D-G29c): freno motor a gas cerrado (src/extract/engine_combustion._coast).
    # Solo el dato de origen: ninguna regla lo lleva a COAST_REF (ver el informe G2.10).
    if c.coast:
        ebt = c.defaults_used.get("engineBrakeTorque")
        rough = c.defaults_used.get("idleRPMRoughness")
        basis = (
            "gas cerrado (régimen estable, motor nuevo): friction + dynamicFriction·ω "
            f"+ engineBrakeTorque = {c.friction:g} + {c.dynamic_friction:g}·ω + "
            f"{c.engine_brake_torque:g} N·m"
            + (f" (engineBrakeTorque ausente: friction·2, {ebt[1]})" if ebt else "")
            + "; desde idleRPM + idleRPMRoughness/2 = "
            f"{c.idle_rpm + c.idle_rpm_roughness / 2:g} rpm"
            + (f" (idleRPMRoughness ausente: 50, {rough[1]})" if rough else "")
            + " hasta el techo; par > 0 = freno"
        )
        refs = [
            _code(xc, "_coast"),
            lua_at("l. 744-783 (updateTorque: gas 0 → par de combustión 0)"),
            lua_at("l. 1858 (engineBrakeTorque or friction·2)"),
            lua_at("l. 620, 716, 1705 (gas de ralentí, idleRPMRoughness)"),
        ]
        put("engine.coast_basis", basis, "text", "computed", *refs, key=name)
        put(
            "engine.coast_curve",
            [[r, t] for r, t in c.coast],
            "table:rpm->N·m",
            "computed",
            *refs,
            note=basis,
            key=name,
        )
    else:
        why = "freno motor no calculable: " + "; ".join(c.coast_blockers)
        ir.set("engine.coast_basis", Param.missing("text", why))
        ir.set("engine.coast_curve", Param.missing("table:rpm->N·m", why))


# G2.8: subsistema del IR -> paso de la vista previa que lo extrae
# (``physics_preview.preview_physics``). Si el paso falló o no se ejecutó, sus
# parámetros quedan ausentes CON ese motivo: el origen puede tener el dato (p. ej. un
# motor ``combustionEngine``) aunque no se haya interpretado.
_STEP_OF_PREFIX: tuple[tuple[str, str], ...] = (
    ("engine.", "engine_model"),
    ("drivetrain.", "drivetrain"),
    ("mass.total", "mass_geometry"),
    ("mass.wheel_assembly", "mass_geometry"),
    ("dimensions.", "mass_geometry"),
    ("brakes.", "chassis"),
    ("tyres.", "chassis"),
    ("suspension.", "chassis"),
    ("aero.", "chassis"),
    ("steering.", "chassis"),  # G2.15
)


def _explain_missing(ir: VehicleIR, pv: Any, is_automation: bool) -> None:
    """Motivo para los parámetros ausentes que no lo tienen (G2.8).

    No cambia ningún estado ni valor: solo rellena ``reason`` de los ``missing`` sin
    motivo, para distinguir «el origen no lo tiene» de «no se interpretó»."""
    for path, p in ir.walk():
        if p.status != "missing" or p.reason:
            continue
        why = ""
        step = next((s for pre, s in _STEP_OF_PREFIX if path.startswith(pre)), None)
        suffixes = (".initial_mode", ".lock_torque")
        if path.endswith(suffixes) and "differential" in path:
            why = (
                "modo inicial y bloqueo solo se interpretan en AWD con central (G2.44)"
            )
        elif path.endswith(".viscous_torque") and "differential" in path:
            why = "viscousTorque solo se interpreta en un diferencial viscous de un AWD con central"
        elif path.startswith("engine.boost_"):
            why = "turbo/compresor con boost: solo camsoEngine con presión > 1 en el mapa de par"
        elif path.startswith("engine.reference_"):
            why = (
                "referencia de Automation (fila 100 del mapa de par): solo en camsoEngine "
                "con esa fila en el mapa"
            )
        elif path in (
            "brakes.abs_present",
            "brakes.abs_slip_limit",
            "brakes.abs_rate_hz",
        ):
            why = (
                "ABS solo en un eje, estado de ABS no determinado o frenos de algún "
                "eje no importados: AC tiene un único ABS (D-G250-ABS)"
            )
        elif step is not None:
            log = pv.stages.get(step)
            if log is None:
                why = f"paso '{step}' de la vista previa no ejecutado"
            elif not log.get("ok"):
                why = f"paso '{step}' fallido: {log.get('error', '')}"
        elif path == "declared.implied_power":
            why = "info_<config>.json sin 'Weight' o sin 'Weight/Power' numéricos"
        elif path.startswith("formats.exporter_version") and not is_automation:
            why = (
                "info.json sin ExporterVersion: el origen no se reconoce como "
                "exportado de Automation (procedencia: Type, Author y "
                "ExporterVersion reconocido; src.importers.automation)"
            )
        elif path.startswith("resources."):
            why = "recursos no leídos: solo los importa import_vehicle (resources.py)"
        if why:
            p.reason = why


def _all_not_applicable(reason: str) -> Differential:
    """Diferencial que no existe en el vehículo: TODOS sus campos «no aplica» (G2.8;
    antes solo ``kind``, y el resto quedaba ausente sin motivo)."""
    d = Differential()
    for f in fields(d):
        setattr(d, f.name, Param.not_applicable(getattr(d, f.name).unit, reason))
    return d


def _all_missing(reason: str) -> Differential:
    """Diferencial cuyo papel no se ha interpretado: todos sus campos ausentes con el
    motivo (no se afirma que no existan)."""
    d = Differential()
    for f in fields(d):
        setattr(d, f.name, Param.missing(getattr(d, f.name).unit, reason))
    return d


def _lsd_fields_outside_lsd(ir: VehicleIR, base: str, diff: Any) -> None:
    """Campos LSD de un diferencial cuyo ``diffType`` no es ``lsd`` (G2.8).

    El importador solo los interpreta con ``lsd``. Si el origen los trae, quedan «sin
    interpretar» con el valor crudo; si no, ausentes con el motivo. No se afirma que
    no apliquen: la semántica de cada ``diffType`` no está verificada aquí."""
    kind = diff.get("kind")
    for name, unit in (("preload", "N·m"), ("lock_coef", "1"), ("rev_lock_coef", "1")):
        if diff.has(name):
            param = Param.unparsed(
                diff.get(name),
                unit,
                f"diffType {kind!r}: el importador solo interpreta parámetros LSD con "
                "diffType 'lsd'",
                diff.source_key(name),
            )
        else:
            param = Param.missing(
                unit,
                f"no definido en el origen (diffType {kind!r}; el importador solo lo "
                "lee con 'lsd')",
                diff.source_key(name),
            )
        ir.set(f"{base}.{name}", param)


def _import_drivetrain(ir: VehicleIR, pv: Any, put, e: Any) -> None:
    res = pv.drivetrain
    dt = res.drivetrain
    lay = res.layout  # src.extract.traction.TractionLayout (G2.5c)
    xd = "src/extract/drivetrain.py"
    xt = "src/extract/traction.py"
    put(
        "drivetrain.layout",
        lay.layout,
        "enum",
        "computed",
        _code(xt, "classify_traction/traction_type"),
    )
    put(
        "drivetrain.driven_wheels",
        list(lay.driven_wheels),
        "text",
        "extracted",
        _code(xd, "build_drivetrain: connectedWheel alcanzable desde el motor"),
        note="ruedas cuyo dispositivo con connectedWheel tiene camino hasta el motor",
    )
    put(
        "drivetrain.gearbox.kind",
        dt.gearbox.type,
        "enum",
        "extracted",
        _code(xd, "build_drivetrain: GEARBOX_TYPES"),
    )
    if dt.cvt_range is not None:
        # CVT: relación continua entre minGearRatio y maxGearRatio; no hay marchas, y
        # una lista [máx, mín] en forward_ratios dispararía reglas de marchas fijas
        why = (
            f"cvtGearbox '{dt.gearbox.name}': relación continua (drivetrain.gearbox."
            "cvt_ratio_min/_max), sin marchas discretas"
        )
        ir.drivetrain.gearbox.reverse_ratio = Param.not_applicable("1", why)
        ir.drivetrain.gearbox.forward_count = Param.not_applicable("1", why)
    else:
        put(
            "drivetrain.gearbox.reverse_ratio",
            dt.reverse,
            "1",
            "extracted",
            _code(xd, "_split_gears"),
        )
        put(
            "drivetrain.gearbox.forward_count",
            len(dt.forward),
            "1",
            "extracted",
            _code(xd, "_split_gears"),
        )
        for k, ratio in enumerate(dt.forward, 1):
            put(
                f"drivetrain.gearbox.forward_ratios.{k}",
                ratio,
                "1",
                "extracted",
                _code(xd, "_split_gears"),
            )
    put(
        "drivetrain.final_ratio",
        dt.final_drive,
        "1",
        "extracted",
        _code(xd, "build_drivetrain: gearRatio"),
    )
    if lay.center is not None:
        _import_awd_differentials(ir, lay, put, xd, xt)
    else:
        _import_2wd_differentials(ir, lay, put, xd)
    _import_driven_axle(ir, lay, put, xt)
    _import_shift_and_clutch(ir, pv, put, e)
    _import_transmission_g211(ir, pv, put)


def _import_driven_axle(ir: VehicleIR, lay: Any, put, xt: str) -> None:
    """Eje motriz único y su diferencial (G2.7, G2.6b; concepto del ORIGEN).

    No se crea ningún ``Param`` nuevo para el diferencial: se enlaza el MISMO objeto
    ``differentials[<eje>]`` que acaba de rellenar el importador, de modo que los dos
    caminos del IR no pueden divergir. ``kind`` queda literal (no se agrupan tipos).
    """
    if lay.center is not None:
        why = "varios ejes motrices; ver drivetrain.differentials.<rol>"
        ir.drivetrain.driven_axle = Param.not_applicable("enum", why)
        ir.drivetrain.driven_axle_differential = _all_not_applicable(why)
        return
    role = lay.driven_axle
    if role is None:  # AWD sin central reconocido o eje motriz sin diferencial
        why = (
            "tracción total sin diferencial central reconocido: papel de cada "
            "diferencial sin interpretar"
            if lay.layout == "AWD"
            else "eje motriz sin diferencial reconocido"
        )
        ir.drivetrain.driven_axle = Param.missing("enum", why)
        ir.drivetrain.driven_axle_differential = _all_missing(why)
        return
    put(
        "drivetrain.driven_axle",
        role,
        "enum",
        "computed",
        _code(xt, "TractionLayout.driven_axle"),
        note="único eje con ruedas motrices y sin diferencial central",
    )
    ir.drivetrain.driven_axle_differential = ir.drivetrain.differentials[role]


_PRELOAD_2WD_NOTE = (
    "par: differential.lua:57 lo suma a un par "
    "(lsdTorque = lsdPreload + coef·|T|). Las declaraciones de "
    "variable de BeamNG lo rotulan «N/m» (error de rótulo; el valor "
    "literal activo no declara unidad). Corregido el 2026-10-26 "
    "(investigacion_powerlut_preload_2026-10-26.md §B)"
)


def _put_lsd(put, base: str, diff: Any, xd: str, preload_locator: str, note: str):
    """Parámetros de un ``lsd`` (solo si ``kind`` es ``lsd``; sin suponer valores)."""
    put(
        f"{base}.preload",
        diff.get("preload"),
        "N·m",
        "extracted",
        _code(xd, preload_locator),
        note=note,
        key=diff.source_key("preload"),
    )
    put(
        f"{base}.lock_coef",
        diff.get("lock_coef"),
        "1",
        "extracted",
        _code(xd, "read_devices"),
        key=diff.source_key("lock_coef"),
    )
    put(
        f"{base}.rev_lock_coef",
        diff.get("rev_lock_coef"),
        "1",
        "extracted",
        _code(xd, "read_devices"),
        key=diff.source_key("rev_lock_coef"),
    )


def _put_lsd_awd(put, base: str, diff: Any, xd: str, xt: str) -> None:
    """``lsd`` de un vehículo AWD con diferencial central (G2.12).

    Lo declarado en el JBeam se importa EXACTAMENTE como antes (``_put_lsd``,
    ``extracted``); lo que falta toma el valor por defecto de ``differential.lua``
    (``DiffSetup.lsd_effective``), ``computed`` con el archivo y la línea, porque es el
    valor que BeamNG usa en la simulación. Solo en esta rama: el camino 2WD
    (``_import_2wd_differentials``) no cambia."""
    from src.extract.traction import DIFF_LUA_SHA256

    _put_lsd(put, base, diff, xd, "read_devices: lsdPreload (differential.lua:57)", "")
    units = {"preload": "N·m", "lock_coef": "1", "rev_lock_coef": "1"}
    locator = (
        "DiffSetup.lsd_effective (differential.lua:57: "
        "lsdTorque = lsdPreload + coef·|T_entrada|)"
    )
    for name, unit in units.items():
        value, is_default, loc = diff.lsd_effective(name)
        if not is_default:
            continue
        path, line = loc.rsplit(":", 1)
        put(
            f"{base}.{name}",
            value,
            unit,
            "computed",
            _code(xt, locator),
            S.EvidenceRef(
                type="code", path=path, locator=f"l. {line}", sha256=DIFF_LUA_SHA256
            ),
            note=(
                f"{diff.source_key(name)} ausente en el JBeam: valor por defecto de "
                f"differential.lua l. {line} (el que usa BeamNG)"
            ),
            key=diff.source_key(name),
        )


def _put_initial_mode(ir: VehicleIR, put, base: str, diff: Any, xt: str) -> None:
    """``initial_mode`` y ``lock_torque`` de un diferencial AWD (G2.44).

    ``differential.lua`` l. 307-309: ``diffType`` lista ⇒ ``mode = lista[1]`` (el resto
    son modos conmutables en el juego); texto ⇒ ese modo. ``lockTorque`` solo se importa
    si el JBeam lo declara (``lockTorque`` o ``lockSpring``): sin él lo calcula el juego
    (l. 235-238, ``stabilityCoef²·min(inercia de las salidas)``) y aquí queda ausente.
    """
    kind = diff.get("kind")
    mode = kind if isinstance(kind, str) else None
    if isinstance(kind, list) and kind and isinstance(kind[0], str):
        mode = kind[0]
    put(
        f"{base}.initial_mode",
        mode,
        "enum",
        "computed",
        _code(xt, "differential.lua l. 307-309: diffType lista ⇒ primer modo"),
        key=diff.source_key("kind"),
    )
    declared = [k for k in ("lockTorque", "lockSpring") if k in diff.params]
    if "lockTorque" in diff.params:
        put(
            f"{base}.lock_torque",
            diff.params["lockTorque"],
            "N·m",
            "extracted",
            _code(xt, "differential.lua l. 346: lockTorque"),
            key=f"{diff.device}.lockTorque",
        )
    elif declared:
        ir.set(
            f"{base}.lock_torque",
            Param.unparsed(
                diff.params[declared[0]],
                "N·m",
                "solo lockSpring declarado: no se interpreta como lockTorque",
                f"{diff.device}.{declared[0]}",
            ),
        )
    else:
        ir.set(
            f"{base}.lock_torque",
            Param.missing(
                "N·m",
                "no declarado: BeamNG lo calcula en tiempo de carga como "
                "stabilityCoef²·min(inercia acumulada de las salidas) "
                "(differential.lua l. 235-238; powertrain.lua stabilityCoef = 250)",
                f"{diff.device}.lockTorque",
            ),
        )


def _put_viscous_torque(ir: VehicleIR, put, base: str, diff: Any, xt: str) -> None:
    """``viscousTorque`` de un diferencial ``viscous`` (differential.lua l. 68-83, 399-404).

    Solo si el JBeam lo declara; sin él BeamNG usa 10·viscousCoef (por defecto 5): no se deduce aquí.
    """
    if "viscousTorque" in diff.params:
        put(
            f"{base}.viscous_torque",
            diff.params["viscousTorque"],
            "N·m",
            "extracted",
            _code(xt, "differential.lua l. 68-83, 399-404: viscousTorque"),
            key=f"{diff.device}.viscousTorque",
        )
    else:
        ir.set(
            f"{base}.viscous_torque",
            Param.missing(
                "N·m",
                "no declarado, o el diferencial no es viscous (BeamNG usaría 10·viscousCoef)",
                f"{diff.device}.viscousTorque",
            ),
        )


def _import_awd_differentials(ir: VehicleIR, lay: Any, put, xd: str, xt: str):
    center = lay.center
    roles_ref = _code(xt, "awd_axle_roles (differential.lua l. 315-317, 64-65)")
    put(
        "drivetrain.front_torque_share",
        lay.front_torque_share,
        "1",
        "computed",
        roles_ref,
        note=f"diferencial central '{center.diff.device}', {center.split_source}",
    )
    for role, diff in (
        ("center", center.diff),
        ("front", lay.axles["front"].diff),
        ("rear", lay.axles["rear"].diff),
    ):
        ensure_entry(ir.drivetrain.differentials, role, Differential)
        base = f"drivetrain.differentials.{role}"
        put(
            f"{base}.kind",
            diff.get("kind"),
            "enum",
            "extracted",
            _code(xd, "read_devices"),
            key=diff.source_key("kind"),
        )
        _put_initial_mode(ir, put, base, diff, xt)
        _put_viscous_torque(ir, put, base, diff, xt)
        if role != "center":
            axle = lay.axles[role]
            put(
                f"{base}.final_ratio",
                axle.final,
                "1",
                "extracted",
                _code(xd, "build_drivetrain: gearRatio"),
                key=f"{diff.device}.gearRatio",
            )
            put(
                f"{base}.torque_share",
                axle.torque_share,
                "1",
                "computed",
                roles_ref,
                note=f"salida {axle.center_output} del central "
                f"'{center.diff.device}', {center.split_source}",
            )
        if diff.get("kind") == "lsd":
            _put_lsd_awd(put, base, diff, xd, xt)
        else:
            _lsd_fields_outside_lsd(ir, base, diff)
    c = ir.drivetrain.differentials["center"]
    c.final_ratio = Param.missing(
        "1",
        "no se importa: el extractor exige un central 1:1 (gearRatio ausente o 1, "
        "differential.lua l. 249; drivetrain._awd_layout)",
        f"{center.diff.device}.gearRatio",
    )
    c.torque_share = Param.not_applicable(
        "1", "el reparto del central está en differentials.<eje>.torque_share"
    )


def _import_2wd_differentials(ir: VehicleIR, lay: Any, put, xd: str):
    if lay.layout == "AWD":
        _import_awd_without_center(ir, lay)
        return
    ir.drivetrain.front_torque_share = Param.not_applicable(
        "1", "sin diferencial central"
    )
    # Sin central: como mucho un eje motriz con papel conocido (FWD/RWD). Con ruedas
    # motrices en los dos ejes y sin central reconocido, ``axles`` está vacío y no se
    # asigna ningún papel (no se supone la estructura).
    for role, axle in lay.axles.items():
        diff = axle.diff
        ensure_entry(ir.drivetrain.differentials, role, Differential)
        base = f"drivetrain.differentials.{role}"
        put(
            f"{base}.kind",
            diff.get("kind"),
            "enum",
            "extracted",
            _code(xd, "read_devices"),
            key=diff.source_key("kind"),
        )
        put(
            f"{base}.final_ratio",
            axle.final,
            "1",
            "extracted",
            _code(xd, "build_drivetrain: gearRatio"),
            key=f"{diff.device}.gearRatio",
        )
        ir.drivetrain.differentials[role].torque_share = Param.not_applicable(
            "1", "sin diferencial central"
        )
        if diff.get("kind") == "lsd":
            _put_lsd(put, base, diff, xd, "read_devices: lsdPreload", _PRELOAD_2WD_NOTE)
        else:
            _lsd_fields_outside_lsd(ir, base, diff)
        other = "rear" if role == "front" else "front"
        ir.drivetrain.differentials[other] = _all_not_applicable(
            f"eje {other} no motriz"
        )
    ir.drivetrain.differentials["center"] = _all_not_applicable(
        "sin diferencial central"
    )


def _import_awd_without_center(ir: VehicleIR, lay: Any) -> None:
    """Ruedas motrices en los dos ejes SIN diferencial central reconocido (G2.8).

    Antes este caso pasaba por la rama 2WD y el reparto y el central quedaban «no
    aplica: sin diferencial central», como si no existieran; en realidad la tracción
    es total y su estructura no se ha interpretado. Ahora: ausentes con el motivo y el
    diferencial del camino motriz conservado en ``unknown`` con sus opciones."""
    why = (
        "tracción total sin diferencial central reconocido "
        "(src/extract/traction.classify_traction): estructura sin interpretar"
    )
    ir.drivetrain.front_torque_share = Param.missing("1", why)
    ir.drivetrain.differentials["center"] = _all_missing(why)
    for diff in lay.unassigned_diffs:  # extract.traction.TractionLayout
        ir.unknown.append(
            UnknownItem(
                "powertrain",
                diff.device,
                dict(diff.params),
                "diferencial del camino motriz con ruedas de los dos ejes: papel "
                "(eje o central) sin interpretar",
            )
        )


def _import_shift_and_clutch(ir: VehicleIR, pv: Any, put, e: Any) -> None:
    res = pv.drivetrain
    dt = res.drivetrain
    xd = "src/extract/drivetrain.py"
    put(
        "drivetrain.gearbox.shift_delay",
        res.shift_controller_raw.get("transmissionShiftDelay"),
        "s",
        "extracted",
        _code("src/ac/drivetrain.py", "analyse: vehicleController"),
    )
    if dt.clutch is not None:
        put(
            "drivetrain.clutch.lock_torque",
            dt.clutch.params.get("lockTorque"),
            "N·m",
            "extracted",
            _code(xd, "read_devices: clutch"),
        )
        # Sin lockTorque en el JBeam, frictionClutch.lua:174 usa
        #   lockTorque = torqueData.maxTorque · 1,25 + maxRPM · inertia · π/30
        # (camsoEngine.lua l. 64-74, 252, 1997, 2007: par neto máximo y techo).
        if (
            dt.clutch.type == "frictionClutch"
            and dt.clutch.params.get("lockTorque") is None
            and e is not None
        ):
            lock = (
                e.peak_torque.net * 1.25
                + e.rpm_ceiling * e.raw.inertia * 3.141592653589793 / 30
            )
            put(
                "drivetrain.clutch.lock_torque_default",
                lock,
                "N·m",
                "computed",
                S.EvidenceRef(
                    type="code",
                    path="lua/vehicle/powertrain/frictionClutch.lua",
                    locator="174",
                ),
                _code(xd, "read_devices: clutch"),
                note="1,25·par neto máx. + techo de rpm · "
                "inercia · π/30 (frictionClutch.lua:174)",
            )


def _import_transmission_g211(ir: VehicleIR, pv: Any, put) -> None:
    """Caja de doble embrague y automática con convertidor de par (G2.11).

    Para cualquier caja: los campos nuevos del IR (``gearbox.shift_crossover_time`` y
    ``torque_converter.*``) quedan «no aplica» si el dispositivo no existe, con el tipo
    de caja en el motivo. Con ``dctGearbox``: tiempo mínimo de traspaso entre
    embragues y capacidad de los embragues (``clutch.lock_torque[_default]``). Con
    ``automaticGearbox``: parámetros del convertidor y del bloqueo; el embrague de
    fricción «no aplica». Lo leído del JBeam es ``extracted``; lo que el Lua sustituye
    por su valor por defecto, ``computed`` con la línea; lo que depende del motor
    (``maxTorque·1,25 + maxRPM·inertia·π/30``), ``computed`` desde el IR del motor.
    """
    from src.extract import automatic_transmission as AT

    res = pv.drivetrain
    dt = res.drivetrain
    trans = getattr(res, "transmission", None)
    kind = dt.gearbox.type
    xa = "src/extract/automatic_transmission.py"

    def lua_at(loc: str) -> S.EvidenceRef:
        path, line = loc.rsplit(":", 1) if ":" in loc else (loc, "")
        return S.EvidenceRef(
            type="code", path=path, locator=line, sha256=AT.GAME_LUA.get(path, "")
        )

    def put_sourced(path: str, sv: Any, unit: str, locator: str, note: str = ""):
        if sv.is_default:
            put(
                path,
                sv.value,
                unit,
                "computed",
                _code(xa, locator),
                lua_at(sv.locator),
                note=(note + "; " if note else "")
                + f"{sv.key} ausente: valor por defecto del juego ({sv.locator})",
                key=sv.key,
            )
        else:
            put(
                path,
                sv.value,
                unit,
                "extracted",
                _code(xa, locator),
                note=note,
                key=sv.key,
            )

    def engine_coupling_default(path: str, lua_loc: str, what: str) -> None:
        e = ir.engine
        need = (e.peak_torque, e.speed_ceiling, e.inertia)
        if not all(p.present for p in need):
            ir.set(
                path,
                Param.missing(
                    "N·m",
                    f"{what}: el valor por defecto del juego ({lua_loc}) necesita el "
                    "par máximo, el techo de rpm y la inercia del motor en el IR",
                ),
            )
            return
        value = AT.default_engine_coupling_torque(
            e.peak_torque.value, e.speed_ceiling.value, e.inertia.value
        )
        put(
            path,
            value,
            "N·m",
            "computed",
            _code(xa, "default_engine_coupling_torque"),
            lua_at(lua_loc),
            note=f"{what}: maxTorque·1,25 + maxRPM·inertia·π/30 ({lua_loc}) con "
            "engine.peak_torque, engine.speed_ceiling y engine.inertia del IR",
        )

    tc_fields = (
        ("stall_torque_ratio", "1"),
        ("coupling_speed_ratio", "1"),
        ("diameter", "m"),
        ("stiffness", "1"),
        ("torque_limit", "N·m"),
        ("lockup_clutch_torque", "N·m"),
        ("lockup_speed", "rpm"),
        ("lockup_range", "rpm"),
        ("lockup_min_gear", "1"),
    )
    conv = trans.converter if trans is not None else None
    if conv is None:
        why = f"sin convertidor de par (caja {kind})"
        for name, unit in tc_fields:
            ir.set(
                f"drivetrain.torque_converter.{name}", Param.not_applicable(unit, why)
            )
    else:
        base = "drivetrain.torque_converter"
        loc = "read_torque_converter"
        put_sourced(
            f"{base}.stall_torque_ratio",
            conv.stall_torque_ratio,
            "1",
            loc,
            "multiplicación de par en calado (torqueConverter.lua l. 38)",
        )
        put_sourced(
            f"{base}.coupling_speed_ratio",
            conv.coupling_av_ratio,
            "1",
            loc,
            "relación de velocidades salida/entrada desde la que la "
            "multiplicación es 1 (l. 38)",
        )
        put_sourced(
            f"{base}.diameter", conv.diameter, "m", loc, "kFactorCoef = 844·D⁵ (l. 181)"
        )
        put_sourced(
            f"{base}.stiffness",
            conv.stiffness,
            "1",
            loc,
            "k(r) = −0,004·s·(r − 1)/(1 + s·|r − 1|) (l. 35)",
        )
        if conv.converter_torque is not None:
            put_sourced(f"{base}.torque_limit", conv.converter_torque, "N·m", loc)
        else:
            engine_coupling_default(
                f"{base}.torque_limit",
                f"{AT.TC_LUA}:88",
                f"{conv.device}.converterTorque ausente",
            )
        put_sourced(
            f"{base}.lockup_clutch_torque",
            conv.lockup_clutch_torque,
            "N·m",
            loc,
            "capacidad del embrague de bloqueo (l. 31-33)",
        )
        put_sourced(
            f"{base}.lockup_speed",
            conv.lockup_rpm,
            "rpm",
            loc,
            "rpm de entrada de la caja desde las que empieza el bloqueo "
            "(shiftLogic/automaticGearbox.lua l. 371, 384; 0 = sin bloqueo)",
        )
        put_sourced(
            f"{base}.lockup_range",
            conv.lockup_range,
            "rpm",
            loc,
            "intervalo de rpm en el que se cierra el bloqueo (l. 371, 384)",
        )
        put_sourced(
            f"{base}.lockup_min_gear",
            conv.lockup_min_gear,
            "1",
            loc,
            "marcha mínima con bloqueo; por debajo el convertidor nunca se "
            "bloquea (l. 370, 383)",
        )
    # CVT (2026-11-19): rango de relaciones y lógica de rpm objetivo
    cvt = trans.cvt if trans is not None else None
    gb = ir.drivetrain.gearbox
    if cvt is None:
        why = f"caja sin relación continua ({kind})"
        gb.cvt_ratio_min = Param.not_applicable("1", why)
        gb.cvt_ratio_max = Param.not_applicable("1", why)
        gb.cvt_rpm_low = Param.not_applicable("rpm", why)
        gb.cvt_rpm_high = Param.not_applicable("rpm", why)
        gb.cvt_aggression = Param.not_applicable("1", why)
        gb.cvt_aggression_down = Param.not_applicable("1", why)
        gb.automatic_modes = Param.not_applicable("text", why)
    else:
        xd = "src/extract/drivetrain.py"
        for path, value in (
            ("drivetrain.gearbox.cvt_ratio_min", cvt.min_ratio),
            ("drivetrain.gearbox.cvt_ratio_max", cvt.max_ratio),
        ):
            put(
                path,
                value,
                "1",
                "extracted",
                _code(xd, "_cvt_range"),
                note=f"{cvt.device}.{'minGearRatio' if path.endswith('min') else 'maxGearRatio'}"
                " (variable $gear_min/$gear_max resuelta con la .pc; el valor por "
                "defecto del juego, 0,5 y 2,5, no se usa: cvtGearbox.lua l. 206-207)",
            )
        put_sourced(
            "drivetrain.gearbox.cvt_rpm_low",
            cvt.low_rpm,
            "rpm",
            "read_cvt",
            "rpm objetivo con agresividad 0 (Automation: IdleRPM + 500; "
            "shiftLogic/cvtGearbox.lua l. 229, 440)",
        )
        put_sourced(
            "drivetrain.gearbox.cvt_rpm_high",
            cvt.high_rpm,
            "rpm",
            "read_cvt",
            "rpm objetivo con agresividad 1 (Automation: min(rpm de potencia máxima, "
            "techo − 500); shiftLogic/cvtGearbox.lua l. 229, 439)",
        )
        put_sourced(
            "drivetrain.gearbox.cvt_aggression",
            cvt.aggression,
            "1",
            "read_cvt",
            "ganancia del lazo: relación += (rpmObjetivo − rpm)·cvtAggression·0,0166667 "
            "(shiftLogic/cvtGearbox.lua l. 236); no es una pendiente",
        )
        put_sourced(
            "drivetrain.gearbox.cvt_aggression_down",
            cvt.aggression_down,
            "1",
            "read_cvt",
            "tasa (1/s) con la que baja la agresividad (aggressionSmoothingDown; "
            "vehicleController.lua l. 1016)",
        )
        put_sourced(
            "drivetrain.gearbox.automatic_modes",
            cvt.automatic_modes,
            "text",
            "read_cvt",
            "modos del selector (PRND; con S el controlador fija un suelo de "
            "agresividad 0,5)",
        )
    # tiempo de traspaso de la DCT
    dct = trans.dct if trans is not None else None
    if dct is None:
        ir.drivetrain.gearbox.shift_crossover_time = Param.not_applicable(
            "s", f"caja sin embragues de traspaso ({kind})"
        )
    else:
        put_sourced(
            "drivetrain.gearbox.shift_crossover_time",
            dct.clutch_time,
            "s",
            "read_dct",
            "traspaso entre embragues con agresividad máxima; con agresividad 0 es "
            f"{AT.DCT_CROSSOVER_MAX_FACTOR:g} veces más largo "
            "(shiftLogic/dctGearbox.lua l. 511, 698)",
        )
        if dct.lock_torque is not None:
            put_sourced(
                "drivetrain.clutch.lock_torque",
                dct.lock_torque,
                "N·m",
                "read_dct",
                "capacidad de cada embrague de la DCT (dctGearbox.lua l. 73-75)",
            )
        else:
            ir.set(
                "drivetrain.clutch.lock_torque",
                Param.missing(
                    "N·m",
                    f"{dt.gearbox.name}.lockTorque no declarado: el juego usa el valor "
                    "por defecto (drivetrain.clutch.lock_torque_default)",
                    f"{dt.gearbox.name}.lockTorque",
                ),
            )
            engine_coupling_default(
                "drivetrain.clutch.lock_torque_default",
                f"{AT.DCT_LUA}:180",
                f"{dt.gearbox.name}.lockTorque ausente (capacidad de cada embrague "
                "de la DCT)",
            )
    if conv is not None:
        why = (
            "sin embrague de fricción: convertidor de par "
            f"'{conv.device}' (su embrague "
            "de bloqueo está en drivetrain.torque_converter.lockup_clutch_torque)"
        )
        ir.drivetrain.clutch.lock_torque = Param.not_applicable("N·m", why)
        ir.drivetrain.clutch.lock_torque_default = Param.not_applicable("N·m", why)


def _static_or_dynamic_stiffness(a: dict, dyn: dict) -> Any:
    """Rigidez vertical del neumático del banco (G2.44): la estática (escalones de
    carga) si es
    positiva; si no (la altura del buje NO decrece con la carga: neumático muy rígido y
    artefactos de rodadura a 20 m/s), la local a 3 Hz si es positiva; si tampoco,
    ``None``.
    """
    st = a.get("vertical_stiffness_static")
    if isinstance(st, (int, float)) and st > 0:
        return st
    d3 = (dyn.get("vosc_3hz") or {}).get("stiffness")
    return d3 if isinstance(d3, (int, float)) and d3 > 0 else None


def _import_unsprung(ir: VehicleIR, axle: Any, base: str, put) -> None:
    """``unsprung_mass_per_wheel`` de un eje (G2.14; ``src/extract/unsprung.py``).

    Presente (``measured``) solo si cada rueda del eje es un cuerpo independiente y las
    dos pesan lo mismo; los nodos de mecanismo unidos al cuerpo (no asignados) van en la
    nota. Eje acoplado (rígido o torsional): ausente con motivo, nunca 0."""
    xs = "src/extract/unsprung.py"
    path = f"{base}.unsprung_mass_per_wheel"
    bodies = list(getattr(axle, "unsprung", None) or [])
    wheels = [c.wheel for c in axle.corners]
    if not bodies:
        ir.set(path, Param.missing("kg", "sin reparto no suspendido (unsprung.py)"))
        return
    coupled = [b for b in bodies if b.coupled]
    if coupled:
        b = coupled[0]
        amb = sum(m for _, m in b.adjacent_mechanism)
        if all(c.linkage is not None and c.linkage.is_axle for c in axle.corners):
            # G2.44: eje rígido con enlaces de barra. Los nodos de unión adyacentes son
            # los montajes de los brazos en el eje (``rigid_axle.axle_nodes``): van con
            # el eje. Valor equivalente por rueda = masa del eje / 2 (en AC un AXLE
            # aporta ``HUB_MASS`` UNA vez: ``susp.hub_mass_rear_axle_*`` = 2 × este
            # valor).
            put(
                path,
                round((b.mass + amb) / 2, 6),
                "kg",
                "computed",
                _code(xs, "partition + rigid_axle.axle_nodes"),
                note=f"eje rígido {'+'.join(b.wheels)}: "
                f"{b.node_mass:.3f} kg de nodos + "
                f"{b.generated_mass:.3f} de ruedas + {amb:.3f} de montajes de "
                "los brazos "
                f"({', '.join(n for n, _ in b.adjacent_mechanism)}) = "
                f"{b.mass + amb:.3f} kg; valor por rueda = mitad del eje",
            )
            return
        if all(
            c.linkage is not None and c.linkage.kind == "dwb_coupled"
            for c in axle.corners
        ):
            # eje torsional (``coupled_axle.py``): cada semieje es una rueda DWB; el
            # cuerpo rígido es simétrico, así que la masa por rueda es su mitad (el
            # mismo reparto que el eje rígido). Aproximación: el brazo está unido a la
            # carrocería por la rótula, pero BeamNG lo cuenta entero como no suspendido.
            put(
                path,
                round((b.mass + amb) / 2, 6),
                "kg",
                "computed",
                _code(xs, "partition + coupled_axle"),
                note=f"eje torsional {'+'.join(b.wheels)}: "
                f"{b.node_mass:.3f} kg de nodos + {b.generated_mass:.3f} de ruedas + "
                f"{amb:.3f} de montajes = {b.mass + amb:.3f} kg; valor por rueda = "
                "mitad del eje (cuerpo simétrico)",
            )
            return
        ir.set(
            path,
            Param.missing(
                "kg",
                f"eje acoplado ({'+'.join(b.wheels)} en un solo cuerpo rígido de "
                f"{b.mass:.3f} kg: {b.node_mass:.3f} de nodos + {b.generated_mass:.3f} "
                f"de ruedas; {amb:.3f} kg de nodos de unión sin asignar): no hay masa "
                "por rueda; su equivalente en AC depende del TYPE del eje (decisión)",
            ),
        )
        return
    masses = [round(b.mass, 6) for b in bodies if b.wheels[0] in wheels]
    mech = sorted({n for b in bodies for n in b.adjacent_mechanism})
    note = (
        "Σ nodeWeight del cuerpo rígido del buje (cierre rígido de vigas NORMAL, "
        "≥ 3 vigas por nodo) + masa generada de la rueda; las vigas de BeamNG no "
        "tienen masa"
    )
    if mech:
        note += (
            "; nodos de mecanismo unidos al buje, NO incluidos (con la carrocería): "
            + ", ".join(f"{n} {m:.3f} kg" for n, m in mech)
        )
    put(
        path,
        _same(masses) if len(masses) == len(wheels) else None,
        "kg",
        "measured",
        _code(xs, "partition"),
        note=note,
    )


# G2.15: wheels.lua de BeamNG 0.38.6 (sha256 1eaf8b31…): valores por defecto y uso
_WHEELS_LUA = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/wheels.lua"


def _wl(locator: str) -> S.EvidenceRef:
    return S.EvidenceRef(
        type="code", path=_WHEELS_LUA, locator=locator, sha256="1eaf8b31…"
    )


def _flag(v: Any) -> bool:
    return v is True or (isinstance(v, (int, float)) and not isinstance(v, bool) and v)


def _import_brake_details(ir: VehicleIR, base: str, bs: list, put) -> None:
    """Codo del pedal, modelo térmico, disco y ABS de las ruedas de un eje (G2.15).

    Declarado ⇒ ``extracted``; no declarado pero usado por el juego con su valor por
    defecto ⇒ ``computed`` con la línea de ``wheels.lua``; sin modelo térmico o sin ABS,
    los parámetros que solo existen en ese modelo ⇒ «no aplica». Nunca 0."""
    bt = "src/extract/brakes_tyres.py"

    def common(fn):
        return _same([fn(b) for b in bs])

    for path, attr, line in (
        ("input_split", "input_split", "l. 1064: clamp(brakeInputSplit or 1, 0, 1)"),
        ("split_coef", "split_coef", "l. 1065: clamp(brakeSplitCoef or 1, 0, 1)"),
    ):
        vals = [getattr(b, attr) for b in bs]
        if all(v is None for v in vals):
            put(
                f"{base}.{path}",
                1.0,
                "1",
                "computed",
                _wl(line),
                note="no declarado: valor por defecto del Lua",
            )
        else:
            put(f"{base}.{path}", _same(vals), "1", "extracted", _code(bt, attr))
    thermal = common(lambda b: bool(_flag(b.raw.get("enableBrakeThermals"))))
    put(
        f"{base}.thermal_model",
        None if thermal is None else ("enabled" if thermal else "disabled"),
        "enum",
        "extracted",
        _code(bt, "enableBrakeThermals"),
        _wl("l. 849 (initThermals) y l. 642: par × brakeThermalEfficiency"),
    )
    na = "sin modelo térmico de frenos (enableBrakeThermals): el juego no lo usa"
    for path, key, unit, default, line in (
        ("pad_material", "padMaterial", "text", "basic", "l. 909"),
        ("disc_type", "brakeType", "enum", "vented-disc", "l. 854"),
        ("disc_diameter", "brakeDiameter", "m", 0.35, "l. 852"),
        ("disc_mass", "brakeMass", "kg", 10.0, "l. 851"),
    ):
        if thermal is False:
            ir.set(f"{base}.{path}", Param.not_applicable(unit, na))
            continue
        vals = [b.raw.get(key) for b in bs]
        vals = [None if v is False else v for v in vals]  # false = anulado por la pieza
        if all(v is None for v in vals):
            put(
                f"{base}.{path}",
                default if thermal else None,
                unit,
                "computed",
                _wl(line),
                note="no declarado: valor por defecto del Lua",
            )
        else:
            put(f"{base}.{path}", _same(vals), unit, "extracted", _code(bt, key))
    abs_on = common(
        lambda b: bool(
            _flag(b.raw.get("enableABS")) or _flag(b.raw.get("enableABSactuator"))
        )
    )
    put(
        f"{base}.abs",
        None if abs_on is None else ("enabled" if abs_on else "disabled"),
        "enum",
        "extracted",
        _code(bt, "enableABS"),
        _wl("l. 1046: hasABS"),
    )
    if abs_on is False:
        ir.set(f"{base}.abs_slip_target", Param.not_applicable("1", "sin ABS"))
    else:
        vals = [b.raw.get("absSlipRatioTarget") for b in bs]
        if all(v is None for v in vals):
            put(
                f"{base}.abs_slip_target",
                0.18 if abs_on else None,
                "1",
                "computed",
                _wl("l. 1038: absSlipRatioTarget or 0.18"),
                note="no declarado: valor por defecto del Lua",
            )
        else:
            put(
                f"{base}.abs_slip_target",
                _same(vals),
                "1",
                "extracted",
                _code(bt, "absSlipRatioTarget"),
                note="objetivo del controlador de ABS de BeamNG "
                "(wheels.lua updateABSCoef)",
            )
    if abs_on is False:
        ir.set(f"{base}.abs_frequency", Param.not_applicable("Hz", "sin ABS"))
    else:
        vals = [b.raw.get("absHz") for b in bs]
        if all(v is None for v in vals):
            put(
                f"{base}.abs_frequency",
                100.0 if abs_on else None,
                "Hz",
                "computed",
                _wl("l. 1048: absFrequency = wd.absHz or 100"),
                note="no declarado: valor por defecto del Lua",
            )
        else:
            put(
                f"{base}.abs_frequency",
                _same(vals),
                "Hz",
                "extracted",
                _code(bt, "absHz"),
            )


def _import_abs_summary(ir: VehicleIR, put) -> None:
    """ABS del vehículo para ``electronics.ini [ABS]`` (G2.50, D-G250-ABS).

    AC tiene UN límite de deslizamiento y UNA frecuencia; BeamNG, uno por eje.
    ``SLIP_RATIO_LIMIT`` = media de ``absSlipRatioTarget`` ponderada por el reparto de
    freno delantero (``front_share_full_pedal``); ``RATE_HZ`` = el menor de los dos ejes.
    ``PRESENT`` = 1 solo si AMBOS ejes tienen ABS; con ABS en uno solo, el resumen queda
    ausente (no se inventa). AC lo aplica todo/nada por rueda (ABS de AC), BeamNG
    regula con un PID hacia ``objetivo + 2/v``: es una aproximación declarada."""
    ax = ir.brakes.axles
    if "front" not in ax or "rear" not in ax:
        return
    st = [ax[a].abs.value for a in ("front", "rear")]
    ref = S.EvidenceRef(
        type="code", path="src/importers/beamng.py", locator="_import_abs_summary"
    )
    if None in st:
        return
    if all(s == "disabled" for s in st):
        put("brakes.abs_present", 0, "1", "computed", ref, note="ningún eje con ABS")
        ir.set("brakes.abs_slip_limit", Param.not_applicable("1", "sin ABS"))
        ir.set("brakes.abs_rate_hz", Param.not_applicable("Hz", "sin ABS"))
        return
    if not all(s == "enabled" for s in st):
        return  # ABS en un solo eje: sin equivalente en AC (decisión pendiente)
    fs = ir.brakes.front_share_full_pedal
    tf, tr = ax["front"].abs_slip_target, ax["rear"].abs_slip_target
    hf, hr = ax["front"].abs_frequency, ax["rear"].abs_frequency
    if not (fs.present and tf.present and tr.present and hf.present and hr.present):
        return
    w = float(fs.value)
    put(
        "brakes.abs_slip_limit",
        round(w * tf.value + (1 - w) * tr.value, 4),
        "1",
        "computed",
        ref,
        note="media de absSlipRatioTarget ponderada por el reparto de freno delantero",
    )
    put("brakes.abs_present", 1, "1", "computed", ref, note="ambos ejes con ABS")
    put(
        "brakes.abs_rate_hz",
        min(hf.value, hr.value),
        "Hz",
        "computed",
        ref,
        note="el menor de los dos ejes (AC tiene una sola frecuencia)",
    )


def _import_steering(ir: VehicleIR, ch: Any, put) -> None:
    """Dirección (G2.15; ``src/extract/steering.py``).

    Presentes: mecanismo, grados de VOLANTE a tope y recorrido de la cremallera. El
    ángulo máximo de la RUEDA y la relación de dirección dependen de la cinemática de la
    mangueta (bieleta, eje de pivote) y de los topes ``SUPPORT``: no son datos
    declarados; quedan ausentes con motivo (informe G2.15 §3)."""
    xs = "src/extract/steering.py"
    st = getattr(ch, "steering", None)
    kin = (
        "depende de la cinemática de la mangueta (bieleta, eje de pivote) y de los "
        "topes SUPPORT del JBeam: sin cinemática de dirección en producción (medido "
        "en el banco "
        "de G2.15, no en el IR)"
    )
    if st is None:
        why = (
            f"dirección no extraída: {getattr(ch, 'steering_error', '') or 'sin datos'}"
        )
        for path, unit in (
            ("mechanism", "enum"),
            ("steering_wheel_lock", "deg"),
            ("rack_travel", "m"),
            ("wheel_lock", "deg"),
            ("steering_ratio", "1"),
        ):
            ir.set(f"steering.{path}", Param.missing(unit, why))
        return
    put(
        "steering.mechanism",
        st.mechanism,
        "enum",
        "measured",
        _code(xs, "build_steering: hidros entre un nodo de raíl y un slidenode"),
    )
    lock_ref = {
        "input": "sección input.steeringWheelLock (input.lua l. 175-176)",
        "hydro": "|steeringWheelLock| del hidro de dirección (input.lua l. 149-153)",
        "default": "valor por defecto 450 (input.lua l. 42)",
    }[st.steering_wheel_lock_source]
    put(
        "steering.steering_wheel_lock",
        st.steering_wheel_lock,
        "deg",
        "computed" if st.steering_wheel_lock_source == "default" else "extracted",
        _code(xs, lock_ref),
        note="grados de VOLANTE con la entrada a tope (hydros.lua l. 500: "
        "volante = s·steeringWheelLock)",
    )
    if st.mechanism == "rack":
        put(
            "steering.rack_travel",
            st.rack_travel,
            "m",
            "computed",
            _code(xs, "|factor|·L0 (nodeBeam.lua processHydros; hydros.lua)"),
            note="desplazamiento de la cremallera a tope en cada sentido",
        )
    else:
        ir.set("steering.rack_travel", Param.missing("m", "; ".join(st.issues)))
    ir.set("steering.wheel_lock", Param.missing("deg", kin))
    ir.set("steering.steering_ratio", Param.missing("1", kin))


# G2.16: campos derivados de los enlaces (unidad) y tolerancias
_LINKAGE_FIELDS = (
    ("link_count", "1"),
    ("attach_rel_pos", "1"),
    ("kinematic_motion_ratio", "1"),
    ("strut_motion_ratio", "1"),  # G2.19
    ("wheel_rate", "N/m"),
    ("spring_preload_travel", "m"),
    ("damper_rate_slow", "N·s/m"),
    ("rest_camber", "deg"),
    ("rest_toe", "deg"),
    # G2.17
    ("rod_inward_shift_per_toe_in", "m/deg"),
    ("damper_rate_slow_bump", "N·s/m"),
    ("damper_rate_slow_rebound", "N·s/m"),
    ("damper_rate_fast_bump", "N·s/m"),
    ("damper_rate_fast_rebound", "N·s/m"),
    ("damper_fast_threshold_bump", "m/s"),
    ("damper_fast_threshold_rebound", "m/s"),
    ("bumpstop_travel_up", "m"),
    ("bumpstop_travel_down", "m"),
    ("bumpstop_rate", "N/m"),
    ("bumpstop_progressive", "N/m²"),
    ("bumpstop_damping", "N·s/m"),
    ("antiroll_rate", "N/m"),
)
_SYMMETRY_TOL = 1e-6  # m / 1: los dos lados deben dar lo mismo (reflejados)
# G2.17 [M] (banco de BeamNG, output/g217_suspension_steering_2026-11-04/banco): dentro
# de la zona de transición de una viga BOUNDED, la fuerza más allá del límite es
# F = LIMIT_LINEAR_FACTOR·beamLimitSpring·p + (beamLimitSpring/boundZone)·p² (7
# vehículos, 28 topes, compresión y extensión: factor 11,000, término cuadrático 1,000)
LIMIT_LINEAR_FACTOR = 11.0
_RACK_LATERAL_COS = 0.9999  # AC solo desplaza la barra en X (lateral)


def _linkage_missing(ir: VehicleIR, base: str, why: str) -> None:
    for name, unit in _LINKAGE_FIELDS:
        ir.set(f"{base}.{name}", Param.missing(unit, why))


def _symmetric(results: list[Any]) -> bool:
    r0 = results[0]
    for r in results[1:]:
        if r.anchor_nodes.keys() != r0.anchor_nodes.keys():
            return False
        for k in r0.anchors:
            pairs = zip(r.anchors[k], r0.anchors[k])
            if any(abs(a - b) > _SYMMETRY_TOL for a, b in pairs):
                return False
        for attr in (
            "spring_motion_ratio",
            "damper_motion_ratio",
            "spring_rest_compression",
            "rod_shift_per_toe_deg",
            "strut_motion_ratio",  # G2.19
            "redundant_link_force",  # G2.30
            "redundant_link_rate",
        ):
            a, b = getattr(r, attr), getattr(r0, attr)
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-6):
                return False
    return True


def _import_linkage(ir: VehicleIR, ch: Any, put) -> None:
    """Estructura de enlaces, anclajes DWB y derivados (G2.16; ``linkage.py``).

    ``linkage`` se mide siempre que haya análisis. Lo demás solo con un doble triángulo
    medido (``dwb_5link``/``dwb_equivalent``) y los dos lados iguales (reflejados); si
    no, ausente con el motivo. Nunca 0. La dirección (relación en el centro, posición y
    desplazamiento de la barra) solo con DWB delantero, cremallera en raíl, la barra
    unida a un extremo de la cremallera y la cremallera lateral."""
    xl = "src/extract/linkage.py"
    ref = _code(xl, "analyse_corner")
    front: Any = None
    for axle, pos in zip(ch.suspension.axles, ("front", "rear")):
        base = f"suspension.axles.{pos}"
        res = [c.linkage for c in axle.corners]
        if not res or any(r is None for r in res):
            why = "sin análisis de enlaces (src/extract/linkage.py)"
            ir.set(f"{base}.linkage", Param.missing("enum", why))
            _linkage_missing(ir, base, why)
            continue
        kinds = sorted({r.kind for r in res})
        if len(kinds) != 1:
            why = f"estructura distinta entre lados: {kinds}"
            ir.set(f"{base}.linkage", Param.missing("enum", why))
            _linkage_missing(ir, base, why)
            continue
        r0 = res[0]
        put(f"{base}.linkage", r0.kind, "enum", "measured", ref, note=r0.reason)
        # G2.19: el puntal en raíl también da una estructura medida completa
        if not (r0.is_dwb or r0.is_strut or r0.is_axle):
            # (texto anterior a G2.19 sin cambios: el puntal en raíl ya no llega aquí)
            why = f"sin doble triángulo medido ('{r0.kind}': {r0.reason})"
            _linkage_missing(ir, base, why)
            ir.set(f"{base}.strut_motion_ratio", Param.missing("1", why))
            continue
        if not _symmetric(res):
            _linkage_missing(ir, base, "los dos lados del eje no son simétricos")
            ir.set(
                f"{base}.strut_motion_ratio",
                Param.missing("1", "los dos lados del eje no son simétricos"),
            )
            continue
        links = (
            "barras del DWB: " + "; ".join(f"{c}–{u}" for c, u in r0.dwb_links)
            if r0.is_dwb
            else r0.reason
        )
        fitted = r0.kind == "dwb_fitted"
        if fitted:  # G2.30: puntos de chasis equivalentes (ajustados), no los físicos
            disp = [round(d * 1000, 1) for d in r0.fit_displacement]
            links += (
                f"; AJUSTADO a la trayectoria elástica del conjunto de {len(r0.links)} "
                f"barras (desplazamientos {disp} mm respecto a los nodos)"
            )
        for name, node in r0.anchor_nodes.items():
            put(
                f"{base}.anchors.{name}",
                [round(c, 6) for c in r0.anchors[name]],
                "m",
                "computed" if fitted and "chassis" in name else "measured",
                ref,
                note=f"nodo {node} − centro de rueda en reposo (lateral hacia dentro, "
                f"arriba, adelante; rueda derecha); {links}",
            )
        if r0.is_axle:  # G2.44
            for i, (car_n, ax_n, jc, ja) in enumerate(r0.axle_links):
                for suffix, vec, node in (("CAR", jc, car_n), ("AXLE", ja, ax_n)):
                    put(
                        f"{base}.anchors.J{i}_{suffix}",
                        [round(c, 6) for c in vec],
                        "m",
                        "measured",
                        ref,
                        note=f"nodo {node} − centro del eje en reposo (ejes AC); viga "
                        f"rígida {car_n}–{ax_n}",
                    )
            put(
                f"{base}.link_count",
                len(r0.axle_links),
                "1",
                "measured",
                ref,
                note="vigas NORMAL rígidas entre el cuerpo del eje y la carrocería",
            )
            put(
                f"{base}.attach_rel_pos",
                r0.attach_rel_pos,
                "1",
                "computed",
                ref,
                note="|x del extremo del muelle en el eje − centro| / semivía "
                "(suspensión de eje de AC coloca el muelle en centro + "
                "semivía·ATTACH_REL_POS)",
            )
        else:
            why = (
                "solo el eje rígido de barras (TYPE=AXLE) tiene enlaces y "
                "ATTACH_REL_POS"
            )
            for fld in ("link_count", "attach_rel_pos"):
                ir.set(f"{base}.{fld}", Param.not_applicable("1", why))
        mr = r0.spring_motion_ratio
        lo, hi = r0.spring_motion_ratio_range or (mr, mr)
        put(
            f"{base}.kinematic_motion_ratio",
            round(mr, 6),
            "1",
            "computed",
            ref,
            note=f"|dL/dh| del muelle en el {'DWB' if r0.is_dwb else 'puntal'} en "
            f"reposo; {lo:.4f}–{hi:.4f} en ±40 mm",
        )
        if r0.is_strut:
            smr = r0.strut_motion_ratio
            slo, shi = r0.strut_motion_ratio_range or (smr, smr)
            put(
                f"{base}.strut_motion_ratio",
                round(smr, 6),
                "1",
                "computed",
                ref,
                note="|dL/dh| del eje del puntal (apoyo superior → nodo deslizante, = "
                f"STRUT_CAR → STRUT_TYRE de AC) en reposo; {slo:.4f}–{shi:.4f} en "
                "±40 mm",
            )
        else:
            ir.set(
                f"{base}.strut_motion_ratio",
                Param.not_applicable("1", f"sin puntal ('{r0.kind}')"),
            )
        k = _same([c.spring_rate for c in axle.corners])
        # G2.30: las barras redundantes de un DWB ajustado añaden rigidez y fuerza en
        # rueda
        k_link = r0.redundant_link_rate if fitted else 0.0
        f_link = r0.redundant_link_force if fitted else 0.0
        put(
            f"{base}.wheel_rate",
            None if k is None else round(k * mr * mr + k_link, 3),
            "N/m",
            "computed",
            ref,
            note=f"beamSpring {k} N/m × MR² ({mr:.6f}²): rigidez vertical en rueda"
            + (
                f" + {k_link:.1f} N/m de las barras redundantes (energía elástica "
                "½Σk·ΔL² del conjunto completo, d²E/dh² en reposo)"
                if fitted
                else ""
            ),
        )
        if fitted and r0.spring_rest_compression is not None and k is not None:
            # fuerza de APOYO (sobre la carrocería, hacia arriba) en reposo = k·MR·c
            # − F_barras:
            # ``redundant_link_force`` es la fuerza de las barras sobre la RUEDA
            # hacia arriba (−dE/dh),
            # que empuja a comprimir más y resta apoyo; el muelle comprimido apoya
            # (+). Precarga = F/rigidez
            f0 = k * mr * r0.spring_rest_compression - f_link
            put(
                f"{base}.spring_preload_travel",
                round(f0 / (k * mr * mr + k_link), 6),
                "m",
                "computed",
                ref,
                note=f"fuerza de apoyo a la altura del JBeam: muelle "
                f"{k * mr * r0.spring_rest_compression:.1f} N − barras {f_link:.1f} N "
                f"(las barras "
                "empujan la rueda hacia arriba: restan apoyo), "
                f"entre la rigidez en rueda total ({k * mr * mr + k_link:.1f} N/m)",
            )
        elif r0.spring_rest_compression is None:
            ir.set(
                f"{base}.spring_preload_travel",
                Param.missing(
                    "m", "muelle sin precompressionRange ni beamPrecompression"
                ),
            )
        elif (
            fitted
        ):  # sin rigidez del muelle común no hay fuerza de apoyo: nunca c/MR solo
            ir.set(
                f"{base}.spring_preload_travel",
                Param.missing(
                    "m", "muelle distinto en los dos lados: sin fuerza de apoyo común"
                ),
            )
        else:
            put(
                f"{base}.spring_preload_travel",
                round(r0.spring_rest_compression / mr, 6),
                "m",
                "computed",
                ref,
                note=f"compresión del muelle en reposo {r0.spring_rest_compression:.6f}"
                " m (L + precompressionRange − longitud en reposo) / MR",
            )
        dampers = [c.damper for c in axle.corners]
        dmr = r0.damper_motion_ratio
        if any(d is None for d in dampers) or dmr is None:
            ir.set(
                f"{base}.damper_rate_slow",
                Param.missing(
                    "N·s/m", "sin amortiguador identificado en los dos lados"
                ),
            )
        else:
            bump = _same([d.bump for d in dampers])
            reb = _same(
                [d.rebound if d.rebound is not None else d.bump for d in dampers]
            )
            if bump is None or reb is None or bump != reb:
                ir.set(
                    f"{base}.damper_rate_slow",
                    Param.missing(
                        "N·s/m",
                        f"beamDamp {bump} ≠ beamDampRebound {reb}: qué coeficiente "
                        "actúa en compresión no está verificado (C++)",
                    ),
                )
            else:
                put(
                    f"{base}.damper_rate_slow",
                    round(bump * dmr * dmr, 3),
                    "N·s/m",
                    "computed",
                    ref,
                    note=f"beamDamp = beamDampRebound = {bump} N·s/m × "
                    f"MR_amortiguador² ({dmr:.6f}²); lento, en rueda",
                )
        for name, val in (
            ("rest_camber", r0.rest_camber_deg),
            ("rest_toe", r0.rest_toe_deg),
        ):
            put(
                f"{base}.{name}",
                round(val, 4),
                "deg",
                "computed",
                ref,
                note="pose de reposo con L·beamPrecompression (caída − = parte alta "
                "hacia dentro; convergencia + = toe-in)",
            )
        _import_linkage_g217(ir, axle, base, res, put)
        if pos == "front":
            front = r0
    _import_steering_linkage(ir, ch, put, front)


def _close(values: list[Any], tol: float = 1e-6) -> Any:
    """El valor si todos coinciden (|Δ| ≤ tol relativo, o absoluto por debajo de 1)."""
    if not values or any(v is None for v in values):
        return None
    v0 = values[0]
    for v in values[1:]:
        if abs(v - v0) > tol * max(1.0, abs(v0)):
            return None
    return v0


_BENCH = (
    "output/g217_suspension_steering_2026-11-04/banco/resultados_s1_s2.md "
    "(banco de BeamNG)"
)
_DAMPER_FIELDS = (
    ("damper_rate_slow_bump", "N·s/m"),
    ("damper_rate_slow_rebound", "N·s/m"),
    ("damper_rate_fast_bump", "N·s/m"),
    ("damper_rate_fast_rebound", "N·s/m"),
    ("damper_fast_threshold_bump", "m/s"),
    ("damper_fast_threshold_rebound", "m/s"),
)
_BUMPSTOP_FIELDS = (
    ("bumpstop_travel_up", "m"),
    ("bumpstop_travel_down", "m"),
    ("bumpstop_rate", "N/m"),
    ("bumpstop_progressive", "N/m²"),
    ("bumpstop_damping", "N·s/m"),
)


def _import_linkage_g217(ir: VehicleIR, axle: Any, base: str, res: list, put) -> None:
    """G2.17: convergencia con signo, amortiguación por sentido y rápida, topes y barra
    estabilizadora con el DWB medido y simétrico (``linkage.py``, ``suspension.py``).
    Lo que no se puede medir queda ausente con su motivo; nunca 0."""
    xl = "src/extract/linkage.py"

    def missing(name: str, unit: str, why: str) -> None:
        ir.set(f"{base}.{name}", Param.missing(unit, why))

    shift = _close([r.rod_inward_shift_per_toe_in for r in res])
    if shift is None:
        missing(
            "rod_inward_shift_per_toe_in",
            "m/deg",
            "la barra no gira la rueda o los lados no coinciden",
        )
    else:
        put(
            f"{base}.rod_inward_shift_per_toe_in",
            round(shift, 9),
            "m/deg",
            "computed",
            _code(xl, "analyse_corner (rod_inward_shift_per_toe_in)"),
            note="desplazamiento HACIA DENTRO del punto del chasis de la barra del DWB "
            "por grado de convergencia (toe-in), en el reposo",
        )
    _import_damper_g217(ir, axle, base, res, put, missing)
    _import_bumpstop_g217(ir, axle, base, res, put, missing)
    _import_antiroll_g217(axle, base, res, put, missing)


def _import_damper_g217(ir, axle, base, res, put, missing) -> None:
    """Amortiguación por sentido y rápida en rueda. [M] banco de G2.17: compresión =
    el amortiguador se ACORTA → beamDamp (lento) y beamDampFast (pendiente por encima de
    beamDampVelocitySplit); extensión → beamDampRebound/beamDampReboundFast/
    beamDampVelocitySplitRebound; continua en el umbral (la misma ley que
    amortiguador de AC [C])."""
    xs = "src/extract/suspension.py"
    xl = "src/extract/linkage.py"
    dampers = [c.damper for c in axle.corners]
    dmr = _close([r.damper_motion_ratio for r in res])
    shortens = {r.damper_shortens_on_bump for r in res}
    why = ""
    if any(d is None for d in dampers) or dmr is None:
        why = "sin amortiguador identificado en los dos lados o palanca distinta"
    elif len(shortens) != 1 or None in shortens:
        why = "sentido del amortiguador frente al recorrido no determinado"
    elif any(d != dampers[0] for d in dampers):
        why = "los amortiguadores de los dos lados no son iguales"
    if why:
        for n, u in _DAMPER_FIELDS:
            missing(n, u, why)
        return
    d = dampers[0]
    # stage2.lua: beamDampRebound ← beamDamp; beamDampFast ← beamDamp;
    # beamDampReboundFast ← beamDampRebound; umbral ← infinito; umbral de extensión ←
    # el de compresión
    comp = (d.bump, d.bump_fast if d.bump_fast is not None else d.bump)
    reb_slow = d.rebound if d.rebound is not None else d.bump
    reb = (reb_slow, d.rebound_fast if d.rebound_fast is not None else reb_slow)
    split_c = d.velocity_split
    split_r = (
        d.velocity_split_rebound
        if d.velocity_split_rebound is not None
        else d.velocity_split
    )
    if not shortens.pop():  # se alarga al subir la rueda: los papeles se cruzan
        comp, reb = reb, comp
        split_c, split_r = split_r, split_c
    src = _code(xs, "DamperData (beamDamp*, beamDampVelocitySplit*)")
    mr_ref = _code(xl, "analyse_corner (damper_motion_ratio)")
    sense = (
        "compresión de la rueda = el amortiguador se acorta → beamDamp/beamDampFast; "
        "extensión → beamDampRebound/beamDampReboundFast; continua en el umbral "
        f"[M] {_BENCH}"
    )
    cutoff = f"; filtro dampCutoffHz {d.cutoff_hz} Hz" if d.cutoff_hz else ""
    for name, coef in (
        ("damper_rate_slow_bump", comp[0]),
        ("damper_rate_slow_rebound", reb[0]),
        ("damper_rate_fast_bump", comp[1]),
        ("damper_rate_fast_rebound", reb[1]),
    ):
        put(
            f"{base}.{name}",
            round(coef * dmr * dmr, 3),
            "N·s/m",
            "computed",
            src,
            mr_ref,
            note=f"{coef} N·s/m × MR_amortiguador² ({dmr:.6f}²), en rueda; {sense}"
            f"{cutoff}",
        )
    for name, split in (
        ("damper_fast_threshold_bump", split_c),
        ("damper_fast_threshold_rebound", split_r),
    ):
        if split is None:
            missing(
                name,
                "m/s",
                "sin beamDampVelocitySplit (stage2.lua: infinito): la amortiguación "
                "rápida es la lenta y el umbral no influye",
            )
            continue
        put(
            f"{base}.{name}",
            round(split / dmr, 6),
            "m/s",
            "computed",
            src,
            mr_ref,
            note=f"umbral {split} m/s a lo largo del amortiguador / MR ({dmr:.6f}): "
            f"velocidad vertical de rueda; {sense}",
        )


def _import_bumpstop_g217(ir, axle, base, res, put, missing) -> None:
    """Topes: recorrido vertical hasta el contacto (cinemática del DWB), ley de la zona
    de transición medida en el juego y amortiguación del tope (sin equivalente en AC).
    """
    xs = "src/extract/suspension.py"
    xl = "src/extract/linkage.py"
    stops = [c.bumpstop for c in axle.corners]
    if any(s is None for s in stops):
        for n, u in _BUMPSTOP_FIELDS:
            missing(n, u, "sin viga de tope identificada en los dos lados")
        return
    keys = {
        (
            s.short_bound_range,
            s.long_bound_range,
            s.bound_zone,
            s.limit_spring,
            s.limit_damp,
            s.limit_damp_rebound,
        )
        for s in stops
    }
    if len(keys) != 1:
        for n, u in _BUMPSTOP_FIELDS:
            missing(n, u, "los topes de los dos lados no son iguales")
        return
    s = stops[0]
    r0 = res[0]
    sref = _code(xl, "analyse_corner (_probe_bumpstop)")
    mru = _close([r.bumpstop_motion_ratio_up for r in res])
    mrd = _close([r.bumpstop_motion_ratio_down for r in res])
    for name, vals, mr in (
        ("bumpstop_travel_up", [r.bumpstop_travel_up for r in res], mru),
        ("bumpstop_travel_down", [r.bumpstop_travel_down for r in res], mrd),
    ):
        val = _close(vals)
        if val is None or mr is None:
            missing(name, "m", r0.bumpstop_reason or "no se alcanza o lados distintos")
            continue
        put(
            f"{base}.{name}",
            round(val, 6),
            "m",
            "computed",
            sref,
            note="recorrido vertical de rueda desde el reposo hasta que la viga "
            "del tope mide la longitud de creación ∓ *BoundRange (arranque "
            f"confirmado en el juego, {_BENCH}); palanca en el contacto {mr:.6f}",
        )
    kl, z = s.limit_spring, s.bound_zone
    if mru is None or not kl or not z:
        why = (
            "sin contacto de compresión o sin beamLimitSpring/boundZone (stage2.lua: "
            "boundZone 1 si falta; ley no medida)"
        )
        missing("bumpstop_rate", "N/m", why)
        missing("bumpstop_progressive", "N/m²", why)
    else:
        put(
            f"{base}.bumpstop_rate",
            round(LIMIT_LINEAR_FACTOR * kl * mru * mru, 1),
            "N/m",
            "computed",
            _code(xs, "BumpStopData.limit_spring"),
            sref,
            note=f"{LIMIT_LINEAR_FACTOR:g}·beamLimitSpring ({kl}) × MR² en el "
            f"contacto de compresión ({mru:.6f}²): término lineal de la ley medida "
            f"en la zona de transición (boundZone {z} m) [M] {_BENCH}",
        )
        put(
            f"{base}.bumpstop_progressive",
            round(kl / z * mru**3, 1),
            "N/m²",
            "computed",
            _code(xs, "BumpStopData.limit_spring, bound_zone"),
            sref,
            note=f"beamLimitSpring/boundZone ({kl}/{z}) × MR³ ({mru:.6f}³): término "
            f"cuadrático de la ley medida en la zona [M] {_BENCH}; sin objetivo AC "
            "(BUMP_STOP_PROGRESSIVE fuera del catálogo: decisión D-G217-BSP)",
        )
    if mru is None or s.limit_damp is None:
        missing("bumpstop_damping", "N·s/m", "sin contacto o sin beamLimitDamp")
    else:
        put(
            f"{base}.bumpstop_damping",
            round(s.limit_damp * mru * mru, 3),
            "N·s/m",
            "computed",
            _code(xs, "BumpStopData.limit_damp"),
            sref,
            note=f"beamLimitDamp ({s.limit_damp}) × MR² en el contacto; AC no "
            "amortigua los topes (suspensión de AC [C])",
        )


def _import_antiroll_g217(axle, base, res, put, missing) -> None:
    """Barra estabilizadora: k_t·(dθ/dh)² (N por m de diferencia de altura entre las
    ruedas del eje), con θ = diedro de la barra de torsión (bdebugImpl.lua: par =
    spring·ángulo) y dθ/dh de la cinemática del DWB de cada lado."""
    xs = "src/extract/suspension.py"
    xl = "src/extract/linkage.py"
    bars = [
        tb
        for tb in axle.torsionbars
        if all(
            any(tb["nodes"] == x["nodes"] for x in r.torsion_angle_rates) for r in res
        )
    ]
    if len(bars) != 1:
        missing(
            "antiroll_rate",
            "N/m",
            f"{len(bars)} barras de torsión con un extremo en cada mangueta del eje "
            "(se necesita exactamente 1)",
        )
        return
    bar = bars[0]
    k_t = bar.get("spring_Nm_per_rad")
    k2 = bar.get("spring2_Nm_per_rad")
    rates = [
        next(
            x["rad_per_m"] for x in r.torsion_angle_rates if x["nodes"] == bar["nodes"]
        )
        for r in res
    ]
    g = _close(rates)
    if not k_t or (k2 is not None and k2 != k_t) or g is None:
        missing(
            "antiroll_rate",
            "N/m",
            "rigidez de la barra ausente, spring2 ≠ spring o relación ángulo/recorrido "
            "distinta entre lados",
        )
        return
    put(
        f"{base}.antiroll_rate",
        round(k_t * g * g, 1),
        "N/m",
        "computed",
        _code(xs, "torsionbars (spring)"),
        _code(xl, "analyse_corner (_probe_torsion)"),
        note=f"k_t ({k_t} N·m/rad) × (dθ/dh)² ({g:.6f} rad/m)²; θ = diedro de "
        f"{'-'.join(bar['nodes'])} (bdebugImpl.lua: par = spring·ángulo); contrastado "
        f"en el banco [M] {_BENCH}",
    )


def _import_steering_linkage(ir: VehicleIR, ch: Any, put, front: Any) -> None:
    """Relación de dirección en el centro con el DWB delantero (G2.16)."""
    xl = "src/extract/linkage.py"
    st = getattr(ch, "steering", None)
    why = ""
    if st is None or st.mechanism != "rack":
        why = "sin cremallera en raíl (src/extract/steering.py)"
    elif front is None:
        why = "sin doble triángulo delantero medido y simétrico"
    elif front.anchor_nodes.get("rod_chassis") not in st.rack_nodes:
        why = (
            f"la barra ({front.anchor_nodes.get('rod_chassis')}) no se une a un "
            f"extremo de la cremallera {list(st.rack_nodes)}"
        )
    elif st.rack_axis_ac is None or abs(st.rack_axis_ac[0]) < _RACK_LATERAL_COS:
        why = (
            f"cremallera no lateral ({st.rack_axis_ac}): AC solo desplaza la barra en X"
        )
    elif front.rod_shift_per_toe_deg is None:
        why = "la barra no gira la rueda en el modelo cinemático"
    if why:
        ir.set("steering.tie_rod_position", Param.missing("enum", why))
        ir.set(
            "steering.rack_displacement_per_wheel_degree", Param.missing("m/deg", why)
        )
        return
    ref = _code(xl, "analyse_corner (rod_shift_per_toe_deg)")
    lsrr = front.rod_shift_per_toe_deg
    put(
        "steering.rack_displacement_per_wheel_degree",
        round(lsrr, 8),
        "m/deg",
        "measured",
        ref,
        note="desplazamiento lateral del extremo de la cremallera por grado de "
        "convergencia de la rueda, en el centro (DWB delantero, reposo)",
    )
    put(
        "steering.tie_rod_position",
        "ahead" if front.rod_ahead_of_axle else "behind",
        "enum",
        "measured",
        ref,
        note="rótula de la barra en la mangueta delante o detrás del centro de rueda",
    )
    put(
        "steering.steering_ratio",
        round(st.steering_wheel_lock * lsrr / st.rack_travel, 6),
        "1",
        "computed",
        ref,
        _code("src/extract/steering.py", "rack_travel, steering_wheel_lock"),
        note=f"en el centro: steeringWheelLock {st.steering_wheel_lock} × "
        f"{lsrr:.8f} m/° / recorrido {st.rack_travel} m (cinemática rígida; el juego "
        "es ≈ 10 % más lento por elasticidad, banco de G2.15)",
    )


def _import_chassis(ir: VehicleIR, ch: Any, put) -> None:
    xs = "src/extract/suspension.py"
    for axle, pos in zip(ch.suspension.axles, ("front", "rear")):
        ensure_entry(ir.suspension.axles, pos, AxleSuspension)
        base = f"suspension.axles.{pos}"
        put(
            f"{base}.label",
            axle.label,
            "text",
            "extracted",
            _code(xs, "AxleSuspension.label"),
        )
        put(
            f"{base}.spring_rate_beam",
            _same([c.spring_rate for c in axle.corners]),
            "N/m",
            "extracted",
            _code(xs, "CornerSuspension.spring_rate"),
            note="a lo largo de la viga, no en rueda",
        )
        put(
            f"{base}.motion_ratio_hyp",
            _same([round(c.motion_ratio_hyp, 6) for c in axle.corners]),
            "1",
            "estimated",
            _code(xs, "motion_ratio_hyp"),
            note="hipótesis del extractor",
        )
        _import_unsprung(ir, axle, base, put)
        for k, tb in enumerate(axle.torsionbars, 1):
            put(
                f"{base}.torsionbar_rates.{k}",
                tb.get("spring_Nm_per_rad"),
                "N·m/rad",
                "extracted",
                _code(xs, "torsionbars"),
            )
    bt = "src/extract/brakes_tyres.py"
    tyres = {t.wheel: t for t in ch.brakes_tyres.tyres}
    brakes = {b.wheel: b for b in ch.brakes_tyres.brakes}
    for pos, wheels in (("front", ch.front_wheels), ("rear", ch.rear_wheels)):
        ts = [tyres[w] for w in wheels if w in tyres]
        if len(ts) == len(wheels):
            ensure_entry(ir.tyres.axles, pos, TyreAxle)
            base = f"tyres.axles.{pos}"
            put(
                f"{base}.radius",
                _same([t.radius for t in ts]),
                "m",
                "measured",
                _code(bt, "WheelTyre.radius"),
                note="radio de los nodos del neumático sin carga",
            )
            put(
                f"{base}.hub_radius",
                _same([t.hub_radius for t in ts]),
                "m",
                "extracted",
                _code(bt, "WheelTyre.hub_radius"),
            )
            put(
                f"{base}.width",
                _same([t.width for t in ts]),
                "m",
                "extracted",
                _code(bt, "tireWidth"),
            )
            put(
                f"{base}.pressure",
                _same([t.pressure_psi for t in ts]),
                "psi",
                "extracted",
                _code(bt, "pressurePSI"),
            )
        bs = [brakes[w] for w in wheels if w in brakes]
        if len(bs) == len(wheels):
            ensure_entry(ir.brakes.axles, pos, BrakeAxle)
            base = f"brakes.axles.{pos}"
            put(
                f"{base}.max_torque_per_wheel",
                _same([round(b.max_torque, 2) for b in bs]),
                "N·m",
                "computed",
                _code(bt, "brake_torque"),
                note="por rueda, entrada de freno 1",
            )
            put(
                f"{base}.parking_torque_per_wheel",
                _same([b.parking_torque for b in bs]),
                "N·m",
                "extracted",
                _code(bt, "parkingTorque"),
            )
        if len(bs) == len(wheels):
            _import_brake_details(ir, f"brakes.axles.{pos}", bs, put)
    missing = [w for w in ch.front_wheels + ch.rear_wheels if w not in brakes]
    if not missing:
        try:
            from src.extract.brakes_tyres import front_share

            fb = [brakes[w] for w in ch.front_wheels]
            rb = [brakes[w] for w in ch.rear_wheels]
            full, knee = front_share(fb, rb, 1.0), front_share(fb, rb, 0.5)
        except Exception:  # sin frenos válidos: el dato queda ausente
            full = knee = None
        put(
            "brakes.front_share_full_pedal",
            full,
            "1",
            "computed",
            _code(bt, "front_share(…, 1.0)"),
        )
        put(
            "brakes.front_share_below_knee",
            knee,
            "1",
            "computed",
            _code(bt, "front_share(…, 0.5)"),
            note="por debajo del codo de brakeInputSplit",
        )
    _import_abs_summary(ir, put)
    _import_steering(ir, ch, put)
    _import_linkage(ir, ch, put)
    model = getattr(ch, "aero_model", None)
    put(
        "aero.model",
        model,
        "enum",
        "measured",
        _code(
            "src/extract/aero.py",
            "aero_model: controlador cefaero o triángulos con liftCoef",
        ),
    )
    a = ch.aero
    if a is not None:
        xa = "src/extract/aero.py"
        put("aero.cda", a.cda, "m²", "extracted", _code(xa, "vehicleArea"))
        put(
            "aero.target_front",
            a.target_front_n,
            "N",
            "extracted",
            _code(xa, "downforceModel"),
            note=(
                f"a {a.target_speed_kmh:g} km/h; positivo = carga, negativo = "
                "sustentación (cefaero.lua:135-142)"
            ),
        )
        put(
            "aero.target_rear",
            a.target_rear_n,
            "N",
            "extracted",
            _code(xa, "downforceModel"),
        )
        put(
            "aero.cla_front",
            a.cla_front,
            "m²",
            "computed",
            _code(xa, "cla (ρ₀ = 1,225, hipótesis)"),
        )
        put(
            "aero.cla_rear",
            a.cla_rear,
            "m²",
            "computed",
            _code(xa, "cla (ρ₀ = 1,225, hipótesis)"),
        )
    else:
        # G2.14: con el paso chassis correcto (200409), la aerodinámica puede faltar por
        # su propio motivo (p. ej. sin controlador cefaero): se conserva, nunca 0
        why = getattr(ch, "aero_error", None) or "sin datos aerodinámicos"
        for path, unit in (
            ("aero.cda", "m²"),
            ("aero.target_front", "N"),
            ("aero.target_rear", "N"),
            ("aero.cla_front", "m²"),
            ("aero.cla_rear", "m²"),
        ):
            ir.set(path, Param.missing(unit, f"aerodinámica no extraída: {why}"))


AERO_BENCH_FIELDS = (
    ("aero.cda_vs_aoa", "cda", "arrastre (C·A) frente al ángulo de ataque"),
    (
        "aero.cla_front_axle_vs_aoa",
        "cla_front",
        "carga en el centro del eje delantero (C·A, + = hacia abajo)",
    ),
    (
        "aero.cla_rear_axle_vs_aoa",
        "cla_rear",
        "carga en el centro del eje trasero (C·A, + = hacia abajo); el arrastre se "
        "aplica también en ese punto",
    ),
)


def _import_aero_bench(ir: VehicleIR, pv: Any, model: Any) -> None:
    """G2.19: tablas de la aerodinámica por triángulos MEDIDAS en el juego.

    Solo con ``aero.model == triangles`` (la calcula el C++ del juego, G2.15 §5) y un
    registro ``beamng_aero_bench`` de ``measurements/`` cuya huella coincide con la del
    origen (``src/importers/measurements.py``; nunca por nombre). La reducción y sus
    convenciones están en ``src/extract/aero_bench.py``."""
    unit = "table:deg->m²"
    if model == "cefaero" and getattr(pv.chassis, "aero", None) is not None:
        # G2.29: cefaero es analítico (sin dependencia del ángulo): tablas planas con
        # las cargas equivalentes en los centros de eje (extract.aero.axle_equivalent)
        from src.extract.aero import AeroDataError, axle_equivalent

        try:
            vals = axle_equivalent(pv.chassis.aero, pv.chassis.geometry)
        except AeroDataError as exc:
            for path, _, _ in AERO_BENCH_FIELDS:
                ir.set(path, Param.missing(unit, f"cefaero sin equivalencia: {exc}"))
            return
        for (path, _, what), v in zip(AERO_BENCH_FIELDS, vals):
            ir.set(
                path,
                Param.of(
                    [[-90.0, v], [90.0, v]],
                    unit,
                    "computed",
                    [_code("src/extract/aero.py", "axle_equivalent")],
                    note=f"{what}; cefaero.lua (F ∝ ½ρv², sin ángulo de ataque): "
                    "sistema de fuerzas equivalente al del Lua",
                ),
            )
        return
    if model != "triangles":
        why = (
            f"solo para la aerodinámica por triángulos (modelo {model!r})"
            if model
            else "modelo aerodinámico sin determinar"
        )
        for path, _, _ in AERO_BENCH_FIELDS:
            ir.set(path, Param.not_applicable(unit, why))
        return
    from src.extract.aero_bench import AeroBenchError, aero_tables

    from . import measurements as meas

    try:
        hit = meas.find(pv.source, "beamng_aero_bench")
    except meas.MeasurementError as exc:
        hit, err = None, str(exc)
    else:
        err = ""
    if hit is None:
        why = err or (
            "aerodinámica por triángulos (C++ del juego, sin fórmula): no hay "
            "medida del banco para este origen (huella "
            f"{meas.source_fingerprint(pv.source)[:12]} sin registro en measurements/)"
        )
        for path, _, _ in AERO_BENCH_FIELDS:
            ir.set(path, Param.missing(unit, why))
        return
    path_rec, rec = hit
    try:
        tables = aero_tables(rec)
    except AeroBenchError as exc:
        for path, _, _ in AERO_BENCH_FIELDS:
            ir.set(path, Param.missing(unit, f"registro de banco inválido: {exc}"))
        return
    rel = path_rec.relative_to(meas.PROJECT_ROOT).as_posix()
    bench = rec.get("bench", {})
    refs = [
        S.EvidenceRef(
            type="file",
            path=rel,
            locator=f"huella {rec['source_fingerprint'][:12]}"
            + (
                " (compatible: huella acotada)"
                if rec.get("_match") == "compatible"
                else ""
            ),
            sha256=hashlib.sha256(path_rec.read_bytes()).hexdigest(),
        ),
        S.EvidenceRef(
            type="report",
            path=str(bench.get("dump", "")),
            locator=f"sesión {bench.get('session', '?')}",
            sha256=str(bench.get("dump_sha256", "")),
        ),
        _code("src/extract/aero_bench.py", "aero_tables / reduce_case"),
    ]
    game = rec.get("game", {})
    note_common = (
        f"medido en {game.get('name', '?')} {game.get('version', '?')} a "
        f"{tables.reference_speed:g} m/s; escala con v² en "
        f"{min(tables.speeds):g}–{max(tables.speeds):g} m/s (CdA ±"
        f"{tables.speed_dev_cda * 100:.2f} %, ClA ±{tables.speed_dev_cla * 100:.2f} %, "
        f"reparto ±{tables.speed_dev_split:.4f} m²); guiñada ±"
        f"{max(abs(y) for y in tables.yaw_angles) if tables.yaw_angles else 0:g}° "
        f"cambia CdA {tables.yaw_dev_cda * 100:.1f} % y ClA "
        f"{tables.yaw_dev_cla * 100:.1f} % (no representado)"
    )
    for path, attr, what in AERO_BENCH_FIELDS:
        ir.set(
            path,
            Param.of(
                [list(row) for row in getattr(tables, attr)],
                unit,
                "measured",
                list(refs),
                note=f"{what}; {note_common}",
            ),
        )


TYRE_BENCH_FIELDS = (
    # (campo de TyreAxle, unidad, clase)
    ("reference_load", "N", "measured"),
    ("free_rolling_radius", "m", "measured"),
    ("vertical_stiffness", "N/m", "measured"),
    ("vertical_damping", "N·s/m", "measured"),
    ("rotational_inertia", "kg·m²", "computed"),
    ("rolling_resistance_vs_speed", "table:m/s->1", "measured"),
    ("force_slip_samples", "dataset", "measured"),
)


def _import_tyre_bench(ir: VehicleIR, pv: Any) -> None:
    """G2.20: neumático MEDIDO en el banco de BeamNG (``beamng_tyre_bench``).

    El rozamiento del neumático de BeamNG lo calcula el C++ del juego (G2.19 §3): sin
    fórmula; solo medidas. El registro se asocia por la huella del origen (nunca por
    nombre; ``src/importers/measurements.py``). Reducción y convenciones:
    ``src/extract/tyre_bench.py`` y ``tools/measurements/import_tyre_bench_g220.py``.
    Sin registro, cada campo queda ausente con el motivo. La longitud de relajación no
    se mide en ningún caso."""
    ir.set(
        "mass.in_game_total",
        Param.missing("kg", "sin medida del banco de neumáticos para este origen"),
    )
    axles = list(ir.tyres.axles)
    if not axles:
        return
    relax_why = (
        "no medida: el banco de G2.20 barre el deslizamiento de forma cuasiestática "
        "(retraso del orden de 10 ms, por debajo de su resolución de 20 ms); hace "
        "falta un "
        "escalón de deriva registrado a ≥ 500 Hz"
    )
    from . import measurements as meas

    try:
        hit = meas.find(pv.source, "beamng_tyre_bench")
    except meas.MeasurementError as exc:
        hit, err = None, str(exc)
    else:
        err = ""
    for pos in axles:
        ir.set(f"tyres.axles.{pos}.relaxation_length", Param.missing("m", relax_why))
    if hit is None and not err and _import_tyre_estimate(ir, pv):
        return
    if hit is None:
        why = err or (
            "rozamiento del neumático calculado por el C++ del juego (sin fórmula): "
            "no hay "
            "medida del banco para este origen (huella "
            f"{meas.source_fingerprint(pv.source)[:12]} sin registro en measurements/)"
        )
        for pos in axles:
            for name, unit, _ in TYRE_BENCH_FIELDS:
                ir.set(f"tyres.axles.{pos}.{name}", Param.missing(unit, why))
        return
    path_rec, rec = hit
    rel = path_rec.relative_to(meas.PROJECT_ROOT).as_posix()
    bench = rec.get("bench", {})
    game = rec.get("game", {})
    sessions = ", ".join(str(x.get("session")) for x in bench.get("sessions", []))
    refs = [
        S.EvidenceRef(
            type="file",
            path=rel,
            locator=f"huella {rec['source_fingerprint'][:12]}"
            + (
                " (compatible: huella acotada)"
                if rec.get("_match") == "compatible"
                else ""
            ),
            sha256=hashlib.sha256(path_rec.read_bytes()).hexdigest(),
        ),
        S.EvidenceRef(
            type="code",
            path=str(bench.get("script", "")),
            locator=f"sesiones {sessions}",
            sha256=str(bench.get("script_sha256", "")),
        ),
        _code("src/extract/tyre_bench.py", "reduce_rows / binned_points / vertical_*"),
    ]
    origin = (
        f"medido en {game.get('name', '?')} {game.get('version', '?')} "
        f"(sesiones {sessions})"
    )
    _import_in_game_mass(ir, bench, refs, origin)
    for pos in axles:
        a = (rec.get("axles") or {}).get(pos)
        if not isinstance(a, dict):
            for name, unit, _ in TYRE_BENCH_FIELDS:
                ir.set(
                    f"tyres.axles.{pos}.{name}",
                    Param.missing(unit, f"el registro del banco no tiene el eje {pos}"),
                )
            continue
        dyn = a.get("vertical_dynamic") or {}
        rep = {k: v.get(pos, {}) for k, v in (rec.get("repeatability") or {}).items()}
        values = {
            "reference_load": (
                a.get("reference_load"),
                "carga media de cada rueda del eje rodando a 20 m/s, par 0, sin carga "
                "añadida "
                "(FZ0 de la parametrización)",
            ),
            "free_rolling_radius": (
                a.get("free_rolling_radius"),
                "v_x/ω con par 0 a 20 m/s; por condición: "
                + ", ".join(
                    f"{k} {v:.5f}"
                    for k, v in sorted(
                        (a.get("free_rolling_radius_by_condition") or {}).items()
                    )
                ),
            ),
            "vertical_stiffness": (
                _static_or_dynamic_stiffness(a, dyn),
                "regresión Fz frente a la altura del buje en 4 escalones de carga "
                "(×0,65…×2 de la carga estática) rodando a 20 m/s (G2.44: si la "
                "pendiente no es positiva —artefacto del banco con neumáticos muy "
                "rígidos, Porsche delantero—, rigidez local de la excitación a 3 Hz); "
                "rigidez local "
                "a 3 Hz "
                f"{(dyn.get('vosc_3hz') or {}).get('stiffness', float('nan')):.0f} N/m "
                "(el neumático no es lineal)",
            ),
            "rotational_inertia": (
                a.get("rotational_inertia"),
                "Σ m·r² de los nodos del conjunto rotatorio (banda + buje) respecto a "
                "la recta "
                "node1–node2, masas del juego (obj:getNodeMass); masa "
                f"{a.get('rotating_mass', 0):.2f} kg",
            ),
            "rolling_resistance_vs_speed": (
                [list(p) for p in a.get("rolling_resistance") or []] or None,
                "−Fx/Fz con par 0 (2.ª mitad de settle, rf_v10, rf_v30)",
            ),
            "force_slip_samples": (
                a.get("samples"),
                "medias por intervalo (0,5° / 0,01) de los barridos lat/lon/comb de "
                "las dos "
                f"ruedas; repetibilidad: {rep}",
            ),
        }
        # la amortiguación vertical no se comporta como un coeficiente viscoso en el
        # banco (depende de la frecuencia y de la duración de la excitación): ausente
        cs = sorted(v.get("damping", 0.0) for v in dyn.values() if isinstance(v, dict))
        values["vertical_damping"] = (
            None,
            "no determinable: la componente en cuadratura de la excitación vertical da "
            + (f"{cs[0]:.0f}…{cs[-1]:.0f} N·s/m" if cs else "valores incoherentes")
            + " según la frecuencia (3–12 Hz) y la duración; no es un coeficiente "
            "viscoso",
        )
        for name, unit, kind in TYRE_BENCH_FIELDS:
            value, what = values[name]
            path = f"tyres.axles.{pos}.{name}"
            if value is None:
                why = (
                    what
                    if name == "vertical_damping"
                    else f"el registro no trae {name}"
                )
                ir.set(path, Param.missing(unit, why))
                continue
            ir.set(
                path, Param.of(value, unit, kind, list(refs), note=f"{what}; {origin}")
            )


def _import_tyre_estimate(ir: VehicleIR, pv: Any) -> bool:
    """Neumático ESTIMADO sin banco (``src/ac/tyre_estimate.py``): sin medida del juego, los
    parámetros SCTM salen de una regresión sobre los coeficientes de rozamiento del JBeam,
    entrenada con las medidas del banco. Los campos quedan con clase ``estimated`` y el
    dataset lleva ``estimated_sctm`` (``t_ac_sctm_fit`` lo devuelve sin ajustar). Devuelve
    ``False`` si no se puede estimar (faltan coeficientes o geometría)."""
    from src.ac.tyre_estimate import TyreEstimateError, WHEEL_KEYS, estimate_axle

    from src.attrs.store import REPO_CATALOG

    from . import automation

    ch, g = pv.chassis, pv.geometry
    if ch is None or g is None or not ch.brakes_tyres.tyres:
        return False
    # solo exportaciones de Automation; un vehículo con decisiones propias en el catálogo
    # (el Ibiza: base congelada, neumáticos por decisiones) no se toca
    if not automation.is_automation(pv.source):
        return False
    if (REPO_CATALOG / "decisions" / f"{pv.source.vehicle_id}.jsonl").exists():
        return False
    # la regresión solo está calibrada con neumáticos de 16 rayos (todos los registros
    # del banco); otro número de rayos cambia el reparto de carga entre nodos: sin estimar
    from src.extract.components import collect_wheel_props

    from src.ac.tyre_estimate import STRUCT_KEYS
    from src.jbeam import evaluate

    wheel_props = {
        wp.name: wp.props
        for wp in collect_wheel_props(pv.resolved)
        if wp.name in {t.wheel for t in ch.brakes_tyres.tyres}
    }
    variables = pv.resolved.variable_values()

    def structure(name: str) -> dict[str, float]:
        out = {}
        for k in STRUCT_KEYS:
            try:
                v = evaluate(wheel_props[name][k], variables)
            except Exception:  # noqa: BLE001 - propiedad ausente o no evaluable
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = float(v)
        return out

    rays = {n: p.get("numRays") for n, p in wheel_props.items()}
    if not rays or any(v != 16 for v in rays.values()):
        return False
    by_wheel = {t.wheel: t for t in ch.brakes_tyres.tyres}
    gwheel = {w.name: w for w in g.wheels}
    fronts = {w for w in ch.front_wheels}
    cal_ref = _code("src/ac/tyre_estimator_coefficients.json", "tyre_estimator v1")
    ref = [_code("src/ac/tyre_estimate.py", "estimate_axle"), cal_ref]
    results = {}
    for pos in ir.tyres.axles:
        names = [w for w in by_wheel if (w in fronts) == (pos == "front")]
        if not names:
            return False
        t = by_wheel[names[0]]
        wp = {k: t.friction_raw.get(k) for k in WHEEL_KEYS}
        if any(
            not isinstance(v, (int, float)) or isinstance(v, bool) for v in wp.values()
        ):
            return False
        share = g.front_axle_fraction if pos == "front" else 1.0 - g.front_axle_fraction
        fz0 = g.total_mass * 9.81 * share / len(names)
        rotating = sum(gwheel[n].generated_mass for n in names) / len(names)
        extra = {
            k: float(t.friction_raw[k])
            for k in (
                "slidingFrictionCoef",
                "stribeckExponent",
                "stribeckVelMult",
                "softnessCoef",
                "treadCoef",
            )
            if isinstance(t.friction_raw.get(k), (int, float))
        }
        extra.update(structure(names[0]))
        extra["tireWidth"] = t.width or 0.0
        extra["pressurePSI"] = t.pressure_psi or 0.0
        try:
            results[pos] = estimate_axle(
                {**{k: float(v) for k, v in wp.items()}, **extra}, fz0, t.radius, t.hub_radius,
                t.pressure_psi, t.width or 0.0, rotating,
            )  # fmt: skip
        except (TyreEstimateError, KeyError):
            return False
        results[pos]["fz0"] = fz0
        # Σ m·r² de los nodos rotatorios: ya medido en la geometría (contraste con el
        # banco en 8 ejes de 4 coches: ± 1 %); la masa rotatoria del banco = masa generada
        results[pos]["spin_inertia"] = sum(gwheel[n].spin_inertia for n in names) / len(
            names
        )
    for pos, e in results.items():
        cal = e["calibration"]
        what = (
            f"ESTIMADO sin banco (regresión sobre {cal['records']} registros del banco, "
            f"v{cal['version']}; no es una medida)"
            + ("; " + "; ".join(e["warnings"]) if e["warnings"] else "")
        )
        vals = {
            "reference_load": (e["fz0"], "N", "carga estática = masa·g·reparto/ruedas"),
            "free_rolling_radius": (
                e["free_rolling_radius"],
                "m",
                "radio × razón media",
            ),
            "vertical_stiffness": (e["vertical_stiffness"], "N/m", "regresión lineal"),
            "rotational_inertia": (
                e["spin_inertia"],
                "kg·m²",
                "Σ m·r² de los nodos rotatorios del JBeam (geometría; ±1 % frente al banco)",
            ),
            "rolling_resistance_vs_speed": (
                e["rolling_resistance"],
                "table:m/s->1",
                "media",
            ),
            "force_slip_samples": (
                {"estimated_sctm": e["sctm"], "columns": [], "rows": []},
                "dataset",
                "parámetros SCTM estimados",
            ),
        }
        for name, unit, _ in TYRE_BENCH_FIELDS:
            path = f"tyres.axles.{pos}.{name}"
            if name not in vals:
                ir.set(path, Param.missing(unit, "no determinable sin banco: " + what))
                continue
            value, vunit, how = vals[name]
            kind = "computed" if name == "rotational_inertia" else "estimated"
            ir.set(path, Param.of(value, vunit, kind, list(ref), note=f"{how}; {what}"))
    return True


def _import_in_game_mass(ir: VehicleIR, bench: dict, refs: list, origin: str) -> None:
    """Masa del juego a partir del cierre de fuerzas del banco (``weight`` = Σ masas · g
    con ``tyre_bench.G``): las sesiones deben coincidir (misma carga estática)."""
    from src.extract.tyre_bench import G

    ws = [
        s["closure"]["weight"]
        for s in bench.get("sessions", [])
        if isinstance(s.get("closure"), dict) and s["closure"].get("weight")
    ]
    if not ws or max(ws) - min(ws) > 1e-6 * max(ws):
        return
    ir.set(
        "mass.in_game_total",
        Param.of(
            ws[0] / G,
            "kg",
            "measured",
            list(refs),
            note=f"Σ obj:getNodeMass = peso del cierre de fuerzas / {G}; {origin}",
        ),
    )


def import_vehicle(
    path: str | Path,
    pc: str | None = None,
    common_zip: str | Path | None = None,
    driver_mass_kg: float | None = 70.0,
) -> VehicleIR:
    """IR de un vehículo en carpeta o zip (solo lectura), con recursos y pinturas
    (``src.importers.resources``); el perfil de Automation lo aplica
    :func:`import_preview`."""
    from src.pipeline.physics_preview import (
        load_vehicle_dir,
        load_vehicle_zip,
        preview_physics,
    )

    from .resources import import_resources

    p = Path(path)
    if p.is_dir():
        if pc is None:
            pcs = sorted(x.name for x in p.glob("*.pc"))
            if len(pcs) != 1:
                raise ValueError(f"indique --pc: {pcs}")
            pc = pcs[0]
        source = load_vehicle_dir(p, pc)
    else:
        source = load_vehicle_zip(p, Path(common_zip) if common_zip else None, pc)
    pv = preview_physics(source, driver_mass_kg)
    ir = import_preview(pv, driver_mass_kg)
    import_resources(ir, source, p if p.is_dir() else None)
    return ir
