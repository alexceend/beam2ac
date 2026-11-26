"""pkg_v17cam = pkg_v16sn_sound + data/dash_cam.ini + BONNET_CAMERA_POS en car.ini.

    python tools/variants/build_v17cam.py --out-root DIR [--base-root DIR] [--repro]

Requiere antes ``measure_cameras.py`` (``<out-root>/cameras_measurement.json``). No modifica nada existente; si
una comprobación falla, borra solo la carpeta nueva. La carpeta del coche conserva el id de v16sn
(``beam2ac_test16sn_seat_ibiza_acfr``): el banco FMOD y GUIDs.txt llevan ese nombre dentro y
deben quedar idénticos.
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
BASE_PKG = IC / "pkg_v16sn_sound"
BASE_MAN = BASE_PKG / "manifest.json"
OUT = ensure_safe_output_path(OUT_IC / "pkg_v17cam")
WORK = OUT_IC  # medición en --out-root
AC = (find_game("assettocorsa") or Path("assettocorsa"))
CAR = "beam2ac_test16sn_seat_ibiza_acfr"
NEW = ["data/car.ini", "data/dash_cam.ini"]
EYES = [0.355, 1.144, -0.344]


def stop(msg: str) -> None:
    sys.exit("STOP: " + msg)


def fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v != 0 else "0"


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("v16sn no coincide con su manifiesto")
if base.name != CAR:
    stop(f"id inesperado {base.name}")
if OUT.exists():
    stop(f"ya existe {OUT}")
if "data/dash_cam.ini" in before:
    stop("la base ya tiene dash_cam.ini")
meas = json.loads((WORK / "cameras_measurement.json").read_text(encoding="utf-8"))
ours = meas["ours"]
if Path(ours["kn5"]).resolve() != (base / "seat_ibiza_acfr.kn5").resolve():
    stop("la medición no es del KN5 de v16sn")

car_old = (base / "data/car.ini").read_bytes()
ini_old = read_ini(base / "data/car.ini")
g = ini_old["GRAPHICS"]
if [float(v) for v in g["DRIVEREYES"].split(";")[0].split(",")] != EYES or ours["DRIVEREYES"] != EYES:
    stop("DRIVEREYES distinto del esperado")
for key in ("BONNET_CAMERA_POS", "BUMPER_CAMERA_POS", "ON_BOARD_PITCH_ANGLE"):
    if key in g:
        stop(f"car.ini ya tiene {key}")

bon = ours["BONNET_derived"]["BONNET_CAMERA_POS"]
kn5p = ours["BONNET_derived"]["kn5"]
rule = ours["BONNET_derived"]["rule"]
bonnet_line = sanitize_ini_comments(
    f"BONNET_CAMERA_POS={', '.join(fmt(v) for v in bon)}\t\t; DIAG test17cam: marco del cuerpo "
    f"(AC); KN5 {', '.join(fmt(v) for v in kn5p)} + GRAPHICS_OFFSET; "
    f"regla medida en 5 referencias: {rule['behind_hood_edge_median']} m por detras del borde trasero "
    f"del capo y {rule['above_surface_median']} m sobre la superficie (camera_work_2026-10-25)"
)
lines = car_old.split(b"\r\n")
idx = [i for i, ln in enumerate(lines) if ln.startswith(b"DRIVEREYES=")]
if len(idx) != 1:
    stop("DRIVEREYES no aparece una sola vez")
lines.insert(idx[0] + 1, bonnet_line.encode("utf-8"))
car_new = b"\r\n".join(lines)
dash_txt = sanitize_ini_comments(
    "; DIAG test17cam: POS = DRIVEREYES (marco del KN5, AC; misma convencion que "
    "sample_assetto); EXP de ks_toyota_ae86\r\n"
    f"[DASH_CAM]\r\nPOS={', '.join(fmt(v) for v in EYES)}\r\nEXP=26\r\n"
)

new_output_dir(OUT)
dst = OUT / CAR
try:
    shutil.copytree(base, dst)
    if tree_hashes(dst) != before:
        raise RuntimeError("la copia no es idéntica a v16sn")
    (ensure_safe_output_path(dst / "data/car.ini")).write_bytes(car_new)
    (ensure_safe_output_path(dst / "data/dash_cam.ini")).write_bytes(dash_txt.encode("utf-8"))
    after = tree_hashes(dst)
    diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
    ini_new = read_ini(dst / "data/car.ini")
    expected = json.loads(json.dumps(ini_old))
    expected["GRAPHICS"]["BONNET_CAMERA_POS"] = bonnet_line.split("=", 1)[1].split(";", 1)[0].strip()
    dash = read_ini(dst / "data/dash_cam.ini")
    keep = sorted(f for f in before if f not in NEW)
    new_lines = [bonnet_line] + dash_txt.split("\r\n")
    checks = {
        "diferencia con v16sn = solo car.ini y dash_cam.ini": diff == sorted(NEW),
        "car.ini = original + una línea tras DRIVEREYES (byte a byte)": car_new.replace(
            b"\r\n" + bonnet_line.encode("utf-8"), b"", 1) == car_old,
        "car.ini: mismas secciones y claves salvo BONNET_CAMERA_POS": ini_new == expected,
        "car.ini sin claves duplicadas": duplicate_keys(dst / "data/car.ini") == [],
        "car.ini: BONNET_CAMERA_POS = medición": [
            float(v) for v in ini_new["GRAPHICS"]["BONNET_CAMERA_POS"].split(",")] == bon,
        "car.ini: sin BUMPER_CAMERA_POS ni ON_BOARD_PITCH_ANGLE (pendientes)": not {
            "BUMPER_CAMERA_POS", "ON_BOARD_PITCH_ANGLE"} & set(ini_new["GRAPHICS"]),
        "bonnet: por encima de la superficie y por detrás del frontal": ours["BONNET_derived"]["kn5"][1]
        > ours["BONNET_derived"]["surface_y_at_z"] and ours["BONNET_derived"]["inside_front"],
        "dash_cam.ini: POS = DRIVEREYES, EXP = 26": dash == {"DASH_CAM": {"POS": "0.355, 1.144, -0.344", "EXP": "26"}},
        "dash_cam.ini sin claves duplicadas": duplicate_keys(dst / "data/dash_cam.ini") == [],
        "DRIVEREYES dentro del habitáculo (techo por encima)": ours["roof_above_eyes_m"] > 0.05,
        "comentarios nuevos sin corchetes": all("[" not in ln.split(";", 1)[1] and "]" not in ln.split(";", 1)[1]
                                                for ln in new_lines if ";" in ln),
        "resto de archivos idénticos (KN5, física, sfx, GUIDs, animaciones, skins, ui)":
            changed_protected(base, dst, keep) == [],
        "banco y GUIDs idénticos a v16sn": sha256(dst / f"sfx/{CAR}.bank") == before[f"sfx/{CAR}.bank"]
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
        raise RuntimeError("v16sn cambió")
except Exception as e:  # noqa: BLE001
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")

installed = CAR in existing_car_ids(AC)
manifest = {
    "test_id": CAR, "variant": "pkg_v17cam", "source_dir": str(base), "source_manifest": str(BASE_MAN),
    "package_dir": str(dst), "installed": False, "distributable": False,
    "purpose": "DIAGNOSTICO: vistas Dash y Bonnet (dash_cam.ini + BONNET_CAMERA_POS); "
               "BUMPER_CAMERA_POS y ON_BOARD_PITCH_ANGLE pendientes",
    "same_car_id_as_v16sn": True,
    "note_install": ("comparte id con v16sn (banco FMOD y GUIDs sin cambiar); una copia con ese id "
                     "ya existe en content/cars" if installed else "comparte id con v16sn"),
    "diff_vs_v16sn": diff, "files": after,
    "camera_values": {"DASH_CAM.POS": EYES, "DASH_CAM.EXP": 26, "BONNET_CAMERA_POS": bon,
                      "BONNET_kn5": kn5p, "rule": rule},
    "checks": checks,
    "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors]},
}
(ensure_safe_output_path(OUT / "manifest.json")).write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diff_vs_v16sn", "checks", "validation", "note_install")},
                 indent=1, ensure_ascii=False))
for f in NEW:
    print(sha256(dst / f)[:16], f)
print(bonnet_line)
print(dash_txt)
