"""Variantes de diagnóstico de la animación de manos sobre v13vw (sin tocar nada existente).

    python tools/variants/build_v14_anim.py --out-root DIR [--base-root DIR] [--repro]

Crea, cada una en su carpeta nueva (falla si existe):
  pkg_v14as_animstatic  beam2ac_test14as_seat_ibiza_acfr  clip estático (pose de v13vw), LOCK=360
  pkg_v14hp_hands_pos   beam2ac_test14hp_seat_ibiza_acfr  manos siguen el aro, signo +, LOCK=75
  pkg_v14hn_hands_neg   beam2ac_test14hn_seat_ibiza_acfr  manos siguen el aro, signo −, LOCK=75
Cada variante = v13vw + animations/steer.ksanim + [STEER_ANIMATION] en data/driver3d.ini. Nada más.
"""

import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.steam_paths import find_game  # noqa: E402
from src.ac.car_validate import read_ini, validate_references, validate_structure  # noqa: E402
from src.ac.driver_pose import FINGERS, Knh  # noqa: E402
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes  # noqa: E402
from src.ac.kn5_inspect import read_kn5  # noqa: E402
from src.ac.kn5_materials import split_kn5  # noqa: E402
from src.ac.steer_anim import (  # noqa: E402
    clip_angle,
    clip_bones,
    clip_worlds,
    frame_to_matrix,
    max_error,
    read_ksanim,
    rim_angle_of,
    static_clip,
    steer_clip,
    write_ksanim,
)
from src.ac.steer_wheel import rim_frame  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
BASE_MAN = IC / "pkg_v13vw_shiftwindow/manifest.json"
AC = (find_game("assettocorsa") or Path("assettocorsa"))
DRIVER_KN5 = AC / "content/driver/driver.kn5"
AE86_CLIP = AC / "content/cars/ks_toyota_ae86/animations/steer.ksanim"
LOCK_STEER = 75.0  # ambas manos alcanzan −86°..+93° (barrido previo); margen
VARIANTS = [
    ("pkg_v14as_animstatic", "beam2ac_test14as_seat_ibiza_acfr", "static", 360.0, 0),
    ("pkg_v14hp_hands_pos", "beam2ac_test14hp_seat_ibiza_acfr", "steer", LOCK_STEER, 1),
    ("pkg_v14hn_hands_neg", "beam2ac_test14hn_seat_ibiza_acfr", "steer", LOCK_STEER, -1),
]
ANIM, DRV = "animations/steer.ksanim", "data/driver3d.ini"
ARM_BONES = {f"DRIVER:RIG_{b}_{s}" for s in FINGERS for b in ("Arm", "ForeArm", "HAND")}


def stop(msg):
    sys.exit("STOP: " + msg)


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("v13vw no coincide con su manifiesto")
if any((OUT_IC / d).exists() for d, *_ in VARIANTS):
    stop("alguna carpeta de variante ya existe")
ids = existing_car_ids(AC)
if any(i in ids for _, i, *_ in VARIANTS) and not ARGS.repro:
    stop("algún id ya existe en la instalación")
drv_old = (base / DRV).read_bytes()
if b"STEER_ANIMATION" in drv_old or (base / "animations").exists():
    stop("la base ya tiene animación")
if not drv_old.endswith(b"\r\n") or b"\n" in drv_old.replace(b"\r\n", b""):
    stop("driver3d.ini sin CRLF uniforme")
knh = Knh((base / "driver_base_pos.knh").read_bytes())
w0 = knh.worlds()
mats = [mm.name for mm in split_kn5((base / "seat_ibiza_acfr.kn5").read_bytes()).materials]
c, z, _ = rim_frame(base / "seat_ibiza_acfr.kn5", mats, "lenkrad")
c, z = [round(t, 5) for t in c], [round(t, 5) for t in z]  # como build_v12_knh.py
driver_nodes = {n.name for n in read_kn5(DRIVER_KN5, keep_vertices=False).nodes}
ae86_bones = [b for b, _ in read_ksanim(AE86_CLIP.read_bytes())[1]]  # solo nombres, para comparar
bones = clip_bones(knh)
summary = []

