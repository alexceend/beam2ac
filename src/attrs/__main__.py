"""Línea de órdenes del modelo de atributos.

    python -m src.attrs check [--catalog catalog]
    python -m src.attrs resolve (--vehicle-dir DIR --pc ARCHIVO.pc | --vehicle-zip ZIP
    [--common-zip ZIP]
                                 [--pc ARCHIVO.pc]) --out output/<carpeta nueva>
                                [--catalog catalog] [--project DIR] [--driver-mass 70]

``resolve`` no escribe física: genera ``report.json`` (determinista) y ``summary.md`` en
una carpeta NUEVA. ``--project`` añade las decisiones de un proyecto encima del
catálogo. Código de salida 2 si la vista previa del conversor falla (el informe se
escribe igualmente y lo indica con ``preview_ok: false``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    preview_physics,
)

from .report import build_report, resolve_vehicle, write_report
from .store import REPO_CATALOG, Catalog


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m src.attrs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="valida el catálogo")
    c.add_argument("--catalog", type=Path, default=REPO_CATALOG)
    r = sub.add_parser("resolve", help="resuelve los atributos de un vehículo")
    r.add_argument("--catalog", type=Path, default=REPO_CATALOG)
    r.add_argument("--project", type=Path)
    r.add_argument("--vehicle-dir", type=Path)
    r.add_argument("--vehicle-zip", type=Path)
    r.add_argument("--common-zip", type=Path)
    r.add_argument("--pc")
    r.add_argument("--driver-mass", type=float, default=70.0)
    r.add_argument("--targets", default="ac_physics")
    r.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    extra = [args.project] if getattr(args, "project", None) else []
    catalog = Catalog.load(args.catalog, *extra)
    if args.cmd == "check":
        print(
            f"catálogo válido: {len(catalog.attributes)} atributos, "
            f"{len(catalog.rules)} reglas, {len(catalog.decisions)} decisiones, "
            f"{len(catalog.validations)} validaciones, "
            f"{len(catalog.families)} familias"
        )
        return 0
    if bool(args.vehicle_dir) == bool(args.vehicle_zip):
        ap.error("indica --vehicle-dir o --vehicle-zip")
    if args.vehicle_dir:
        if not args.pc:
            ap.error("--vehicle-dir necesita --pc")
        source = load_vehicle_dir(args.vehicle_dir, args.pc)
    else:
        source = load_vehicle_zip(args.vehicle_zip, args.common_zip, args.pc)
    pv = preview_physics(source)
    ctx, results = resolve_vehicle(pv, catalog, args.targets, args.driver_mass)
    report = build_report(pv, ctx, results, catalog)
    paths = write_report(args.out, report)
    print(f"{report['vehicle']}/{report['variant']}: {report['summary']['by_status']}")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0 if pv.ok else 2


if __name__ == "__main__":
    sys.exit(main())
