"""v14ha: agarre alterno continuo sin cruce, sobre test14hc (no modifica nada existente).

    python tools/variants/build_v14ha.py --out-root DIR [--base-root DIR] [--repro]

Ciclo (src/ac/hand_over_hand.py, ángulos en el plano del aro vistos desde el piloto,
0° = las 9, 90° = las 12): la mano izquierda agarra entre −55° y 30°, la derecha entre
140° y 225°; cada una agarra 85° de volante y vuelve en 40° (Hermite C1, despegue de 8 cm
hacia el piloto), nunca las dos a la vez. Periodo 125° = LOCK: el fmodf de AC cae en la
misma pose. Signo +, pose de v13vw a 0°. Ninguna mano pasa de las 12: no hay cruce.
"""

import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.ac.car_validate import read_ini, validate_references, validate_structure  # noqa: E402
from src.ac.driver_pose import FINGERS, Knh, _dot, _nrm, _sub  # noqa: E402
from src.ac.hand_over_hand import AFFECTED, HandCycle, HohParams, hoh_clip, pose_locals, rim_angle  # noqa: E402
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes  # noqa: E402
from src.ac.kn5_inspect import read_kn5  # noqa: E402
from src.ac.steer_anim import ac_clip_pos, ac_pose_at, clip_worlds, frame_to_matrix, max_error, read_ksanim, write_ksanim  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
BASE_MAN = IC / "pkg_v14hc_hands_continuous/manifest.json"
OTHERS = {k: IC / f"{k}/manifest.json" for k in ("pkg_v14hp_hands_pos", "pkg_v14as_animstatic")}
OUT = OUT_IC / "pkg_v14ha_alternating_grip"
NEW_ID = "beam2ac_test14ha_seat_ibiza_acfr"
AC = Path(r"G:\SteamLibrary\steamapps\common\assettocorsa")
FRAMES = 600
ANIM, DRV = "animations/steer.ksanim", "data/driver3d.ini"


def seg_dist(p1, q1, p2, q2):
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    c, b = _dot(d1, r), _dot(d1, d2)
    den = a * e - b * b
    s = max(0, min(1, (b * f - c * e) / den)) if den > 1e-12 else 0.0
    t = max(0, min(1, (b * s + f) / e))
    s = max(0, min(1, (b * t - c) / a))
    return _nrm(_sub([p1[i] + d1[i] * s for i in range(3)], [p2[i] + d2[i] * t for i in range(3)]))


def stop(msg):
    sys.exit("STOP: " + msg)


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("test14hc no coincide con su manifiesto")
if OUT.exists():
    stop(f"ya existe {OUT}")
if NEW_ID in existing_car_ids(AC) and not ARGS.repro:
    stop("el id ya existe en la instalación")
c, z = m["clip"]["rim_centre"], m["clip"]["rim_axis"]
knh = Knh((base / "driver_base_pos.knh").read_bytes())
w0 = knh.worlds()
base_ang = {s: rim_angle(w0[f"DRIVER:RIG_HAND_{s}"][3][:3], c, z) for s in "LR"}
params = HohParams(
    HandCycle(-55.0, 85.0, 40.0, base_ang["L"]),
    HandCycle(140.0, 85.0, 40.0, base_ang["R"]),
    lift_m=0.08,
)
LOCK = params.period
drv_old = (base / DRV).read_bytes()
old_line, new_line = b"LOCK=450\r\n", f"LOCK={LOCK:g}\r\n".encode()
if drv_old.count(old_line) != 1 or not drv_old.endswith(old_line):
    stop("driver3d.ini de test14hc distinto del esperado")
clip, info = hoh_clip(knh, params, c, z, frames=FRAMES)
data = write_ksanim(clip)

