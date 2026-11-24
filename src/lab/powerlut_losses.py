"""Pérdidas de transmisión de BeamNG llevadas a ``power.lut`` (regla PROPUESTA, C5).

Laboratorio aislado: nada de producción lo importa y no cambia la regla aprobada
(``eng.power_lut@2``, par neto del motor).

Hechos:

* **[C] AC** no aplica pérdidas de caja ni de diferencial (``transmisión 2WD de AC``): el
  par en rueda es ``T_lut · relación_total``.
* **[C] BeamNG** (Lua de los dispositivos): cada uno resta
  ``friction·clamp(ω) + dynamicFriction·ω + torqueLossCoef·T`` en su entrada.
* **[I] Coches oficiales de AC:** ``power.lut`` ≈ 0,81–0,90 × el par declarado del
  motor (AE86 0,887; R8 LMS 0,882; Abarth 0,90 y Celica AWD 0,81 con el boost del
  wastegate). Son pérdidas de coches reales, no las de BeamNG: un factor fijo de 0,87
  no reproduce BeamNG.

Regla propuesta (exacta en la marcha de referencia ``g_ref``; error acotado en las
demás):

    T_lut(rpm) = T_neto(rpm)·(1 − c_caja) − f_caja − d_caja·ω_motor
                 − (f_dif + d_dif·ω_motor/g_ref) / g_ref

con ``f`` (friction, N·m), ``d`` (dynamicFriction, N·m·s/rad) y ``c`` (torqueLossCoef)
de la caja y del diferencial. Si ``d = c = 0`` (los coches de Automation estudiados), la
pérdida es constante: ``f_caja + f_dif/g_ref``.
"""

from __future__ import annotations

import math


def engine_side_loss(
    rpm: float,
    f_gb: float,
    f_dif: float,
    g_ref: float,
    d_gb: float = 0.0,
    d_dif: float = 0.0,
) -> float:
    w = rpm * 2 * math.pi / 60
    return f_gb + d_gb * w + (f_dif + d_dif * w / g_ref) / g_ref


def corrected_curve(
    curve,
    f_gb: float,
    f_dif: float,
    g_ref: float,
    c_gb: float = 0.0,
    d_gb: float = 0.0,
    d_dif: float = 0.0,
) -> list[list[float]]:
    out = []
    for rpm, t in curve:
        if t <= 0:
            out.append([rpm, t])  # por debajo de 0 (freno motor): sin pérdida añadida
            continue
        out.append(
            [
                rpm,
                round(
                    t * (1 - c_gb)
                    - engine_side_loss(rpm, f_gb, f_dif, g_ref, d_gb, d_dif),
                    2,
                ),
            ]
        )
    return out


def wheel_torque_error(
    t_net: float,
    f_gb: float,
    f_dif: float,
    gears: list[float],
    g_ref: float,
    final: float,
) -> list[dict]:
    """Par en rueda de BeamNG frente al de AC con la curva corregida, por marcha
    (régimen,
    solo fricciones constantes)."""
    rows = []
    for g in gears:
        bng = ((t_net - f_gb) * g - f_dif) * final
        ac = (t_net - (f_gb + f_dif / g_ref)) * g * final
        rows.append(
            {
                "gear": g,
                "bng": bng,
                "ac_corrected": ac,
                "ac_current": t_net * g * final,
                "err_corrected_pct": 100 * (ac - bng) / bng,
                "err_current_pct": 100 * (t_net * g * final - bng) / bng,
            }
        )
    return rows


# --------------------------------------------------------------------------------------
# Cadena completa (fase C6). Cada dispositivo de BeamNG resta su fricción UNA vez, en su
# propia entrada [C]: manualGearbox.lua l. 56-57, differential.lua l. 32-33, shaft.lua
# l. 18/28 (y l. 44 en los ejes de rueda, como frictionTorque de la rueda). Llevada al
# lado del motor, la fricción de un dispositivo se divide por la relación acumulada
# entre el motor y la entrada de ese dispositivo.


def chain_engine_side_loss(devices: list[dict], gear: float) -> float:
    """``devices``: dicts con ``friction`` (N·m, total del dispositivo) y
    ``ratio_before`` (relación acumulada motor → entrada del dispositivo; la marcha
    se indica con el valor especial ``"gear"`` dentro de una lista de factores)."""
    total = 0.0
    for d in devices:
        r = 1.0
        for f in d["ratio_before"]:
            r *= gear if f == "gear" else float(f)
        total += float(d["friction"]) / r
    return total


