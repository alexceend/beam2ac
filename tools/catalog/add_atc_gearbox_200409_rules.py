"""Reglas de marchas y FINAL para la automática con convertidor (``automaticGearbox`` +
``torqueConverter``) del exportador 200409 (G2.43, 2026-11-14; D-G211-TC opción b).

    python tools/catalog/add_atc_gearbox_200409_rules.py [--dry-run]

Reutiliza la construcción de ``add_dct_gearbox_rules`` con condiciones propias
(``gearbox.kind == automaticGearbox``) y ``COUNT``/``GEAR_n`` hasta 9 (``GEAR_9`` ya existe
en el catálogo desde G2.19). Solo añade reglas (no volver a ejecutar).

Aproximación explícita: AC no tiene convertidor de par (G2.11): se traslada solo la relación
rígida ``FINAL·marcha`` (régimen estable con el bloqueo cerrado). Se pierde el calado
(~1650 rpm), la multiplicación (×1,8 parado) y el deslizamiento. Probado como decisión de
proyecto en AC (portland experimental, G2.27, 32/32), nunca como regla.
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
from src.extract.automatic_transmission import AUTO_LUA, GAME_LUA, TC_LUA  # noqa: E402

D.DATE = "2026-11-14"
D.AUTHOR = "claude (G2.43: desbloqueo de la automática con convertidor del 200409)"
D.BASE_CONDITIONS = [
    D.cond("ir.drivetrain.gearbox.kind", "eq", "automaticGearbox"),
    D.cond("ir.formats.exporter_version", "in", D.EXPORTERS),
    D.cond("ir.engine.device_type", "eq", "combustionEngine"),
    D.cond("ir.drivetrain.torque_converter.stall_torque_ratio", "exists", None),
]
D.SOURCE_DOC = [
    D.ev("code", "src/extract/drivetrain.py", "build_drivetrain, _split_gears"),
    D.ev("code", "src/extract/automatic_transmission.py", "read_transmission"),
    D.ev("code", D.GAME + AUTO_LUA, "gearRatios por signo", GAME_LUA[AUTO_LUA]),
    D.ev("code", D.GAME + TC_LUA, "bloqueo del convertidor", GAME_LUA[TC_LUA]),
]
D.ASSUMPTION = (
    "APROXIMACIÓN (D-G211-TC b): AC no tiene convertidor de par; se aplica la relación rígida "
    "FINAL·marcha (régimen estable con el bloqueo cerrado). No se reproducen el calado "
    "(~1650 rpm), la multiplicación de par (×1,8 parado) ni el deslizamiento en las marchas "
    "bajas; el cambio automático de AC es una ayuda del piloto"
)
D.DOC = "docs/research/g243_200409_bloqueados_2026-11-14.md"
D.MAX_GEARS_IN_CATALOG = 9


def build() -> list[S.Rule]:
    rules = []
    for r in D.build():
        r.id = r.id.replace("_dct", "_atc_200409")
        r.notes = "G2.43: automaticGearbox + torqueConverter del 200409; no amplía dt.*_dct"
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