for folder, test_id, kind, lock, sign in VARIANTS:
    out = ensure_safe_output_path(OUT_IC / folder)
    dst = ensure_safe_output_path(out / test_id)
    new_output_dir(out)
    try:
        if kind == "static":
            clip, info = static_clip(knh), {"lock_deg": lock, "sign": 0}
        else:
            clip, info = steer_clip(knh, c, z, lock, sign)
        data = write_ksanim(clip)
        shutil.copytree(base, dst)
        if tree_hashes(dst) != before:
            raise RuntimeError("la copia no es idéntica a la base")
        (dst / "animations").mkdir()
        (dst / ANIM).write_bytes(data)
        lock_txt = f"{lock:g}"
        add = f"\r\n[STEER_ANIMATION]\r\nNAME=steer.ksanim\r\nLOCK={lock_txt}\r\n".encode()
        (dst / DRV).write_bytes(drv_old.rstrip(b"\r\n") + add)
        # --- comprobaciones
        after = tree_hashes(dst)
        diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
        if diff != [ANIM, DRV]:
            raise RuntimeError(f"diferencias inesperadas: {diff}")
        a, b = read_ini(base / DRV), read_ini(dst / DRV)
        if {k: v for k, v in b.items() if k != "STEER_ANIMATION"} != a or b["STEER_ANIMATION"] != {
            "NAME": "steer.ksanim",
            "LOCK": lock_txt,
        }:
            raise RuntimeError("driver3d.ini: cambio de claves inesperado")
        ver, back = read_ksanim((dst / ANIM).read_bytes())
        names = [n for n, _ in back]
        checks = {
            "versión 2, 100 fotogramas en todos los huesos": ver == 2
            and {len(f) for _, f in back} == {100},
            "huesos = DRIVER:* del knh, en orden": names == bones,
            "huesos existen en driver.kn5": all(n in driver_nodes for n in names),
            "mismos huesos que el clip del AE86 (solo nombres)": names == ae86_bones,
        }
        worst_static = max(
            max_error(frame_to_matrix(fr), knh.local[n])
            for n, frames in back
            if n not in ARM_BONES or kind == "static"
            for fr in frames
        )
        checks["huesos sin animar = pose del knh en todos los fotogramas (< 1e-5)"] = (
            worst_static < 1e-5
        )
        angle_err = 0.0
        if kind == "steer":
            for f in range(100):
                w = clip_worlds(knh, back, f)
                for side in FINGERS:
                    hand = f"DRIVER:RIG_HAND_{side}"
                    got = rim_angle_of(w[hand][3][:3], w0[hand][3][:3], c, z)
                    angle_err = max(angle_err, abs(got - sign * clip_angle(f, 100, lock)))
                    arm = f"DRIVER:RIG_Arm_{side}"
                    if math.dist(w[arm][3][:3], w0[arm][3][:3]) > 1e-5:
                        raise RuntimeError("el hombro se mueve")
            checks["mano gira signo·ángulo alrededor del aro (< 0,01°)"] = angle_err < 0.01
        if not all(checks.values()):
            raise RuntimeError("comprobación fallida: " + str(checks))
        if tree_hashes(base) != before:
            raise RuntimeError("la base cambió")
    except Exception as e:
        shutil.rmtree(out)
        stop(f"{folder}: {e}; carpeta nueva eliminada")
    req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini",
           "tyres.ini", "lods.ini", "setup.ini", "driver3d.ini", "aero.ini")
    vs, vr = validate_structure(dst, req), validate_references(dst, AC / "content/driver")
    manifest = {
        "test_id": test_id, "source_dir": str(base), "source_manifest": str(BASE_MAN),
        "package_dir": str(dst), "installed": False, "distributable": False,
        "purpose": ("DIAGNOSTICO animación de manos: " + (
            "clip estático que reproduce la pose de v13vw" if kind == "static"
            else f"manos giran con el aro, signo {sign:+d}, LOCK={lock_txt}")),
        "diff_vs_source": diff, "files": after,
        "clip": {**info, "bones": len(names), "frames": 100, "rim_centre": c, "rim_axis": z,
                 "max_static_error": worst_static, "max_angle_error_deg": angle_err,
                 "sha256": sha256(dst / ANIM)},
        "checks": checks,
        "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors]},
        "licence": "clip generado desde driver_base_pos.knh (derivado del AE86 de Kunos): no distribuible",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    summary.append((folder, test_id, diff, sha256(dst / ANIM)[:16], sha256(dst / DRV)[:16],
                    round(worst_static, 8), round(angle_err, 5), info.get("reach_margin_m"),
                    len(vs.errors), len(vr.errors), sha256(out / "manifest.json")[:16]))
for s in summary:
    print(s)
