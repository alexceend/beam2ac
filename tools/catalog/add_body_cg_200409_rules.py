"""Reglas ``CG_LOCATION`` y ``BASEY`` del 200409 sin piloto (G2.18, 2026-11-05).

    python tools/catalog/add_body_cg_200409_rules.py [--dry-run]

Encargada por el usuario en G2.18, que tomó la decisión D-G217-CG = opción A: «mantener
el cálculo correspondiente a 0 kg de piloto; es la única referencia que reproduce
exactamente BeamNG sin introducir una masa heurística». Solo añade; nunca sobrescribe.
Los exportadores 38022/40340 no reciben nada (lista cerrada ``["200409"]``).

Reglas (``provisional``, transformación ``ac_body_cg``):

* ``susp.cg_location_200409_nodriver@1`` → ``[BASIC] CG_LOCATION``;
* ``susp.basey_front_200409_nodriver@1`` / ``susp.basey_rear_200409_nodriver@1`` →
  ``[FRONT]/[REAR] BASEY``.

Condiciones: exportador 200409 **y** ``ir.mass.driver == 0``. Con otra masa de piloto
la regla no se aplica (queda «decisión necesaria» con el motivo): el CdG sin piloto solo
es coherente con un ``TOTALMASS`` sin piloto (``car.totalmass_200409@1`` suma
``ir.mass.driver``). Entradas: masa total y fracción delantera medidas, ``HUB_MASS``
resueltos (solo ejes independientes: un eje acoplado lo deja sin resolver) y, para
``BASEY``, la altura de los centros de rueda de cada eje respecto al CdG
(``ir.dimensions.wheel_center_height_rel_cog_*``, G2.18). Reproduce los valores «sin
piloto» de ``output/g214_suspension_2026-11-03/cdg.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-05"
AUTHOR = "claude (G2.18: preparación de la validación en AC, encargada por el usuario)"
DOC = "docs/research/g218_preparacion_validacion_ac_2026-11-05.md"
COND = [
    S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"]),
    S.Condition(fact="ir.mass.driver", op="eq", value=0),
]


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


SOURCE_DOC = [
    ev("code", "src/extract/mass_geometry.py", "compute_mass_geometry (CdG, ejes)"),
    ev("code", "src/importers/beamng.py", "dimensions.wheel_center_height_rel_cog_*"),
    ev("code", "src/attrs/transforms.py", "t_ac_body_cg"),
]
TARGET_DOC = [
    ev(
        "doc",
        "docs/research/ac_suspension_coordinates.md",
        "§2.7 base de rueda (±TRACK/2, BASEY, (1 − CG_LOCATION)·WB | −CG_LOCATION·WB); "
        "§2.8 reparto de masas cuerpo/bujes",
    ),
    ev(
        "doc",
        "docs/research/g217_suspension_steering_2026-11-04.md",
        "§7 suspensión de AC [C]; decisión D-G217-CG",
    ),
]
EVIDENCE = [
    ev("report", DOC, "§3 decisión D-G217-CG = A"),
    ev(
        "report",
        "output/g214_suspension_2026-11-03/cdg.md",
        "valores «sin piloto» de los 6 vehículos con ejes independientes",
    ),
    ev(
        "report",
        "output/g213_chassis_geometry_2026-11-03/banco/resultados.md",
        "nodos y masa del conversor = juego (≤ 2,7·10⁻¹⁶ m)",
    ),
]
ASSUMPTIONS = [
    "piloto 0 kg (decisión D-G217-CG = A del usuario en G2.18): BeamNG no tiene piloto; "
    "solo coherente con TOTALMASS sin piloto, por eso la regla exige ir.mass.driver = 0",
    "el cuerpo de AC = vehículo entero de BeamNG menos 2·HUB_MASS por eje en los centros "
    "de rueda (mismo reparto que car.totalmass_200409@1)",
    "sin los 2 nodos de cámara (0,48 kg) de camerasInternal (decisión técnica de G2.14)",
    "ningún vehículo 200409 se ha probado en AC",
]


def rule(rid: str, target: str, output: str, unit: str, base: bool) -> S.Rule:
    inputs = {
        "total": "ir.mass.total",
        "front_fraction": "ir.dimensions.front_axle_fraction",
        "hub_front": "ac:suspensions.ini/FRONT/HUB_MASS",
        "hub_rear": "ac:suspensions.ini/REAR/HUB_MASS",
    }
    units_in = {
        "total": "kg",
        "front_fraction": "1",
        "hub_front": "kg",
        "hub_rear": "kg",
    }
    if base:
        inputs |= {
            "height_front": "ir.dimensions.wheel_center_height_rel_cog_front",
            "height_rear": "ir.dimensions.wheel_center_height_rel_cog_rear",
        }
        units_in |= {"height_front": "m", "height_rear": "m"}
    formula = (
        "CG_LOCATION = (M·f − 2·h_d) / (M − 2·h_d − 2·h_t)"
        if not base
        else "BASEY = d_eje + (2·h_d·d_d + 2·h_t·d_t) / (M − 2·h_d − 2·h_t)"
    )
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind="computed",
        transform=S.Transform(
            id="ac_body_cg",
            params={"output": output},
            input_units=units_in,
            output_unit=unit,
            formula=formula,
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=list(COND),
        assumptions=list(ASSUMPTIONS),
        open_issues=[],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=list(EVIDENCE),
        validations=[],
        history=[
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                reason="G2.18: decisión D-G217-CG = A (CdG del cuerpo sin piloto)",
                status_from="",
                status_to="provisional",
            )
        ],
        notes="G2.18: solo exportador 200409, ejes independientes y piloto 0 kg",
    )


def build() -> list[S.Rule]:
    return [
        rule(
            "susp.cg_location_200409_nodriver",
            "ac:suspensions.ini/BASIC/CG_LOCATION",
            "cg_location",
            "1",
            False,
        ),
        rule(
            "susp.basey_front_200409_nodriver",
            "ac:suspensions.ini/FRONT/BASEY",
            "basey_front",
            "m",
            True,
        ),
        rule(
            "susp.basey_rear_200409_nodriver",
            "ac:suspensions.ini/REAR/BASEY",
            "basey_rear",
            "m",
            True,
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        return 0
    for r in rules:
        print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
