"""Reglas de aplicabilidad según el tipo de suspensión de AC (tarea B4, 2026-10-26).

    python tools/catalog/add_suspension_applicability_rules.py

Evidencia (análisis de AC, ya documentado):
* suspensión de puntal de AC lee STRUT_CAR, STRUT_TYRE,
  WBCAR_BOTTOM_FRONT/REAR, WBTYRE_BOTTOM, WBCAR_STEER y WBTYRE_STEER, y NO lee
  WBCAR_TOP_FRONT, WBCAR_TOP_REAR ni WBTYRE_TOP
  (docs/research/ibiza_front_suspension_research.md §2.1).
* suspensión de AC (DWB) lee los 8 puntos WB* y no STRUT_*
  (docs/research/ac_suspension_coordinates.md §2.2).

Así que: con TYPE=STRUT las tres claves «TOP» no aplican; con TYPE=DWB, STRUT_CAR y
STRUT_TYRE no aplican. Para AXLE y ML no se afirma nada (no hay evidencia de qué leen):
la regla solo dice «no aplica» en el caso demostrado. Ámbito genérico: describe AC, no
un vehículo. Se niega a sobrescribir.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-10-26"
STRUT_DOC = S.EvidenceRef(
    type="doc",
    path="docs/research/ibiza_front_suspension_research.md",
    locator="§2.1 suspensión de puntal de AC",
)
DWB_DOC = S.EvidenceRef(
    type="doc",
    path="docs/research/ac_suspension_coordinates.md",
    locator="§1 y §2.2 suspensión de AC",
)
# clave -> (tipo con el que NO aplica, evidencia)
NOT_READ_BY = {
    "WBCAR_TOP_FRONT": ("STRUT", STRUT_DOC),
    "WBCAR_TOP_REAR": ("STRUT", STRUT_DOC),
    "WBTYRE_TOP": ("STRUT", STRUT_DOC),
    "STRUT_CAR": ("DWB", DWB_DOC),
    "STRUT_TYRE": ("DWB", DWB_DOC),
}


def rules() -> list[S.Rule]:
    out = []
    for axle in ("FRONT", "REAR"):
        for key, (type_, doc) in NOT_READ_BY.items():
            type_attr = f"ac:suspensions.ini/{axle}/TYPE"
            out.append(
                S.Rule(
                    id=f"app.{axle.lower()}.{key.lower()}",
                    version=1,
                    target=f"ac:suspensions.ini/{axle}/{key}",
                    kind="applicability",
                    transform=S.Transform(
                        id="constant", params={"value": None}, output_unit="m"
                    ),
                    scope=S.Scope(level="generic"),
                    status="validated",
                    applies_when=[S.Condition(fact=type_attr, op="ne", value=type_)],
                    source_doc=[doc],
                    target_doc=[doc],
                    evidence=[doc],
                    notes=f"con [{axle}] TYPE={type_} AC no lee {key}",
                    history=[
                        S.HistoryEntry(
                            date=DATE,
                            author="claude (tarea B4)",
                            action="create",
                            status_to="validated",
                            reason="claves leídas por cada clase de "
                            "suspensión (análisis)",
                        )
                    ],
                )
            )
    return out


def main() -> int:
    for r in rules():
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
