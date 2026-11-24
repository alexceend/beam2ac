"""Reglas ``TYPE=STRUT`` del 200409 para el puntal en raíl medido (G2.19).

    python tools/catalog/add_suspension_strut_200409_rules.py [--dry-run]

Encargada por el usuario en G2.19 (puntal delantero de atlanta). Solo añade; nunca
sobrescribe. Todas exigen ``ir.formats.exporter_version in ["200409"]`` y la estructura
MEDIDA del eje ``ir.suspension.axles.<eje>.linkage in ["strut_rail"]``
(``src/extract/linkage.py``: un raíl con el nodo deslizante en la mangueta y un extremo en
la carrocería + brazo inferior de 2 barras + bieleta; rango 5). Nunca la etiqueta de la
pieza ni el nombre del vehículo. Las reglas DWB (``linkage`` DWB) no cambian.

AC [C] (``output/g217_…/acs_suspension``):

* suspensión de puntal de AC: rótula del cuerpo del puntal en ``STRUT_CAR``, deslizadera
  sin giro hacia el buje a lo largo de ``STRUT_CAR → STRUT_TYRE``, barras
  ``WBCAR_BOTTOM_FRONT/REAR → WBTYRE_BOTTOM`` y ``WBCAR_STEER → WBTYRE_STEER``.
* suspensión de puntal de AC: los 7 puntos se escriben para la rueda derecha
  (X + hacia dentro) y se reflejan en la izquierda (como los del DWB).
* suspensión de puntal de AC: muelle (k + prog·x)·x y amortiguador A LO LARGO del puntal;
  topes por recorrido VERTICAL del buje con rigidez FIJA 500 000 N/m
  (``BUMP_STOP_RATE`` solo actúa con ``PACKER_RANGE``).
* suspensión de puntal de AC: ``WBCAR_STEER.x`` = base + desplazamiento +
  signo·``TOE_OUT`` (igual que el DWB).

Equivalencias (``ac_strut_axis``, s = palanca del eje del puntal en reposo):
SPRING_RATE = rigidez en rueda / s²; DAMP_* = amortiguación en rueda / s²;
umbrales rápidos = umbral en rueda × s; ROD_LENGTH = recorrido de precarga en rueda × s.
Clones del DWB con la MISMA semántica en STRUT (comprobada arriba): STATIC_CAMBER,
TOE_OUT, ``[ARB]``, BUMPSTOP_UP/DN, STEER_RATIO y LINEAR_STEER_ROD_RATIO.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-06"
AUTHOR = "claude (G2.19: desbloqueo integral del 200409, encargada por el usuario)"
DOC = "docs/research/g219_desbloqueo_integral_200409.md"
EVID = "output/g219_2026-11-06/puntal"
ACS = "output/g217_suspension_steering_2026-11-04/acs_suspension/c"
KINDS = ["strut_rail"]
AXLES = (("front", "FRONT"), ("rear", "REAR"))
POINTS = (
    ("strut_chassis", "STRUT_CAR"),
    ("strut_upright", "STRUT_TYRE"),
    ("lower_chassis_front", "WBCAR_BOTTOM_FRONT"),
    ("lower_chassis_rear", "WBCAR_BOTTOM_REAR"),
    ("lower_upright", "WBTYRE_BOTTOM"),
    ("rod_chassis", "WBCAR_STEER"),
    ("rod_upright", "WBTYRE_STEER"),
)
# (clave AC, campo del IR, unidad, magnitud)
AXIS = (
    ("SPRING_RATE", "wheel_rate", "N/m", "rate"),
    ("ROD_LENGTH", "spring_preload_travel", "m", "length"),
    ("DAMP_BUMP", "damper_rate_slow_bump", "N·s/m", "rate"),
    ("DAMP_REBOUND", "damper_rate_slow_rebound", "N·s/m", "rate"),
    ("DAMP_FAST_BUMP", "damper_rate_fast_bump", "N·s/m", "rate"),
    ("DAMP_FAST_REBOUND", "damper_rate_fast_rebound", "N·s/m", "rate"),
    ("DAMP_FAST_BUMPTHRESHOLD", "damper_fast_threshold_bump", "m/s", "speed"),
    ("DAMP_FAST_REBOUNDTHRESHOLD", "damper_fast_threshold_rebound", "m/s", "speed"),
)
# reglas DWB cuya semántica en STRUT es la misma (se clonan cambiando solo la
# condición de estructura): (regla DWB, sufijo de la nueva)
CLONES = (
    ("susp.static_camber_{a}_200409@1", "susp.static_camber_{a}_strut_200409"),
    ("susp.bumpstop_up_{a}_200409@1", "susp.bumpstop_up_{a}_strut_200409"),
    ("susp.bumpstop_dn_{a}_200409@1", "susp.bumpstop_dn_{a}_strut_200409"),
    ("susp.arb_{a}_200409@1", "susp.arb_{a}_strut_200409"),
)
FRONT_CLONES = (
    ("susp.toe_out_front_200409@1", "susp.toe_out_front_strut_200409"),
    ("ctrl.steer_ratio_rod_ahead_200409@1", "ctrl.steer_ratio_rod_ahead_strut_200409"),
    ("ctrl.steer_ratio_rod_behind_200409@1", "ctrl.steer_ratio_rod_behind_strut_200409"),
    (
        "ctrl.linear_steer_rod_ratio_200409@2",
        "ctrl.linear_steer_rod_ratio_strut_200409",
    ),
)
CLONE_NOTE = {
    "susp.static_camber": "suspensión de puntal de AC lee STATIC_CAMBER (×0,017453, signo "
    "por lado) como el DWB",
    "susp.bumpstop_up": "suspensión de puntal de AC: tope por recorrido vertical del buje "
    "frente a BUMPSTOP_UP (rigidez fija 500 000 N/m, no BUMP_STOP_RATE)",
    "susp.bumpstop_dn": "suspensión de puntal de AC: tope por recorrido vertical del buje "
    "frente a BUMPSTOP_DN (rigidez fija 500 000 N/m, no BUMP_STOP_RATE)",
    "susp.arb": "[ARB] actúa sobre el recorrido vertical de las dos ruedas, sin depender "
    "del tipo de suspensión (G2.17 §6)",
    "susp.toe_out": "suspensión de puntal de AC: WBCAR_STEER.x = base + "
    "desplazamiento + signo·TOE_OUT, como el DWB",
    "ctrl.steer_ratio_rod": "dirección de AC llama a el desplazamiento de la barra de dirección; el "
    "puntal lo implementa igual que el DWB",
    "ctrl.linear_steer_rod_ratio": "dirección de AC llama a el desplazamiento de la barra de dirección; "
    "el puntal lo implementa igual que el DWB",
}


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


def cond(axle: str) -> list[S.Condition]:
    return [
        S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"]),
        S.Condition(
            fact=f"ir.suspension.axles.{axle}.linkage", op="in", value=list(KINDS)
        ),
    ]


SOURCE_DOC = [
    ev("code", "src/extract/linkage.py", "_strut_corner (raíl + brazo + bieleta)"),
    ev("code", "src/extract/suspension.py", "collect_rails (rails + slidenodes)"),
    ev("code", "src/importers/beamng.py", "_import_linkage (strut_motion_ratio)"),
]
TARGET_DOC = [
    ev("code", ACS, "suspensión de puntal de AC (rótula, deslizadera, barras)"),
    ev("code", ACS, "suspensión de puntal de AC (reflejo de X)"),
    ev("code", ACS, "suspensión de puntal de AC (muelle, amortiguador, topes)"),
    ev("code", ACS, "suspensión de puntal de AC"),
]
EVIDENCE = [
    ev("report", DOC, "§8 puntal"),
    ev(
        "report",
        f"{EVID}/validacion_direccion.md",
        "giro de rueda del modelo frente al banco de G2.15 (atlanta)",
    ),
    ev("report", f"{EVID}/validacion_puntal.md", "cinemática del puntal y palancas"),
]
ASSUMPTIONS = [
    "mangueta rígida con L·beamPrecompression y el nodo deslizante en su raíl (en la "
    "pose del JBeam está a 2,37 mm del raíl en atlanta; el juego lo lleva al raíl)",
    "ningún 200409 se ha probado en AC",
]


def history(reason: str) -> list[S.HistoryEntry]:
    return [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="create",
            reason=reason,
            status_from="",
            status_to="provisional",
        )
    ]


def mk(rid, target, kind, transform, inputs, axle, assumptions, notes) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind=kind,
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=cond(axle),
        assumptions=assumptions,
        open_issues=[],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=list(EVIDENCE),
        validations=[],
        history=history("G2.19: puntal en raíl medido → TYPE=STRUT de AC"),
        notes="G2.19: solo 200409 con puntal en raíl medido; " + notes,
    )


def clone(src_key: str, new_id: str, axle: str) -> S.Rule:
    raw = json.loads((ROOT / "catalog/rules" / f"{src_key}.json").read_text("utf-8"))
    r = S.from_dict(raw)
    r = copy.deepcopy(r)
    r.id = new_id
    r.version = 1
    r.supersedes = ""
    r.conditions = [
        c
        for c in r.conditions
        if not (c.fact.endswith(".linkage") and c.fact.startswith("ir.suspension"))
    ]
    r.conditions = [
        c if c.fact != "ir.formats.exporter_version" else c for c in r.conditions
    ] + [
        S.Condition(
            fact=f"ir.suspension.axles.{axle}.linkage", op="in", value=list(KINDS)
        )
    ]
    key = next(k for k in CLONE_NOTE if src_key.startswith(k))
    r.assumptions = list(r.assumptions) + [
        f"misma semántica en STRUT que en DWB: {CLONE_NOTE[key]} [C]"
    ]
    if "bumpstop" in src_key:
        r.assumptions.append(
            "en STRUT la rigidez del tope de AC es fija (500 000 N/m vertical) y no "
            "reproduce la ley del tope de BeamNG (bumpstop_rate); solo el punto de "
            "contacto es equivalente"
        )
    r.source_doc = list(r.source_doc) + list(SOURCE_DOC)
    r.target_doc = list(r.target_doc) + list(TARGET_DOC)
    r.evidence = list(r.evidence) + list(EVIDENCE)
    r.history = history(f"G2.19: clon de {src_key} para el puntal en raíl medido")
    r.notes = f"G2.19: clon de {src_key} con la estructura strut_rail"
    return r


def build() -> list[S.Rule]:
    rules: list[S.Rule] = []
    for axle, sec in AXLES:
        base = f"ir.suspension.axles.{axle}"
        rules.append(
            mk(
                f"susp.type_strut_{axle}_200409",
                f"ac:suspensions.ini/{sec}/TYPE",
                "computed",
                S.Transform(
                    id="constant",
                    params={"value": "STRUT"},
                    output_unit="enum",
                    formula="STRUT si la estructura medida del eje es un puntal en raíl",
                ),
                {},
                axle,
                list(ASSUMPTIONS),
                "TYPE",
            )
        )
        for anchor, key in POINTS:
            rules.append(
                mk(
                    f"susp.{key.lower()}_{axle}_strut_200409",
                    f"ac:suspensions.ini/{sec}/{key}",
                    "measured",
                    S.Transform(
                        id="vector_identity",
                        params={"length": 3},
                        input_units={"x": "m"},
                        output_unit="m",
                        formula="nodo − centro de rueda en reposo (X hacia dentro, "
                        "rueda derecha)",
                    ),
                    {"x": f"{base}.anchors.{anchor}"},
                    axle,
                    list(ASSUMPTIONS),
                    f"punto {key}",
                )
            )
        for key, field, unit, quantity in AXIS:
            rules.append(
                mk(
                    f"susp.{key.lower()}_{axle}_strut_200409",
                    f"ac:suspensions.ini/{sec}/{key}",
                    "computed",
                    S.Transform(
                        id="ac_strut_axis",
                        params={"quantity": quantity},
                        input_units={"x": unit, "strut_mr": "1"},
                        output_unit=unit,
                        formula={
                            "rate": "en rueda / s²",
                            "length": "en rueda × s",
                            "speed": "en rueda × s",
                        }[quantity],
                    ),
                    {"x": f"{base}.{field}", "strut_mr": f"{base}.strut_motion_ratio"},
                    axle,
                    list(ASSUMPTIONS),
                    f"{key} a lo largo del puntal",
                )
            )
        for src, new in CLONES:
            rules.append(clone(src.format(a=axle), new.format(a=axle), axle))
    for src, new in FRONT_CLONES:
        rules.append(clone(src, new, "front"))
    return rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key, "→", r.target)
        print(len(rules), "reglas")
        return 0
    for r in rules:
        print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
