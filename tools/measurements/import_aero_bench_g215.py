"""Registra las medidas aerodinámicas del banco de G2.15 como medidas del origen (G2.19).

    python tools/measurements/import_aero_bench_g215.py [--dry-run]

Fuente: banco de BeamNG 0.38.6 de G2.15, sesión 4 (la válida), carpeta de usuario
aislada ``output/g215_direction_brakes_aero_2026-11-03/banco/userpath``:

* volcado por vehículo ``userpath/current/beam2ac_diraero/<id>.json`` (bloque ``aero``);
* extensión ``lua_src/lua/vehicle/extensions/beam2acDirAero.lua``;
* copia de los vehículos cargados ``userpath/current/vehicles/<id>/``.

Para cada vehículo del 200409 de ``samples_beamng/``:

1. comprueba que la copia que cargó el juego es IDÉNTICA byte a byte a la muestra
   (``*.jbeam``, ``*.pc``, ``info.json``, ``materials.cs``); si no, se detiene;
2. calcula la huella del origen con ``src.importers.measurements.source_fingerprint``;
3. escribe ``measurements/beamng_bench/aero/<id>.json`` (``beamng_aero_bench``) con los
   casos del volcado SIN modificar, la huella, la versión del juego y los sha256 del
   volcado, del script y de los archivos del vehículo.

El nombre del archivo es solo una etiqueta: el importador asocia el registro por la
huella, nunca por el nombre. Solo escribe dentro de ``measurements/`` y nunca sobrescribe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.jbeam.lax_json import loads  # noqa: E402
from src.importers.measurements import (  # noqa: E402
    MEASUREMENTS_ROOT,
    SCHEMA,
    source_fingerprint,
)
from src.pipeline.physics_preview import load_vehicle_dir  # noqa: E402

BENCH = ROOT / "output/g215_direction_brakes_aero_2026-11-03/banco"
USER = BENCH / "userpath/current"
DUMP = USER / "beam2ac_diraero"
SCRIPT = BENCH / "lua_src/lua/vehicle/extensions/beam2acDirAero.lua"
LOG = USER / "beamng.log"
OUT = MEASUREMENTS_ROOT / "beamng_bench/aero"
CHECKED = (".jbeam", ".pc", ".cs")
CHECKED_NAMES = ("info.json",)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def game_version() -> str:
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
        if "Log started - v " in line:
            return line.split("Log started - v ", 1)[1].split(" ", 1)[0]
    raise SystemExit("no se encuentra la versión del juego en beamng.log")


def vehicle_files(vd: Path) -> dict[str, str]:
    return {
        f.relative_to(vd).as_posix(): sha(f)
        for f in sorted(vd.rglob("*"))
        if f.is_file() and (f.suffix in CHECKED or f.name in CHECKED_NAMES)
    }


def build() -> list[tuple[Path, dict]]:
    version = game_version()
    out = []
    for vd in sorted((ROOT / "samples_beamng").glob("*/vehicles/*")):
        info = loads((vd / "info.json").read_text(encoding="utf-8")).value
        if str(info.get("ExporterVersion")) != "200409":
            continue
        copy = USER / "vehicles" / vd.name
        dump = DUMP / f"{vd.name}.json"
        if not copy.is_dir() or not dump.is_file():
            raise SystemExit(f"{vd.name}: falta la copia o el volcado del banco")
        mine, theirs = vehicle_files(vd), vehicle_files(copy)
        if mine != theirs:
            raise SystemExit(f"{vd.name}: la copia del banco NO es idéntica a la muestra")
        pcs = sorted(p.name for p in vd.glob("*.pc"))
        if len(pcs) != 1:
            raise SystemExit(f"{vd.name}: se esperaba una sola .pc: {pcs}")
        src = load_vehicle_dir(vd, pcs[0])
        data = json.loads(dump.read_text(encoding="utf-8"))
        cases = [c for c in data["aero"] if c.get("v", 0) > 0]
        rec = {
            "schema": SCHEMA,
            "kind": "beamng_aero_bench",
            "source_fingerprint": source_fingerprint(src),
            "label": {"vehicle_id": vd.name, "pc": pcs[0]},
            "game": {"name": "BeamNG.drive", "version": version},
            "bench": {
                "report": "docs/research/g215_direction_brakes_aero_2026-11-03.md §5.2",
                "session": 4,
                "method": "coche parado y apoyado, freno de mano; obj:setWind(−v·dir); "
                "3 s de reposo y 1 s de muestras por caso (media); "
                "obj:calcTotalAeroForces(), obj:calcTotalAeroTorque(centro de cada "
                "rueda), obj:getAirDensity()",
                "dump": dump.relative_to(ROOT).as_posix(),
                "dump_sha256": sha(dump),
                "script": SCRIPT.relative_to(ROOT).as_posix(),
                "script_sha256": sha(SCRIPT),
                "loaded_copy": copy.relative_to(ROOT).as_posix(),
                "loaded_copy_identical_to_source": True,
            },
            "source_files_sha256": mine,
            "frame": "carrocería (adelante, izquierda, arriba) de refNodes; posiciones en m "
            "respecto a refNodes.ref; F en N; torque[w] = calcTotalAeroTorque(centro de "
            "la rueda w) = F × (r − P) (signo opuesto al momento usual); pitch p ⇒ "
            "avance en adelante·cos p + arriba·sin p; yaw y ⇒ avance hacia la izquierda",
            "limits": [
                "ruedas sin girar (viento sobre el coche parado)",
                "una sesión; actitud de reposo; sin medir otras alturas",
                "ángulos de ataque −4…+4° a 50 m/s; velocidades 20–70 m/s a 0°",
            ],
            "cases": cases,
        }
        out.append((OUT / f"{vd.name}.json", rec))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    recs = build()
    for path, rec in recs:
        print(path.relative_to(ROOT), rec["source_fingerprint"][:12], len(rec["cases"]))
    if args.dry_run:
        return 0
    root = MEASUREMENTS_ROOT.resolve()
    for path, rec in recs:
        target = path.resolve()
        if root not in target.parents:
            raise SystemExit(f"fuera de measurements/: {target}")
        if target.exists():
            raise SystemExit(f"ya existe {target}; no se sobrescribe")
    for path, rec in recs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("escrito", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
