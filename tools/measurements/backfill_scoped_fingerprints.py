"""Añade ``scoped_fingerprint`` (G2.47) a los registros de ``measurements/`` anteriores.

Es ADITIVO: solo se escribe el campo nuevo, tras comprobar que la huella completa del
registro coincide con la de un origen del corpus recalculada ahora; los datos medidos no
se tocan. Copia previa de cada registro en ``--backup`` (carpeta nueva). Un registro sin
origen disponible se informa y se deja igual (sigue valiendo por huella exacta).

    python tools/measurements/backfill_scoped_fingerprints.py --backup output/<nueva> [--dry-run]
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.importers import measurements as M  # noqa: E402
from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402

COMMON = Path("G:/SteamLibrary/steamapps/common/BeamNG.drive/content/vehicles/common.zip")


def candidate_sources():
    for d in sorted(glob.glob(str(ROOT / "samples_beamng/*/vehicles/*"))) + sorted(
        glob.glob(str(ROOT / "sample_beamng/vehicles/*"))
    ):
        for pc in sorted(Path(d).glob("*.pc")):
            yield f"{d}::{pc.name}", lambda d=d, pc=pc: load_vehicle_dir(Path(d), pc.name)
    mods = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
    for z in sorted(mods.glob("*.zip")):
        yield str(z), lambda z=z: load_vehicle_zip(z, COMMON if COMMON.is_file() else None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    recs = {p: M.load_record(p) for p in M.measurement_files()}
    todo = {p: r for p, r in recs.items() if "scoped_fingerprint" not in r}
    backup = ensure_safe_output_path(Path(a.backup) / "x").parent
    done: set[Path] = set()
    for label, load in candidate_sources():
        try:
            src = load()
            fp = M.source_fingerprint(src)
        except Exception as exc:  # origen ilegible: no es de este corpus
            print("omitido", label, str(exc)[:80])
            continue
        for p, r in todo.items():
            if r["source_fingerprint"] != fp or p in done or r["kind"] not in M.SCOPES:
                continue
            sf = M.scoped_fingerprint(src, r["kind"])
            print(f"{p.name:48} {r['kind']:18} {sf[:14]} <- {label}")
            if not a.dry_run:
                (backup / p.parent.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, backup / p.parent.name / p.name)
                raw = json.loads(p.read_text("utf-8"))
                raw["scoped_fingerprint"] = sf
                p.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", "utf-8")
            done.add(p)
    for p in todo:
        if p not in done:
            print("SIN ORIGEN (queda igual):", p.name)


if __name__ == "__main__":
    main()
