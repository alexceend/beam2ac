"""v14hc: test14hp sin el salto a ±75° (no modifica nada existente).

    python tools/variants/build_v14hc.py --out-root DIR [--base-root DIR] [--repro]

Causa (AC): con |deg| > LOCK, modelo de piloto de AC aplica fmodf y el clip vuelve al
fotograma central. Corrección mínima, solo en la animación:
  * LOCK=450 > STEER_LOCK=423 → no hay fmodf en todo el recorrido (hipótesis: el ángulo
    gráfico no supera STEER_LOCK);
  * manos = volante hasta 60°, después saturación C1 hacia 75° (alcance de los brazos);
  * 600 fotogramas (1,5°/fotograma) con la correspondencia exacta deg = LOCK·(1 − 2f/n).
Signo +, como test14hp (confirmado por el usuario).
"""

import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.ac.car_validate import read_ini, validate_references, validate_structure  # noqa: E402
from src.ac.driver_pose import FINGERS, Knh  # noqa: E402
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes  # noqa: E402
from src.ac.kn5_inspect import read_kn5  # noqa: E402
from src.ac.steer_anim import (  # noqa: E402
    ac_frame_angle,
    ac_pose_at,
    clip_bones,
    clip_worlds,
    frame_to_matrix,
    max_error,
    read_ksanim,
    rim_angle_of,
    saturating_follow,
    steer_clip,
    write_ksanim,
)
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
BASE_MAN = IC / "pkg_v14hp_hands_pos/manifest.json"
AS_MAN = IC / "pkg_v14as_animstatic/manifest.json"
OUT = OUT_IC / "pkg_v14hc_hands_continuous"
NEW_ID = "beam2ac_test14hc_seat_ibiza_acfr"
AC = Path(r"G:\SteamLibrary\steamapps\common\assettocorsa")
LOCK, FRAMES, FOLLOW, MAX_HAND, SIGN = 450.0, 600, 60.0, 75.0, 1
STEER_LOCK_CAR = 423.0
ANIM, DRV = "animations/steer.ksanim", "data/driver3d.ini"
ARM_BONES = {f"DRIVER:RIG_{b}_{s}" for s in FINGERS for b in ("Arm", "ForeArm", "HAND")}


def stop(msg):
    sys.exit("STOP: " + msg)


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("test14hp no coincide con su manifiesto")
if OUT.exists():
    stop(f"ya existe {OUT}")
if NEW_ID in existing_car_ids(AC) and not ARGS.repro:
    stop("el id ya existe en la instalación")
car = read_ini(base / "data/car.ini")
if float(car["CONTROLS"]["STEER_LOCK"]) != STEER_LOCK_CAR or LOCK <= STEER_LOCK_CAR:
    stop("STEER_LOCK del coche inesperado")
drv_old = (base / DRV).read_bytes()
old_line, new_line = b"LOCK=75\r\n", f"LOCK={LOCK:g}\r\n".encode()
if drv_old.count(old_line) != 1 or not drv_old.endswith(old_line):
    stop("driver3d.ini de test14hp distinto del esperado")
mm = json.loads(BASE_MAN.read_text(encoding="utf-8"))["clip"]
c, z = mm["rim_centre"], mm["rim_axis"]  # mismo marco del aro que test14hp
knh = Knh((base / "driver_base_pos.knh").read_bytes())
w0 = knh.worlds()
fn = saturating_follow(FOLLOW, MAX_HAND)
clip, info = steer_clip(knh, c, z, LOCK, SIGN, frames=FRAMES, angle_fn=ac_frame_angle, hand_fn=fn)
data = write_ksanim(clip)

