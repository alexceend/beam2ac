"""Ecuaciones reconstruidas de frenos, diferencial, embrague y caja (laboratorio).

Cada función cita su fuente. Las de BeamNG salen del Lua del juego (solo lectura); las
de AC, del análisis de AC. Todas son
puras: las entradas son explícitas y las unidades van en el nombre (``_nm`` = N·m,
``_rad_s`` = rad/s, ``_c`` = °C). Un dato ausente se pasa como ``None`` y se trata
según la fuente; si no hay fuente para un valor por defecto, se lanza ``LabInputError``
en vez de suponerlo.

Fuentes (sha256 completo en
``output/research_powerlut_preload_2026-10-26/code_evidence.json`` y en el informe de
la investigación):

* BeamNG ``lua/vehicle/wheels.lua`` (sha256 ``1eaf8b31…``): freno, codo, ABS, térmica.
* BeamNG ``lua/vehicle/powertrain/differential.lua`` (``1b53a791…``): diferenciales.
* BeamNG ``lua/vehicle/powertrain/frictionClutch.lua`` (``17c25459…``): embrague.
* BeamNG ``lua/vehicle/powertrain/manualGearbox.lua`` (``fc9089a1…``): caja.
* AC frenos de AC,
  neumático de AC, ABS de AC, ``transmisión 2WD de AC``, transmisión de AC, lectura de INI de AC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class LabInputError(ValueError):
    """Entrada ausente o inválida sin valor por defecto documentado."""


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


# --------------------------------------------------------------------------------------
# Frenos: BeamNG
# --------------------------------------------------------------------------------------

#: Coeficientes de ``thermalEfficiencyConfig`` (wheels.lua l. 81-87), copiados tal cual.
BNG_PAD_EFFICIENCY = {
    "basic": (0.018462, -0.013846, 3.0, 7.0, -0.988),
    "premium": (0.016, -0.012, 2.6, 7.3, -0.985),
    "sport": (0.019231, -0.0136, 2.0, 9.75, -0.993),
    "semi-race": (0.008462, -0.0117, 1.5, 10.75, -0.982),
    "full-race": (0.009231, -0.0115, 0.8, 12.5, -0.988),
    "carbon-ceramic": (0.016, -0.011, -0.8, 16.0, -0.996),
    "godmode": (0.007692, -0.007692, 10.0, 100.0, -1.001),
}


def bng_brake_input_factor(
    pedal: float, split: float | None, coef: float | None
) -> float:
    """Factor de entrada del freno con codo (wheels.lua l. 545/556).

    ``min(b, s) + max(b − s, 0)·k``; ``s`` y ``k`` se recortan a [0, 1] y valen 1 si
    faltan (l. 1064-1065). ``pedal`` en [0, 1].
    """
    if not 0.0 <= pedal <= 1.0:
        raise LabInputError(f"pedal fuera de [0, 1]: {pedal}")
    s = _clamp(1.0 if split is None else split, 0.0, 1.0)
    k = _clamp(1.0 if coef is None else coef, 0.0, 1.0)
    return min(pedal, s) + max(pedal - s, 0.0) * k


def bng_thermal_efficiency(temp_c: float, pad: str) -> float:
    """``calculateThermalEfficiency`` (wheels.lua l. 167-176).

    σ(z1) + σ(z2) + a, recortado a [0, 1].
    """
    if pad not in BNG_PAD_EFFICIENCY:
        raise LabInputError(f"material de pastilla desconocido: {pad!r}")
    w1, w2, b1, b2, a = BNG_PAD_EFFICIENCY[pad]

    def sig(z: float) -> float:
        return 1.0 / (1.0 + math.exp(-z))

    return _clamp(sig(w1 * temp_c + b1) + sig(w2 * temp_c + b2) + a, 0.0, 1.0)


def bng_wheel_brake_torque_nm(
    brake_torque_nm: float,
    pedal: float,
    split: float | None = None,
    coef: float | None = None,
    abs_coef: float = 1.0,
    thermal_efficiency: float = 1.0,
    parking_torque_nm: float | None = None,
    parkingbrake_input: float = 0.0,
) -> float:
    """Par de freno deseado por rueda en régimen (sin el retardo de presión).

    wheels.lua: ``max(brakeTorque·factor·absCoef, parkingTorque·parkingbrake)``
    (l. 545-547, 596) y después ``× brakeThermalEfficiency`` (l. 642). ``parkingTorque``
    ausente = 0 (l. 1051). No incluye ``frictionTorque`` de la rueda (l. 653, se suma
    aparte) ni el ``brakeSpring``.
    """
    if brake_torque_nm < 0:
        raise LabInputError("brakeTorque negativo")
    main = brake_torque_nm * bng_brake_input_factor(pedal, split, coef) * abs_coef
    park = (parking_torque_nm or 0.0) * parkingbrake_input
    return max(main, park) * thermal_efficiency


# --------------------------------------------------------------------------------------
# Frenos: Assetto Corsa
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ACBrakeInputs:
    """Claves de ``brakes.ini``. ``None`` = clave ausente ⇒ 0 (getFloat)."""

    max_torque_nm: float | None
    front_share: float | None
    handbrake_torque_nm: float | None
    limit_down: float = 0.0  # constructor BrakeSystem (sin setup.ini [FRONT_BIAS])
    limit_up: float = 1.0
    rear_correction_nm: float = 0.0  # origen no localizado: se pide explícito


def ac_wheel_brake_torques_nm(
    cfg: ACBrakeInputs,
    pedal: float,
    handbrake: float = 0.0,
    abs_override: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    brake_power_multiplier: float = 1.0,
) -> tuple[float, float, float, float]:
    """Par efectivo por rueda (FL, FR, RL, RR) en AC.

    frenos de AC: ``P = MAX_TORQUE·mult·pedal``; delantera = ``P·bias``; trasera =
    ``max(0, (1 − bias)·P − rearCorrection)``; freno de mano trasero =
    ``handbrake·HANDBRAKE_TORQUE``; ``bias`` recortado a [limitDown, limitUp].
    neumático de AC: efectivo = ``max(brakeTorque·absOverride, handBrakeTorque)``.
    Sin modelo térmico (solo con [TEMPS_FRONT] y [TEMPS_REAR]).
    """
    if not 0.0 <= pedal <= 1.0 or not 0.0 <= handbrake <= 1.0:
        raise LabInputError("pedal o freno de mano fuera de [0, 1]")
    power = (cfg.max_torque_nm or 0.0) * brake_power_multiplier * pedal
    bias = _clamp(cfg.front_share or 0.0, cfg.limit_down, cfg.limit_up)
    front = power * bias
    rear = max(0.0, (1.0 - bias) * power - cfg.rear_correction_nm)
    hb = handbrake * (cfg.handbrake_torque_nm or 0.0)
    raw = (front, front, rear, rear)
    hbs = (0.0, 0.0, hb, hb)
    fl, fr, rl, rr = (max(t * a, h) for t, a, h in zip(raw, abs_override, hbs))
    return fl, fr, rl, rr


def ac_brakes_from_bng_at_pedal(
    front_rate_nm: float,
    front_split: float | None,
    front_coef: float | None,
    rear_rate_nm: float,
    rear_split: float | None,
    rear_coef: float | None,
    pedal_ref: float,
) -> tuple[float, float]:
    """Conversión candidata (no validada): (MAX_TORQUE, FRONT_SHARE) que reproduce el
    par por rueda de BeamNG **en un único régimen de pedal** ``pedal_ref``.

    De frenos de AC: T_del = MAX·bias·p y T_tras = MAX·(1 − bias)·p ⇒
    MAX = (T_del + T_tras)/p y bias = T_del/(T_del + T_tras).
    """
    if not 0.0 < pedal_ref <= 1.0:
        raise LabInputError("pedal_ref debe estar en (0, 1]")
    tf = front_rate_nm * bng_brake_input_factor(pedal_ref, front_split, front_coef)
    tr = rear_rate_nm * bng_brake_input_factor(pedal_ref, rear_split, rear_coef)
    if tf + tr <= 0:
        raise LabInputError("par total nulo")
    return (tf + tr) / pedal_ref, tf / (tf + tr)


# --------------------------------------------------------------------------------------
# Diferencial
# --------------------------------------------------------------------------------------


def bng_lsd_side_cap_nm(
    input_torque_nm: float,
    preload_nm: float | None,
    lock_coef: float | None,
    rev_lock_coef: float | None,
    device_lock_torque_nm: float | None = None,
) -> float:
    """Tope del par de bloqueo POR SALIDA de un LSD de BeamNG.

    Fuente: differential.lua l. 53-61.

    ``lsdTorque = preload + c·|T_in|`` con ``c = lsdLockCoef`` si T_in > 0 y
    ``lsdRevLockCoef`` si T_in < 0 (recortado a ≤ 1). El muelle limita ``lockTorque`` a
    ``lsdTorque`` (vía ``maxDiffAngle``) y además a ``device.lockTorque`` (l. 61).
    Por defecto: preload 50, coef 0,2, rev = coef (l. 333-335).
    ``device_lock_torque_nm`` = None ⇒ sin segundo tope (el valor automático,
    250²·min(I), depende de inercias que el laboratorio no conoce).
    ``T_in`` es el par a la salida del diferencial (incluye ``gearRatio``), sin la
    fricción interna ni el limitador de velocidad (l. 50-51).
    """
    p = 50.0 if preload_nm is None else preload_nm
    c_fwd = 0.2 if lock_coef is None else lock_coef
    c_rev = c_fwd if rev_lock_coef is None else rev_lock_coef
    c = c_fwd if input_torque_nm > 0 else c_rev if input_torque_nm < 0 else 0.0
    c = min(c, 1.0)  # l. 55: min(…, 1); un coef negativo no se recorta en el Lua
    cap = p + c * abs(input_torque_nm)
    if device_lock_torque_nm is not None:
        cap = min(cap, device_lock_torque_nm)
    return max(cap, 0.0)


def bng_lsd_difference_cap_nm(*args: float | None, **kwargs: float | None) -> float:
    """Diferencia máxima de par entre salidas de BeamNG.

    Salidas = T·reparto ∓ lockTorque (l. 64-65) ⇒ |T1 − T2| ≤ 2·tope por salida.
    """
    return 2.0 * bng_lsd_side_cap_nm(*args, **kwargs)  # type: ignore[arg-type]


def ac_lsd_difference_cap_nm(
    engine_torque_nm: float,
    ratio_total: float,
    preload_nm: float | None,
    power: float | None,
    coast: float | None,
    clutch_engagement: float = 1.0,
) -> float:
    """Límite de |T_rueda_der − T_rueda_izq| del LSD 2WD de AC (transmisión 2WD de AC).

    ``límite = |ratio|·(POWER si T>0, si no COAST)·|T·embrague| + PRELOAD``; claves
    ausentes = 0 (getFloat). Con POWER ≥ 1 y COAST ≥ 1 el diferencial es ``Spool`` (no
    se modela aquí: ``LabInputError``).
    """
    pw = power or 0.0
    co = coast or 0.0
    if pw >= 1.0 and co >= 1.0:
        raise LabInputError("POWER ≥ 1 y COAST ≥ 1: AC usa Spool (bloqueado), no LSD")
    t = engine_torque_nm * clutch_engagement
    ramp = pw if t > 0 else co
    return abs(ratio_total) * ramp * abs(t) + (preload_nm or 0.0)


def max_axle_torque_with_low_grip_nm(
    input_torque_nm: float,
    low_wheel_limit_nm: float,
    high_wheel_limit_nm: float,
    difference_cap_nm: float,
) -> float:
    """Par total que el eje puede transmitir SIN que la rueda de poca adherencia acelere
    (equilibrio cuasiestático; modelo idealizado común a ambos juegos, no del Lua ni
    de AC).

    La rueda con poca adherencia admite como mucho ``low``; la otra recibe ``low +
    diferencia`` hasta su propio límite ``high``. Total = min(T_in, 2·low + cap,
    low + high). Es el **límite matemático**: el comportamiento real depende de la
    dinámica del muelle (BeamNG) o del umbral de deslizamiento (AC).
    """
    for v in (low_wheel_limit_nm, high_wheel_limit_nm, difference_cap_nm):
        if v < 0:
            raise LabInputError("límites negativos")
    return min(
        abs(input_torque_nm),
        2.0 * low_wheel_limit_nm + difference_cap_nm,
        low_wheel_limit_nm + high_wheel_limit_nm,
    )


# --------------------------------------------------------------------------------------
# Embrague
# --------------------------------------------------------------------------------------


def bng_clutch_default_lock_torque_nm(
    max_engine_torque_nm: float, max_rpm: float, engine_inertia_kgm2: float
) -> float:
    """``lockTorque`` por defecto (frictionClutch.lua l. 174):
    ``1,25·maxTorque + maxRPM·inercia·π/30``. ``maxTorque`` es el de ``getTorqueData``.
    """
    return 1.25 * max_engine_torque_nm + max_rpm * engine_inertia_kgm2 * math.pi / 30.0


def ac_clutch_torque_nm(
    slip_rad_s: float, pedal_engagement: float, max_torque_nm: float | None
) -> float:
    """Par del embrague de AC cuando está ABIERTO (transmisión 2WD de AC):
    ``Δω/(|Δω| + 4)·locClutch·MAX_TORQUE``; MAX_TORQUE ausente o 0 ⇒ 450
    (transmisión de AC). Con el embrague cerrado (locClutch ≥ 1 y velocidades a menos
    del 10 %), motor y ruedas van rígidos y MAX_TORQUE no limita nada."""
    mt = max_torque_nm or 450.0
    return slip_rad_s / (abs(slip_rad_s) + 4.0) * pedal_engagement * mt


def ac_clutch_is_rigid(
    pedal_engagement: float, engine_rad_s: float, root_rad_s: float
) -> bool:
    """Estado cerrado del embrague de AC (el cálculo 2WD de AC).

    locClutch ≥ 1 y |root/engine − 1| < 0,1; si ω_motor = 0, cerrado solo si root = 0.
    """
    if pedal_engagement < 1.0:
        return False
    if engine_rad_s == 0.0:
        return root_rad_s == 0.0
    return abs(root_rad_s / engine_rad_s - 1.0) < 0.1


# --------------------------------------------------------------------------------------
# Caja y par a la rueda
# --------------------------------------------------------------------------------------


def wheel_torque_nm(
    engine_torque_nm: float,
    gear_ratio: float | None,
    final_ratio: float | None,
    gearbox_friction_nm: float = 0.0,
    gearbox_loss_coef: float = 0.0,
    gearbox_dynamic_friction: float = 0.0,
    input_rad_s: float = 0.0,
) -> float:
    """Par total en el lado de la rueda.

    BeamNG (manualGearbox.lua l. 56-57): ``(T − fricción·clamp(ω,−1,1) − dyn·ω −
    pérdida·T)·relación``; el diferencial vuelve a multiplicar por su ``gearRatio``.
    AC (el cálculo 2WD de AC): ``ratio_total·T`` sin pérdidas de caja (pasar fricción y pérdidas 0).
    Una relación ausente o 0 no tiene valor por defecto válido ⇒ ``LabInputError``
    (AC leería 0: punto muerto o sin tracción).
    """
    for name, r in (
        ("relación de marcha", gear_ratio),
        ("relación final", final_ratio),
    ):
        if r is None or r == 0 or math.isnan(r):
            raise LabInputError(f"{name} ausente o 0: AC leería 0 (sin tracción)")
    friction = (
        gearbox_friction_nm * _clamp(input_rad_s, -1.0, 1.0)
        + gearbox_dynamic_friction * input_rad_s
        + gearbox_loss_coef * engine_torque_nm
    )
    assert gear_ratio is not None and final_ratio is not None
    return (engine_torque_nm - friction) * gear_ratio * final_ratio
