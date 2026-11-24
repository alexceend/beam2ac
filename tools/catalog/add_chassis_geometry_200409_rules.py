"""Reglas de geometría del chasis para el exportador 200409 (G2.13, 2026-11-03).

    python tools/catalog/add_chassis_geometry_200409_rules.py [--dry-run]

Encargada por el usuario en G2.13. Solo añade; nunca sobrescribe (reglas inmutables). No
amplía ninguna regla anterior: ``susp.wheelbase@2``, ``susp.track_front@2`` y
``susp.track_rear@2`` siguen limitadas al vehículo ``seat_ibiza_acfr``, y los exportadores
38022/40340 siguen recibiendo solo propuestas. Todas las reglas nuevas llevan
``ir.formats.exporter_version in ["200409"]`` (lista cerrada; nunca rangos).

Reglas (``provisional``, tipo ``measured``, transformación identidad en metros):

* ``susp.wheelbase_200409@1``: ``[BASIC] WHEELBASE`` = ``ir.dimensions.wheelbase``;
* ``susp.track_front_200409@1`` / ``susp.track_rear_200409@1``: ``[FRONT]``/``[REAR] TRACK``
  = ``ir.dimensions.track_front`` / ``track_rear``.

Origen (medido): centro de rueda = media de ``node1``/``node2`` de cada ``pressureWheels``
con las posiciones finales de los nodos (``nodeOffset`` con ``sign(0) = 0`` e ids repetidos
en una pieza = última fila, reglas del juego). Contrastado con lo que BeamNG 0.38.6
instancia en los 7 vehículos 200409 (``output/g213_chassis_geometry_2026-11-03/banco``):
Δ centro de rueda 0 m, batalla y vías idénticas.

Destino: en AC la base de cada rueda es (±TRACK/2, BASEY, reparto de WHEELBASE por
CG_LOCATION) (``docs/research/ac_suspension_coordinates.md`` §2): TRACK es la distancia
lateral entre las posiciones de diseño de los bujes y WHEELBASE la longitudinal entre ejes.

Supuesto (por eso ``inferred``/E2, nunca ``known``): la pose de definición del JBeam (sin
carga) equivale a la base de diseño de AC; probado en AC solo con el Ibiza (38022), no en
ningún 200409.

NO se añaden reglas para ``TOTALMASS``, ``CG_LOCATION`` ni ``BASEY``: dependen de
``HUB_MASS`` y de la suspensión (fuera del alcance de G2.13).
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
AUTHOR = "claude (G2.13: geometría del chasis del exportador 200409, encargada por el usuario)"
DOC = "docs/research/g213_chassis_geometry_2026-11-03.md"
EVID = "output/g213_chassis_geometry_2026-11-03"
LUA = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/common"
EXPORTERS = ["200409"]
ASSUMPTION = (
    "la pose de definición del JBeam (nodos sin carga, antes de asentarse) equivale a la base "
    "de diseño de las ruedas en AC; comprobado en AC solo con el Ibiza (exportador 38022, "
    "susp.wheelbase@2/track_*@2), sin prueba en AC de ningún 200409"
)


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


SOURCE_DOC = [
    ev(
        "code",
        "src/extract/mass_geometry.py",
        "_group_axles (centros de rueda por eje)",
    ),
    ev(
        "code",
        "src/extract/wheels.py",
        "collect_wheels (centro = media de node1/node2)",
    ),
    ev("code", "src/extract/nodes.py", "apply_node_offset, collect_nodes"),
    ev(
        "code",
        f"{LUA}/jbeam/utils.lua",
        "updateNodeTransformCache: cachePosX + sign(posX)·op.x",
        "93d597ba093cbf631afd1a15274039ef86a6f2244ebadac03bd801d877c8f213",
    ),
    ev(
        "code",
        f"{LUA}/mathlib.lua",
        "l. 1134 sign(x): −1, 0, 1",
        "5c13f764807c8d38afb5b55d6e54ff3c916e1750e1ed7761ef2e6a5c2f976a43",
    ),
    ev(
        "code",
        f"{LUA}/jbeam/tableSchema.lua",
        "processJbeamTableRow: ctx.newList[newID] = newRow (id repetido: vale la última fila)",
        "e3ebee180750afa7d83e278f663942d3784d8f2d3a32f4a7577dd7961fea67f3",
    ),
]
TARGET_DOC = [
    ev(
        "doc",
        "docs/research/ac_suspension_coordinates.md",
        "§2 bases de rueda (TRACK × 0,5, WHEELBASE)",
    ),
    ev("doc", "sample_assetto/data/suspensions.ini"),
]
EVIDENCE = [
    ev("report", DOC),
    ev(
        "report",
        f"{EVID}/banco/resultados.md",
        "posiciones instanciadas por BeamNG 0.38.6 frente al conversor",
    ),
    ev(
        "code",
        f"{EVID}/banco/lua_src/lua/vehicle/extensions/beam2acGeo.lua",
        "volcado de v.data.nodes",
    ),
]


def rule(rid: str, target: str, fact: str, note: str) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind="measured",
        transform=S.Transform(
            id="identity", params={}, input_units={"x": "m"}, output_unit="m"
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs={"x": fact},
        conditions=[
            S.Condition(fact="ir.formats.exporter_version", op="in", value=EXPORTERS)
        ],
        assumptions=[ASSUMPTION],
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
                reason="G2.13: geometría medida en los nodos, idéntica a la que instancia BeamNG en los "
                "7 vehículos 200409; misma semántica de destino que la regla del Ibiza",
                status_from="",
                status_to="provisional",
            )
        ],
        notes=note,
    )


def build() -> list[S.Rule]:
    return [
        rule(
            "susp.wheelbase_200409",
            "ac:suspensions.ini/BASIC/WHEELBASE",
            "ir.dimensions.wheelbase",
            "G2.13: solo exportador 200409; distancia longitudinal entre los centros de rueda medios "
            "de los dos ejes (solo con 2 ejes)",
        ),
        rule(
            "susp.track_front_200409",
            "ac:suspensions.ini/FRONT/TRACK",
            "ir.dimensions.track_front",
            "G2.13: solo exportador 200409; distancia lateral entre los centros de rueda del eje "
            "delantero",
        ),
        rule(
            "susp.track_rear_200409",
            "ac:suspensions.ini/REAR/TRACK",
            "ir.dimensions.track_rear",
            "G2.13: solo exportador 200409; distancia lateral entre los centros de rueda del eje trasero",
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