def chain_wheel_error(
    t_net: float, devices: list[dict], gears: list[float], final: float, g_ref: float
) -> list[dict]:
    """Par total en rueda de BeamNG (restando cada fricción en su sitio) frente a AC con
    ``power.lut`` corregido en ``g_ref`` y frente a AC sin corrección."""
    loss_ref = chain_engine_side_loss(devices, g_ref)
    rows = []
    for g in gears:
        bng = (t_net - chain_engine_side_loss(devices, g)) * g * final
        ac_cur = t_net * g * final
        ac_cor = (t_net - loss_ref) * g * final
        rows.append(
            {
                "gear": g,
                "bng": bng,
                "ac_current": ac_cur,
                "ac_corrected": ac_cor,
                "err_current_pct": 100 * (ac_cur - bng) / bng,
                "err_corrected_pct": 100 * (ac_cor - bng) / bng,
                "loss_engine_side_nm": chain_engine_side_loss(devices, g),
            }
        )
    return rows


# --------------------------------------------------------------------------------------
# Análisis por marcha y rpm (fase C6, continuación). Con fricciones constantes
# (dynamicFriction = torqueLossCoef = 0) la pérdida de la cadena llevada al motor es
# afín en 1/g:
#
#     L(g) = a + b/g,   a = Σ f_i  (dispositivos sin la marcha en su relación previa)
#                       b = Σ f_i / Π(factores fijos previos)  (tras la caja)
#
# y NO depende de las rpm (por encima de |ω| = 1 rad/s, donde clamp(ω, −1, 1) satura).
# Consecuencias:
#   * ``a`` se representa exactamente en power.lut (vale para todas las marchas);
#   * ``b/g`` en el lado del motor equivale a un par CONSTANTE en la rueda, b·FINAL,
#     independiente de la marcha: ninguna constante de power.lut lo reproduce en todas.
#   * Con una pérdida constante L_ref en power.lut, el error del par en rueda de AC es
#     (L(g) − L_ref)·g·FINAL en N·m (constante en rpm), y su valor relativo
#     (L(g) − L_ref)/(T(rpm) − L(g)) crece donde el par neto es bajo.


def chain_affine_loss(devices: list[dict]) -> tuple[float, float]:
    """Devuelve ``(a, b)`` con ``L(g) = a + b/g``. Solo admite dispositivos cuya
    relación previa contiene la marcha como mucho una vez."""
    a = b = 0.0
    for d in devices:
        factors = list(d["ratio_before"])
        n_gear = sum(1 for f in factors if f == "gear")
        if n_gear > 1:
            raise ValueError(f"{d.get('name')}: la marcha aparece {n_gear} veces")
        fixed = 1.0
        for f in factors:
            if f != "gear":
                fixed *= float(f)
        if n_gear:
            b += float(d["friction"]) / fixed
        else:
            a += float(d["friction"]) / fixed
    return a, b


def minimax_constant_loss(devices: list[dict], gears: list[float]) -> float:
    """Pérdida constante que minimiza el máximo error absoluto en el lado del motor:
    L(g) es monótona en g, así que es el punto medio de L(g_min) y L(g_max)."""
    losses = [chain_engine_side_loss(devices, g) for g in gears]
    return (min(losses) + max(losses)) / 2


def gear_rpm_matrix(
    curve,
    devices: list[dict],
    gears: list[float],
    final: float,
    lut_loss: float,
    rpm_min: float,
    rpm_max: float,
) -> list[dict]:
    """Par en rueda (total del eje) de BeamNG y de AC por marcha y rpm, a gas a fondo
    y en régimen (fricciones saturadas). ``curve``: par neto del motor (power.lut sin
    pérdidas); AC usa ``T − lut_loss`` (0 = curva actual). Solo filas con par neto
    mayor que la pérdida de la marcha y rpm en [rpm_min, rpm_max]."""
    rows = []
    for g in gears:
        loss_g = chain_engine_side_loss(devices, g)
        for rpm, t in curve:
            if not (rpm_min <= rpm <= rpm_max) or t <= loss_g:
                continue
            bng = (t - loss_g) * g * final
            ac = (t - lut_loss) * g * final
            rows.append(
                {
                    "gear": g,
                    "rpm": rpm,
                    "bng": bng,
                    "ac": ac,
                    "err_nm": ac - bng,
                    "err_pct": 100 * (ac - bng) / bng,
                }
            )
    return rows
