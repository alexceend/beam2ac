"""G2.22: leyes de control del banco (velocidad, radio de giro, embrague) y criterios en línea.

Convenciones medidas en F0 (``output/g222_2026-11-07/f0/intento_05``), nunca supuestas:

* ``steer`` de Custom AI normalizado: 0,2 → 84,6° de volante (= 0,2·STEER_LOCK 423);
* ``steer`` positivo da guiñada (``localAngularVelocity.y``) NEGATIVA ⇒ ``YAW_SIGN = −1``:
  se controla ``y = YAW_SIGN·ψ̇``, que tiene el signo del mando;
* marcha: ``ac.StateCar.gear`` 0 = punto muerto, 1 = 1.ª (Custom AI: 1 = N, 2 = 1.ª).

Radio: ``ψ̇* = v/R``; prealimentación del volante ``δ = atan(L/R)`` en rueda →
``steer_ff = δ·|STEER_RATIO|/STEER_LOCK`` (grados), más un PI sobre la guiñada. Velocidad: PI
sobre el gas (el freno solo fuera de la ventana de medida).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

YAW_SIGN = -1.0  # F0: steer +0,2 → yaw −0,053 rad/s


@dataclass
class CarGeometry:
    steer_lock_deg: float = 423.0  # [CONTROLS] STEER_LOCK de los coches de diagnóstico
    steer_ratio: float = 12.14  # |STEER_RATIO|
    wheelbase: float = 2.50033  # suspensions.ini WHEELBASE
    limiter_rpm: float = 10000.0
    shaft_inertia: float = 1.34984994  # shaftInertia medido (AWD)

    def steer_ff(self, radius: float) -> float:
        delta = math.degrees(math.atan(self.wheelbase / radius))
        return delta * self.steer_ratio / self.steer_lock_deg


@dataclass
class PI:
    kp: float
    ki: float
    lo: float
    hi: float
    i: float = 0.0

    def reset(self) -> None:
        self.i = 0.0

    def __call__(self, err: float, dt: float, ff: float = 0.0) -> float:
        u = ff + self.kp * err + self.i
        if self.lo < u < self.hi or (u >= self.hi and err < 0) or (u <= self.lo and err > 0):
            self.i += self.ki * err * dt  # anti-saturación: solo integra si no empuja más allá
        return min(self.hi, max(self.lo, ff + self.kp * err + self.i))


@dataclass
class Circle:
    """Mantiene un círculo de radio ``radius`` en el sentido ``direction`` (+1/−1, signo del
    mando de volante)."""

    geo: CarGeometry
    radius: float
    direction: float
    pi: PI = field(default_factory=lambda: PI(0.25, 0.4, -1.0, 1.0))

    def steer(self, speed_ms: float, yaw: float, dt: float) -> float:
        target = self.direction * max(speed_ms, 0.5) / self.radius
        y = YAW_SIGN * yaw
        ff = self.direction * self.geo.steer_ff(self.radius)
        return self.pi(target - y, dt, ff)

    def radius_error(self, speed_ms: float, yaw: float) -> float:
        y = YAW_SIGN * yaw * self.direction
        if y <= 1e-3:
            return math.inf
        return speed_ms / y / self.radius - 1.0


@dataclass
class Speed:
    target_kmh: float
    pi: PI = field(default_factory=lambda: PI(0.06, 0.05, 0.0, 1.0))

    def gas(self, speed_kmh: float, dt: float) -> float:
        return self.pi(self.target_kmh - speed_kmh, dt)


def online_saturation(tel: dict, diff: str, lim: float, traction: str = "AWD",
                      min_dw: float = 0.5, dt_phys: float = 1 / 333.0, margin: float = 2.0) -> bool:
    """Criterio PRE-REGISTRADO de saturación real (``preregistro.json``): |Δω| ≥ umbral de G2.21
    y estable paso a paso, |Δω| ≥ margen × cambio por paso del bloqueo (eje ``dt·lim/I``;
    central ``dt·lim/(2I)``; ``src/lab/ac_step4wd.py``)."""
    w = tel["shaftv"] if traction == "AWD" else tel["ang"]
    inertia = 1.34984994
    if diff == "front":
        dw, step = w[0] - w[1], dt_phys * lim / inertia
    elif diff == "rear":
        dw, step = w[2] - w[3], dt_phys * lim / inertia
    elif diff == "centre":
        dw, step = 0.5 * (w[2] + w[3]) - 0.5 * (w[0] + w[1]), dt_phys * lim / (2 * inertia)
    else:
        dw, step = w[0] - w[1], dt_phys * lim / inertia
    return abs(dw) >= max(min_dw, margin * step)
