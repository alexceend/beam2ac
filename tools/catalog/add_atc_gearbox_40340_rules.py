"""Reglas de marchas y FINAL para la automática con convertidor (``automaticGearbox`` +
``torqueConverter``) del exportador 40340 con ``camsoEngine`` (2026-11-21; muestra
``thershow_turbot_aster``: RWD, 3 marchas).

    python tools/catalog/add_atc_gearbox_40340_rules.py [--dry-run]

Mismo método que ``add_atc_gearbox_200409_rules`` (D-G211-TC opción b: relación rígida
``FINAL·marcha``, sin convertidor) con condiciones propias del 40340. Solo añade reglas
(inmutables; no volver a ejecutar).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import add_atc_gearbox_200409_rules as A  # noqa: E402
import add_dct_gearbox_rules as D  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

D.DATE = "2026-11-21"
D.AUTHOR = "claude (automática 40340 con turbot_aster, encargado por el usuario)"
D.BASE_CONDITIONS = [
    D.cond("ir.drivetrain.gearbox.kind", "eq", "automaticGearbox"),
    D.cond("ir.formats.exporter_version", "in", ["40340"]),
    D.cond("ir.engine.device_type", "eq", "camsoEngine"),
    D.cond("ir.drivetrain.torque_converter.stall_torque_ratio", "exists", None),
]
D.DOC = "docs/research/turbot_aster_2026-11-21.md"


def build() -> list[S.Rule]:
    rules = []
    for r in D.build():
        r.id = r.id.replace("_dct", "_atc_40340")
        r.notes = "40340 + camsoEngine (turbot_aster); no amplía dt.*_atc_200409 ni dt.*_dct"
        rules.append(r)
    return rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))
        if r.key in cat.rules:
            print("ya existe:", r.key)
            return 1
    for r in rules:
        print("ok" if args.dry_run else "escrita:", r.key if args.dry_run else write_rule(REPO_CATALOG, r))
    if not args.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
