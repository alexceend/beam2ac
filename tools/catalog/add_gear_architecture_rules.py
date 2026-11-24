"""Relaciones de transmisión por arquitectura (2WD, caja manual o secuencial), 2026-10-30.

    python tools/catalog/add_gear_architecture_rules.py [--dry-run]

Reglas PARALELAS de ámbito ``architecture`` para ``[GEARS] COUNT``, ``GEAR_R``,
``GEAR_1..8`` y ``FINAL``, copiadas de las del Ibiza (ámbito vehículo, que no cambian:
el ámbito más específico gana). Arquitectura = tracción FWD/RWD y caja
``manualGearbox`` o ``sequentialGearbox``. Evidencia [C] en los dos lados:

* BeamNG: ambas cajas leen ``gearRatios`` igual (manualGearbox.lua l. 549-575 =
  sequentialGearbox.lua l. 359-385) y transmiten el par igual (l. 55-57); el grupo final
  es el ``gearRatio`` del diferencial del eje motriz (differential.lua l. 33, 249).
* AC: ``transmisión 2WD de AC`` usa marcha·FINAL sin otras relaciones
  (output/research_frenos_diferencial_transmision_2026-10-26/code_evidence.json).

Tracción total: fuera del ámbito (el cálculo 4WD de AC sin analizar) → quedan como propuesta.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-10-30"
AUTHOR = "claude (reglas reutilizables por arquitectura, objetivo 4)"
DOC = "docs/research/catalogo_motor_awd_vmax_2026-10-30.md"
CODE = "output/research_frenos_diferencial_transmision_2026-10-26/code_evidence.json"
LUA = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/powertrain/"
SOURCES = [
    "dt.gear_count@1",
    "dt.gear_r@1",
    *[f"dt.gear_{k}@1" for k in range(1, 9)],
    "dt.final@1",
]
ARCH = [
    S.Condition(fact="bng.drivetrain.traction", op="in", value=["FWD", "RWD"]),
    S.Condition(
        fact="bng.gearbox.type",
        op="in",
        value=["manualGearbox", "sequentialGearbox"],
    ),
]


def derive(old: S.Rule) -> S.Rule:
    new = copy.deepcopy(old)
    new.id = old.id + "_2wd"
    new.version, new.supersedes = 1, ""
    new.scope = S.Scope(level="architecture", conditions=list(ARCH))
    new.status = "provisional"
    new.validations = []
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code", path=LUA + "manualGearbox.lua", locator="l. 549-575"
        ),
        S.EvidenceRef(
            type="code", path=LUA + "sequentialGearbox.lua", locator="l. 359-385"
        ),
        S.EvidenceRef(type="code", path=LUA + "differential.lua", locator="l. 33, 249"),
    ]
    new.target_doc = list(old.target_doc) + [
        S.EvidenceRef(type="code", path=CODE, locator="transmisión 2WD de AC")
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(type="report", path=DOC, locator="§4")
    ]
    new.history = [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="create",
            status_to="provisional",
            reason=f"derivada de {old.key} (Ibiza, ámbito vehículo, sin cambios): "
            "relación directa con semántica confirmada en los dos lados para 2WD",
        )
    ]
    new.notes = f"derivada de {old.key}"
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = [derive(cat.rules[k]) for k in SOURCES]
    for r in rules:
        S.from_dict(S.to_dict(r))
        print(
            ("ok (sin escribir): " + r.key)
            if args.dry_run
            else write_rule(REPO_CATALOG, r)
        )
    if not args.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
