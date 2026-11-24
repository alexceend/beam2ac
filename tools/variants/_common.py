"""Argumentos comunes de los reconstructores de variantes (``tools/variants``).

Cada script adaptado lee sus paquetes base de ``--base-root`` (por defecto, los
paquetes aprobados de ``output/seat_ibiza_acfr/ingame_cycle``, solo lectura) y crea
su paquete nuevo en ``--out-root``. La carpeta del paquete se crea con
``src.safe_paths.new_output_dir``: si ya existe, el script se para sin tocar nada, y
los paquetes sellados con ``manifest.json`` nunca se sobrescriben.

``--repro`` marca una reconstrucción: el aviso «el id ya existe en la instalación de
AC» deja de detener el script (el paquete reconstruido no se instala; solo se compara).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.safe_paths import claim_output_dir, new_output_dir  # noqa: E402,F401

APPROVED_IC = ROOT / "output" / "seat_ibiza_acfr" / "ingame_cycle"


def parse(description: str | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(description or "").strip().splitlines()[0])
    ap.add_argument(
        "--base-root",
        default=str(APPROVED_IC),
        help="carpeta con los pkg_* de entrada (solo lectura; por defecto los aprobados)",
    )
    ap.add_argument(
        "--out-root",
        required=True,
        help="carpeta donde se crean los pkg_* nuevos (no puede estar sellada)",
    )
    ap.add_argument(
        "--repro",
        action="store_true",
        help="reconstrucción: un id ya instalado en AC no detiene el script",
    )
    args = ap.parse_args()
    args.base_root = Path(args.base_root).resolve()
    args.out_root = claim_output_dir(args.out_root)
    return args
