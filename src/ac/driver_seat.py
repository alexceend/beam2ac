"""Posición por defecto del piloto en un coche sin perfil propio (G2.32).

Hasta G2.31 solo el Ibiza tenía ``hips_target`` (perfil validado en el juego); en los demás
no había ``driver_base_pos.knh`` y AC colocaba el piloto en su pose por defecto: el casco
asomaba por el techo (``output/g231_2026-11-12/ac/revision_capturas.json``).

Regla (todo en el marco del KN5, ejes AC, metros; evidencia en ``docs/research/g232_*.md``):

* ``hips = DRIVEREYES + EYES_TO_HIPS``. La diferencia sale del único par validado en el juego
  (Ibiza: ojos 0,355 / 1,144 / −0,344, caderas 0,34 / 0,52 / −0,10, ``pkg_v8dp``).
* Holgura de cabeza: el hueso ``RIG_Head`` del knh de Kunos queda ``HEAD_BONE_ABOVE_HIPS`` sobre
  las caderas; en el Ibiza el techo exterior queda ``VALIDATED_ROOF_MINUS_HEAD`` sobre él. Si
  el coche tiene menos, se bajan las caderas hasta esa holgura (nunca se sube).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

Vec3 = tuple[float, float, float]

EYES_TO_HIPS: Vec3 = (-0.015, -0.624, 0.244)  # Ibiza validado: caderas − ojos
HEAD_BONE_ABOVE_HIPS = (
    0.486  # ks_toyota_ae86 driver_base_pos.knh: RIG_Head − RIG_Hips (y)
)
VALIDATED_ROOF_MINUS_HEAD = 0.320  # Ibiza: techo SOBRE las caderas (x 0,34, z −0,10: 1,3259) − hueso de la cabeza (1,006)
POSE_SOURCE_CAR = "ks_toyota_ae86"  # mismo knh base que el perfil del Ibiza


def default_hips(
    eyes: Vec3, roof_at: Callable[[float, float], float | None] | None
) -> tuple[Vec3, dict[str, Any]]:
    """Caderas por defecto a partir de los ojos; ``roof_at(x, z)`` = techo exterior en esa vertical."""
    hips = tuple(e + d for e, d in zip(eyes, EYES_TO_HIPS))
    roof = roof_at(hips[0], hips[2]) if roof_at else None
    rep: dict[str, Any] = {
        "rule": "hips = DRIVEREYES + EYES_TO_HIPS (par validado del Ibiza)",
        "roof_y": roof,
        "lowered_m": 0.0,
    }
    if roof is not None:
        limit = roof - VALIDATED_ROOF_MINUS_HEAD - HEAD_BONE_ABOVE_HIPS
        if hips[1] > limit:
            rep["lowered_m"] = round(hips[1] - limit, 4)
            hips = (hips[0], limit, hips[2])
    out = tuple(round(h, 4) for h in hips)
    rep["hips"] = list(out)
    return out, rep  # type: ignore[return-value]


def roof_function(kn5: Path) -> Callable[[float, float], float | None]:
    """``roof_at(x, z)`` sobre el KN5 (lee la carrocería una vez por franja de x)."""
    from src.ac import cameras

    cache: dict[float, list] = {}

    def at(x: float, z: float) -> float | None:
        key = round(x, 3)
        if key not in cache:
            cache[key] = cameras.body_triangles(Path(kn5), x)[1]
        return cameras.top_y(cache[key], x, z)

    return at


def roof_y_at(kn5: Path, x: float, z: float) -> float | None:
    """Y exterior del techo sobre (x, z) en el marco del KN5 (``cameras.top_y``)."""
    from src.ac import cameras

    _, tris = cameras.body_triangles(Path(kn5), x)
    return cameras.top_y(tris, x, z)


# ----------------------------------------------------- ojos del piloto sin origen fiable
# Referencias validadas (KN5, m): Ibiza, Quail, Hyacinth. x = mediana; z = fracción de la batalla
# medida desde el eje trasero (0,393 / 0,477 / 0,435); techo − ojos = 0,205 / 0,205 / 0,180.
SEAT_X = 0.322
SEAT_Z_FRAC = 0.435
ROOF_MINUS_EYES = 0.205
PLAUSIBLE_ROOF_MINUS_EYES = (0.10, 0.40)
MIN_ABS_X = 0.15


def wheel_axles_z(kn5: Path) -> tuple[float, float]:
    """(z eje trasero, z eje delantero) de los nulos WHEEL_xx del KN5."""
    from src.ac.kn5_inspect import read_kn5

    w = {
        n.name: n.translation
        for n in read_kn5(Path(kn5), keep_vertices=False).nodes
        if n.name.startswith("WHEEL_") and n.type == 1
    }
    zr = (w["WHEEL_LR"][2] + w["WHEEL_RR"][2]) / 2
    zf = (w["WHEEL_LF"][2] + w["WHEEL_RF"][2]) / 2
    return zr, zf


def resolve_eyes(kn5: Path, beam_eyes: Vec3 | None) -> tuple[Vec3, dict[str, Any]]:
    """Ojos que se escriben en DRIVEREYES: los de BeamNG si caen dentro del habitáculo; si
    faltan (200409: sin ``cockpitCamera``) o no son plausibles (p. ej. Autist: la cámara por
    defecto de Automation, x = 0 y 0,23 m sobre el suelo), una posición por defecto basada
    en las referencias validadas. Siempre editable en la interfaz."""
    roof = roof_y_at(kn5, beam_eyes[0], beam_eyes[2]) if beam_eyes else None
    if beam_eyes and roof is not None:
        gap = roof - beam_eyes[1]
        if (
            PLAUSIBLE_ROOF_MINUS_EYES[0] <= gap <= PLAUSIBLE_ROOF_MINUS_EYES[1]
            and abs(beam_eyes[0]) >= MIN_ABS_X
        ):
            return tuple(beam_eyes), {"source": "beamng", "roof_y": roof}  # type: ignore[return-value]
    zr, zf = wheel_axles_z(kn5)
    z = zr + SEAT_Z_FRAC * (zf - zr)
    top = roof_y_at(kn5, SEAT_X, z)
    if top is None:
        raise ValueError("sin techo sobre la posición por defecto del piloto")
    eyes = (SEAT_X, round(top - ROOF_MINUS_EYES, 3), round(z, 3))
    why = (
        "sin cámara de cabina"
        if beam_eyes is None
        else (
            f"cámara de BeamNG no plausible ({beam_eyes[0]:.3f}, {beam_eyes[1]:.3f}, {beam_eyes[2]:.3f}): "
            f"techo − ojos = {None if roof is None else round(roof - beam_eyes[1], 3)} m, |x| = {abs(beam_eyes[0]):.3f}"
        )
    )
    return eyes, {
        "source": "default",
        "reason": why,
        "rule": f"x {SEAT_X} (mediana de 3 coches validados); z = eje trasero + {SEAT_Z_FRAC}·batalla; "
        f"y = techo − {ROOF_MINUS_EYES}",
        "roof_y": top,
        "beamng_eyes": None if beam_eyes is None else list(beam_eyes),
    }
