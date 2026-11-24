"""python -m src.audio build <carpeta_vehiculo> --car-id beam2ac_<id> --out output/<nueva> [--no-build]
python -m src.audio status <carpeta_vehiculo>
"""

from __future__ import annotations

import argparse
import json
import sys

from src.audio import pipeline
from src.audio.plan import build_plan
from src.extract.audio_sources import scan_audio


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("vehicle_dir")
    b.add_argument("--car-id", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--idle", type=float)
    b.add_argument("--limiter", type=float)
    b.add_argument("--no-build", action="store_true")
    c = sub.add_parser("collect", help="recoge el banco de un build hecho a mano")
    c.add_argument("out")
    s = sub.add_parser("status")
    s.add_argument("vehicle_dir")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        from pathlib import Path

        from src.audio import fmod_build

        spec = json.loads((Path(a.out) / "spec.json").read_text(encoding="utf-8"))
        ev = set(spec["secondary"]) | set(spec["engine"])
        print(fmod_build.collect(Path(a.out), spec["car"], ev))
        return 0
    if a.cmd == "status":
        p = build_plan(scan_audio(a.vehicle_dir), "x")
        p.pop("spec", None)
        p.pop("wav", None)
        print(json.dumps({**p, **pipeline.ladder(p["status"])}, indent=1, default=str))
        return 0
    r = pipeline.run(
        a.vehicle_dir, a.car_id, a.out, a.idle, a.limiter, build=not a.no_build
    )
    r.pop("wav", None)
    print(json.dumps(r, indent=1, default=str))
    return 1 if r["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
