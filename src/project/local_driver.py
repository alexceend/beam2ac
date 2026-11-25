"""Piloto local del exportador (fase C1): pose sentada y manos animadas.

Reutiliza SIN cambios lo validado en el juego con v8dp, v12 y v14ha
(src.ac.assemble, pasos 3e y 3f):

* ``driver_base_pos.knh``: ``src.ac.driver_pose.build_driver_pose`` coloca las caderas
  del piloto en el asiento y las manos ante el aro del volante (IK). Parte del
  ``driver_base_pos.knh`` de un coche de Kunos de la instalación local (solo lectura).
* ``animations/steer.ksanim`` y ``[STEER_ANIMATION]`` en ``data/driver3d.ini``:
  ``src.ac.hands`` (agarre alterno ``v14ha``, aceptado por el usuario) sobre esa pose.

Causa del defecto del intento 004: el exportador omitía estos tres archivos (dependen
de un archivo de Kunos y no son distribuibles). Sin ``driver_base_pos.knh`` AC deja el
modelo del piloto en su pose por defecto, y sin ``steer.ksanim`` las manos no siguen el
volante.

Necesita el perfil del propio vehículo (``driver``, ``hands``, ``steering_wheel``), el
volante montado por la etapa ``model`` y ``--ac-root``. El resultado es SOLO LOCAL.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any


class LocalDriverError(RuntimeError):
    """Faltan datos para la pose o las manos."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hips_only_files(
    ac_root: Path, hips: tuple[float, float, float]
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Solo caderas (G2.32): ``driver_base_pos.knh`` sin perfil ni volante montado, para los
    coches sin ``hips_target`` propio. Sin manos animadas. No distribuible (knh de Kunos).
    """
    from src.ac.driver_pose import place_hips
    from src.ac.driver_seat import POSE_SOURCE_CAR

    src = Path(ac_root) / "content" / "cars" / POSE_SOURCE_CAR / "driver_base_pos.knh"
    if not src.is_file():
        raise LocalDriverError(f"no existe {src}")
    raw = src.read_bytes()
    knh, seat = place_hips(raw, hips)
    return {"driver_base_pos.knh": knh}, {
        "pose_source": str(src),
        "pose_source_sha256": _sha(raw),
        "profile": None,
        "hips_target": list(hips),
        "pose": seat,
        "hands": None,
        "distributable": False,
        "why": "deriva del driver_base_pos.knh de un coche de Kunos (instalación local)",
    }


# Ibiza (validado en AC): caderas (0,34; 0,52; −0,10) y volante con centro (0,329; 0,667; 0,384), eje (0; −0,016; 1).
# El IK de las manos exige una posición de caderas respecto al volante: se conserva ESA relación en el marco del
# volante (lateral, «arriba» perpendicular al eje, adelante = eje) para otros coches con volante montado.
IBIZA_HIPS = (0.34, 0.52, -0.10)
IBIZA_WHEEL = ((0.329, 0.667, 0.384), (0.0, -0.016, 1.0))


def _basis(axis: tuple[float, float, float]):
    def nrm(v):
        n = sum(x * x for x in v) ** 0.5
        return tuple(x / n for x in v)

    fwd = nrm(axis)
    up = nrm(
        tuple(
            y - sum(a * b for a, b in zip((0, 1, 0), fwd)) * f
            for y, f in zip((0, 1, 0), fwd)
        )
    )
    side = nrm(
        tuple(
            x
            - sum(a * b for a, b in zip((1, 0, 0), fwd)) * f
            - sum(a * b for a, b in zip((1, 0, 0), up)) * u
            for x, f, u in zip((1, 0, 0), fwd, up)
        )
    )
    return side, up, fwd


def hips_from_wheel(steer: dict[str, Any]) -> tuple[float, float, float]:
    """Caderas para un volante montado (``rim_centre``, ``rim_axis``) con la relación del Ibiza."""
    cb = _basis(IBIZA_WHEEL[1])
    off = [
        sum((h - c) * b for h, c, b in zip(IBIZA_HIPS, IBIZA_WHEEL[0], axis))
        for axis in cb
    ]
    c, ax = steer["rim_centre"], steer["rim_axis"]
    basis = _basis(tuple(ax))
    return tuple(round(c[i] + sum(off[k] * basis[k][i] for k in range(3)), 4) for i in range(3))  # type: ignore[return-value]


def generic_profile(steer: dict[str, Any]) -> dict[str, Any]:
    """Perfil mínimo para un coche SIN perfil propio pero con volante montado (detectado o elegido):
    las constantes de pose y manos son las del Ibiza (validadas allí; ``hands`` aceptadas provisionalmente), el
    aro es el material del volante montado. Las caderas las pone el exportador (``hips_override``).
    """
    from src.ac.assemble import PROFILE_DIR

    base = json.loads(
        (PROFILE_DIR / "seat_ibiza_acfr.json").read_text(encoding="utf-8")
    )
    if not steer.get("rim_material"):
        raise LocalDriverError("el volante montado no indica su material de aro")
    return {
        "driver": {**base["driver"], "hips_target": None},
        "hands": base["hands"],
        "steering_wheel": {"rim_material_token": steer["rim_material"]},
        "generic": True,
    }


def local_driver_files(
    model_dir: Path,
    model: dict[str, Any],
    ac_root: Path,
    hips_override: tuple[float, float, float] | None = None,
    generic: dict[str, Any] | None = None,
    anim_edits: dict[str, Any] | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Archivos ``{ruta: bytes}`` de la pose y las manos, y su informe. Con ``generic`` (``generic_profile``)
    no hace falta perfil del vehículo, pero sí ``hips_override``. ``anim_edits``: ajustes del editor de
    animación (``anim_preview.apply_override``); sin ellos, el perfil tal cual."""
    from src.ac import cockpit_config
    from src.ac import hands as ac_hands
    from src.ac.driver_pose import build_driver_pose
    from src.ac.kn5_materials import split_kn5
    from src.safe_paths import PROJECT_ROOT

    if generic is not None:
        if hips_override is None:
            raise LocalDriverError("el piloto genérico necesita las caderas")
        prof = {"path": None, "sha256": None, "generic": True}
        profile = generic
    else:
        prof = model.get("profile") or {}
        if not prof.get("path"):
            raise LocalDriverError("sin perfil del vehículo: no hay pose ni manos")
        path = PROJECT_ROOT / prof["path"]
        data = path.read_bytes()
        if _sha(data) != prof.get("sha256"):
            raise LocalDriverError(
                f"el perfil {prof['path']} cambió desde la etapa model"
            )
        profile = json.loads(data)
    if anim_edits:
        from .anim_preview import apply_override

        profile = apply_override(profile, anim_edits)
    steer = model.get("steering_wheel")
    for key in ("driver", "hands", "steering_wheel"):
        if not profile.get(key):
            raise LocalDriverError(f"el perfil no tiene {key!r}")
    if not steer:
        raise LocalDriverError("la etapa model no montó el volante (STEER_HR)")
    drv = profile["driver"]
    src_knh = (
        Path(ac_root)
        / "content"
        / "cars"
        / drv["pose_source_car"]
        / "driver_base_pos.knh"
    )
    if not src_knh.is_file():
        raise LocalDriverError(f"no existe {src_knh}")
    nd = int(drv["rim_frame_decimals"])
    kn5 = model_dir / "model.kn5"
    with tempfile.TemporaryDirectory(prefix="beam2ac_driver_") as tmp:
        knh_path = Path(tmp) / "driver_base_pos.knh"
        pose = build_driver_pose(
            src_knh,
            knh_path,
            tuple(hips_override or drv["hips_target"]),
            tuple(round(t, nd) for t in steer["rim_centre"]),
            tuple(round(t, nd) for t in steer["rim_axis"]),
            float(drv["knuckle_target_m"]),
        )
        knh = knh_path.read_bytes()
    mats = [m.name for m in split_kn5(kn5.read_bytes()).materials]
    centre, axis = ac_hands.rim(
        kn5, mats, profile["steering_wheel"]["rim_material_token"]
    )
    res = ac_hands.alternating_grip(knh, centre, axis, profile["hands"])
    driver3d = ac_hands.add_steer_animation(
        cockpit_config.render_driver3d_ini(), res.lock_deg
    )
    files = {
        "driver_base_pos.knh": knh,
        f"animations/{ac_hands.ANIM_NAME}": res.ksanim,
        "data/driver3d.ini": driver3d.encode("utf-8"),
    }
    report = {
        "pose_source": str(src_knh),
        "pose_source_sha256": _sha(src_knh.read_bytes()),
        "profile": prof,
        "hips_target": list(hips_override or drv["hips_target"]),
        "pose": (
            {k: v for k, v in pose.items() if k != "path"}
            if isinstance(pose, dict)
            else None
        ),
        "hands": {
            "variant": ac_hands.VARIANT,
            "lock_deg": res.lock_deg,
            "frames": res.frames,
            "reach_margin_m": res.reach_margin_m,
        },
        "distributable": False,
        "why": "deriva del driver_base_pos.knh de un coche de Kunos "
        "(instalación local)",
        "anim_edits": anim_edits or None,
    }
    return files, report
