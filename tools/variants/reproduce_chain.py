"""Reconstruye la cadena v13vw → v18bu en una carpeta NUEVA y la compara con lo aprobado.

    python tools/variants/reproduce_chain.py [--out-root output/seat_ibiza_acfr/repro_<fecha>]

* Entrada raíz (solo lectura): ``ingame_cycle/pkg_v13_integrated`` aprobado (salida del
  ensamblador; esta cadena no lo reconstruye).
* Cada paso es un script adaptado de ``tools/variants`` ejecutado en un proceso propio con
  ``--base-root <out-root> --out-root <out-root> --repro`` (v13vw toma su base de los
  aprobados). Ningún paso escribe fuera de ``--out-root``; los paquetes aprobados están
  sellados por su ``manifest.json`` (``src.safe_paths``).
* Comparación: el árbol de cada paquete reconstruido (sha256 por archivo) frente a
  ``files`` del manifiesto aprobado. Las mediciones de cámara se comparan sin las rutas
  absolutas.
* Escribe ``<out-root>/repro_report.json`` y ``<out-root>/logs/<paso>.txt``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from src.ac.ingame_package import tree_hashes  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

APPROVED = ROOT / "output/seat_ibiza_acfr/ingame_cycle"
CAMERA_WORK = ROOT / "output/seat_ibiza_acfr/camera_work_2026-10-25"
# (paso, script, base aprobada para el primer paso, paquetes que produce)
STEPS = [
    ("v13vw", "build_v13vw.py", True, ["pkg_v13vw_shiftwindow"]),
    ("v14_anim", "build_v14_anim.py", False,
     ["pkg_v14as_animstatic", "pkg_v14hp_hands_pos", "pkg_v14hn_hands_neg"]),
    ("v14hc", "build_v14hc.py", False, ["pkg_v14hc_hands_continuous"]),
    ("v14ha", "build_v14ha.py", False, ["pkg_v14ha_alternating_grip"]),
    ("v15li", "build_v15li.py", False, ["pkg_v15li_lights"]),
    ("v16sn", "build_v16sn.py", False, ["pkg_v16sn_sound"]),
    ("measure_cameras", "measure_cameras.py", False, []),
    ("v17cam", "build_v17cam.py", False, ["pkg_v17cam"]),
    ("measure_bumper", "measure_bumper.py", False, []),
    ("v18bu", "build_v18bu.py", False, ["pkg_v18bu"]),
]
PATH_KEYS = {"kn5"}


def _strip_paths(obj):
    if isinstance(obj, dict):
        return {k: _strip_paths(v) for k, v in obj.items() if k not in PATH_KEYS}
    if isinstance(obj, list):
        return [_strip_paths(v) for v in obj]
    return obj


def compare_package(new_pkg: Path, approved_pkg: Path) -> dict:
    man = json.loads((approved_pkg / "manifest.json").read_text(encoding="utf-8"))
    expected = man["files"]
    car_dir = Path(man["package_dir"]).name
    got = tree_hashes(new_pkg / car_dir) if (new_pkg / car_dir).is_dir() else {}
    same = sorted(f for f in expected if got.get(f) == expected[f])
    differ = sorted(f for f in expected if f in got and got[f] != expected[f])
    missing = sorted(f for f in expected if f not in got)
    extra = sorted(f for f in got if f not in expected)
    return {
        "approved_manifest": str(approved_pkg / "manifest.json"),
        "car_dir": car_dir,
        "files_expected": len(expected),
        "identical": len(same),
        "different": differ,
        "missing": missing,
        "extra": extra,
        "reproduced": not differ and not missing and not extra and bool(expected),
    }


def compare_json(new: Path, original: Path) -> dict:
    a = _strip_paths(json.loads(new.read_text(encoding="utf-8")))
    b = _strip_paths(json.loads(original.read_text(encoding="utf-8")))
    return {"new": str(new), "original": str(original), "equal_without_paths": a == b}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args()
    stamp = dt.date.today().isoformat()
    out = new_output_dir(args.out_root or ROOT / f"output/seat_ibiza_acfr/repro_{stamp}")
    logs = new_output_dir(out / "logs")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    report: dict = {"out_root": str(out), "approved_root": str(APPROVED), "root_input":
                    str(APPROVED / "pkg_v13_integrated"), "steps": []}
    for name, script, from_approved, pkgs in STEPS:
        cmd = [sys.executable, str(HERE / script), "--out-root", str(out), "--repro",
               "--base-root", str(APPROVED if from_approved else out)]
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ensure_safe_output_path(logs / f"{name}.txt").write_text(
            proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
        step = {"step": name, "script": f"tools/variants/{script}", "returncode": proc.returncode,
                "packages": {p: compare_package(out / p, APPROVED / p) for p in pkgs
                             if proc.returncode == 0}}
        if name.startswith("measure") and proc.returncode == 0:
            fname = "cameras_measurement.json" if name == "measure_cameras" else "bumper_measurement.json"
            step["measurement"] = compare_json(out / fname, CAMERA_WORK / fname)
        report["steps"].append(step)
        print(name, "rc", proc.returncode,
              {p: (c["reproduced"], c["identical"], c["files_expected"], c["different"])
               for p, c in step["packages"].items()}, step.get("measurement", {}).get("equal_without_paths", ""))
        if proc.returncode != 0:
            print(proc.stdout[-1500:], proc.stderr[-1500:])
            break
    report["all_reproduced"] = all(
        s["returncode"] == 0 and all(c["reproduced"] for c in s["packages"].values())
        and s.get("measurement", {}).get("equal_without_paths", True)
        for s in report["steps"]) and len(report["steps"]) == len(STEPS)
    ensure_safe_output_path(out / "repro_report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("all_reproduced", report["all_reproduced"], "->", out / "repro_report.json")
    return 0 if report["all_reproduced"] else 1


if __name__ == "__main__":
    sys.exit(main())
