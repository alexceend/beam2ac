"""``[COAST_SETTINGS]`` de Assetto Corsa: desfase de gas en retención (DESTINO, G2.33, EXPERIMENTAL).

Ley de AC (motor de AC l. 317-376, los ajustes de freno motor de AC, motor de AC; analizado
en ``output/g210_d_g29_decisions_2026-11-02/acs_ini/`` y ``output/g227_2026-11-10/acs/c/``). Con
``gasCoastOffset = o > 0`` (``[COAST_SETTINGS] LUT`` en la posición ``DEFAULT``) y ``coastEntryRpm = MINIMUM +
ACTIVATION_RPM``:

    g(rpm)   = o · clamp((rpm − MINIMUM) / coastEntryRpm, 0, 1)
    gas_eff  = (1 − g)·gas + g                      (gas ∈ [0, 1])
    v15      = −s·(rpm − MINIMUM)                   (freno de ``[COAST_REF]``, rpm > MINIMUM; 0 si no)
    T_neto   = (Value − v15)·gas_eff + v15          (Value = power.lut a plena carga)

con gas = 0 el freno vale  ``B(rpm) = (1 − g)·s·(rpm − MINIMUM) − g·Value(rpm)``: el desfase SOLO puede
quitar freno (``gasCoastOffset > 0``) y se anula en MINIMUM, así que no crea freno en el ralentí. Lo que sí
permite es subir la pendiente ``s`` cerca de MINIMUM y recortar el exceso arriba con ``g·Value``.

Efecto secundario (hecho [C], no medido en el juego): ``gas_eff`` sustituye al pedal para TODA posición
(``p → p + g·(1 − p)``), no solo al soltarlo.

Elección de parámetros (sin tocar nada del origen): ``ACTIVATION_RPM = techo − 2·MINIMUM`` ⇒ la rampa llega
a 1 justo en el techo y ``o`` es el gas mínimo en el techo; para cada ``o`` la pendiente óptima es cerrada
(mínimos cuadrados lineales); se busca ``o`` en una rejilla acotada por ``max_throttle_offset``. Solo biblioteca
estándar. No lo usa ninguna regla del catálogo: ver ``docs/research/g233_coast_settings_2026-11-12.md``.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Sequence

Points = Sequence[Sequence[float]]


def interp(points: Points, x: float) -> float:
    """Interpolación lineal de curva de AC (extremos constantes)."""
    xs = [p[0] for p in points]
    if x <= xs[0]:
        return float(points[0][1])
    if x >= xs[-1]:
        return float(points[-1][1])
    i = bisect.bisect_right(xs, x)
    (x0, y0), (x1, y1) = points[i - 1], points[i]
    return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))


def throttle_floor(
    rpm: float, offset: float, minimum: float, entry_rpm: float
) -> float:
    """``g(rpm)``: gas mínimo que impone ``gasCoastOffset`` (0 en MINIMUM, ``offset`` desde entry_rpm)."""
    if offset <= 0:
        return 0.0
    return offset * min(1.0, max(0.0, (rpm - minimum) / entry_rpm))


def coast_brake(
    rpm: float,
    slope: float,
    offset: float,
    minimum: float,
    entry_rpm: float,
    power: Points,
) -> float:
    """Freno motor (N·m, > 0 = frena) con el pedal suelto: ``(1 − g)·s·x − g·Value`` (0 si rpm ≤ MINIMUM)."""
    if rpm <= minimum:
        return 0.0
    g = throttle_floor(rpm, offset, minimum, entry_rpm)
    return (1 - g) * slope * (rpm - minimum) - g * interp(power, rpm)


def decay_time(brake, inertia: float, r0: float, r1: float, n: int = 3000) -> float:
    """Tiempo (s) de ``r0`` a ``r1`` rpm en punto muerto: ``dt = I·dω/B(r)`` (modelo de G2.31)."""
    k = 30 / math.pi
    dr = (r0 - r1) / n
    return sum(
        inertia * (dr / k) / max(brake(r0 - (i + 0.5) * dr), 1e-9) for i in range(n)
    )


@dataclass(frozen=True)
class CoastSettingsFit:
    offset: float  # o = gas mínimo en el techo (valor de la LUT)
    slope: float  # s (N·m/rpm sobre MINIMUM)
    ref_rpm: float  # [COAST_REF] RPM
    ref_torque: float  # [COAST_REF] TORQUE = s·(RPM − MINIMUM)
    activation_rpm: int  # [COAST_SETTINGS] ACTIVATION_RPM
    rms_error: float  # N·m frente a la curva de origen
    max_abs_error: float
    rms_baseline: float  # mismo error con o = 0 (mínimos cuadrados de G2.31)


def fit_coast_settings(
    coast: Points,
    power: Points,
    minimum: float,
    ceiling: float,
    max_throttle_offset: float,
    step: float = 0.005,
) -> CoastSettingsFit | None:
    """Mejor ``(o, s)`` con ``0 < o ≤ max_throttle_offset``; ``None`` si ``o = 0`` es el mínimo (sin mejora),
    si ``techo − 2·MINIMUM < 0`` o si algún punto quedara sin freno (la ley no puede dar freno negativo).

    ``coast``: ``(rpm, N·m > 0)`` de la curva de origen; ``power``: ``power.lut`` (``Value``); ``minimum`` ya
    truncado como lo lee AC (``getInt``). Sin umbral de mejora: gana el mínimo estricto del error cuadrático medio.
    """
    minimum = float(int(minimum))
    act = int(round(ceiling)) - 2 * int(minimum)
    if act < 0 or ceiling <= minimum:
        return None
    entry = minimum + act
    pts = [(r, t) for r, t in coast if minimum < r <= ceiling]
    if not pts:
        return None
    xs = [r - minimum for r, _ in pts]
    vals = [interp(power, r) for r, _ in pts]

    def solve(o: float) -> tuple[float, float, float]:
        g = [throttle_floor(r, o, minimum, entry) for r, _ in pts]
        a = [(1 - gi) * x for gi, x in zip(g, xs)]
        if not any(a):
            return 0.0, float("inf"), float("inf")
        s = sum(ai * (t + gi * v) for ai, gi, v, (_, t) in zip(a, g, vals, pts)) / sum(
            ai * ai for ai in a
        )
        err = [s * ai - gi * v - t for ai, gi, v, (_, t) in zip(a, g, vals, pts)]
        return (
            s,
            math.sqrt(sum(e * e for e in err) / len(err)),
            max(abs(e) for e in err),
        )

    s0, rms0, _ = solve(0.0)
    best: tuple[float, float, float, float, float] | None = None
    n = int(round(max_throttle_offset / step))
    for k in range(1, n + 1):
        o = k * step
        s, rms, mx = solve(o)
        if s <= 0 or any(
            coast_brake(r, s, o, minimum, entry, power) <= 0 for r, _ in pts
        ):
            continue
        if best is None or rms < best[2]:
            best = (o, s, rms, mx, k)
    if best is None or best[2] >= rms0:
        return None
    o, s, rms, mx, _ = best
    ref_rpm = pts[-1][0]
    torque = round(s * (ref_rpm - minimum), 2)
    return CoastSettingsFit(
        o, torque / (ref_rpm - minimum), ref_rpm, torque, act, rms, mx, rms0
    )


def render_lut(offset: float) -> str:
    """``coast_settings.lut``: una fila ⇒ curva de AC devuelve ``offset`` para cualquier índice."""
    return f"0|{offset:g}\n"


def render_engine_ini_sections(
    fit: CoastSettingsFit, lut_name: str = "coast_settings.lut"
) -> str:
    return (
        "[COAST_SETTINGS]\n"
        f"LUT={lut_name}\n"
        "DEFAULT=0\n"
        f"ACTIVATION_RPM={fit.activation_rpm}\n"
    )
