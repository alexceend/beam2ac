"""CLI del flujo nuevo (G1): importar → IR → reglas → modelo de destino AC.

    python -m src.convert inspect <carpeta|zip> [--pc P] [--common-zip C]
        [--project output/projects/<n>] --out output/<nueva>

Escribe en una carpeta NUEVA (``new_output_dir``): ``ir.json``, ``resolution.json``,
``target/data/*`` (INI y LUT generados, sin instalar), ``target_report.json`` y
``summary.json``. Solo lee el origen y, si se da, las decisiones del proyecto.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.safe_paths import ensure_safe_output_path, new_output_dir

from .service import ConversionSession


def _dump(path: Path, data) -> None:
    ensure_safe_output_path(path).write_text(
        json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n", "utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m src.convert")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ins = sub.add_parser("inspect")
    ins.add_argument("source")
    ins.add_argument("--pc")
    ins.add_argument("--common-zip")
    ins.add_argument("--project")
    ins.add_argument("--driver-mass", type=float, default=70.0)
    ins.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    s = ConversionSession.from_source(
        a.source, a.pc, a.common_zip, a.driver_mass, a.project
    )
    s.resolve()
    target = s.target()
    out = new_output_dir(a.out)
    _dump(out / "ir.json", s.ir.to_dict())
    _dump(out / "resolution.json", [r.to_dict() for r in s.results])
    data = out / "target" / "data"
    ensure_safe_output_path(data).mkdir(parents=True)
    for name, text in target.files.items():
        ensure_safe_output_path(data / name).write_text(text, encoding="utf-8")
    report = target.to_dict()
    report.pop("files")
    _dump(out / "target_report.json", report)
    summary = s.summary()
    _dump(out / "summary.json", summary)
    print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
