"""pkg_v18bu = pkg_v17cam + BUMPER_CAMERA_POS en car.ini (nada más).

    python tools/variants/build_v18bu.py --out-root DIR [--base-root DIR] [--repro]

Requiere antes ``measure_bumper.py`` (``<out-root>/bumper_measurement.json``). No modifica nada existente; si una
comprobación falla, borra solo la carpeta nueva. Conserva el id de carpeta de v16sn/v17cam
(banco FMOD y GUIDs.txt sin cambios).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.steam_paths import find_game  # noqa: E402
from src.ac.car_validate import (  # noqa: E402
    changed_protected,
    duplicate_keys,
    read_ini,
    validate_references,
    validate_structure,
)
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes  # noqa: E402
from src.ac.ini_format import sanitize_ini_comments  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
BASE_MAN = IC / "pkg_v17cam/manifest.json"
OUT = ensure_safe_output_path(OUT_IC / "pkg_v18bu")
WORK = OUT_IC  # medición en --out-root
AC = (find_game("assettocorsa") or Path("assettocorsa"))
CAR = "beam2ac_test16sn_seat_ibiza_acfr"
NEW = ["data/car.ini"]


def stop(msg: str) -> None:
    sys.exit("STOP: " + msg)


def fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v != 0 else "0"


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("v17cam no coincide con su manifiesto")
if base.name != CAR or OUT.exists():
    stop(f"id inesperado o ya existe {OUT}")
meas = json.loads((WORK / "bumper_measurement.json").read_text(encoding="utf-8"))
bum, kn5p, rule = meas["BUMPER_CAMERA_POS"], meas["kn5"], meas["rule"]

car_old = (base / "data/car.ini").read_bytes()
ini_old = read_ini(base / "data/car.ini")
if "BUMPER_CAMERA_POS" in ini_old["GRAPHICS"]:
    stop("v17cam ya tiene BUMPER_CAMERA_POS")
line = sanitize_ini_comments(
    f"BUMPER_CAMERA_POS={', '.join(fmt(v) for v in bum)}\t\t; DIAG test18bu: marco del cuerpo "
    f"(AC, como BONNET); KN5 {', '.join(fmt(v) for v in kn5p)}; convencion Kunos "
    f"(ae86, abarth500, celica, r8): y cuerpo mediana {rule['y_body_median']}, z = frontal "
    f"{rule['z_minus_front_median']} m (camera_work_2026-10-25)"
)
lines = car_old.split(b"\r\n")
idx = [i for i, ln in enumerate(lines) if ln.startswith(b"BONNET_CAMERA_POS=")]
if len(idx) != 1:
    stop("BONNET_CAMERA_POS no aparece una sola vez")
lines.insert(idx[0] + 1, line.encode("utf-8"))
car_new = b"\r\n".join(lines)

new_output_dir(OUT)
dst = OUT / CAR
try:
    shutil.copytree(base, dst)
    if tree_hashes(dst) != before:
        raise RuntimeError("la copia no es idéntica a v17cam")
    ensure_safe_output_path(dst / "data/car.ini").write_bytes(car_new)
    after = tree_hashes(dst)
    diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
    ini_new = read_ini(dst / "data/car.ini")
    expected = json.loads(json.dumps(ini_old))
    expected["GRAPHICS"]["BUMPER_CAMERA_POS"] = line.split("=", 1)[1].split(";", 1)[0].strip()
    g_old, g_new = ini_old["GRAPHICS"], ini_new["GRAPHICS"]
    keep = sorted(f for f in before if f not in NEW)
    checks = {
        "diferencia con v17cam = solo data/car.ini": diff == NEW,
        "car.ini = v17cam + una línea tras BONNET_CAMERA_POS (byte a byte)": car_new.replace(
            b"\r\n" + line.encode("utf-8"), b"", 1) == car_old,
        "car.ini: mismas secciones y claves salvo BUMPER_CAMERA_POS": ini_new == expected,
        "DRIVEREYES y BONNET_CAMERA_POS sin cambios": all(g_new[k] == g_old[k] for k in ("DRIVEREYES", "BONNET_CAMERA_POS")),
        "BUMPER_CAMERA_POS = medición": [float(v) for v in g_new["BUMPER_CAMERA_POS"].split(",")] == bum,
        "car.ini sin claves duplicadas": duplicate_keys(dst / "data/car.ini") == [],
        "cámara fuera de la carrocería en todo el rango Kunos (>= 0,10 m)":
            meas["min_clearance_over_kunos_z_range"] is not None and meas["min_clearance_over_kunos_z_range"] >= 0.10,
        "comentario sin corchetes": "[" not in line.split(";", 1)[1] and "]" not in line.split(";", 1)[1],
        "dash_cam.ini, KN5, física, sfx, GUIDs y resto idénticos": changed_protected(base, dst, keep) == [],
        "banco y GUIDs idénticos a v17cam": sha256(dst / f"sfx/{CAR}.bank") == before[f"sfx/{CAR}.bank"]
        and sha256(dst / "sfx/GUIDs.txt") == before["sfx/GUIDs.txt"],
    }
    vr = validate_references(dst, AC / "content/driver")
    req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini", "tyres.ini",
           "lods.ini", "setup.ini", "driver3d.ini", "aero.ini", "lights.ini", "sounds.ini", "dash_cam.ini")
    vs = validate_structure(dst, req)
    checks["validate_references sin errores"] = vr.ok
    checks["validate_structure sin errores"] = vs.ok
    if not all(checks.values()):
        raise RuntimeError(f"comprobación fallida: {checks} {vr.errors} {vs.errors}")
    if tree_hashes(base) != before:
        raise RuntimeError("v17cam cambió")
except Exception as e:  # noqa: BLE001
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")

manifest = {
    "test_id": CAR, "variant": "pkg_v18bu", "source_dir": str(base), "source_manifest": str(BASE_MAN),
    "package_dir": str(dst), "installed": False, "distributable": False,
    "purpose": "DIAGNOSTICO: vista Bumper (BUMPER_CAMERA_POS); resto = v17cam",
    "same_car_id_as_v16sn": True, "id_present_in_content_cars": CAR in existing_car_ids(AC),
    "BUMPER_CAMERA_POS": {"before": None, "after": bum, "kn5": kn5p, "rule": rule},
    "diff_vs_v17cam": diff, "files": after, "checks": checks,
    "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors]},
}
ensure_safe_output_path(OUT / "manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diff_vs_v17cam", "checks", "validation")},
                 indent=1, ensure_ascii=False))
print(sha256(dst / "data/car.ini")[:16], "data/car.ini")
print(line)
