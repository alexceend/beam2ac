"""Reglas de masa no suspendida y TOTALMASS del exportador 200409 (G2.14, 2026-11-03).

    python tools/catalog/add_suspension_mass_200409_rules.py [--dry-run]

Encargada por el usuario en G2.14. Solo añade; nunca sobrescribe (reglas inmutables). No
amplía ninguna regla anterior: ``car.totalmass@2`` sigue limitada a ``seat_ibiza_acfr`` y
los exportadores 38022/40340 no reciben nada nuevo. Todas llevan
``ir.formats.exporter_version in ["200409"]`` (lista cerrada).

Reglas (``provisional``):

* ``susp.hub_mass_front_200409@1`` / ``susp.hub_mass_rear_200409@1`` (``measured``,
  identidad en kg): ``[FRONT]/[REAR] HUB_MASS`` =
  ``ir.suspension.axles.<eje>.unsprung_mass_per_wheel``.
  - Origen (medido, ``src/extract/unsprung.py``): en BeamNG la masa está en los nodos y
    las vigas no tienen masa; el cuerpo rígido del buje (cierre rígido de vigas NORMAL)
    + la masa generada de la rueda es exactamente lo que se mueve con la rueda.
  - El hecho solo existe en ejes INDEPENDIENTES (en un eje acoplado está ausente con
    motivo): la regla no puede aplicarse a un eje rígido/torsional.
  - Destino (AC, ``docs/research/ac_suspension_coordinates.md`` §2.8): ``HUB_MASS``
    es la masa del cuerpo rígido del buje de DWB/STRUT (≤ 0 → 20 kg), con los brazos sin
    masa, como en BeamNG; la masa física = ``TOTALMASS`` + Σ``HUB_MASS``.
  - Contraste: el mismo método da 49,15 kg en el eje delantero del Ibiza (= ``HUB_MASS``
    de la base física aprobada, probada en AC) y 48,68 / 57,16 kg en el Porsche (fase C5).
* ``car.totalmass_200409@1`` (``computed``, ``linear_sum``): misma fórmula que
  ``car.totalmass@2`` (masa BeamNG + piloto − 2·HUB_MASS_del − 2·HUB_MASS_tras). Solo se
  resuelve si los dos ``HUB_MASS`` se resuelven, es decir, con los dos ejes
  independientes (en AC cada esquina DWB/STRUT aporta su ``HUB_MASS``; un ``AXLE`` aporta
  UNA vez el ``HUB_MASS`` del eje entero, ``getMass`` × 0,5 por lado: la fórmula −2·HUB no
  le valdría, y por eso un eje acoplado lo deja sin resolver).

Supuestos (por eso ``provisional`` y nunca ``known``): los nodos de mecanismo unidos al
buje (los extremos de la cremallera) van con la carrocería, como en el Ibiza y el
Porsche; la inercia del buje de AC es una caja fija (no configurable); ningún 200409 se
ha probado en AC.

NO se añaden reglas para ``CG_LOCATION`` ni ``BASEY``: dependen de la posición del piloto
(aproximación antropométrica) y del ``HUB_MASS`` de los ejes acoplados.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-03"
AUTHOR = "claude (G2.14: suspensión y parámetros derivados del 200409, encargada por el usuario)"
DOC = "docs/research/g214_suspension_2026-11-03.md"
EVID = "output/g214_suspension_2026-11-03"
EXPORTERS = ["200409"]
COND = [S.Condition(fact="ir.formats.exporter_version", op="in", value=EXPORTERS)]
HUB_ASSUMPTIONS = [
    "el cuerpo rígido del buje de BeamNG (cierre rígido de vigas NORMAL, src/extract/unsprung.py) "
    "equivale al cuerpo del buje de AC (DWB/STRUT); en ambos los brazos no tienen masa",
    "los nodos de mecanismo unidos al buje (extremos de la cremallera, unidos al chasis por hydros) "
    "van con la carrocería, como en el Ibiza (HUB_MASS 49,15 aprobado) y el Porsche (fase C5)",
    "la inercia del buje de AC es una caja fija (0,2; 0,6; 0,6) que no se puede igualar",
]
HUB_ASSUMPTIONS += [
    "con TYPE=STRUT AC reparte HUB_MASS entre el buje (0,8) y el tubo del puntal (0,2); la masa "
    "física por esquina sigue siendo HUB_MASS (docs/research/ibiza_front_suspension_research.md, "
    "[Confirmed])",
    "ningún vehículo 200409 se ha probado en AC",
]


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


SOURCE_DOC = [
    ev("code", "src/extract/unsprung.py", "partition (cierre rígido de nodos)"),
    ev("code", "src/importers/beamng.py", "_import_unsprung"),
    ev(
        "report",
        "output/g213_chassis_geometry_2026-11-03/banco/resultados.md",
        "masa física del juego = Σ nodeWeight (+ cámaras): las vigas no tienen masa",
    ),
]
TARGET_DOC = [
    ev(
        "doc",
        "docs/research/ac_suspension_coordinates.md",
        "§2.8 reparto de masas: cuerpo, bujes; getMass de DWB/STRUT y de AXLE (× 0,5)",
    ),
    ev("doc", "docs/research/ensamblado.md", "§9.13: masa en AC = TOTALMASS + bujes + combustible"),
]
EVIDENCE = [
    ev("report", DOC),
    ev("report", f"{EVID}/masas.md", "partición por cierre rígido en 10 vehículos"),
    ev(
        "file",
        "catalog/decisions/seat_ibiza_acfr.jsonl",
        "ac:suspensions.ini/FRONT/HUB_MASS = 49.15 (base aprobada; mismo método)",
    ),
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


def hub_rule(axle: str) -> S.Rule:
    sec = axle.upper()
    return S.Rule(
        id=f"susp.hub_mass_{axle}_200409",
        version=1,
        target=f"ac:suspensions.ini/{sec}/HUB_MASS",
        kind="measured",
        transform=S.Transform(
            id="identity", params={}, input_units={"x": "kg"}, output_unit="kg"
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs={"x": f"ir.suspension.axles.{axle}.unsprung_mass_per_wheel"},
        conditions=list(COND),
        assumptions=list(HUB_ASSUMPTIONS),
        open_issues=[],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=list(EVIDENCE),
        validations=[],
        history=history(
            "G2.14: masa no suspendida medida por cierre rígido de nodos; solo ejes "
            "independientes del 200409"
        ),
        notes=f"G2.14: solo exportador 200409 y eje {axle} independiente (el hecho no existe "
        "en un eje acoplado)",
    )


def totalmass_rule() -> S.Rule:
    return S.Rule(
        id="car.totalmass_200409",
        version=1,
        target="ac:car.ini/BASIC/TOTALMASS",
        kind="computed",
        transform=S.Transform(
            id="linear_sum",
            params={
                "coefficients": {
                    "total": 1,
                    "driver": 1,
                    "hub_front": -2,
                    "hub_rear": -2,
                }
            },
            input_units={
                "total": "kg",
                "driver": "kg",
                "hub_front": "kg",
                "hub_rear": "kg",
            },
            output_unit="kg",
            formula="TOTALMASS = masa BeamNG + piloto − 2·HUB_MASS_del − 2·HUB_MASS_tras",
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs={
            "total": "ir.mass.total",
            "driver": "ir.mass.driver",
            "hub_front": "ac:suspensions.ini/FRONT/HUB_MASS",
            "hub_rear": "ac:suspensions.ini/REAR/HUB_MASS",
        },
        conditions=list(COND),
        assumptions=[
            "masa total de BeamNG = carrocería + bujes (suma de nodos; sin los 2 nodos de "
            "cámara de camerasInternal, 0,48 kg: decisión técnica de G2.14)",
            "cada eje aporta 2·HUB_MASS (DWB/STRUT por esquina); solo se resuelve con los dos "
            "ejes independientes, porque HUB_MASS solo se resuelve en ellos",
            "piloto según la decisión 2 del proyecto (ir.mass.driver)",
            "ningún vehículo 200409 se ha probado en AC",
        ],
        open_issues=[],
        source_doc=[
            ev("code", "src/extract/mass_geometry.py", "compute_mass_geometry"),
            ev("doc", "CLAUDE.md", "decisión 2"),
            ev("file", "catalog/rules/car.totalmass@2.json", "misma fórmula (Ibiza, validated)"),
        ],
        target_doc=list(TARGET_DOC),
        evidence=list(EVIDENCE),
        validations=[],
        history=history(
            "G2.14: TOTALMASS del 200409 con la fórmula de car.totalmass@2 sobre los "
            "HUB_MASS medidos"
        ),
        notes="G2.14: solo exportador 200409; la masa física de AC (TOTALMASS + ΣHUB_MASS) "
        "es masa BeamNG + piloto sea cual sea el reparto",
    )


def build() -> list[S.Rule]:
    return [hub_rule("front"), hub_rule("rear"), totalmass_rule()]


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
