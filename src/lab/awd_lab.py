"""Laboratorio de tracción total (G2.12): ports mínimos de AC y BeamNG, AISLADO.

Nada de producción importa este módulo (igual que el resto de ``src/lab``). Sirve para
comprobar numéricamente la lectura de los dos códigos, no para convertir:

* AC, transmisión de AC (AC) y el bloqueo de un
  diferencial de eje de ``transmisión 4WD de AC`` (ver
  ``output/g212_awd_2026-11-02/acs_awd_analisis.md``):

  - par del motor → semiejes: delanteros ``+2·s·Δω``, traseros ``+2·(1−s)·Δω``;
  - bloqueo: ``Δω_i ∓= dt·(Δ/(|Δ|+0,1))·lim/I·0,5`` con
    ``lim = PRELOAD + (POWER|COAST)·|T_eje|``.

* BeamNG, ``differential.lua`` ``LSDUpdateTorque`` (l. 42-66, sha 1b53a791…):
  ``out1 = T·A − lock``, ``out2 = T·(1−A) + lock``, ``|lock| ≤ lsdTorque`` con un muelle
  angular (``lockSpring``) cuyo ángulo se limita a ``sqrt(lsdTorque/lockSpring)``.

Las dos integraciones usan el mismo modelo de neumático (par de reacción
``−clamp(c·ω, ±F)``, coche retenido) para comparar el par que llega a la rueda con
adherencia cuando la otra patina. Es un modelo de prueba, no el neumático de ningún
juego.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def ac_engine_block_axle_torques(
    engine_torque: float,
    ratio: float,
    front_share: float,
    wheel_inertia: float,
    dt: float,
) -> tuple[float, float]:
    """Par equivalente (I·Δω/dt, por eje) que reparte UN paso de
    el bloque de aceleración de la transmisión de AC con las 4 inercias iguales
    (``loadINI`` las iguala con TYPE=AWD) y sin reacción de neumáticos.

    Δω de la raíz = dt·T/I_bloque, I_bloque = 4·I/r² (sin motor ni embrague; solo cambia
    la escala, no el reparto). Devuelve (par delantero, par trasero) en el lado de la
    rueda.
    """
    i_block = 4.0 * wheel_inertia / (ratio * ratio)
    d_root = dt * engine_torque / i_block
    acc = d_root / ratio  # el bloque de aceleración de la transmisión de AC
    d_front = 2.0 * front_share * acc
    d_rear = 2.0 * (1.0 - front_share) * acc
    t_front = 2.0 * wheel_inertia * d_front / dt
    t_rear = 2.0 * wheel_inertia * d_rear / dt
    return t_front, t_rear


def _tyre(omega: float, stiffness: float, grip: float) -> float:
    """Par de reacción del modelo de prueba (coche retenido): −clamp(c·ω, ±F)."""
    return -max(-grip, min(grip, stiffness * omega))


@dataclass(frozen=True)
class SplitMuResult:
    torque_to_grip_wheel: float  # par medio que transmite la rueda con adherencia (N·m)
    torque_to_ice_wheel: float
    slip_speed: float  # ω_hielo − ω_agarre al final (rad/s)


def ac_axle_split_mu(
    axle_torque: float,
    preload: float,
    power: float,
    wheel_inertia: float = 1.5,
    ice_grip: float = 20.0,
    grip: float = 1.0e5,
    stiffness: float = 2000.0,
    dt: float = 1.0 / 333.0,
    steps: int = 20000,
) -> SplitMuResult:
    """Un eje de AC (estructura de el cálculo 4WD de AC/el cálculo 2WD de AC): el par del eje se reparte
    por
    igual (bloque), cada rueda recibe su reacción y el bloqueo actúa con la ventana de
    0,1 rad/s. ``axle_torque`` > 0 (tracción): ``lim = preload + power·axle_torque``."""
    lim = preload + power * abs(axle_torque)
    w_ice = w_grip = 0.0
    acc_grip = acc_ice = 0.0
    n_avg = steps // 4
    for k in range(steps):
        fb_ice = _tyre(w_ice, stiffness, ice_grip)
        fb_grip = _tyre(w_grip, stiffness, grip)
        w_ice += dt * (axle_torque * 0.5 + fb_ice) / wheel_inertia
        w_grip += dt * (axle_torque * 0.5 + fb_grip) / wheel_inertia
        d = w_ice - w_grip
        corr = dt * (d / (abs(d) + 0.1)) * lim / wheel_inertia * 0.5
        w_ice -= corr
        w_grip += corr
        if k >= steps - n_avg:
            acc_grip += -fb_grip
            acc_ice += -fb_ice
    return SplitMuResult(acc_grip / n_avg, acc_ice / n_avg, w_ice - w_grip)


def beamng_lsd_split_mu(
    axle_torque: float,
    lsd_preload: float,
    lsd_lock_coef: float,
    split: float = 0.5,
    wheel_inertia: float = 1.5,
    ice_grip: float = 20.0,
    grip: float = 1.0e5,
    stiffness: float = 2000.0,
    lock_spring: float | None = None,
    dt: float = 1.0 / 2000.0,
    steps: int = 100000,
) -> SplitMuResult:
    """Un diferencial ``lsd`` de BeamNG (l. 42-66) entre dos ruedas, con la entrada ya
    en
    el lado de la rueda (``gearRatio`` incluido). ``lock_spring`` por defecto: 250²·I
    (valor automático, l. 235-238). Paso de 2 kHz (el del powertrain de BeamNG)."""
    k_spring = lock_spring if lock_spring is not None else 250.0**2 * wheel_inertia
    lock_cap = k_spring  # lockTorque = lockSpring con el cálculo automático
    lsd_torque = lsd_preload + min(lsd_lock_coef, 1.0) * abs(axle_torque)
    max_angle = math.sqrt(lsd_torque / k_spring)
    angle = 0.0
    w_ice = w_grip = 0.0  # salida 1 = hielo, salida 2 = agarre
    acc_grip = acc_ice = 0.0
    n_avg = steps // 4
    for k in range(steps):
        d = w_ice - w_grip
        angle = max(-max_angle, min(max_angle, angle + d * dt))
        lock = max(
            -lock_cap,
            min(lock_cap, angle * angle * k_spring * math.copysign(1.0, angle)),
        )
        out_ice = axle_torque * split - lock
        out_grip = axle_torque * (1.0 - split) + lock
        fb_ice = _tyre(w_ice, stiffness, ice_grip)
        fb_grip = _tyre(w_grip, stiffness, grip)
        w_ice += dt * (out_ice + fb_ice) / wheel_inertia
        w_grip += dt * (out_grip + fb_grip) / wheel_inertia
        if k >= steps - n_avg:
            acc_grip += -fb_grip
            acc_ice += -fb_ice
    return SplitMuResult(acc_grip / n_avg, acc_ice / n_avg, w_ice - w_grip)


def proposed_ac_lock(
    lsd_preload: float, lsd_lock_coef: float, side_ratio: float = 1.0
) -> tuple[float, float]:
    """Propuesta estructural (NO validada en el juego) de G2.12: ``(PRELOAD, POWER)``
    de AC
    que dan la misma diferencia máxima de par que un ``lsd`` de BeamNG.

    BeamNG: diferencia máx. = 2·lsdTorque (par de bloqueo a cada salida con signo
    opuesto). AC: diferencia máx. = lim (lim/2 a cada semieje). ⇒ lim = 2·lsdTorque.
    ``side_ratio`` lleva la precarga al lado de la rueda: 1 en un diferencial de eje
    (la entrada ya incluye el grupo final), el grupo final en el central 1:1 (su par
    está en el lado del árbol y AC suma PRELOAD a ``POWER·|T|·r``, que está en la
    rueda). POWER no cambia de escala: en los dos el término proporcional usa el par en
    la rueda.
    """
    return 2.0 * lsd_preload * side_ratio, 2.0 * lsd_lock_coef
