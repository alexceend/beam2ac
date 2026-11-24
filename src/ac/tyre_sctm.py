"""Modelo de neumático SCTM de Assetto Corsa (``tyres.ini`` VERSION ≥ 10), emulado
(G2.20).

Transcripción del análisis de AC (G2.17;
``output/g220_2026-11-07/acs_tyre/``) [C]:

* modelo del neumático de AC (resolución, curva pura, desplazamientos estáticos y valores por
  defecto);
* neumático de AC: lectura de las claves. Los nombres de campo del
  local ``def`` salen desplazados 8 bytes en el análisis; el mapa clave → campo se
  reconstruyó con los desplazamientos reales de ``TyreModelData`` y
  ``BrushSlipProvider``
  (``acs_tyre/tipos.txt``) y se comprobó con neumático de AC, que
  copia
  los campos a ``SCTM``;
* ``neumático de AC``: entradas del modelo (deslizamiento
longitudinal
  = (ω·r_ef − v_x)/|v_x|, deriva = ksCalcSlipAngleRAD(v_y, v_x), u = rendimiento térmico
  ×
  desgaste × presión × agarre de la pista);
* neumático de AC: carga = (RADIUS − d)·(RATE + (p − p_ref)·
  PRESSURE_SPRING_GAIN) + amortiguación DAMP; radio efectivo = RADIUS (+
  RADIUS_ANGULAR_K·|ω|).

Claves → modelo (VERSION ≥ 10)::

    FZ0                    carga de referencia (N)
    DY_REF, DX_REF         μ a FZ0 (DX_REF = 0 ⇒ 1,2·DY_REF)
    LS_EXPY, LS_EXPX       D(Fz) = D_REF·(Fz/FZ0)^(LS_EXP − 1)
    FRICTION_LIMIT_ANGLE   p0 = tan(FLA) (deslizamiento del máximo a FZ0; 0 ⇒ 7,5°)
    FLEX_GAIN              p1 = tan((1 + FLEX_GAIN)·FLA) (máximo a 2·FZ0)
    FALLOFF_LEVEL          asy: nivel asintótico tras el máximo (fracción de D)
    FALLOFF_SPEED          rapidez de la caída tras el máximo
    CX_MULT                el máximo longitudinal está en p/CX_MULT
    BRAKE_DX_MOD           Dx·(1 + valor) con κ < 0 (0 o ausente ⇒ ×1)
    SPEED_SENSITIVITY      D/(1 + SS·v_desliz)
    COMBINED_FACTOR        norma de la combinación (ausente ⇒ 2: euclídea)
    CAMBER_GAIN            deriva equivalente += sin(caída)·CAMBER_GAIN
    DCAMBER_0, DCAMBER_1   D/(1 + c0·γ − c1·γ²) (alguna 0 ⇒ 0,1 y −0,8)
    PRESSURE_FLEX_GAIN     p·(1 + …)⁻¹ con la presión relativa a PRESSURE_IDEAL

Forma de la curva normalizada (la curva pura de fuerza lateral), con x = s/p::

    f = 1 − (1 − x)³                          (s ≤ p)   [= 3x(1−x)² + 3x² − 2x³]
    f = asy + (1 − asy)/(1 + (s − p)·FALLOFF_SPEED)      (s > p)

Fuerzas (``solve``): s_x = κ/(1 + κ), s_y = tan(α + sin γ·CAMBER_GAIN)/(1 + κ),
s = ‖(s_x, s_y)‖ (norma COMBINED_FACTOR), Fy = f(s; p)·D_y·(s_y/s)·Fz,
Fx = f(s; p/CX_MULT)·D_x·(s_x/s)·Fz, con D/(1 + SS·v_desliz) y
v_desliz = √((V·sin α)² + (cos α·V·κ)²).

Nota: AC calcula en coma flotante de 32 bits; aquí se usa ``float`` de Python (64 bits).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

DEG = math.pi / 180.0


@dataclass(frozen=True)
class SctmParams:
    """Claves de ``tyres.ini`` (VERSION 10) que intervienen en las fuerzas SCTM."""

    fz0: float
    dy_ref: float
    dx_ref: float
    ls_expy: float
    ls_expx: float
    friction_limit_angle: float  # grados
    flex_gain: float = 0.0
    falloff_level: float = 1.0
    falloff_speed: float = 0.0
    cx_mult: float = 1.0
    brake_dx_mod: float = 0.0  # valor del INI (efectivo: 1 + valor; 0 ⇒ 1)
    speed_sensitivity: float = 0.0
    combined_factor: float = 2.0
    camber_gain: float = 0.0
    dcamber_0: float = 0.0
    dcamber_1: float = 0.0
    pressure_flex_gain: float = 0.0

    def with_(self, **kw: Any) -> "SctmParams":
        return replace(self, **kw)

    # ---- campos internos de SCTM (la asignación de compuesto de AC) -----------------------------------
    @property
    def max_slip0(self) -> float:
        fla = self.friction_limit_angle if self.friction_limit_angle != 0 else 7.5
        return math.tan(fla * 0.017453)

    @property
    def max_slip1(self) -> float:
        fla = self.friction_limit_angle if self.friction_limit_angle != 0 else 7.5
        return math.tan((self.flex_gain + 1.0) * fla * 0.017453)

    @property
    def brake_dx_mult(self) -> float:
        return 1.0 if self.brake_dx_mod == 0 else self.brake_dx_mod + 1.0

    @property
    def dcamber(self) -> tuple[float, float]:
        if self.dcamber_0 == 0.0 or self.dcamber_1 == 0.0:
            return 0.1, -0.8
        return self.dcamber_0, self.dcamber_1

    def ls_mult(self, d_ref: float, ls_exp: float) -> float:
        """``calcLoadSensMult``: D(FZ0) = D_REF ⇒ mult = D_REF·FZ0/FZ0^LS_EXP."""
        return d_ref * self.fz0 / (self.fz0**ls_exp)


def static_d(p: SctmParams, load: float, axis: str) -> float:
    """``getStaticDY``/``getStaticDX`` (sin tablas DY_CURVE/DX_CURVE)."""
    if load == 0.0:
        return 0.0
    if axis == "y":
        return (load**p.ls_expy) * p.ls_mult(p.dy_ref, p.ls_expy) / load
    dx_ref = p.dx_ref if p.dx_ref != 0.0 else p.dy_ref * 1.2
    return (load**p.ls_expx) * p.ls_mult(dx_ref, p.ls_expx) / load


def pure_shape(slip: float, peak: float, asy: float, falloff_speed: float) -> float:
    """neumático de AC con ``cf`` expresado por su deslizamiento de máximo ``peak``.

    Según el análisis: v5 = 2·cf·0,0064/3, v6 = 1/(v5/3) = peak."""
    v5 = 3.0 / peak
    if peak < slip:
        return (1.0 / ((slip - peak) * falloff_speed + 1.0)) * (1.0 - asy) + asy
    x = slip / peak
    return (1.0 - x) * (1.0 - x) * (v5 * slip) + (3.0 - x * 2.0) * (x * x)


def peak_slip(
    p: SctmParams,
    load: float,
    u: float = 1.0,
    grain: float = 0.0,
    pressure_ratio: float = 0.0,
) -> float:
    """Deslizamiento (combinado, en unidades de tan) del máximo lateral.

    cf = 234,375/(ms(Fz)·(1 + 0,75(u − 1)))/(1 + grain/100)·(1 + PFG·pr) y
    peak = 3/(0,0128·cf)."""
    ms = (load - p.fz0) / p.fz0 * (p.max_slip1 - p.max_slip0) + p.max_slip0
    cf = (
        (1.0 / (ms * ((u - 1.0) * 0.75 + 1.0))) * 3.0 * 78.125 / (grain * 0.01 + 1.0)
    ) * (p.pressure_flex_gain * pressure_ratio + 1.0)
    return 3.0 / (cf * 2.0 * 0.0064)


def solve(
    p: SctmParams,
    load: float,
    slip_angle: float,
    slip_ratio: float,
    camber: float = 0.0,
    speed: float = 20.0,
    u: float = 1.0,
    pressure_ratio: float = 0.0,
) -> tuple[float, float]:
    """neumático de AC → (Fy, Fx) en N. ``slip_angle`` y ``camber`` en radianes.

    Sin ampollas ni granulado (blister = grain = 0), sin DY/DX_CURVE ni DCAMBER_LUT y
    con
    ``useSimpleModel`` falso (coche del jugador)."""
    if load <= 0.0 or (slip_angle == 0.0 and slip_ratio == 0.0 and camber == 0.0):
        return 0.0, 0.0
    v8 = math.sin(camber) * p.camber_gain + slip_angle
    v9 = math.tan(v8)
    v13 = math.sin(slip_angle)
    dy = static_d(p, load, "y")
    dx = static_d(p, load, "x")
    v20 = u * dy
    v21 = u * dx
    if slip_ratio < 0.0:
        v21 *= p.brake_dx_mult
    # caída (sin tabla): signo de |γ| según el sentido de la deriva
    v23 = abs(camber)
    if (camber < 0.0 or v8 < 0.0) and (camber > 0.0 or v8 > 0.0):
        v23 = -v23
    v24 = -v23
    c0, c1 = p.dcamber
    v28 = v24 * c0 - v24 * v24 * c1
    if v28 <= -1.0:
        v28 = -0.9
    v27 = v20 + (v20 / (v28 + 1.0) - v20) * 1.0  # dCamberBlend = 1 (neumático de AC)
    v31 = math.cos(slip_angle)
    v32 = math.sqrt((speed * v13) ** 2 + (v31 * (speed * slip_ratio)) ** 2)
    v37 = v32 * p.speed_sensitivity + 1.0
    v38 = v27 / v37
    v39 = v21 / v37
    peak = peak_slip(p, load, u, 0.0, pressure_ratio)
    sr = slip_ratio if slip_ratio > -0.99998999 else -0.9999949
    v42 = sr + 1.0
    v43 = slip_ratio / v42
    v44 = v9 / v42
    cfac = p.combined_factor
    if cfac <= 0.0 or cfac == 2.0:
        slip = math.sqrt(v44 * v44 + v43 * v43)
    else:
        slip = (abs(v44) ** cfac + abs(v43) ** cfac) ** (1.0 / cfac)
    if slip == 0.0:
        return 0.0, 0.0
    asy, fs = p.falloff_level, p.falloff_speed
    fx_shape = pure_shape(slip, peak / p.cx_mult, asy, fs) * v39
    fy_shape = pure_shape(slip, peak, asy, fs)
    fx = (v43 / slip) * fx_shape * load
    fy = fy_shape * v38 * (v44 / slip) * load
    return fy, fx


def ini_values(p: SctmParams) -> dict[str, float]:
    """Las claves del INI que fija ``p`` (nombres de neumático de AC)."""
    return {
        "FZ0": p.fz0,
        "DY_REF": p.dy_ref,
        "DX_REF": p.dx_ref,
        "LS_EXPY": p.ls_expy,
        "LS_EXPX": p.ls_expx,
        "FRICTION_LIMIT_ANGLE": p.friction_limit_angle,
        "FLEX_GAIN": p.flex_gain,
        "FALLOFF_LEVEL": p.falloff_level,
        "FALLOFF_SPEED": p.falloff_speed,
        "CX_MULT": p.cx_mult,
        "BRAKE_DX_MOD": p.brake_dx_mod,
        "SPEED_SENSITIVITY": p.speed_sensitivity,
        "COMBINED_FACTOR": p.combined_factor,
        "CAMBER_GAIN": p.camber_gain,
    }
