"""``aero.ini`` del 200409 desde la aerodinámica MEDIDA en el juego (G2.19, D-G215-AERO).

    python tools/catalog/add_aero_bench_200409_rules.py [--dry-run]

Encargada por el usuario en G2.19. Solo añade; nunca sobrescribe. Solo exportador 200409
con aerodinámica por triángulos (``ir.aero.model == triangles``) y medida del banco
asociada al origen (``ir.aero.cda_vs_aoa`` presente; ``src/importers/measurements.py``).

Representación (informe G2.19 §4), tres alas de AC de área CHORD·SPAN = 1 m²:

* ``WING_0`` ``BODY``: arrastre medido CdA(aoa) en el CENTRO DEL EJE TRASERO; sin
  sustentación (la carga la llevan las otras dos alas);
* ``WING_1`` ``FRONT``: carga medida en el centro del eje delantero ClA_d(aoa), sin arrastre;
* ``WING_2`` ``REAR``: carga medida en el centro del eje trasero ClA_t(aoa), sin arrastre.

Ese sistema de fuerzas tiene, en cada caso medido, la misma resultante y el mismo momento de
cabeceo que el medido en BeamNG (``src/extract/aero_bench.py``). AC [C]
(ala de AC/la resistencia/la sustentación, ``output/g215_…/acs_dir_frenos_aero/c``):
aoa = atan(v_arriba/v_adelante) del ala en el marco del coche (= ángulo del banco); fuerza
= ½·ρ·v²·LUT(ANGLE + aoa)·GAIN·CHORD·SPAN, arrastre contra la velocidad, carga
perpendicular; ``getFloat`` ⇒ ``CD_GAIN``/``CL_GAIN`` ausentes = 0 (se escriben a 1);
curva de AC: interpolación lineal y valores extremos fuera de la tabla.
``[HEADER] VERSION`` = 1: con VERSION ≥ 2 ala de AC lee zonas de daño
(``ZONE_*_CL/CD``) que el origen no tiene.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-06"
AUTHOR = "claude (G2.19: desbloqueo integral del 200409, encargada por el usuario)"
DOC = "docs/research/g219_desbloqueo_integral_200409.md"
ACS = "output/g215_direction_brakes_aero_2026-11-03/acs_dir_frenos_aero/c"
COND = [
    S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"]),
    S.Condition(fact="ir.aero.model", op="eq", value="triangles"),
    S.Condition(fact="ir.aero.cda_vs_aoa", op="exists", value=None),
]
WINGS = {
    0: ("BODY", "rear", "cda", "drag"),
    1: ("FRONT", "front", "cla_front", "lift"),
    2: ("REAR", "rear", "cla_rear", "lift"),
}
FACT = {
    "cda": "ir.aero.cda_vs_aoa",
    "cla_front": "ir.aero.cla_front_axle_vs_aoa",
    "cla_rear": "ir.aero.cla_rear_axle_vs_aoa",
}
ZERO = [[-90.0, 0.0], [90.0, 0.0]]


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


SOURCE_DOC = [
    ev("code", "src/extract/aero_bench.py", "reduce_case / aero_tables"),
    ev("code", "src/importers/measurements.py", "find (huella del origen)"),
    ev("code", "src/importers/beamng.py", "_import_aero_bench"),
]
TARGET_DOC = [
    ev("code", ACS, "ala de AC (CHORD, SPAN, POSITION, LUT, GAIN, ANGLE)"),
    ev("code", ACS, "ala de AC (aoa = atan(lv.y/lv.z))"),
    ev("code", ACS, "ala de AC"),
    ev("doc", "docs/research/g215_direction_brakes_aero_2026-11-03.md", "§5.4"),
]
EVIDENCE = [
    ev("report", DOC, "§4 aerodinámica"),
    ev(
        "report",
        "docs/research/g215_direction_brakes_aero_2026-11-03.md",
        "§5.2 banco (sesión 4)",
    ),
    ev(
        "report",
        "output/g219_2026-11-06/aero/validacion_aero.md",
        "emulación de las alas de AC frente a los 14 casos del banco",
    ),
]
BASE_ASSUMPTIONS = [
    "aerodinámica medida en BeamNG 0.38.6 con el coche parado (viento), ruedas sin "
    "girar, actitud de reposo; ángulos −4…+4° a 50 m/s, escala v² comprobada en "
    "20–70 m/s",
    "guiñada no representada (±10° cambia CdA ≤ 2,4 % y ClA ≤ 5,1 % en el banco); sin "
    "efecto suelo (no medido a otras alturas); sin daño aerodinámico",
    "ningún 200409 se ha probado en AC",
]
HISTORY = [
    S.HistoryEntry(
        date=DATE,
        author=AUTHOR,
        action="create",
        reason="G2.19 (D-G215-AERO): aerodinámica por triángulos medida en el juego, "
        "representada con tres alas de AC (arrastre + carga por eje)",
        status_from="",
        status_to="provisional",
    )
]


def mk(
    rid: str,
    target: str,
    kind: str,
    transform: S.Transform,
    inputs: dict[str, str],
    assumptions: list[str],
    notes: str,
) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind=kind,
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=list(COND),
        assumptions=assumptions,
        open_issues=[],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=list(EVIDENCE),
        validations=[],
        history=list(HISTORY),
        notes="G2.19: solo 200409 con aerodinámica por triángulos medida; " + notes,
    )


def const(value, unit: str, formula: str = "") -> S.Transform:
    return S.Transform(
        id="constant", params={"value": value}, output_unit=unit, formula=formula
    )


def build() -> list[S.Rule]:
    rules = [
        mk(
            "aero.header_version_200409",
            "ac:aero.ini/HEADER/VERSION",
            "format",
            const(1, "1"),
            {},
            [
                "VERSION 1: ala de AC solo lee ZONE_*_CL/CD con VERSION ≥ 2 [C]; el "
                "origen no tiene daño aerodinámico representable"
            ],
            "sin zonas de daño",
        )
    ]
    for n, (name, axle, field, role) in WINGS.items():
        w = f"ac:aero.ini/WING_{n}"
        rid = f"aero.wing{n}"
        rules.append(
            mk(
                f"{rid}_name_200409",
                f"{w}/NAME",
                "format",
                const(name, "text"),
                {},
                [],
                "nombre del ala (solo informativo en AC)",
            )
        )
        for key in ("CHORD", "SPAN"):
            rules.append(
                mk(
                    f"{rid}_{key.lower()}_200409",
                    f"{w}/{key}",
                    "computed",
                    const(1.0, "m", "área CHORD·SPAN = 1 m²: la LUT lleva C·A"),
                    {},
                    [
                        "ala de referencia de 1 m² (CHORD = SPAN = 1 m): solo fija la "
                        "escala de la LUT (C·A en m²); no es una superficie física"
                    ]
                    + BASE_ASSUMPTIONS,
                    f"{key} de normalización",
                )
            )
        rules.append(
            mk(
                f"{rid}_position_200409",
                f"{w}/POSITION",
                "computed",
                S.Transform(
                    id="ac_axle_point",
                    params={"axle": axle},
                    input_units={"wheelbase": "m", "cg_location": "1", "basey": "m"},
                    output_unit="m",
                    formula="(0, BASEY, (1 − CG)·WB)"
                    if axle == "front"
                    else "(0, BASEY, −CG·WB)",
                ),
                {
                    "wheelbase": "ac:suspensions.ini/BASIC/WHEELBASE",
                    "cg_location": "ac:suspensions.ini/BASIC/CG_LOCATION",
                    "basey": f"ac:suspensions.ini/{axle.upper()}/BASEY",
                },
                [
                    "la medida reparte la carga en los centros de eje (momento de "
                    "cabeceo medido respecto al centro del eje trasero): las fuerzas "
                    "van en los centros de eje de AC"
                ]
                + BASE_ASSUMPTIONS,
                f"centro del eje {'delantero' if axle == 'front' else 'trasero'}",
            )
        )
        measured = "CD" if role == "drag" else "CL"
        for coef in ("CL", "CD"):
            lut = f"beam2ac_aero_w{n}_{coef.lower()}.lut"
            rules.append(
                mk(
                    f"{rid}_lut_aoa_{coef.lower()}_file_200409",
                    f"{w}/LUT_AOA_{coef}",
                    "format",
                    const(lut, "text"),
                    {},
                    [],
                    "nombre del archivo de la tabla",
                )
            )
            if coef == measured:
                rules.append(
                    mk(
                        f"{rid}_lut_aoa_{coef.lower()}_200409",
                        f"{w}/LUT_AOA_{coef}/table",
                        "measured",
                        S.Transform(
                            id="ac_wing_table",
                            params={},
                            input_units={
                                "table": "table:deg->m²",
                                "chord": "m",
                                "span": "m",
                            },
                            output_unit="table:deg->1",
                            formula="LUT(aoa) = C·A(aoa) / (CHORD·SPAN)",
                        ),
                        {
                            "table": FACT[field],
                            "chord": f"{w}/CHORD",
                            "span": f"{w}/SPAN",
                        },
                        list(BASE_ASSUMPTIONS),
                        f"{'arrastre' if role == 'drag' else 'carga'} medido",
                    )
                )
            else:
                rules.append(
                    mk(
                        f"{rid}_lut_aoa_{coef.lower()}_200409",
                        f"{w}/LUT_AOA_{coef}/table",
                        "computed",
                        const(ZERO, "table:deg->1", "0 en todo el rango"),
                        {},
                        [
                            (
                                "sin sustentación en el ala de arrastre: toda la carga "
                                "medida va en WING_1/WING_2"
                            )
                            if coef == "CL"
                            else (
                                "sin arrastre en las alas de carga: todo el arrastre "
                                "medido va en WING_0"
                            )
                        ]
                        + BASE_ASSUMPTIONS,
                        "parte nula de la representación (no es un desconocido)",
                    )
                )
        for key in ("CL_GAIN", "CD_GAIN"):
            rules.append(
                mk(
                    f"{rid}_{key.lower()}_200409",
                    f"{w}/{key}",
                    "computed",
                    const(1.0, "1"),
                    {},
                    [
                        f"{key} = 1: la LUT ya es el coeficiente medido; getFloat "
                        "devolvería 0 si faltara (sin fuerza) [C]"
                    ]
                    + BASE_ASSUMPTIONS,
                    "ganancia unidad",
                )
            )
        rules.append(
            mk(
                f"{rid}_angle_200409",
                f"{w}/ANGLE",
                "computed",
                const(0.0, "deg"),
                {},
                [
                    "ANGLE = 0: la LUT está indexada directamente por el aoa del coche "
                    "(ángulo del banco = aoa de AC)"
                ]
                + BASE_ASSUMPTIONS,
                "sin ángulo propio",
            )
        )
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
    Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
