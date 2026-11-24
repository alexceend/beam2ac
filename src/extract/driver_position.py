"""Posición estimada del centro de masas del piloto, solo con datos de BeamNG (G2.45).

El origen (JBeam de Automation) no tiene piloto ni asiento: BeamNG simula el coche
vacío.
Para repartir la masa del piloto en ``CG_LOCATION``, ``BASEY`` e inercia explícita hace
falta
una posición. Es una ESTIMACIÓN GEOMÉTRICA, nunca una medida, en dos niveles:

1. ``camera``: la cámara de cabina (``camerasInternal`` con ``cockpitCamera: true``)
como
   ojos, si es plausible (|x| ≥ 0,15 m, dentro de la batalla y a 0,10–0,40 m bajo el
   techo).
2. ``default``: sin cámara plausible (200409, Autist y Sucette: cámara por defecto de
   Automation en x = 0), ojos en x = ``SEAT_X``, z = eje trasero +
   ``SEAT_Z_FRAC``·batalla,
   y = techo − ``ROOF_MINUS_EYES``. Las constantes son las de ``src.ac.driver_seat``
   (G2.32;
   Ibiza, Quail y Hyacinth validados en AC) y se contrastan aquí con los 7 coches que
   traen
   cámara plausible: techo − ojos 0,20 ± 0,02 m (techo = percentil 99 de la altura de
   los
   nodos; el máximo lo contaminan antenas y alerones), fracción de batalla 0,43 ± 0,04.

Del ojo al centro de masas del cuerpo sentado, en dos tramos:

* ojos → caderas: ``EYES_TO_HIPS`` = mediana de 107 coches de AC (``DRIVEREYES`` de
  ``data/car.ini`` frente a ``DRIVER:RIG_Hips`` de ``driver_base_pos.knh``; solo
  lectura,
  ``output/g245_2026-11-14/eyes_hips.py``): y −0,561 (sd 0,086), z +0,069 (sd 0,172;
  las caderas van algo POR DELANTE de los ojos: el piloto va reclinado). Es la posición
  del
  modelo de piloto de AC, no la de una persona real;
* caderas → centro de masas: ``HIPS_TO_COM`` (antropometría de una persona sentada:
  centro de masas ≈ 0,15 m sobre el punto H y ≈ 0,08 m delante por los muslos). NO sale
  del
  corpus: hipótesis.

La incertidumbre declarada es de ± 0,20 m por eje (sd de las caderas más la hipótesis);
su
efecto está acotado en ``docs/research/g245_piloto_2026-11-14.md``. La inercia propia
del
cuerpo del piloto se ignora (≈ m·0,25² ≈ 4 kg·m² frente a miles del coche).

Marco: ejes AC (x izquierda, y arriba, z adelante), relativos al CdG del vehículo
entero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.coords import beamng_to_ac
from src.jbeam import ResolvedConfig, evaluate, parse_table

Vec3 = tuple[float, float, float]

SEAT_X = 0.322  # mediana de Ibiza, Quail y Hyacinth (G2.32)
SEAT_Z_FRAC = 0.435  # fracción de la batalla desde el eje trasero (G2.32)
ROOF_MINUS_EYES = 0.205  # techo − ojos (G2.32; aquí: 0,20 ± 0,02 con el techo de nodos)
PLAUSIBLE_GAP = (0.10, 0.40)
MIN_ABS_X = 0.15
PLAUSIBLE_ZFRAC = (0.20, 0.80)
EYES_TO_HIPS: Vec3 = (0.0, -0.561, 0.069)  # mediana de 107 coches de AC (ver docstring)
HIPS_TO_COM: Vec3 = (0.0, 0.15, 0.08)  # hipótesis antropométrica (ver docstring)
EYES_TO_COM: Vec3 = (
    EYES_TO_HIPS[0] + HIPS_TO_COM[0],
    EYES_TO_HIPS[1] + HIPS_TO_COM[1],
    EYES_TO_HIPS[2] + HIPS_TO_COM[2],
)
UNCERTAINTY = 0.20  # m por eje, solo informativa (documentada, no entra en el cálculo)
ROOF_PERCENTILE = 0.99


@dataclass(frozen=True)
class DriverPosition:
    eyes: Vec3  # ejes AC, marco de los nodos
    com: Vec3  # centro de masas del piloto, mismo marco
    source: str  # "camera" | "default"
    roof_y: float
    note: str


def cockpit_camera_ac(resolved: ResolvedConfig) -> Vec3 | None:
    """Cámara con ``cockpitCamera`` en ejes AC; ``None`` si no hay exactamente una."""
    variables = resolved.variable_values()
    found: list[Vec3] = []
    for _, section in resolved.section_contributions("camerasInternal"):
        for row in parse_table(section).rows:
            if row.properties.get("cockpitCamera") is True:
                pos = tuple(evaluate(row.get(c), variables) for c in ("x", "y", "z"))
                if all(isinstance(v, (int, float)) for v in pos):
                    found.append(beamng_to_ac(pos))  # type: ignore[arg-type]
    return found[0] if len(found) == 1 else None


def roof_height(node_positions_ac: list[Vec3]) -> float:
    ys = sorted(p[1] for p in node_positions_ac)
    return ys[min(len(ys) - 1, int(len(ys) * ROOF_PERCENTILE))]


def estimate(
    camera_ac: Vec3 | None,
    node_positions_ac: list[Vec3],
    z_rear: float,
    z_front: float,
) -> DriverPosition:
    """Ojos y centro de masas del piloto. ``z_rear``/``z_front``: ejes en z AC."""
    if not node_positions_ac or not z_front > z_rear:
        raise ValueError("sin nodos o sin batalla: no se puede situar al piloto")
    roof = roof_height(node_positions_ac)
    wb = z_front - z_rear
    why = "sin cámara de cabina (cockpitCamera)"
    if camera_ac is not None:
        gap = roof - camera_ac[1]
        zf = (camera_ac[2] - z_rear) / wb
        if (
            PLAUSIBLE_GAP[0] <= gap <= PLAUSIBLE_GAP[1]
            and abs(camera_ac[0]) >= MIN_ABS_X
            and PLAUSIBLE_ZFRAC[0] <= zf <= PLAUSIBLE_ZFRAC[1]
        ):
            eyes: Vec3 = tuple(camera_ac)  # type: ignore[assignment]
            return _result(eyes, "camera", roof, "ojos = cámara de cabina de BeamNG")
        why = (
            f"cámara de cabina no plausible (x {camera_ac[0]:.3f}, techo−ojos "
            f"{gap:.3f} m, {zf:.2f} de la batalla)"
        )
    eyes = (SEAT_X, roof - ROOF_MINUS_EYES, z_rear + SEAT_Z_FRAC * wb)
    return _result(
        eyes,
        "default",
        roof,
        f"{why}: x {SEAT_X}, z = eje trasero + {SEAT_Z_FRAC}·batalla, "
        f"y = techo − {ROOF_MINUS_EYES}",
    )


def _result(eyes: Vec3, source: str, roof: float, note: str) -> DriverPosition:
    com = tuple(e + d for e, d in zip(eyes, EYES_TO_COM))
    return DriverPosition(eyes, com, source, roof, note)  # type: ignore[arg-type]


def analyse(resolved: ResolvedConfig, nodes: list[Any], z_rear: float, z_front: float):
    """Atajo para el importador: ``nodes`` = ``collect_nodes`` (ejes BeamNG)."""
    return estimate(
        cockpit_camera_ac(resolved),
        [beamng_to_ac(n.pos) for n in nodes],
        z_rear,
        z_front,
    )
