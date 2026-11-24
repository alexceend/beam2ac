"""Editor de animación del piloto (2026-11-21): ajustes de las manos y vista previa.

Dos partes, ambas sobre lo que ya usa el exportador (``local_driver.local_driver_files``):

* **Ajustes** (``anim_edits``): versiones de solo añadir, con la misma clave que ``cockpit_edits``
  (huella del origen + configuración). Parámetros del ciclo de manos del perfil
  (``left``/``right`` = [a_min, grip, transfer] en grados, ``lift_m``) y ``knuckle_target_m``
  (distancia nudillo-aro, profundidad de agarre). Las caderas (altura del piloto) siguen en
  ``cockpit_edits`` (ítem ``hips``). El exportador los aplica sobre el perfil.
* **Vista previa**: la MISMA pose y el MISMO ciclo de manos que se escriben en ``steer.ksanim``
  (``build_driver_pose`` + ``hand_over_hand.pose_locals``), devueltos como posiciones de los
  huesos ``DRIVER:RIG_*`` por fotograma de un periodo del ciclo. No es la malla con piel del
  piloto (el KN5 de Kunos con pesos no se lee aquí): es el esqueleto que mueve el juego.

Marco: KN5, ejes AC (+X izquierda, +Y arriba, +Z adelante), metros.
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

from src.safe_paths import DEFAULT_OUTPUT_DIR, ensure_safe_output_path

ROOT = DEFAULT_OUTPUT_DIR / "anim_edits"
CYCLE_LIMITS = {
    "a_min": (-360.0, 360.0),
    "grip": (5.0, 360.0),
    "transfer": (5.0, 360.0),
}
LIFT_LIMITS = (0.0, 0.30)
KNUCKLE_LIMITS = (-0.10, 0.15)  # negativo = manos hacia el piloto
FRAMES_LIMITS = (8, 120)


class AnimError(ValueError):
    pass


def _num(v: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise AnimError(f"{what}: se esperaba un número finito")
    if not lo <= v <= hi:
        raise AnimError(f"{what}: {v} fuera de [{lo}, {hi}]")
    return float(v)


def clean(edits: Any) -> dict[str, Any]:
    """Valida los ajustes; solo se guardan las claves presentes."""
    if edits is None:
        return {}
    if not isinstance(edits, dict):
        raise AnimError("ajustes: se esperaba un objeto")
    unknown = set(edits) - {"left", "right", "lift_m", "knuckle_target_m"}
    if unknown:
        raise AnimError(f"claves desconocidas: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for side in ("left", "right"):
        if side in edits:
            v = edits[side]
            if not isinstance(v, (list, tuple)) or len(v) != 3:
                raise AnimError(f"{side}: se esperan [a_min, grip, transfer]")
            out[side] = [
                round(_num(x, f"{side}.{n}", *CYCLE_LIMITS[n]), 3)
                for x, n in zip(v, ("a_min", "grip", "transfer"))
            ]
    if "lift_m" in edits:
        out["lift_m"] = round(_num(edits["lift_m"], "lift_m", *LIFT_LIMITS), 4)
    if "knuckle_target_m" in edits:
        out["knuckle_target_m"] = round(
            _num(edits["knuckle_target_m"], "knuckle_target_m", *KNUCKLE_LIMITS), 4
        )
    if ("left" in out) != ("right" in out):
        raise AnimError("left y right se guardan juntas (mismo periodo)")
    if "left" in out and abs(sum(out["left"][1:]) - sum(out["right"][1:])) > 1e-6:
        raise AnimError(
            "las dos manos deben tener el mismo periodo (grip + transfer): "
            f"{sum(out['left'][1:])} frente a {sum(out['right'][1:])}"
        )
    return out


def _dir(key: str) -> Path:
    from .cockpit_edits import _KEY

    if not _KEY.match(key):
        raise AnimError("clave de ajustes no válida")
    return ROOT / key


def versions(key: str) -> list[dict[str, Any]]:
    d = _dir(key)
    return [
        json.loads(p.read_text("utf-8"))
        for p in sorted(d.glob("v[0-9]*.json"), key=lambda p: int(p.stem[1:]))
    ]


def current(key: str) -> dict[str, Any]:
    v = versions(key)
    return v[-1] if v else {"version": 0, "edits": {}}


def save(key: str, edits: Any, note: str = "") -> dict[str, Any]:
    edits = clean(edits)
    d = _dir(key)
    ensure_safe_output_path(d / "x").parent.mkdir(parents=True, exist_ok=True)
    n = current(key)["version"] + 1
    rec = {
        "schema": "beam2ac.anim_edits/1",
        "version": n,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        "edits": edits,
    }
    with open(
        ensure_safe_output_path(d / f"v{n:03d}.json"), "x", encoding="utf-8"
    ) as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    return rec


def for_project(project: Any) -> dict[str, Any]:
    from .cockpit_edits import project_key

    key = project_key(project)
    return {**current(key), "key": key}


def apply_override(profile: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    """Perfil con los ajustes aplicados (copia; el perfil original no se toca)."""
    edits = clean(edits)
    out = json.loads(json.dumps(profile))
    for k in ("left", "right", "lift_m"):
        if k in edits:
            out["hands"][k] = edits[k]
    if "knuckle_target_m" in edits:
        out["driver"]["knuckle_target_m"] = edits["knuckle_target_m"]
    return out


# ----------------------------------------------------------------------- vista previa
def _selected(names: list[str]) -> list[str]:
    return [n for n in names if n.startswith("DRIVER:RIG_")]


def preview(
    model_dir: Path,
    model: dict[str, Any],
    profile: dict[str, Any],
    ac_root: Path,
    hips: tuple[float, float, float],
    frames: int = 36,
) -> dict[str, Any]:
    """Pose del piloto en ``frames`` ángulos de volante equiespaciados de un periodo del ciclo.

    ``profile`` ya lleva los ajustes (``apply_override``). Lanza los errores del IK
    (``DriverPoseError``, ``SteerAnimError``) si las caderas o el aro no son alcanzables.
    """
    from src.ac import hands as ac_hands
    from src.ac.driver_pose import Knh, build_driver_pose
    from src.ac.hand_over_hand import HandCycle, HohParams, pose_locals, rim_angle
    from src.ac.kn5_materials import split_kn5

    if (
        not isinstance(frames, int)
        or not FRAMES_LIMITS[0] <= frames <= FRAMES_LIMITS[1]
    ):
        raise AnimError(f"frames: de {FRAMES_LIMITS[0]} a {FRAMES_LIMITS[1]}")
    steer = model.get("steering_wheel")
    if not steer:
        raise AnimError(
            "el modelo no tiene volante montado: elige el aro en la ventana «Volante»"
        )
    drv, hp = profile["driver"], profile["hands"]
    src = (
        Path(ac_root)
        / "content"
        / "cars"
        / drv["pose_source_car"]
        / "driver_base_pos.knh"
    )
    if not src.is_file():
        raise AnimError(f"no existe {src} (hace falta la instalación local de AC)")
    nd = int(drv["rim_frame_decimals"])
    kn5 = model_dir / "model.kn5"
    mats = [m.name for m in split_kn5(kn5.read_bytes()).materials]
    centre, axis = ac_hands.rim(
        kn5, mats, profile["steering_wheel"]["rim_material_token"]
    )
    with tempfile.TemporaryDirectory(prefix="beam2ac_anim_") as tmp:
        out = Path(tmp) / "driver_base_pos.knh"
        build_driver_pose(
            src,
            out,
            tuple(hips),
            tuple(round(t, nd) for t in steer["rim_centre"]),
            tuple(round(t, nd) for t in steer["rim_axis"]),
            float(drv["knuckle_target_m"]),
        )
        knh = Knh(out.read_bytes())
    w0 = knh.worlds()
    base = {s: rim_angle(w0[f"DRIVER:RIG_HAND_{s}"][3][:3], centre, axis) for s in "LR"}
    left, right = hp["left"], hp["right"]
    params = HohParams(
        HandCycle(float(left[0]), float(left[1]), float(left[2]), base["L"]),
        HandCycle(float(right[0]), float(right[1]), float(right[2]), base["R"]),
        lift_m=float(hp["lift_m"]),
    )
    names = _selected(knh.nodes)
    index = {n: i for i, n in enumerate(names)}
    parents = []
    for n in names:
        p = knh.parent[n]
        while p is not None and p not in index:
            p = knh.parent[p]
        parents.append(index[p] if p is not None else -1)
    period = params.period
    seq, margin, grips = [], math.inf, []
    for k in range(frames):
        deg = period * k / frames
        local, info = pose_locals(knh, params, deg, centre, axis)
        w = knh.worlds(local)
        seq.append([[round(c, 4) for c in w[n][3][:3]] for n in names])
        margin = min(margin, info["L"]["margin"], info["R"]["margin"])
        grips.append([bool(info["L"]["grip"]), bool(info["R"]["grip"])])
    radius = sum(math.dist(w0[f"DRIVER:RIG_HAND_{s}"][3][:3], centre) for s in "LR") / 2
    return {
        "bones": [
            {"name": n.removeprefix("DRIVER:RIG_"), "parent": p}
            for n, p in zip(names, parents)
        ],
        "frames": seq,
        "grips": grips,
        "period_deg": period,
        "wheel": {
            "centre": list(centre),
            "axis": list(axis),
            "hand_radius_m": round(radius, 4),
        },
        "hips": list(hips),
        "reach_margin_m": round(margin, 4),
        "base_angles_deg": {k: round(v, 2) for k, v in base.items()},
        "applied": {
            "left": left,
            "right": right,
            "lift_m": hp["lift_m"],
            "knuckle_target_m": drv["knuckle_target_m"],
        },
    }
