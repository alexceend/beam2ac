"""Ajusta el modelo SCTM a TODAS las medidas del banco de neumáticos y las junta con los
coeficientes de rozamiento del JBeam (2026-11-16).

    python tools/analysis/tyre_fit_all_benches.py --out output/<nueva>/fits.json

Objetivo: comprobar si los parámetros del JBeam (que BeamNG pasa a su C++ con ``setNode``)
explican los parámetros SCTM ajustados, para convertir neumáticos SIN ejecutar el banco.
Solo lectura de ``measurements/``; escribe un JSON en una carpeta nueva de ``output/``.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.tyre_fit import INI_FIELDS, fit_dataset  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    out = ensure_safe_output_path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(glob.glob(str(ROOT / "measurements/beamng_bench/tyre/*.json"))):
        rec = json.loads(Path(f).read_text("utf-8"))
        for axle, ax in rec["axles"].items():
            item = {
                "record": Path(f).stem,
                "axle": axle,
                "wheel_params": ax["wheel_params"],
                "reference_load": ax["reference_load"],
                "vertical_stiffness_static": ax["vertical_stiffness_static"],
                "free_rolling_radius": ax["free_rolling_radius"],
            }
            try:
                res = fit_dataset(ax["samples"], ax["reference_load"])
                item["fit"] = {k: getattr(res.params, v) for k, v in INI_FIELDS.items()}
                item["rms"] = res.rms
                item["n"] = res.n
            except Exception as exc:  # noqa: BLE001 - se anota y se sigue
                item["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(item)
            print(item["record"], axle, item.get("error", item.get("rms")), flush=True)
    out.write_text(json.dumps(rows, indent=1), "utf-8")
    print("escrito", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
