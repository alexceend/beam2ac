"""Aerodinámica por triángulos medida en BeamNG → coeficientes por eje (G2.19).

La aerodinámica por triángulos (``dragCoef``/``liftCoef``/``stallAngle`` en el JBeam) la
calcula el C++ del juego; no hay Lua ni fórmula reconstruida (G2.15 §5.3). Este módulo
solo REDUCE las medidas del banco de G2.15 (``obj:calcTotalAeroForces``,
``obj:calcTotalAeroTorque`` con viento ``obj:setWind``; registro
``beamng_aero_bench`` de ``src/importers/measurements.py``). No inventa ni ajusta
nada: cada fila de las tablas es una medida.

Convenciones (verificadas en G2.15 y G2.19):

* Marco de la carrocería del banco: (adelante, izquierda, arriba), derecho; ``F`` en N.
* ``calcTotalAeroTorque(P)`` = F × (r − P): el SIGNO OPUESTO al momento usual
  M_P = (r − P) × F (comprobado: T_A − T_B = −(B − A) × F en los 7 coches,
  ``output/g219_2026-11-06/aero/``). Aquí M = −T.
* Caso ``pitch`` = p: el coche avanza en la dirección adelante·cos p + arriba·sin p
  (``beam2acDirAero.lua`` ``setWind``). En AC, ala de AC calcula
  aoa = atan(v_arriba / v_adelante) con la velocidad del ala en el marco del coche,
  así que
  **aoa de AC = +p** [C].
* Ejes del viento de AC (ala de AC/la sustentación, [C]): arrastre a lo largo
  de −n con n = (adelante cos p, arriba sin p); la sustentación positiva (CL > 0 =
  carga) a lo largo de u_L = (adelante sin p, arriba −cos p), perpendicular a n.

Reducción de un caso (q = ½·ρ·v²):

    D = −F·n            (arrastre)
    L = F·u_L           (carga total, + hacia abajo)
    L_d: carga en el CENTRO del eje delantero tal que el momento de cabeceo respecto al
         centro del eje trasero O_t sea el medido:  L_d·((O_d − O_t) × u_L)_izq = M_izq
    L_t = L − L_d       (en O_t; el arrastre también se aplica en O_t)
    CdA = D/q, ClA_d = L_d/q, ClA_t = L_t/q                     [m²]

Un sistema de fuerzas {D en O_t, L_d en O_d, L_t en O_t} tiene la MISMA resultante y el
MISMO momento de cabeceo que el medido, para cualquier punto: es la representación
exacta del caso (salvo la fuerza lateral y los momentos de balanceo/guiñada, que se
informan).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

Vec = tuple[float, float, float]
FRONT = ("FL", "FR")
REAR = ("RL", "RR")


class AeroBenchError(ValueError):
    """Registro de banco incompleto o incoherente."""


def _v(x: Any, what: str) -> Vec:
    if not isinstance(x, (list, tuple)) or len(x) != 3:
        raise AeroBenchError(f"{what}: se esperaba un vector de 3 números")
    out = tuple(float(c) for c in x)
    if not all(math.isfinite(c) for c in out):
        raise AeroBenchError(f"{what}: valor no finito")
    return out  # type: ignore[return-value]


def _mid(a: Vec, b: Vec) -> Vec:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@dataclass(frozen=True)
class CaseCoefficients:
    v: float  # m/s
    pitch: float  # deg (= aoa de AC)
    yaw: float  # deg
    rho: float  # kg/m³
    cda: float  # m²
    cla: float  # m², + = carga
    cla_front: float  # m², carga en el centro del eje delantero
    cla_rear: float  # m², carga en el centro del eje trasero
    cya: float  # m², fuerza lateral (+ izquierda); no representada
    wheelbase: float  # m, distancia entre centros de eje en el banco


def reduce_case(case: dict[str, Any]) -> CaseCoefficients:
    """Coeficientes de un caso del banco (fórmulas en el docstring del módulo)."""
    try:
        v = float(case["v"])
        p = float(case["pitch"])
        yaw = float(case["yaw"])
        rho = float(case["rho"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AeroBenchError(f"caso incompleto: {exc}") from None
    if not (v > 0 and rho > 0):
        raise AeroBenchError(f"caso sin viento o sin densidad: v={v}, ρ={rho}")
    F = _v(case.get("F"), "F")
    axles = case.get("axles") or {}
    torque = case.get("torque") or {}
    for w in FRONT + REAR:
        if w not in axles or w not in torque:
            raise AeroBenchError(f"caso sin la rueda {w}")
    o_f = _mid(_v(axles["FL"], "FL"), _v(axles["FR"], "FR"))
    o_r = _mid(_v(axles["RL"], "RL"), _v(axles["RR"], "RR"))
    # momento usual en O_t = −(media de calcTotalAeroTorque en RL y RR): el par es afín
    # en el punto, así que la media es el par en el punto medio
    t_rl, t_rr = _v(torque["RL"], "T RL"), _v(torque["RR"], "T RR")
    m_r = tuple(-(a + b) / 2 for a, b in zip(t_rl, t_rr))
    q = 0.5 * rho * v * v
    pr = math.radians(p)
    c, s = math.cos(pr), math.sin(pr)
    n = (c, 0.0, s)
    u_l = (s, 0.0, -c)
    drag = -_dot(F, n)
    lift = _dot(F, u_l)
    r = (o_f[0] - o_r[0], o_f[1] - o_r[1], o_f[2] - o_r[2])
    arm = _cross(r, u_l)[1]
    if abs(arm) < 1e-6:
        raise AeroBenchError("brazo de cabeceo nulo (ejes coincidentes)")
    lift_f = m_r[1] / arm
    return CaseCoefficients(
        v=v,
        pitch=p,
        yaw=yaw,
        rho=rho,
        cda=drag / q,
        cla=lift / q,
        cla_front=lift_f / q,
        cla_rear=(lift - lift_f) / q,
        cya=F[1] / q,
        wheelbase=math.sqrt(_dot(r, r)),
    )


@dataclass
class AeroBenchTables:
    """Tablas frente al ángulo de ataque (deg → m²) a la velocidad del barrido."""

    reference_speed: float  # m/s del barrido de ángulos
    cda: list[list[float]]
    cla_front: list[list[float]]
    cla_rear: list[list[float]]
    speeds: list[float]  # velocidades del barrido a ángulo 0
    # máxima desviación relativa frente a la velocidad de referencia (ángulo 0)
    speed_dev_cda: float
    speed_dev_cla: float
    # máxima desviación absoluta del reparto (m²) entre velocidades (ángulo 0)
    speed_dev_split: float
    # efecto de la guiñada (no representado): máxima desviación relativa de CdA y ClA
    yaw_angles: list[float] = field(default_factory=list)
    yaw_dev_cda: float = 0.0
    yaw_dev_cla: float = 0.0
    cases: list[CaseCoefficients] = field(default_factory=list)


def aero_tables(record: dict[str, Any]) -> AeroBenchTables:
    """Tablas de un registro ``beamng_aero_bench``.

    Barrido de ángulos = casos con guiñada 0 a la velocidad en la que hay ángulos ≠ 0
    (una sola). Exige el ángulo 0 y al menos 3 ángulos distintos."""
    raw = record.get("cases")
    if not isinstance(raw, list) or not raw:
        raise AeroBenchError("registro sin casos")
    cases = [reduce_case(c) for c in raw if float(c.get("v", 0) or 0) > 0]
    sweep_speeds = {c.v for c in cases if c.pitch != 0 and c.yaw == 0}
    if len(sweep_speeds) != 1:
        raise AeroBenchError(
            f"se esperaba un barrido de ángulos a una velocidad: {sorted(sweep_speeds)}"
        )
    v_ref = sweep_speeds.pop()
    sweep = sorted(
        (c for c in cases if c.v == v_ref and c.yaw == 0), key=lambda c: c.pitch
    )
    pitches = [c.pitch for c in sweep]
    if 0.0 not in pitches or len(set(pitches)) != len(pitches) or len(pitches) < 3:
        raise AeroBenchError(f"barrido de ángulos insuficiente: {pitches}")
    zero = [c for c in cases if c.pitch == 0 and c.yaw == 0]
    ref = next(c for c in zero if c.v == v_ref)
    speeds = sorted(c.v for c in zero)

    def rel(a: float, b: float) -> float:
        return abs(a - b) / abs(b) if b else abs(a - b)

    yawed = [c for c in cases if c.yaw != 0 and c.pitch == 0 and c.v == v_ref]
    return AeroBenchTables(
        reference_speed=v_ref,
        cda=[[c.pitch, c.cda] for c in sweep],
        cla_front=[[c.pitch, c.cla_front] for c in sweep],
        cla_rear=[[c.pitch, c.cla_rear] for c in sweep],
        speeds=speeds,
        speed_dev_cda=max(rel(c.cda, ref.cda) for c in zero),
        speed_dev_cla=max(rel(c.cla, ref.cla) for c in zero),
        speed_dev_split=max(abs(c.cla_front - ref.cla_front) for c in zero),
        yaw_angles=sorted(c.yaw for c in yawed),
        yaw_dev_cda=max((rel(c.cda, ref.cda) for c in yawed), default=0.0),
        yaw_dev_cla=max((rel(c.cla, ref.cla) for c in yawed), default=0.0),
        cases=cases,
    )