new_output_dir(OUT)
dst = OUT / NEW_ID
try:
    shutil.copytree(base, dst)
    if tree_hashes(dst) != before:
        raise RuntimeError("la copia no es idéntica a test14hc")
    (dst / ANIM).write_bytes(data)
    (dst / DRV).write_bytes(drv_old[: -len(old_line)] + new_line)
    after = tree_hashes(dst)
    diffs = {"pkg_v14hc_hands_continuous": sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))}
    for k, man in OTHERS.items():
        files = json.loads(man.read_text(encoding="utf-8"))["files"]
        diffs[k] = sorted(f for f in set(after) | set(files) if after.get(f) != files.get(f))
    if any(d != [ANIM, DRV] for d in diffs.values()):
        raise RuntimeError(f"diferencias inesperadas: {diffs}")
    a, b = read_ini(base / DRV), read_ini(dst / DRV)
    a["STEER_ANIMATION"]["LOCK"] = f"{LOCK:g}"
    if a != b:
        raise RuntimeError("driver3d.ini cambia algo más que LOCK")
    ver, back = read_ksanim((dst / ANIM).read_bytes())
    names = [n for n, _ in back]
    old_names = [n for n, _ in read_ksanim((base / ANIM).read_bytes())[1]]
    driver_nodes = {n.name for n in read_kn5(AC / "content/driver/driver.kn5", keep_vertices=False).nodes}
    mid = clip_worlds(knh, back, FRAMES // 2)
    worst_other = max(
        max_error(frame_to_matrix(fr), knh.local[n]) for n, frs in back if n not in AFFECTED for fr in frs
    )
    # límites por fotograma (solver)
    margin, elbow_lo, elbow_hi, clave_hi = math.inf, 999.0, 0.0, 0.0
    for f in range(FRAMES):
        _, inf = pose_locals(knh, params, LOCK * (1 - 2 * f / FRAMES), c, z)
        for s in "LR":
            margin = min(margin, inf[s]["margin"])
            elbow_lo, elbow_hi = min(elbow_lo, inf[s]["elbow_deg"]), max(elbow_hi, inf[s]["elbow_deg"])
            clave_hi = max(clave_hi, inf[s]["clave_deg"])
    # barrido con la correspondencia exacta de AC (fmodf + interpolación), más allá de ±LOCK
    span, step = 500.0, 0.05
    depth0 = {s: _dot(_sub(w0[f"DRIVER:RIG_HAND_{s}"][3][:3], c), z) for s in "LR"}
    st = dict(hand_step=0.0, elbow_step=0.0, fore=9.0, fore_upper=9.0, hands=9.0, depth=-9.0,
              grip_err=0.0, grip_err_hold=0.0, hold_samples=0, nogrip=0, cross=0, period_err=0.0)
    prev = None
    n = int(round(2 * span / step))
    for i in range(n + 1):
        d = -span + i * step
        w = ac_pose_at(knh, back, d, LOCK)

        def g(nm):
            return w[f"DRIVER:{nm}"][3][:3]

        cur = {s: (g(f"RIG_HAND_{s}"), g(f"RIG_ForeArm_{s}")) for s in "LR"}
        if prev is not None:
            for s in "LR":
                st["hand_step"] = max(st["hand_step"], _nrm(_sub(cur[s][0], prev[s][0])))
                st["elbow_step"] = max(st["elbow_step"], _nrm(_sub(cur[s][1], prev[s][1])))
        prev = cur
        st["fore"] = min(st["fore"], seg_dist(g("RIG_ForeArm_L"), g("RIG_HAND_L"), g("RIG_ForeArm_R"), g("RIG_HAND_R")))
        st["fore_upper"] = min(st["fore_upper"],
                               seg_dist(g("RIG_ForeArm_L"), g("RIG_HAND_L"), g("RIG_Arm_R"), g("RIG_ForeArm_R")),
                               seg_dist(g("RIG_ForeArm_R"), g("RIG_HAND_R"), g("RIG_Arm_L"), g("RIG_ForeArm_L")))
        st["hands"] = min(st["hands"], _nrm(_sub(g("RIG_HAND_L"), g("RIG_HAND_R"))))
        grips = {}
        # banda donde AC mantiene el último fotograma (pos·n > n − 1, los últimos 2·LOCK/n grados)
        hold = ac_clip_pos(d, LOCK) * FRAMES > FRAMES - 1
        st["hold_samples"] += hold
        for s, cyc in (("L", params.left), ("R", params.right)):
            st["depth"] = max(st["depth"], _dot(_sub(g(f"RIG_HAND_{s}"), c), z) - depth0[s])
            ang, _, grip = cyc.state(d)
            grips[s] = grip
            if grip:  # la mano que agarra va con el aro (el ángulo esperado se pliega al periodo)
                got = rim_angle(g(f"RIG_HAND_{s}"), c, z)
                key = "grip_err_hold" if hold else "grip_err"
                st[key] = max(st[key], abs((got - ang + 180.0) % 360.0 - 180.0))
        if not (grips["L"] or grips["R"]):
            st["nogrip"] += 1
        la, ra = rim_angle(g("RIG_HAND_L"), c, z), rim_angle(g("RIG_HAND_R"), c, z) % 360.0
        if la > 90.0 or ra < 90.0:  # alguna mano pasa de las 12: cruce
            st["cross"] += 1
        if abs(d) < LOCK - 1:
            w2 = ac_pose_at(knh, back, d + LOCK if d < 0 else d - LOCK, LOCK)
            st["period_err"] = max(st["period_err"], max(_nrm(_sub(w[k][3][:3], w2[k][3][:3])) for k in w))
    checks = {
        "versión 2, 600 fotogramas en todos los huesos": ver == 2 and {len(f) for _, f in back} == {FRAMES},
        "mismos huesos y orden que test14hc": names == old_names,
        "huesos existen en driver.kn5": all(x in driver_nodes for x in names),
        "pose central = v13vw (< 1e-6)": max(max_error(mid[x], w0[x]) for x in names) < 1e-6,
        "huesos ajenos (no clavícula/brazo/antebrazo/mano) = knh en todos los fotogramas": worst_other < 1e-5,
        "siempre hay una mano agarrada": st["nogrip"] == 0,
        "la mano que agarra sigue al aro (< 0,1°) fuera de la banda final": st["grip_err"] < 0.1,
        "banda final de AC: retraso ≤ 2·LOCK/n": st["grip_err_hold"] <= 2 * LOCK / FRAMES + 0.01,
        "sin cruce (ninguna mano pasa de las 12)": st["cross"] == 0,
        "antebrazos separados ≥ 0,15 m (ejes de hueso)": st["fore"] >= 0.15 and st["fore_upper"] >= 0.15,
        "las manos no avanzan más allá de su agarre (< 1e-5 m, float32)": st["depth"] <= 1e-5,
        "continuidad: paso de mano ≤ 2 mm por 0,05° de volante": st["hand_step"] <= 0.002,
        "continuidad: paso de codo ≤ 2 mm por 0,05° de volante": st["elbow_step"] <= 0.002,
        "periodicidad: pose(deg) = pose(deg ± LOCK) (< 2 mm)": st["period_err"] < 0.002,
        "alcance ≥ 3 cm": margin >= 0.03,
        "codo entre 60° y 160°": elbow_lo >= 60 and elbow_hi <= 160,
    }
    if not all(checks.values()):
        raise RuntimeError(f"comprobación fallida: {checks} {st}")
    if tree_hashes(base) != before:
        raise RuntimeError("test14hc cambió")
except Exception as e:
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")
req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini",
       "tyres.ini", "lods.ini", "setup.ini", "driver3d.ini", "aero.ini")
vs, vr = validate_structure(dst, req), validate_references(dst, AC / "content/driver")
manifest = {
    "test_id": NEW_ID, "source_dir": str(base), "source_manifest": str(BASE_MAN), "package_dir": str(dst),
    "installed": False, "distributable": False,
    "purpose": "DIAGNOSTICO: agarre alterno continuo sin cruce (LOCK = periodo 125°, 600 fotogramas)",
    "diffs": diffs, "files": after,
    "cycle": {"left": vars(params.left), "right": vars(params.right), "lift_m": params.lift_m,
              "clave_max_deg": params.clave_max_deg, "lock_deg": LOCK, "frames": FRAMES},
    "limits": {"reach_margin_m": margin, "elbow_deg": [elbow_lo, elbow_hi], "clave_max_deg": clave_hi},
    "sweep": {"range_deg": span, "step_deg": step, **st},
    "checks": checks,
    "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors]},
    "clip_sha256": sha256(dst / ANIM),
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diffs", "limits", "sweep", "checks", "validation")},
                 indent=1, ensure_ascii=False, default=str))
print("steer.ksanim", sha256(dst / ANIM), "driver3d", sha256(dst / DRV), "manifest", sha256(OUT / "manifest.json"))
