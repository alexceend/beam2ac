"""Siembra el catálogo de atributos ``catalog/`` (una sola vez; se niega a sobrescribir).

    python tools/catalog/seed_catalog.py [--root catalog]

Después de sembrarlo, la fuente de verdad son los JSON del catálogo: las reglas se cambian
creando una versión nueva (``supersedes``), nunca editando este script ni la regla publicada.

Qué contiene (2026-10-26):
* ``attributes/assettocorsa.json``: las claves de física de AC usadas por el proyecto (base
  aprobada del Ibiza ∪ claves que generan los borradores), con unidad SOLO si está documentada
  (``docs/research/…``); si no, ``undocumented``.
* ``families.json``: ``automation_export`` (pertenencia por metadatos declarados y Lua).
* ``rules/``: las correspondencias que ya implementan los generadores (fases 3–5 y 7), con su
  ámbito real: vehículo ``seat_ibiza_acfr`` salvo ``power.lut`` y ``TRACTION/TYPE``, que tienen
  validación numérica en dos vehículos con el mismo código (plan §6.1) y se declaran de familia;
  requisitos de formato genéricos.
* ``validations/``: validaciones registradas (info_*.json y prueba funcional en el juego).
* ``targets/ac_physics.json``: atributos que se resuelven.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402

DATE = "2026-10-26"
AUTHOR = "claude (sembrado del catálogo)"
CAMSO_ENGINE_SHA = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
IBIZA = "seat_ibiza_acfr"
BASE = "output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25/seat_ibiza_acfr/data"


def R(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, note=note)


DOC = {
    "engine": [R("doc", "docs/research/motor.md"), R("doc", "docs/research/beamng_to_assetto_engine_mapping.md")],
    "drivetrain": [R("doc", "docs/research/transmision.md")],
    "susp": [R("doc", "docs/research/ac_suspension_coordinates.md"),
             R("doc", "docs/research/suspension_frenos_neumaticos_aero.md")],
    "tyres": [R("doc", "docs/research/suspension_frenos_neumaticos_aero.md", "neumáticos")],
    "brakes": [R("doc", "docs/research/suspension_frenos_neumaticos_aero.md", "frenos")],
    "car": [R("doc", "docs/research/ensamblado.md")],
    "aero": [R("doc", "docs/research/ensamblado.md", "§9.7 aero mínima")],
    "setup": [R("doc", "docs/research/ensamblado.md", "§9.12")],
}
ACS = "docs/research/ac_suspension_coordinates.md"

# (archivo, clave, unidad, dtype, grupo de documentación, nota de unidad, requerido para cargar)
UNDOC = "undocumented"
ATTRS: list[tuple[str, str, str, str, str, str, bool]] = []


def A(file: str, key: str, unit: str = UNDOC, dtype: str = "number", group: str = "", note: str = "",
      required: bool = False) -> None:
    ATTRS.append((file, key, unit, dtype, group, note, required))


# engine.ini / power.lut
A("engine.ini", "HEADER/VERSION", "1", "integer", "engine")
A("engine.ini", "HEADER/POWER_CURVE", "text", "text", "engine")
A("engine.ini", "HEADER/COAST_CURVE", "text", "text", "engine")
A("engine.ini", "ENGINE_DATA/INERTIA", "kg·m²", group="engine")
A("engine.ini", "ENGINE_DATA/LIMITER", "rpm", group="engine")
A("engine.ini", "ENGINE_DATA/MINIMUM", "rpm", group="engine")
A("engine.ini", "COAST_REF/RPM", "rpm", group="engine")
A("engine.ini", "COAST_REF/TORQUE", "N·m", group="engine")
A("engine.ini", "COAST_REF/NON_LINEARITY", "1", group="engine")
# drivetrain.ini
A("drivetrain.ini", "TRACTION/TYPE", "enum", "enum", "drivetrain")
A("drivetrain.ini", "GEARS/COUNT", "1", "integer", "drivetrain")
A("drivetrain.ini", "GEARS/GEAR_R", "1", group="drivetrain")
for k in range(1, 9):
    A("drivetrain.ini", f"GEARS/GEAR_{k}", "1", group="drivetrain")
A("drivetrain.ini", "GEARS/FINAL", "1", group="drivetrain")
A("drivetrain.ini", "DIFFERENTIAL/PRELOAD", "N·m", group="drivetrain", note="Nm según el SDK de Kunos")
A("drivetrain.ini", "GEARBOX/VALID_SHIFT_RPM_WINDOW", "rpm", group="drivetrain", required=True)
A("drivetrain.ini", "CLUTCH/MAX_TORQUE", "N·m", group="drivetrain")
# suspensions.ini
A("suspensions.ini", "BASIC/WHEELBASE", "m", group="susp")
A("suspensions.ini", "BASIC/CG_LOCATION", "1", group="susp", note=f"{ACS}:120 fracción de WHEELBASE")
A("suspensions.ini", "ARB/FRONT", "N/m", group="susp", note=f"{ACS}:262")
A("suspensions.ini", "ARB/REAR", "N/m", group="susp", note=f"{ACS}:262")
for axle in ("FRONT", "REAR"):
    A("suspensions.ini", f"{axle}/TYPE", "enum", "enum", "susp", required=(axle == "REAR"))
    A("suspensions.ini", f"{axle}/TRACK", "m", group="susp")
    A("suspensions.ini", f"{axle}/BASEY", "m", group="susp")
    A("suspensions.ini", f"{axle}/SPRING_RATE", "N/m", group="susp", note="en rueda")
    A("suspensions.ini", f"{axle}/PROGRESSIVE_SPRING_RATE", group="susp")
    for d in ("DAMP_BUMP", "DAMP_REBOUND", "DAMP_FAST_BUMP", "DAMP_FAST_REBOUND"):
        A("suspensions.ini", f"{axle}/{d}", "N·s/m", group="susp")
    for d in ("DAMP_FAST_BUMPTHRESHOLD", "DAMP_FAST_REBOUNDTHRESHOLD"):
        A("suspensions.ini", f"{axle}/{d}", "m/s", group="susp")
    A("suspensions.ini", f"{axle}/BUMPSTOP_UP", "m", group="susp")
    A("suspensions.ini", f"{axle}/BUMPSTOP_DN", "m", group="susp")
    A("suspensions.ini", f"{axle}/BUMP_STOP_RATE", "N/m", group="susp")
    A("suspensions.ini", f"{axle}/PACKER_RANGE", "m", group="susp")
    A("suspensions.ini", f"{axle}/HUB_MASS", "kg", group="susp", note=f"{ACS}:390 (≤ 0 equivale a 20 kg)")
    A("suspensions.ini", f"{axle}/ROD_LENGTH", "m", group="susp", note=f"{ACS}:226")
    A("suspensions.ini", f"{axle}/STATIC_CAMBER", "deg", group="susp", note=f"{ACS}:314")
    A("suspensions.ini", f"{axle}/TOE_OUT", "m", group="susp", note=f"{ACS}:192")
    for p in ("STRUT_CAR", "STRUT_TYRE", "WBCAR_BOTTOM_FRONT", "WBCAR_BOTTOM_REAR", "WBCAR_STEER",
              "WBCAR_TOP_FRONT", "WBCAR_TOP_REAR", "WBTYRE_BOTTOM", "WBTYRE_STEER", "WBTYRE_TOP"):
        A("suspensions.ini", f"{axle}/{p}", "m", "vector", "susp", note=f"{ACS} §2.1-2.2 (marco de la rueda)")
# tyres.ini
A("tyres.ini", "HEADER/VERSION", "1", "integer", "tyres", note="elección de modelo de neumático de AC")
A("tyres.ini", "COMPOUND_DEFAULT/INDEX", "1", "integer", "tyres")
TYRE_KEYS_DOC = {"RADIUS": "m", "RIM_RADIUS": "m", "WIDTH": "m", "PRESSURE_STATIC": "psi"}
TYRE_KEYS_UNDOC = ("ANGULAR_INERTIA", "CAMBER_GAIN", "DAMP", "DCAMBER_0", "DCAMBER_1", "DX0", "DX1", "DX_REF",
                   "DY0", "DY1", "DY_REF", "FALLOFF_LEVEL", "FALLOFF_SPEED", "FLEX", "FLEX_GAIN",
                   "FRICTION_LIMIT_ANGLE", "FZ0", "LS_EXPX", "LS_EXPY", "PRESSURE_D_GAIN", "PRESSURE_FLEX_GAIN",
                   "PRESSURE_IDEAL", "PRESSURE_RR_GAIN", "PRESSURE_SPRING_GAIN", "RATE", "RELAXATION_LENGTH",
                   "ROLLING_RESISTANCE_0", "ROLLING_RESISTANCE_1", "ROLLING_RESISTANCE_SLIP", "XMU", "BRAKE_DX_MOD",
                   "COMBINED_FACTOR", "SPEED_SENSITIVITY", "WEAR_CURVE", "SHORT_NAME")
THERMAL_KEYS = ("BLISTER_GAIN", "BLISTER_GAMMA", "COOL_FACTOR", "CORE_TRANSFER", "FRICTION_K", "GRAIN_GAIN",
                "GRAIN_GAMMA", "INTERNAL_CORE_TRANSFER", "PATCH_TRANSFER", "PERFORMANCE_CURVE", "ROLLING_K",
                "SURFACE_ROLLING_K", "SURFACE_TRANSFER")
for axle in ("FRONT", "REAR"):
    for k, u in TYRE_KEYS_DOC.items():
        A("tyres.ini", f"{axle}/{k}", u, group="tyres")
    A("tyres.ini", f"{axle}/NAME", "text", "text", "tyres")
    for k in TYRE_KEYS_UNDOC:
        A("tyres.ini", f"{axle}/{k}", group="tyres", note="modelo de neumático de AC sin equivalente documentado")
    for k in THERMAL_KEYS:
        A("tyres.ini", f"THERMAL_{axle}/{k}", group="tyres", note="térmico de AC sin equivalente en BeamNG")
# brakes.ini
A("brakes.ini", "DATA/FRONT_SHARE", "1", group="brakes")
A("brakes.ini", "DATA/MAX_TORQUE", "N·m", group="brakes", note="¿por rueda o total? sin verificar")
A("brakes.ini", "DATA/HANDBRAKE_TORQUE", "N·m", group="brakes", note="misma ambigüedad")
A("brakes.ini", "DATA/COCKPIT_ADJUSTABLE", "1", "integer", "brakes")
# car.ini
A("car.ini", "HEADER/VERSION", "1", "integer", "car")
A("car.ini", "INFO/SCREEN_NAME", "text", "text", "car")
A("car.ini", "BASIC/TOTALMASS", "kg", group="car", note="con piloto y sin combustible (convención Kunos)")
A("car.ini", "BASIC/GRAPHICS_OFFSET", "m", "vector", "car", note=f"{ACS} §2.7")
A("car.ini", "FUEL/FUEL", "L", group="car")
A("car.ini", "FUEL/MAX_FUEL", "L", group="car")
A("car.ini", "CONTROLS/FFMULT", "1", group="car")
A("car.ini", "CONTROLS/STEER_LOCK", "deg", group="car")
A("car.ini", "CONTROLS/STEER_RATIO", "1", group="car")
A("car.ini", "CONTROLS/LINEAR_STEER_ROD_RATIO", group="car", note="m por grado según el comentario de la base; sin fuente del SDK")
A("car.ini", "EXPLICIT_INERTIA/INERTIA", "kg·m²", "vector", "car")
# aero.ini / setup.ini
A("aero.ini", "WING_0/NAME", "text", "text", "aero", required=True)
A("setup.ini", "DISPLAY_METHOD/SHOW_CLICKS", "1", "integer", "setup", required=True)


def attr_id(file: str, key: str) -> str:
    return f"ac:{file}/{key}"


def attributes() -> list[dict]:
    out = [S.to_dict(S.AttributeDef(
        id="ac:power.lut", game="assettocorsa", unit="table:rpm->N·m", dtype="table",
        description="curva de par a plena carga de AC", doc=DOC["engine"]))]
    for file, key, unit, dtype, group, note, required in ATTRS:
        out.append(S.to_dict(S.AttributeDef(
            id=attr_id(file, key), game="assettocorsa", unit=unit, dtype=dtype, description=note,
            doc=DOC.get(group, []), required_for_load=required)))
    return out


def scope_vehicle() -> S.Scope:
    return S.Scope(level="vehicle", vehicle=IBIZA)


def camso_conditions() -> list[S.Condition]:
    return [S.Condition(fact="bng.engine.device_type", op="eq", value="camsoEngine"),
            S.Condition(fact="bng.code.camsoEngine_sha256", op="eq", value=CAMSO_ENGINE_SHA)]


def hist(reason: str) -> list[S.HistoryEntry]:
    return [S.HistoryEntry(date=DATE, author=AUTHOR, action="create", reason=reason, status_to="")]


def rule(rid: str, target: str, kind: str, transform: S.Transform, scope: S.Scope, status: str,
         inputs: dict | None = None, conditions=None, applies_when=None, assumptions=None, open_issues=None,
         source_doc=None, target_doc=None, evidence=None, validations=None, notes: str = "") -> S.Rule:
    r = S.Rule(
        id=rid, version=1, target=target, kind=kind, transform=transform, scope=scope, status=status,
        inputs=inputs or {}, conditions=conditions or [], applies_when=applies_when or [],
        assumptions=assumptions or [], open_issues=open_issues or [], source_doc=source_doc or [],
        target_doc=target_doc or [], evidence=evidence or [], validations=validations or [], notes=notes,
        history=[],
    )
    r.history = hist(f"sembrado desde el comportamiento de los generadores existentes ({notes or rid})")
    for h in r.history:
        h.status_to = status
    return r


def T(tid: str, out: str, inputs: dict | None = None, params: dict | None = None, formula: str = "") -> S.Transform:
    return S.Transform(id=tid, output_unit=out, input_units=inputs or {}, params=params or {}, formula=formula)


def code(path: str, loc: str) -> S.EvidenceRef:
    return R("code", path, loc)


INVENTORY = R("report", "docs/research/inventario_spicy_mandala_2026-10-25.md")
RUN3 = R("report", "output/spicy_mandala/attribute_inventory_2026-10-25/run3/inventory.json")
BASE_REF = R("file", BASE, note="base física aprobada y congelada del Ibiza (solo lectura)")


def rules() -> list[S.Rule]:
    out: list[S.Rule] = []
    gen = S.Scope(level="generic")
    # --- requisitos de formato
    for rid, target, value, doc, assumptions in (
        ("fmt.setup_show_clicks", "ac:setup.ini/DISPLAY_METHOD/SHOW_CLICKS", 0,
         R("doc", "docs/research/ensamblado.md", "§9.12: sin setup.ini AC no crea QuickMenuPitstop"), []),
        ("fmt.valid_shift_window", "ac:drivetrain.ini/GEARBOX/VALID_SHIFT_RPM_WINDOW", 800,
         R("doc", "docs/research/ensamblado.md", "§9.15: sin la clave AC divide por 0"),
         ["valor por referencia: ks_toyota_celica_st185 y ks_toyota_ae86 usan 800; ks_audi_r8_lms y "
          "ks_abarth500_assetto_corse usan 1000"]),
        ("fmt.car_header_version", "ac:car.ini/HEADER/VERSION", 1,
         R("code", "src/ac/assemble.py", "render_car_ini"), []),
        ("fmt.engine_header_version", "ac:engine.ini/HEADER/VERSION", 1,
         R("code", "src/ac/engine.py", "render_engine_ini"), []),
        ("fmt.power_curve_file", "ac:engine.ini/HEADER/POWER_CURVE", "power.lut",
         R("code", "src/ac/engine.py", "render_engine_ini"), []),
        ("fmt.coast_curve_mode", "ac:engine.ini/HEADER/COAST_CURVE", "FROM_COAST_REF",
         R("code", "src/ac/engine.py", "render_engine_ini"), []),
    ):
        unit = "text" if isinstance(value, str) else ("rpm" if "WINDOW" in target else "1")
        out.append(rule(rid, target, "format", T("constant", unit, params={"value": value}), gen, "validated",
                        assumptions=assumptions, source_doc=[doc], target_doc=[doc], evidence=[doc],
                        notes="requisito de formato de AC"))

    # --- motor (camsoEngine)
    ec = "src/extract/engine_camso.py"
    eng_src = [code(ec, "camsoEngine.lua citado por línea"), *DOC["engine"]]
    eng_tgt = [R("doc", "sample_assetto/data/engine.ini", "comentarios de Kunos"), *DOC["engine"]]
    eng_ev = [R("report", "docs/research/motor.md"), RUN3]
    out.append(rule("eng.limiter", attr_id("engine.ini", "ENGINE_DATA/LIMITER"), "computed",
                    T("identity", "rpm", {"x": "rpm"}), scope_vehicle(), "provisional",
                    {"x": "bng.engine.rpm_ceiling"}, camso_conditions(),
                    assumptions=["LIMITER ≈ techo de rpm derivado del Lua (hipótesis de equivalencia del generador)"],
                    source_doc=eng_src, target_doc=eng_tgt, evidence=eng_ev, notes="src/ac/engine.py render_engine_ini"))
    out.append(rule("eng.minimum", attr_id("engine.ini", "ENGINE_DATA/MINIMUM"), "direct",
                    T("identity", "rpm", {"x": "rpm"}), scope_vehicle(), "provisional",
                    {"x": "bng.engine.idle_rpm"}, camso_conditions(), assumptions=["MINIMUM = idleRPM (hipótesis)"],
                    source_doc=eng_src, target_doc=eng_tgt, evidence=eng_ev))
    out.append(rule("eng.inertia", attr_id("engine.ini", "ENGINE_DATA/INERTIA"), "direct",
                    T("identity", "kg·m²", {"x": "kg·m²"}), scope_vehicle(), "provisional",
                    {"x": "bng.engine.inertia"}, camso_conditions(),
                    assumptions=["solo la inercia del motor; falta additionalEngineInertia del embrague (Lua:1001)"],
                    source_doc=eng_src, target_doc=eng_tgt, evidence=eng_ev))
    for rid, key, fact, unit in (("eng.coast_rpm", "COAST_REF/RPM", "bng.engine.coast_ref_rpm", "rpm"),
                                 ("eng.coast_torque", "COAST_REF/TORQUE", "bng.engine.coast_ref_torque", "N·m"),
                                 ("eng.coast_nonlinearity", "COAST_REF/NON_LINEARITY",
                                  "bng.engine.coast_ref_nonlinearity", "1")):
        out.append(rule(rid, attr_id("engine.ini", key), "computed", T("identity", unit, {"x": unit}),
                        scope_vehicle(), "provisional", {"x": fact}, camso_conditions(),
                        assumptions=["freno motor lineal desde 0 anclado en el techo de rpm (src/ac/engine.py fit_coast_ref)"],
                        source_doc=[code("src/ac/engine.py", "fit_coast_ref"), *eng_src], target_doc=eng_tgt,
                        evidence=eng_ev))
    out.append(rule("eng.power_lut", "ac:power.lut", "computed",
                    T("table", "table:rpm->N·m", {"x": "table:rpm->N·m"}),
                    S.Scope(level="family", family="automation_export"), "validated",
                    {"x": "bng.engine.net_wot_curve"}, camso_conditions(),
                    assumptions=["fricción interpolada linealmente (createCurve no está en la muestra)",
                                 "decisión 1: el boost ya está en la tabla; sin [TURBO_n]"],
                    source_doc=eng_src, target_doc=eng_tgt, evidence=[*eng_ev, INVENTORY],
                    validations=["seat_ibiza_acfr.35b84.engine_curve", "spicy_mandala.56a86.engine_curve"],
                    notes="familia: validación numérica independiente en 2 vehículos con el mismo camsoEngine.lua"))
    # --- transmisión
    xd = "src/extract/drivetrain.py"
    dt_src = [code(xd, "build_drivetrain"), *DOC["drivetrain"]]
    dt_tgt = [R("doc", "sample_assetto/data/drivetrain.ini"), *DOC["drivetrain"]]
    dt_ev = [R("report", "docs/research/transmision.md"), RUN3]
    out.append(rule("dt.traction", attr_id("drivetrain.ini", "TRACTION/TYPE"), "computed",
                    T("identity", "enum", {"x": "enum"}), S.Scope(level="family", family="automation_export"),
                    "validated", {"x": "bng.drivetrain.traction"},
                    [S.Condition(fact="bng.drivetrain.traction", op="in", value=["FWD", "RWD"])],
                    source_doc=[code("src/ac/drivetrain.py", "traction_type"), *DOC["drivetrain"]], target_doc=dt_tgt,
                    evidence=[*dt_ev, INVENTORY],
                    validations=["seat_ibiza_acfr.35b84.traction", "spicy_mandala.56a86.traction"],
                    notes="familia: tracción validada contra info_*.json en 2 vehículos (FWD y RWD); AWD sin soporte"))
    out.append(rule("dt.gear_count", attr_id("drivetrain.ini", "GEARS/COUNT"), "direct",
                    T("identity", "1", {"x": "1"}), scope_vehicle(), "validated", {"x": "bng.gearbox.forward_count"},
                    source_doc=dt_src, target_doc=dt_tgt, evidence=dt_ev))
    out.append(rule("dt.gear_r", attr_id("drivetrain.ini", "GEARS/GEAR_R"), "direct",
                    T("identity", "1", {"x": "1"}), scope_vehicle(), "validated", {"x": "bng.gearbox.reverse_ratio"},
                    source_doc=dt_src, target_doc=dt_tgt, evidence=dt_ev))
    for k in range(1, 9):
        out.append(rule(f"dt.gear_{k}", attr_id("drivetrain.ini", f"GEARS/GEAR_{k}"), "direct",
                        T("identity", "1", {"x": "1"}), scope_vehicle(), "validated",
                        {"x": f"bng.gearbox.forward_ratio_{k}"},
                        applies_when=[S.Condition(fact="bng.gearbox.forward_count", op="ge", value=k)],
                        source_doc=dt_src, target_doc=dt_tgt, evidence=dt_ev))
    out.append(rule("dt.final", attr_id("drivetrain.ini", "GEARS/FINAL"), "direct",
                    T("identity", "1", {"x": "1"}), scope_vehicle(), "validated", {"x": "bng.diff.final_ratio"},
                    source_doc=dt_src, target_doc=dt_tgt, evidence=dt_ev))
    out.append(rule("dt.preload", attr_id("drivetrain.ini", "DIFFERENTIAL/PRELOAD"), "direct",
                    T("assume_unit", "N·m", {"x": "N/m"}, {"declared": "N/m", "as_unit": "N·m"}),
                    scope_vehicle(), "provisional", {"x": "bng.diff.lsd_preload"},
                    applies_when=[S.Condition(fact="bng.diff.type", op="eq", value="lsd")],
                    source_doc=dt_src, target_doc=dt_tgt, evidence=dt_ev,
                    notes="src/ac/drivetrain.py: 'unidad declarada N/m, se asume Nm'"))
    # --- suspensión (geometría medida y tipo por etiqueta)
    mg = "src/extract/mass_geometry.py"
    s_src = [code(mg, "_group_axles"), *DOC["susp"]]
    s_tgt = [R("doc", ACS), R("doc", "sample_assetto/data/suspensions.ini")]
    s_ev = [R("report", "docs/research/masa_geometria.md"), RUN3]
    out.append(rule("susp.wheelbase", attr_id("suspensions.ini", "BASIC/WHEELBASE"), "measured",
                    T("identity", "m", {"x": "m"}), scope_vehicle(), "validated", {"x": "bng.geometry.wheelbase"},
                    source_doc=s_src, target_doc=s_tgt, evidence=s_ev))
    for pos, axle in (("front", "FRONT"), ("rear", "REAR")):
        out.append(rule(f"susp.track_{pos}", attr_id("suspensions.ini", f"{axle}/TRACK"), "measured",
                        T("identity", "m", {"x": "m"}), scope_vehicle(), "validated",
                        {"x": f"bng.geometry.track_{pos}"}, source_doc=s_src, target_doc=s_tgt, evidence=s_ev))
        out.append(rule(f"susp.type_{pos}", attr_id("suspensions.ini", f"{axle}/TYPE"), "computed",
                        T("identity", "enum", {"x": "enum"}), scope_vehicle(), "provisional",
                        {"x": f"bng.suspension.{pos}.ac_type"},
                        assumptions=["tipo AC deducido de la etiqueta declarada de la pieza (MacPherson/strut → STRUT)"],
                        source_doc=[code("src/extract/suspension.py", "TYPE_BY_LABEL"), *DOC["susp"]],
                        target_doc=s_tgt, evidence=s_ev))
        # --- neumáticos
        bt = "src/extract/brakes_tyres.py"
        t_src = [code(bt, "WheelTyre"), *DOC["tyres"]]
        t_tgt = [R("doc", "sample_assetto/data/tyres.ini"), *DOC["tyres"]]
        for key, fact, unit, kind, assumptions in (
            ("RADIUS", "radius", "m", "measured", ["radio de los nodos sin carga"]),
            ("RIM_RADIUS", "hub_radius", "m", "direct", []),
            ("WIDTH", "width", "m", "direct", []),
            ("PRESSURE_STATIC", "pressure", "psi", "direct", []),
        ):
            out.append(rule(f"tyre.{pos}.{key.lower()}", attr_id("tyres.ini", f"{axle}/{key}"), kind,
                            T("identity", unit, {"x": unit}), scope_vehicle(), "provisional",
                            {"x": f"bng.tyre.{pos}.{fact}"}, assumptions=assumptions, source_doc=t_src,
                            target_doc=t_tgt, evidence=[RUN3]))
    # --- frenos
    out.append(rule("brk.front_share", attr_id("brakes.ini", "DATA/FRONT_SHARE"), "computed",
                    T("identity", "1", {"x": "1"}), scope_vehicle(), "provisional",
                    {"x": "bng.brakes.front_share_pedal_full"},
                    open_issues=["AC no modela el codo de brakeInputSplit: reparto distinto a pedal 100 % y por "
                                 "debajo del codo (Ibiza 0,857 / 0,818)"],
                    source_doc=[code("src/extract/brakes_tyres.py", "front_share"), *DOC["brakes"]],
                    target_doc=[R("doc", "sample_assetto/data/brakes.ini"), *DOC["brakes"]], evidence=[RUN3]))
    # --- car.ini
    c_tgt = [R("doc", "sample_assetto/data/car.ini"), *DOC["car"]]
    out.append(rule("car.screen_name", attr_id("car.ini", "INFO/SCREEN_NAME"), "direct",
                    T("identity", "text", {"x": "text"}), scope_vehicle(), "validated", {"x": "bng.info.name"},
                    source_doc=[code("src/ac/assemble.py", "info.json Name")], target_doc=c_tgt, evidence=[RUN3]))
    for rid, key, fact in (("car.fuel", "FUEL/FUEL", "bng.fuel.start"), ("car.max_fuel", "FUEL/MAX_FUEL", "bng.fuel.capacity")):
        out.append(rule(rid, attr_id("car.ini", key), "direct", T("identity", "L", {"x": "L"}), scope_vehicle(),
                        "validated", {"x": fact}, source_doc=[code("src/ac/assemble.py", "mainTank")],
                        target_doc=c_tgt, evidence=[RUN3]))
    out.append(rule(
        "car.totalmass", attr_id("car.ini", "BASIC/TOTALMASS"), "computed",
        T("linear_sum", "kg", {"total": "kg", "driver": "kg", "hub_front": "kg", "hub_rear": "kg"},
          {"coefficients": {"total": 1, "driver": 1, "hub_front": -2, "hub_rear": -2}},
          "TOTALMASS = masa BeamNG + piloto − 2·HUB_MASS_del − 2·HUB_MASS_tras"),
        scope_vehicle(), "validated",
        {"total": "bng.mass.total", "driver": "project.driver_mass",
         "hub_front": attr_id("suspensions.ini", "FRONT/HUB_MASS"),
         "hub_rear": attr_id("suspensions.ini", "REAR/HUB_MASS")},
        assumptions=["masa total de BeamNG = carrocería + bujes (suma de nodos)"],
        source_doc=[code("src/extract/mass_geometry.py", "compute_mass_geometry"), R("doc", "CLAUDE.md", "decisión 2")],
        target_doc=[R("doc", ACS, "§2.8: AC suma HUB_MASS a la masa del cuerpo"),
                    R("doc", "docs/research/ensamblado.md", "§9.13: masa en AC = TOTALMASS + bujes + combustible")],
        evidence=[BASE_REF, R("doc", "docs/research/ibiza_mass_suspension_proposal.md", "§1.4")],
        notes="reproduce el TOTALMASS de la base aprobada (1119,6); el generador antiguo escribía masa + piloto"))
    return out


def validations() -> dict[str, list[S.Validation]]:
    ev_ib = [RUN3, R("report", "docs/PLAN.md", "valores de referencia 35b84")]
    ev_sp = [RUN3, INVENTORY]
    ib = [
        S.Validation(id="seat_ibiza_acfr.35b84.engine_curve", vehicle=IBIZA, variant="35b84", method="info_reference",
                     covers=["ac:power.lut"], result="pass", expected={"torque": 308, "power_hp": 339},
                     observed={"torque": 307.8, "power_hp": 339.26}, tolerance=0.5, evidence=ev_ib),
        S.Validation(id="seat_ibiza_acfr.35b84.traction", vehicle=IBIZA, variant="35b84", method="info_reference",
                     covers=["ac:drivetrain.ini/TRACTION/TYPE"], result="pass", expected="FWD", observed="FWD",
                     evidence=ev_ib),
        S.Validation(id="seat_ibiza_acfr.35b84.mass", vehicle=IBIZA, variant="35b84", method="info_reference",
                     covers=["bng.mass.total"], result="pass", expected=1226, observed=1226.27, tolerance=0.5,
                     evidence=ev_ib),
        S.Validation(id="seat_ibiza_acfr.pkg_v18bu.functional", vehicle=IBIZA, variant="35b84",
                     method="in_game_functional", covers=["package:pkg_v18bu"], result="pass",
                     evidence=[R("doc", "CLAUDE.md", "Fase B: base física funcional, no calibrada"), BASE_REF],
                     note="el paquete carga y se conduce; NO valida la precisión de ningún valor"),
    ]
    sp = [
        S.Validation(id="spicy_mandala.56a86.engine_curve", vehicle="spicy_mandala", variant="56a86",
                     method="info_reference", covers=["ac:power.lut"], result="pass",
                     expected={"torque": 113, "power_hp_implied": 90.897}, observed={"torque": 113.1, "power_hp": 90.851},
                     tolerance=0.5, evidence=ev_sp,
                     note="potencia implícita Weight/(Weight/Power); rpm de pico: empate en la meseta"),
        S.Validation(id="spicy_mandala.56a86.traction", vehicle="spicy_mandala", variant="56a86",
                     method="info_reference", covers=["ac:drivetrain.ini/TRACTION/TYPE"], result="pass",
                     expected="RWD", observed="RWD", evidence=ev_sp),
        S.Validation(id="spicy_mandala.56a86.mass", vehicle="spicy_mandala", variant="56a86",
                     method="info_reference", covers=["bng.mass.total"], result="fail", expected=1025,
                     observed=1028.19, tolerance=0.5, evidence=ev_sp, note="Δ +3,19 kg sin explicar"),
    ]
    return {IBIZA: ib, "spicy_mandala": sp}


def families() -> list[dict]:
    return [S.to_dict(S.Family(
        id="automation_export",
        description="Vehículos exportados por Automation (Camshaft Software) a BeamNG",
        membership=[S.Condition(fact="bng.info.type", op="eq", value="Automation"),
                    S.Condition(fact="bng.code.camsoEngine_sha256", op="exists")],
        evidence=[R("file", "info.json", "Type 'Automation', Author 'Camshaft Software LTD'"),
                  R("code", "lua/powertrain/camsoEngine.lua", "dispositivo propio del exportador")],
    ))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT / "catalog")
    args = ap.parse_args(argv)
    root = args.root
    if (root / "rules").exists() or (root / "attributes").exists():
        raise SystemExit(f"{root} ya tiene contenido; no se sobrescribe")
    (root / "attributes").mkdir(parents=True)
    (root / "rules").mkdir()
    (root / "validations").mkdir()
    (root / "decisions").mkdir()
    (root / "targets").mkdir()

    def dump(path: Path, data) -> None:
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    attrs = attributes()
    dump(root / "attributes" / "assettocorsa.json", attrs)
    dump(root / "families.json", families())
    for r in rules():
        dump(root / "rules" / f"{r.key}.json", S.to_dict(r))
    for veh, vals in validations().items():
        dump(root / "validations" / f"{veh}.json", [S.to_dict(v) for v in vals])
    dump(root / "targets" / "ac_physics.json",
         {"schema": "beam2ac.targets/1", "targets": [a["id"] for a in attrs]})
    print(f"catálogo sembrado en {root}: {len(attrs)} atributos, {len(rules())} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
