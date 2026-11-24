"""Reglas de marchas y FINAL para la caja de doble embrague (``dctGearbox``) del exportador
40340 con ``camsoEngine`` (2026-11-22; muestra ``vendeta_romaigna``: AWD, 7 marchas).

    python tools/catalog/add_dct_gearbox_40340_rules.py [--dry-run]

Mismo método que ``add_dct_gearbox_rules`` (200409) con condiciones propias del 40340.
``dctGearbox.lua`` es del juego, no del exportador: su huella en la instalación local
coincide con la fijada en ``GAME_LUA`` (``d07d3ba3…``), así que la semántica de régimen
estable (relación rígida ``FINAL·marcha``) es la misma que en 200409. Los parámetros
(``gearRatios``, ``friction``, ``dctClutchTime``, ``transmissionShiftDelay``) tienen el
mismo formato; ``friction`` ≠ 0 también en las DCT 200409 y no se traslada.
Solo añade reglas (inmutables; no volver a ejecutar).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import add_dct_gearbox_rules as D  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

D.DATE = "2026-11-22"
D.AUTHOR = "claude (DCT 40340 con vendeta_romaigna, encargado por el usuario)"
D.BASE_CONDITIONS = [
    D.cond("ir.drivetrain.gearbox.kind", "eq", "dctGearbox"),
    D.cond("ir.formats.exporter_version", "in", ["40340"]),
    D.cond("ir.engine.device_type", "eq", "camsoEngine"),
]


def build() -> list[S.Rule]:
    rules = []
    for r in D.build():
        r.id = r.id.replace("_dct", "_dct_40340")
        r.evidence = [
            D.ev("report", D.DOC, "§3"),
            D.ev(
                "file",
                "vendeta_romaigna.zip",
                "camso_transmission_059f0.jbeam: dctGearbox, 7 marchas",
            ),
        ]
        r.notes = "40340 + camsoEngine (vendeta_romaigna); no amplía dt.*_dct (200409) ni dt.*_atc_40340"
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
        print(
            "ok" if args.dry_run else "escrita:",
            r.key if args.dry_run else write_rule(REPO_CATALOG, r),
        )
    if not args.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
