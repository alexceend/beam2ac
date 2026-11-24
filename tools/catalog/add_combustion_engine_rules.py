"""Reglas de motor para ``combustionEngine`` del exportador 200409 (G2.10, 2026-11-02).

    python tools/catalog/add_combustion_engine_rules.py --phase a [--dry-run]
    python tools/catalog/add_combustion_engine_rules.py --phase a2 [--dry-run]
    python tools/catalog/add_combustion_engine_rules.py --phase b --measurement M [--dry-run]

Decisiones D-G29a/b de G2.9, encargadas por el usuario en G2.10. Solo añade; nunca
sobrescribe (reglas inmutables; validaciones en archivos nuevos). No reutiliza las
reglas ``camso``: cada regla se construye aquí con sus propias condiciones, siempre
``ir.engine.device_type == combustionEngine`` Y ``ir.formats.exporter_version in
["200409"]`` (lista cerrada; nunca rangos).

Fase a (D-G29a), estado ``provisional`` (la semántica de AC sale de los comentarios de
Kunos, sin verificar en AC, igual que en las reglas ``camso``):

* ``eng.minimum_combustion@1``: MINIMUM = ``idleRPM`` (objetivo del controlador de
  ralentí, combustionEngine.lua l. 706-717, 1700-1701).
* ``eng.limiter_combustion@1``: LIMITER = ``maxRPM`` tras el limitador (l. 1957-1971),
  solo con limitador activo (``hasRevLimiter`` y tipo conocido, l. 1964-1989).
* ``eng.inertia_combustion@1``: INERTIA = ``inertia`` del motor, solo si los hijos
  ``clutchlike`` no añaden ``additionalEngineInertia`` (l. 932-941).

Fase a2: ``eng.minimum_combustion@2`` y ``eng.limiter_combustion@2`` con la lectura y el
uso de MINIMUM y LIMITER verificados en AC (``getInt`` = ``std::stoi``: un MINIMUM
con decimales se trunca; LIMITER = umbral de corte del gas en motor de AC). INERTIA
se lee con ``getFloat`` pero su uso no se ha analizado: se queda en @1.

Fase b (D-G29b): ``eng.power_lut_combustion@1`` (``validated``) + una validación
``in_game_value`` por vehículo medido, a partir de ``medicion.json`` del banco de par
en BeamNG (``output/g210_d_g29_decisions_2026-11-02/medicion_beamng/``). Solo con
sobrealimentación turbo (``ir.engine.forced_induction == True``; el extractor deja la
curva ausente con compresor, N2O o válvula por marcha), que es lo medido.

D-G29c (freno motor): sin regla; ver el informe.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, _writable, write_rule  # noqa: E402
from src.extract.engine_combustion import GAME_LUA, LUA  # noqa: E402

DATE = "2026-11-02"
AUTHOR = "claude (G2.10: decisiones D-G29a/b/c, encargadas por el usuario)"
DOC = "docs/research/g210_d_g29_decisions_2026-11-02.md"
EVID = "output/g210_d_g29_decisions_2026-11-02"
GAME = "G:/SteamLibrary/steamapps/common/BeamNG.drive/"
EXPORTERS = ["200409"]
# Tolerancia de la validación en el juego: |power.lut − medida| ≤ 0,5 % de la medida en
# cada rpm medida (la misma cifra que la tolerancia de las validaciones info del Ibiza,
# aquí relativa). Ver el informe §2.2.
TOLERANCE_PCT = 0.5


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


def cond(fact: str, op: str, value) -> S.Condition:
    return S.Condition(fact=fact, op=op, value=value)


def lua(locator: str) -> S.EvidenceRef:
    return ev("code", GAME + LUA, locator, GAME_LUA[LUA])


BASE_CONDITIONS = [
    cond("ir.engine.device_type", "eq", "combustionEngine"),
    cond("ir.formats.exporter_version", "in", EXPORTERS),
]
TARGET_DOC = [
    ev("doc", "sample_assetto/data/engine.ini", "comentarios de Kunos"),
    ev("doc", "docs/research/assetto_engine_semantics.md", "[ENGINE_DATA]"),
    ev("doc", "docs/research/beamng_to_assetto_engine_mapping.md"),
]
COMMON_SOURCE = [
    ev("code", "src/extract/engine_combustion.py", "read_combustion_engine"),
    ev("code", "src/importers/beamng.py", "_import_combustion_engine"),
]


def rule(
    rid: str,
    target: str,
    kind: str,
    transform: S.Transform,
    inputs: dict[str, str],
    extra: list[S.Condition],
    assumptions: list[str],
    source_doc: list[S.EvidenceRef],
    evidence: list[S.EvidenceRef],
    reason: str,
    status: str = "provisional",
    validations: list[str] | None = None,
    open_issues: list[str] | None = None,
    notes: str = "",
) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind=kind,
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status=status,
        inputs=inputs,
        conditions=list(BASE_CONDITIONS) + extra,
        assumptions=assumptions,
        open_issues=open_issues or [],
        source_doc=COMMON_SOURCE + source_doc,
        target_doc=list(TARGET_DOC),
        evidence=[ev("report", DOC)] + evidence,
        validations=validations or [],
        history=[
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                reason=reason,
                status_from="",
                status_to=status,
            )
        ],
        notes=notes,
    )


def identity(unit: str) -> S.Transform:
    return S.Transform(
        id="identity", params={}, input_units={"x": unit}, output_unit=unit
    )


def phase_a() -> list[S.Rule]:
    seven = ev("report", f"{EVID}/siete_200409.md", "por vehículo")
    return [
        rule(
            "eng.minimum_combustion",
            "ac:engine.ini/ENGINE_DATA/MINIMUM",
            "direct",
            identity("rpm"),
            {"x": "ir.engine.idle_speed"},
            [],
            [
                "MINIMUM de AC (ralentí, assetto_engine_semantics.md) = idleRPM de "
                "combustionEngine, objetivo del controlador de ralentí (idleAV = idleRPM · "
                "rpmToAV, l. 1700-1701; control l. 706-717); equivalencia en AC sin "
                "verificar (misma hipótesis que eng.minimum_camso91392@2)"
            ],
            [lua("l. 1700-1701 (idleRPM, idleAV); l. 706-717 (control de ralentí)")],
            [seven],
            "D-G29a: idleRPM es un dato del JBeam (sin valor por defecto en el Lua) "
            "con la misma semántica de ralentí que en camsoEngine",
            open_issues=[
                "Automation 200409 exporta idleRPM con decimales (645.5273…); la "
                "lectura de un MINIMUM no entero en AC no está verificada"
            ],
        ),
        rule(
            "eng.limiter_combustion",
            "ac:engine.ini/ENGINE_DATA/LIMITER",
            "computed",
            identity("rpm"),
            {"x": "ir.engine.speed_ceiling"},
            [cond("ir.engine.rev_limiter_mode", "eq", "activo")],
            [
                "LIMITER de AC (rpm de corte) = maxRPM de combustionEngine tras el "
                "limitador (revLimiterRPM, por defecto maxRPM = min(maxRPM del JBeam, "
                "rpm máx. de la tabla), l. 1957-1971); el modo de corte (rpmDrop, "
                "timeBased, soft) y LIMITER_HZ no se trasladan; equivalencia en AC "
                "sin verificar (misma hipótesis que eng.limiter_camso91392@2)"
            ],
            [lua("l. 1955-1989 (maxRPM, hasRevLimiter, revLimiterType/RPM)")],
            [seven],
            "D-G29a: el techo lo calcula el Lua a partir de datos del JBeam y de sus "
            "valores por defecto (registrados en el IR); sin limitador el juego reduce "
            "el par pasado maxRPM (redlineTorqueDropOffRange) y AC corta: se excluye",
        ),
        rule(
            "eng.inertia_combustion",
            "ac:engine.ini/ENGINE_DATA/INERTIA",
            "direct",
            identity("kg·m²"),
            {"x": "ir.engine.inertia"},
            [cond("ir.engine.clutch_additional_inertia", "eq", 0.0)],
            [
                "INERTIA de AC («engine inertia», comentario de Kunos) = inercia de "
                "rotación del motor de BeamNG (device.inertia, kg·m², l. 1717); "
                "equivalencia de semántica en AC sin verificar (misma hipótesis que "
                "eng.inertia_camso91392@2)"
            ],
            [
                lua(
                    "l. 1717 (inertia or 0.1); l. 932-949 (validate: + "
                    "additionalEngineInertia de los hijos clutchlike)"
                ),
                ev("code", GAME + "lua/vehicle/powertrain/dctGearbox.lua", "l. 387"),
                ev(
                    "code",
                    GAME + "lua/vehicle/powertrain/torqueConverter.lua",
                    "l. 146",
                ),
                ev(
                    "code", GAME + "lua/vehicle/powertrain/frictionClutch.lua", "l. 242"
                ),
            ],
            [seven],
            "D-G29a: el Lua suma a la inercia del motor la additionalEngineInertia de "
            "sus hijos clutchlike (por defecto 0 en el Lua de cada hijo); la regla solo "
            "aplica cuando esa suma es 0 y por tanto la inercia total es la del motor",
        ),
    ]


ACS = "output/g210_d_g29_decisions_2026-11-02/acs_ini"
ACS_SHA = ""


def acs(locator: str, file: str) -> S.EvidenceRef:
    return ev("code", f"{ACS}/{file}", locator, note="AC")


def phase_a2(cat: Catalog) -> list[S.Rule]:
    """Versión 2 de MINIMUM y LIMITER: la lectura y el uso en AC quedan verificados
    (análisis de la misma AC que en fases anteriores). Misma transformación,
    entradas, ámbito y condiciones."""
    out = []
    changes = {
        "eng.minimum_combustion@1": (
            [
                "MINIMUM de AC = idleRPM de combustionEngine (objetivo del controlador de "
                "ralentí, l. 1700-1701, 706-717). En AC (motor de AC) "
                "MINIMUM se lee con lectura de INI de AC = std::stoi (wcstol base 10; 0 → "
                "1000): un valor con decimales se TRUNCA (645.527 → 645). motor de AC "
                " lo usa como ralentí: por debajo de MINIMUM el par de "
                "salida es al menos 15 N·m (sin calado) y el freno motor es 0"
            ],
            [
                acs(
                    "motor de AC l. 166-180 (MINIMUM)",
                    "",
                ),
                acs(
                    "lectura de INI de AC (wcstol)",
                    "",
                ),
                acs("motor de AC", ""),
            ],
            "lectura de MINIMUM en AC verificada (getInt trunca los decimales): "
            "se cierra el open_issue de la versión 1",
        ),
        "eng.limiter_combustion@1": (
            [
                "LIMITER de AC = maxRPM de combustionEngine tras el limitador "
                "(revLimiterRPM, por defecto maxRPM = min(maxRPM del JBeam, rpm máx. de la "
                "tabla), l. 1957-1971). En AC LIMITER se lee con getInt (Engine::"
                "loadINI) y motor de AC corta el gas cuando "
                "rpm > LIMITER·limiterMultiplier durante LIMITER_HZ ciclos; BeamNG "
                "(rpmDrop) lo corta cuando ω > limiterAV hasta caer revLimiterRPMDrop: "
                "mismo umbral, distinta duración del corte (LIMITER_HZ no se traslada)"
            ],
            [
                acs(
                    "motor de AC l. 264-313 (LIMITER, LIMITER_HZ)",
                    "",
                ),
                acs("motor de AC (limiterOn)", ""),
            ],
            "umbral del limitador de AC verificado en AC",
        ),
    }
    for key, (assumptions, extra_doc, reason) in changes.items():
        old = cat.rules[key]
        new = S.from_dict(S.to_dict(old))
        new.version, new.supersedes = old.version + 1, old.key
        new.assumptions = assumptions
        new.open_issues = []
        new.target_doc = list(old.target_doc) + extra_doc
        new.history = list(old.history) + [
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="new_version",
                reason=reason + "; transformación, entradas, ámbito y condiciones sin "
                "cambios",
                status_from=old.status,
                status_to=new.status,
            )
        ]
        out.append(new)
    return out


def phase_b(measurement: Path) -> tuple[list[S.Rule], dict[str, list[S.Validation]]]:
    data = json.loads(measurement.read_text("utf-8"))
    rel = measurement.resolve().relative_to(ROOT).as_posix()
    validations: dict[str, list[S.Validation]] = {}
    worst_meas = worst_lut = 0.0
    for name, d in sorted(data.items()):
        if "error" in d or "variant" not in d:
            raise SystemExit(f"{name}: medición sin datos utilizables")
        rows = [r for r in d["wot_points"] if r["estimated_Nm"] is not None]
        expected = {str(r["rpm"]): round(r["measured_norm_Nm"], 4) for r in rows}
        observed = {str(r["rpm"]): round(r["powerlut_lerp_Nm"], 4) for r in rows}
        pct = max(
            abs(r["delta_lut_Nm"]) / abs(r["measured_norm_Nm"]) * 100 for r in rows
        )
        worst_meas = max(worst_meas, d["summary"]["max_abs_delta_norm_Nm"])
        worst_lut = max(worst_lut, d["summary"]["max_abs_delta_lut_Nm"])
        result = "pass" if pct <= TOLERANCE_PCT else "fail"
        validations[name] = [
            S.Validation(
                id=f"{name}.{d['variant']}.engine_curve_in_game",
                vehicle=name,
                method="in_game_value",
                covers=["ac:power.lut"],
                result=result,
                variant=d["variant"],
                expected=expected,
                observed=observed,
                tolerance=TOLERANCE_PCT,
                evidence=[
                    ev("report", rel, name),
                    ev(
                        "code",
                        f"{EVID}/medicion_beamng/lua_src/lua/vehicle/extensions/"
                        "beam2acDyno.lua",
                        "banco en el eje del motor",
                    ),
                ],
                date=DATE,
                note=(
                    "esperado: par neto medido en BeamNG "
                    f"{d['game_version']} (banco en el eje, régimen estable a plena "
                    "carga, normalizado a densidad relativa 1), N·m por rpm; observado: "
                    "power.lut del conversor (interpolación lineal de "
                    "ir.engine.wot_torque_curve) en esas rpm; tolerancia en % de la "
                    f"medida; peor desviación {pct:.4f} %"
                ),
            )
        ]
    ids = [v.id for vs in validations.values() for v in vs]
    r = rule(
        "eng.power_lut_combustion",
        "ac:power.lut",
        "computed",
        S.Transform(
            id="table",
            params={},
            input_units={"x": "table:rpm->N·m"},
            output_unit="table:rpm->N·m",
        ),
        {"x": "ir.engine.wot_torque_curve"},
        [cond("ir.engine.forced_induction", "eq", True)],
        [
            "curva = estimación de régimen estable del juego (turbocharger.lua "
            "getTorqueCoefs, válvula en start + rango/2) × curva NA − fricción; medida "
            f"en BeamNG con un banco en el eje del motor en {len(ids)} vehículos: "
            f"|medida − estimación| ≤ {worst_meas:.2g} N·m en las rpm medidas",
            "AC interpola linealmente entre los puntos del IR (rpm de la tabla del "
            "JBeam, techo y rpm añadidas hasta ≤ 0,1 N·m de la curva por rpm, "
            "src.importers.beamng.refine_knots): |power.lut − medida| ≤ "
            f"{worst_lut:.3f} N·m en las rpm medidas (500 rpm … techo − 1)",
            "densidad relativa del aire = 1: la estimación no incluye "
            "intakeAirDensityCoef (combustionEngine.lua l. 748); la medida se normaliza",
            "decisión 1: el boost ya está en la tabla; sin [TURBO_n]; la dinámica del "
            "turbo (retardo, PID de la válvula) no se traslada",
        ],
        [
            lua("l. 82-103 (getTorqueData, turbo); l. 731-802 (updateTorque)"),
            ev(
                "code",
                GAME + "lua/vehicle/powertrain/turbocharger.lua",
                "l. 527-547 getTorqueCoefs; l. 163-205 updateFixedStep",
                GAME_LUA["lua/vehicle/powertrain/turbocharger.lua"],
            ),
        ],
        [ev("report", rel), ev("report", f"{EVID}/medicion_beamng/medicion.md")],
        "D-G29b: la curva estimada coincide con el par medido en el juego en régimen "
        "estable en todos los vehículos 200409 del corpus (no solo la potencia máxima)",
        status="validated",
        validations=ids,
        notes="solo turbo (lo medido); NA, compresor y N2O quedan fuera por condición o "
        "porque el extractor deja la curva ausente",
    )
    return [r], validations


def write_validations(name: str, items: list[S.Validation]) -> Path:
    target = _writable(REPO_CATALOG / "validations" / f"{name}.json")
    if target.exists():
        raise SystemExit(f"{target} ya existe: las validaciones no se sobrescriben")
    for v in items:
        S.from_dict(S.to_dict(v), "validation")
    target.write_text(
        json.dumps([S.to_dict(v) for v in items], indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["a", "a2", "b"], required=True)
    ap.add_argument("--measurement", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    validations: dict[str, list[S.Validation]] = {}
    if args.phase == "a":
        rules = phase_a()
    elif args.phase == "a2":
        rules = phase_a2(cat)
    else:
        if args.measurement is None:
            ap.error("--phase b necesita --measurement")
        rules, validations = phase_b(args.measurement)
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        for name, vs in validations.items():
            print("ok (sin escribir):", name, [(v.id, v.result) for v in vs])
        return 0
    for name, vs in validations.items():
        print("escrita:", write_validations(name, vs))
    for r in rules:
        print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