new_output_dir(OUT)
dst = OUT / NEW_ID
try:
    shutil.copytree(base, dst)
    if tree_hashes(dst) != before:
        raise RuntimeError("la copia no es idéntica a test14hp")
    (dst / ANIM).write_bytes(data)
    (dst / DRV).write_bytes(drv_old[: -len(old_line)] + new_line)
    after = tree_hashes(dst)
    diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
    if diff != [ANIM, DRV]:
        raise RuntimeError(f"diferencias inesperadas con test14hp: {diff}")
    a, b = read_ini(base / DRV), read_ini(dst / DRV)
    a["STEER_ANIMATION"]["LOCK"] = f"{LOCK:g}"
    if a != b:
        raise RuntimeError("driver3d.ini cambia algo más que LOCK")
    as_files = json.loads(AS_MAN.read_text(encoding="utf-8"))["files"]
    diff_as = sorted(f for f in set(after) | set(as_files) if after.get(f) != as_files.get(f))
    if diff_as != [ANIM, DRV]:
        raise RuntimeError(f"diferencias inesperadas con test14as: {diff_as}")
    ver, back = read_ksanim((dst / ANIM).read_bytes())
    names = [n for n, _ in back]
    driver_nodes = {n.name for n in read_kn5(AC / "content/driver/driver.kn5", keep_vertices=False).nodes}
    old_names = [n for n, _ in read_ksanim((base / ANIM).read_bytes())[1]]
    mid = clip_worlds(knh, back, FRAMES // 2)
    worst_body = max(
        max_error(frame_to_matrix(fr), knh.local[n]) for n, frs in back if n not in ARM_BONES for fr in frs
    )
    # barrido con la correspondencia exacta de AC (fmodf + interpolación de fotogramas)
    step, d, prev, max_jump, max_err, max_err_lin = 0.05, -STEER_LOCK_CAR, None, 0.0, 0.0, 0.0
    while d <= STEER_LOCK_CAR + 1e-9:
        w = ac_pose_at(knh, back, d, LOCK)
        ang = {s: rim_angle_of(w[f"DRIVER:RIG_HAND_{s}"][3][:3], w0[f"DRIVER:RIG_HAND_{s}"][3][:3], c, z)
               for s in FINGERS}
        for s, x in ang.items():
            max_err = max(max_err, abs(x - fn(d)))
            if abs(d) <= FOLLOW:
                max_err_lin = max(max_err_lin, abs(x - d))
            if prev is not None:
                max_jump = max(max_jump, abs(x - prev[s]))
        prev, d = ang, d + step
    checks = {
        "versión 2, 600 fotogramas en todos los huesos": ver == 2 and {len(f) for _, f in back} == {FRAMES},
        "mismos huesos y orden que test14hp": names == old_names == clip_bones(knh),
        "huesos existen en driver.kn5": all(n in driver_nodes for n in names),
        "fotograma central = pose de v13vw (< 1e-6)": max(max_error(mid[n], w0[n]) for n in names) < 1e-6,
        "huesos ajenos a los brazos = pose del knh en todos los fotogramas": worst_body < 1e-5,
        "sin saltos: paso de manos ≤ paso de volante en ±423°": max_jump <= step * 1.05,
        "manos = saturación prevista (< 0,05°)": max_err < 0.05,
        "manos = volante dentro de ±60° (< 0,01°)": max_err_lin < 0.01,
        "alcance positivo": info["reach_margin_m"] > 0,
    }
    if not all(checks.values()):
        raise RuntimeError(f"comprobación fallida: {checks}")
    if tree_hashes(base) != before:
        raise RuntimeError("test14hp cambió")
except Exception as e:
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")
req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini",
       "tyres.ini", "lods.ini", "setup.ini", "driver3d.ini", "aero.ini")
vs, vr = validate_structure(dst, req), validate_references(dst, AC / "content/driver")
manifest = {
    "test_id": NEW_ID, "source_dir": str(base), "source_manifest": str(BASE_MAN), "package_dir": str(dst),
    "installed": False, "distributable": False,
    "purpose": "DIAGNOSTICO: test14hp sin el salto a ±75° (LOCK 450 > STEER_LOCK, saturación 60→75°, 600 fotogramas)",
    "diff_vs_test14hp": diff, "diff_vs_test14as": diff_as, "files": after,
    "clip": {**{k: v for k, v in info.items()}, "follow_linear_deg": FOLLOW, "hand_max_deg": MAX_HAND,
             "rim_centre": c, "rim_axis": z, "sha256": sha256(dst / ANIM)},
    "sweep": {"range_deg": STEER_LOCK_CAR, "step_deg": step, "max_hand_jump_deg": max_jump,
              "max_error_vs_target_deg": max_err, "max_error_follow_zone_deg": max_err_lin,
              "worst_body_bone_error": worst_body},
    "checks": checks,
    "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors]},
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diff_vs_test14hp", "diff_vs_test14as", "sweep",
                                          "checks", "validation")}, indent=1, ensure_ascii=False))
print("steer.ksanim", sha256(dst / ANIM), "driver3d", sha256(dst / DRV), "manifest", sha256(OUT / "manifest.json"))
print("margen de alcance m", round(info["reach_margin_m"], 4))
