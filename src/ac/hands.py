"""Animación de manos del ensamblador: variante aprobada ``v14ha_alternating_grip``.

Reproduce ``tools/variants/build_v14ha.py`` (aceptada provisionalmente por el usuario):
agarre alterno sin cruce, con ``LOCK`` = periodo del ciclo y 600 fotogramas
(``src.ac.hand_over_hand.hoh_clip``). Los parámetros del ciclo son propios del coche y
vienen del perfil del vehículo (``hands``); este módulo no los cambia ni elige otra
variante.

* Marco del aro: :func:`src.ac.steer_wheel.rim_frame` sobre el KN5 con el volante ya
  montado, redondeado a 5 decimales (``build_v14_anim.py``, heredado por v14hc/v14ha).
* Ángulo base de cada mano: el de la pose de ``driver_base_pos.knh``.
* ``driver3d.ini``: se añade ``[STEER_ANIMATION] NAME=steer.ksanim LOCK=<periodo>``.

El clip deriva de ``driver_base_pos.knh`` (pose de Kunos): no es distribuible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.ac.driver_pose import Knh
from src.ac.hand_over_hand import HandCycle, HohParams, hoh_clip, rim_angle
from src.ac.steer_anim import clip_bones, clip_worlds, max_error, write_ksanim
from src.ac.steer_wheel import rim_frame

VARIANT = "v14ha_alternating_grip"
ANIM_NAME = "steer.ksanim"


class HandsError(ValueError):
    """Perfil de manos incompleto o pose incompatible."""


@dataclass(frozen=True)
class HandsResult:
    ksanim: bytes
    lock_deg: float
    frames: int
    rim_centre: tuple[float, float, float]
    rim_axis: tuple[float, float, float]
    base_angles: dict[str, float]
    reach_margin_m: float
    centre_pose_error: float


def rim(kn5_path: Path, materials: list[str], token: str, decimals: int = 5):
    c, z, _ = rim_frame(kn5_path, materials, token)
    return tuple(round(t, decimals) for t in c), tuple(round(t, decimals) for t in z)


def alternating_grip(knh_bytes: bytes, centre, axis, profile: dict) -> HandsResult:
    """Clip de agarre alterno. ``profile`` = clave ``hands`` del perfil del vehículo:
    ``variant`` (debe ser ``v14ha_alternating_grip``), ``left``/``right`` =
    [a_min, grip, transfer] en grados, ``lift_m`` y ``frames``."""
    if profile.get("variant") != VARIANT:
        raise HandsError(
            f"variante de manos {profile.get('variant')!r}: "
            f"solo está integrada {VARIANT}"
        )
    try:
        left, right = profile["left"], profile["right"]
        lift, frames = float(profile["lift_m"]), int(profile["frames"])
    except (KeyError, TypeError, ValueError) as e:
        raise HandsError(f"perfil de manos incompleto: {e}") from None
    knh = Knh(knh_bytes)
    w0 = knh.worlds()
    base = {s: rim_angle(w0[f"DRIVER:RIG_HAND_{s}"][3][:3], centre, axis) for s in "LR"}
    params = HohParams(
        HandCycle(float(left[0]), float(left[1]), float(left[2]), base["L"]),
        HandCycle(float(right[0]), float(right[1]), float(right[2]), base["R"]),
        lift_m=lift,
    )
    clip, info = hoh_clip(knh, params, centre, axis, frames=frames)
    names = [n for n, _ in clip]
    if names != clip_bones(knh):
        raise HandsError("los huesos del clip no son los del knh")
    mid = clip_worlds(knh, clip, frames // 2)
    err = max(max_error(mid[x], w0[x]) for x in names)
    if err >= 1e-6:
        raise HandsError(f"la pose central del clip no es la del knh (error {err})")
    return HandsResult(
        write_ksanim(clip),
        params.period,
        frames,
        tuple(centre),
        tuple(axis),
        base,
        info["reach_margin_m"],
        err,
    )


def add_steer_animation(driver3d_ini: str, lock_deg: float) -> str:
    """``driver3d.ini`` + ``[STEER_ANIMATION]`` (no debe existir ya)."""
    if "[STEER_ANIMATION]" in driver3d_ini.upper():
        raise HandsError("driver3d.ini ya tiene STEER_ANIMATION")
    return (
        driver3d_ini.rstrip("\n")
        + f"\n[STEER_ANIMATION]\nNAME={ANIM_NAME}\nLOCK={lock_deg:g}\n"
    )
