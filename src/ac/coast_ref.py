"""``[COAST_REF]`` de Assetto Corsa: ajuste lineal del freno motor (DESTINO).

AC describe el freno motor con ``COAST_REF`` (``RPM``, ``TORQUE``, ``NON_LINEARITY``):
el comentario del ``engine.ini`` de muestra dice «coast engine brake from ZERO to
TORQUE value at rpm with linear (0) …». Con ``NON_LINEARITY = 0``:

    freno(rpm) = TORQUE · rpm / RPM

El ajuste se ANCLA en el último punto de la curva de origen que no supera el techo de
rpm (no se optimiza) y se informa del error máximo frente a esa curva:

    RPM, TORQUE = último (rᵢ, tᵢ) con rᵢ ≤ techo
    error = max |TORQUE · rᵢ / RPM − tᵢ|   sobre los puntos rᵢ ≤ techo

Función pura sin dependencias del origen. La usan ``src.ac.engine.fit_coast_ref``
(borradores) y la transformación ``ac_coast_ref`` de ``src.attrs.transforms`` (reglas
``eng.coast_*@2`` sobre ``ir.engine.coast_curve`` e ``ir.engine.speed_ceiling``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class CoastRefFit:
    rpm: float
    torque: float
    non_linearity: float
    max_abs_error: float  # N·m, frente a la curva de freno motor de origen
    worst_rpm: float
    rms_error: float = 0.0  # N·m (solo en el ajuste por mínimos cuadrados)


def coast_ref_linear(
    points: Iterable[Sequence[float]], ceiling: float
) -> CoastRefFit | None:
    """Ajuste lineal desde 0 anclado en el techo; ``None`` sin puntos ≤ techo.

    ``points``: pares ``(rpm, N·m)`` en orden de rpm creciente, tal como los da la curva
    de origen (no se reordenan: el ancla es el último punto ≤ techo, como siempre).
    """
    pts = [(r, t) for r, t in points if r <= ceiling]
    if not pts:
        return None
    ref_rpm, ref_torque = pts[-1]
    errors = [(abs(ref_torque * r / ref_rpm - t), r) for r, t in pts]
    worst, worst_rpm = max(errors)
    return CoastRefFit(ref_rpm, ref_torque, 0.0, worst, worst_rpm)


def acs_coast_brake(rpm: float, ref_rpm: float, torque: float, minimum: float) -> float:
    """Freno motor (N·m, > 0) que aplica AC con ``NON_LINEARITY = 0``.

    AC (G2.10 §2.3, ``output/g210_d_g29_decisions_2026-11-02/acs_ini/``):
    motor de AC da ``coast1 = −TORQUE/((1 − NL)·RPM − MINIMUM)`` y
    motor de AC usa ``v = (rpm − MINIMUM)·coast1`` (``v = 0`` si
    ``rpm ≤ MINIMUM``). Con NL = 0 el freno es lineal DESDE MINIMUM:

        freno(rpm) = TORQUE · (rpm − MINIMUM)/(RPM − MINIMUM)   (rpm > MINIMUM)
    """
    if rpm <= minimum:
        return 0.0
    return torque * (rpm - minimum) / (ref_rpm - minimum)


def coast_ref_from_minimum(
    points: Iterable[Sequence[float]], ceiling: float, minimum: float
) -> CoastRefFit | None:
    """Mismo ancla que :func:`coast_ref_linear` (último punto ≤ techo; NL = 0), con el
    error medido con la ley de AC (:func:`acs_coast_brake`), que es la que AC
    aplica de verdad. ``minimum`` = MINIMUM tal como lo lee AC (``getInt``: entero).
    ``None`` sin puntos ≤ techo o si el ancla no supera MINIMUM (división por cero)."""
    pts = [(r, t) for r, t in points if r <= ceiling]
    if not pts or pts[-1][0] <= minimum:
        return None
    ref_rpm, ref_torque = pts[-1]
    errors = [
        (abs(acs_coast_brake(r, ref_rpm, ref_torque, minimum) - t), r) for r, t in pts
    ]
    worst, worst_rpm = max(errors)
    return CoastRefFit(ref_rpm, ref_torque, 0.0, worst, worst_rpm)


def coast_ref_least_squares(
    points: Iterable[Sequence[float]], ceiling: float, minimum: float
) -> CoastRefFit | None:
    """Ajuste por MÍNIMOS CUADRADOS de la pendiente de la ley de AC (G2.31).

    Con ``NON_LINEARITY = 0`` el freno de AC es ``s·(rpm − MINIMUM)``
    (:func:`acs_coast_brake`). La pendiente que minimiza ``Σ (s·xᵢ − tᵢ)²`` sobre los
    puntos ``MINIMUM < rᵢ ≤ techo`` (``xᵢ = rᵢ − MINIMUM``) es
    ``s = Σ tᵢ·xᵢ / Σ xᵢ²``. Se escribe como ``RPM`` = último punto ≤ techo y
    ``TORQUE = s·(RPM − MINIMUM)`` (la ley solo depende de ``s``).

    Frente al ancla en el techo (:func:`coast_ref_from_minimum`), que da error 0 arriba
    y el máximo error en el ralentí, reparte el error en todo el rango (el error en
    MINIMUM es inevitable con cualquier pendiente). ``None`` sin puntos > MINIMUM."""
    pts = [(r, t) for r, t in points if r <= ceiling]
    use = [(r - minimum, t) for r, t in pts if r > minimum]
    if not use:
        return None
    s = sum(t * x for x, t in use) / sum(x * x for x, _t in use)
    ref_rpm = pts[-1][0]
    torque = s * (ref_rpm - minimum)
    errors = [(abs(s * x - t), x + minimum) for x, t in use]
    worst, worst_rpm = max(errors)
    rms = (sum(e * e for e, _r in errors) / len(errors)) ** 0.5
    return CoastRefFit(ref_rpm, torque, 0.0, worst, worst_rpm, rms)
