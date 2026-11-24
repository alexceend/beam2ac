"""Catálogo de referencias de AC.

    python -m src.catalog ac --out output/<carpeta nueva> [--cars
    G:/…/assettocorsa/content/cars]

Lee ``content/cars`` en solo lectura y escribe ``index.json`` (índice por clave con
independencia por contenido), ``cars/<id>.json`` (una referencia por coche) y
``summary.json`` en una carpeta NUEVA. Comprueba con sha256 que los archivos leídos no
cambian durante la ejecución.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.safe_paths import ensure_safe_output_path, new_output_dir

from .ac_refs import build_index, scan


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m src.catalog",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ac")
    a.add_argument("--cars", type=Path, default=None)
    a.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("pair", help="candidatos de emparejamiento BeamNG ↔ AC")
    p.add_argument("--bng-inventory", type=Path, required=True)
    p.add_argument("--ac-inventory", type=Path, required=True)
    p.add_argument("--mods-dir", type=Path, help="mods de BeamNG del usuario (zip)")
    p.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "pair":
        return _pair(args)

    t0 = time.perf_counter()
    refs = scan(args.cars)
    again = {r.car_id: (r.files, r.acd_sha256) for r in scan(args.cars)}
    unchanged = all(again[r.car_id] == (r.files, r.acd_sha256) for r in refs)
    index = build_index(refs)
    index["summary"]["sources_unchanged_during_scan"] = unchanged
    index["summary"]["scan_seconds"] = round(time.perf_counter() - t0, 1)
    out = new_output_dir(args.out)
    (out / "cars").mkdir()
    for r in refs:
        ensure_safe_output_path(out / "cars" / f"{r.car_id}.json").write_text(
            json.dumps(r.to_dict(), indent=1, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    ensure_safe_output_path(out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    ensure_safe_output_path(out / "summary.json").write_text(
        json.dumps(index["summary"], indent=1, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(index["summary"], indent=1, ensure_ascii=False))
    return 0 if unchanged else 2


def _pair(args: argparse.Namespace) -> int:
    from . import pairing

    bng = pairing.load_bng_inventory(args.bng_inventory)
    if args.mods_dir:
        bng += pairing.load_mod_zips(args.mods_dir)
    ac = pairing.load_ac_inventory(args.ac_inventory)
    cands = pairing.pair(bng, ac)
    summary = {
        "bng_vehicles": len(bng),
        "bng_automation": sum(b.automation for b in bng),
        "ac_cars": len(ac),
        "candidates": sum(c.classification == "candidato" for c in cands),
        "hypotheses": sum(c.classification == "hipótesis" for c in cands),
        "note": "ningún emparejamiento está confirmado: revisión humana pendiente; las "
        "marcas oficiales de BeamNG son ficticias y no se infieren",
    }
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "pairing.json").write_text(
        json.dumps(
            {"summary": summary, "candidates": [c.to_dict() for c in cands]},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = ["# Candidatos de emparejamiento BeamNG ↔ AC", "", f"{summary}", ""]
    for c in cands:
        lines += [
            f"## {c.bng_name} ({c.bng}) ↔ {c.ac}: **{c.classification}**",
            f"- revisión: {c.review}; configuración comparada: {c.best_config}",
        ]
        lines += [f"- a favor: {x}" for x in c.for_]
        lines += [f"- en contra: {x}" for x in c.against]
        lines += [f"- no comparable: {x}" for x in c.not_comparable]
        lines.append("")
    ensure_safe_output_path(out / "pairing.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
