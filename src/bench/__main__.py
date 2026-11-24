"""``python -m src.bench tyres|aero <carpeta_vehiculo> [--work output/<nueva>] [--pc P]``.

Mide en BeamNG (carpeta de usuario aislada, 2 sesiones en serie) un vehículo SIN medida y
escribe ``measurements/beamng_bench/<tipo>/<id>.json``. Solo con BeamNG cerrado. Sin
``--work`` el trabajo vive en ``output/bench_jobs/<tipo>_<huella>`` y repetir la orden
REANUDA (las sesiones hechas no se repiten). Misma lógica que la aplicación de
escritorio (``src.bench.service``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.bench import service
from src.bench import tyre_runner as tr


def main(argv: list[str] | None = None) -> int:
    # la consola de Windows (cp1252) no codifica «≈» de los mensajes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m src.bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (
        ("tyres", "banco de neumáticos de un vehículo (≈ 20 min)"),
        ("aero", "banco aerodinámico de un vehículo 200409 (≈ 4 min)"),
    ):
        t = sub.add_parser(name, help=hlp)
        t.add_argument("vehicle_dir", type=Path, help="carpeta del vehículo o zip")
        t.add_argument("--work", type=Path)
        t.add_argument("--pc")
        t.add_argument(
            "--label", help="nombre del archivo de medida (por defecto, el id)"
        )
        t.add_argument("--beamng-root", type=Path, default=None)
        t.add_argument("--status", action="store_true", help="solo muestra el estado")
    a = ap.parse_args(argv)
    try:
        if a.status:
            st = service.status(a.cmd, a.vehicle_dir, a.pc)
            print(st)
            return 0
        rec = service.run(
            a.cmd,
            a.vehicle_dir,
            a.pc,
            work=a.work,
            label=a.label,
            beamng_root=a.beamng_root,
            log=lambda s: print(s, flush=True),
        )
    except tr.BenchError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2
    print("medida escrita:", rec["_target"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
