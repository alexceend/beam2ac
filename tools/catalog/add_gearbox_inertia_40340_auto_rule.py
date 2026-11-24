"""Regla ``dt.gearbox_inertia_40340_auto@1``: ``[GEARBOX] INERTIA`` = 0,01 para las cajas automática y CVT
del exportador 40340 (2026-11-21). Clon de ``dt.gearbox_inertia_40340@1`` (manual/secuencial) y de
``dt.gearbox_inertia_200409@1`` (que ya cubre ``automaticGearbox``, probada en AC). Una sola ejecución.

    python tools/catalog/add_gearbox_inertia_40340_auto_rule.py [--dry-run]
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


def build(cat: Catalog) -> S.Rule:
    base = cat.rules["dt.gearbox_inertia_40340@1"]
    r = copy.deepcopy(base)
    r.id = "dt.gearbox_inertia_40340_auto"
    r.version = 1
    r.supersedes = ""
    for c in r.conditions:
        if c.fact == "ir.drivetrain.gearbox.kind":
            c.value = ["automaticGearbox", "cvtGearbox"]
    r.notes = (
        "40340: automaticGearbox y cvtGearbox (turbot_aster, agus_1_nrt_1); no amplía dt.gearbox_inertia_40340@1 "
        "(manual/secuencial)"
    )
    r.history = [
        S.HistoryEntry(
            date="2026-11-21",
            author="claude (automática/CVT 40340, encargado por el usuario)",
            action="create",
            reason="sin esta clave AC lee la inercia de la caja como ausente (1,0 kg·m² según dt.gearbox_inertia_40340); "
            "el 200409 automático ya lleva 0,01",
            status_from="",
            status_to=r.status,
        )
    ]
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    r = build(cat)
    S.from_dict(S.to_dict(r))
    if r.key in cat.rules:
        print("ya existe:", r.key)
        return 1
    print("ok" if args.dry_run else "escrita:", r.key if args.dry_run else write_rule(REPO_CATALOG, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
