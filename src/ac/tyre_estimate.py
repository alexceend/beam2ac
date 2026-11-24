"""Estimación del neumático de AC SIN el banco de BeamNG (2026-11-16).

El rozamiento de BeamNG lo calcula el C++ del juego a partir de los coeficientes que el
JBeam pasa a ``setNode`` (``frictionCoef``, ``noLoadCoef``, ``fullLoadCoef``,
``loadSensitivitySlope``, …; el motor de BeamNG los guarda en un
registro de 88 bytes por nodo). La ley que los combina no está recuperada (ver
``docs/research/neumaticos_sin_banco_2026-11-16.md``): aquí se ESTIMAN los parámetros
SCTM de AC con una regresión entrenada con las medidas del banco de BeamNG
(``measurements/beamng_bench/tyre``) y CONGELADA en ``tyre_estimator_coefficients.json``
(con la lista y el sha256 de los registros usados; reentrenar con
``tools/analysis/tyre_estimator_train.py``).

LEY RECUPERADA del motor de BeamNG (rutina de contacto; ver el informe):
con ``N`` la carga normal del nodo, ``factor_carga = max(noLoad − pendiente·N, fullLoad)``
(lineal con tope, SIN exponencial); la fricción dinámica es
``(exp(−(v·k/velStribeck)^expStribeck)·(estática − deslizante) + deslizante)·factor_carga``.
El banco coincide con esa ley tomando ``N = Fz0/m`` con ``m`` ≈ 2,2 (ajustado: el reparto de la
carga entre nodos de contacto no es un dato del juego): ``DY_REF ≈ 1,01·factor_carga``, error
medio 1,8 % en 38 ejes. Cada parámetro usa el conjunto de regresores que mejor lo predice
dejando una familia de exportador fuera.

Lo estimado NUNCA es medida: el informe lo marca y avisa cuando los coeficientes del
neumático salen del rango de calibración.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

COEFFICIENTS = Path(__file__).with_name("tyre_estimator_coefficients.json")
# nombre de campo SctmParams -> (mínimo, máximo) de tyre_fit (se recorta la predicción)
LIMITS = {
    "dy_ref": (0.2, 4.0),
    "dx_ref": (0.2, 4.0),
    "ls_expy": (0.3, 1.2),
    "ls_expx": (0.3, 1.2),
    "friction_limit_angle": (0.5, 40.0),
    "flex_gain": (-0.95, 3.0),
    "falloff_level": (0.2, 1.0),
    "falloff_speed": (0.0, 100.0),
    "cx_mult": (0.2, 8.0),
    "brake_dx_mod": (-0.6, 1.0),
    "speed_sensitivity": (0.0, 0.1),
    "combined_factor": (1.0, 6.0),
    "camber_gain": (-1.0, 3.0),
}
WHEEL_KEYS = ("frictionCoef", "noLoadCoef", "fullLoadCoef", "loadSensitivitySlope")
# opcionales (valores por defecto de ``stage2.lua``: ``stribeckExponent or 1.75``,
# ``stribeckVelMult or 1``; el deslizante por defecto es el estático)
OPTIONAL_KEYS = {
    "slidingFrictionCoef": None,
    "stribeckExponent": 1.75,
    "stribeckVelMult": 1.0,
    # estructura del neumático (forma de la curva fuerza-deslizamiento)
    "tireWidth": 0.2,
    "pressurePSI": 30.0,
    "softnessCoef": 0.5,
    "treadCoef": 0.5,
}
# propiedades estructurales de la malla del neumático (muelles de la banda y refuerzos,
# pressureWheels): fijan rigidez de deriva y de carcasa. Valor por defecto = mediana de los
# 17 coches de la calibración (aviso si falta)
STRUCT_KEYS = {
    "wheelReinfBeamSpring": None,
    "wheelSideTransitionZone": None,
    "wheelTreadReinfBeamSpring": None,
    "wheelPeripheryBeamSpring": None,
    "wheelTreadBeamSpring": None,
}
# Rigidez de deriva de la carcasa (modelo de cepillo): el pico de la fuerza lateral se da en
# tan(α_p) = 3·μ·Fz/C_α. C_α medido (de FRICTION_LIMIT_ANGLE y DY_REF de los 38 ejes) =
# exp(3,65)·ancho^0,83·k_banda^0,73 (k_banda = wheelTreadBeamSpring): error 6,7 % dejando un
# coche fuera frente a 62 % sin modelo; con él FRICTION_LIMIT_ANGLE sale con 6,6 % de error
CORNERING = (3.65, 0.83, 0.73)  # (ln C0, exponente del ancho, exponente de k_banda)


class TyreEstimateError(ValueError):
    pass


@lru_cache(maxsize=1)
def coefficients() -> dict[str, Any]:
    return json.loads(COEFFICIENTS.read_text("utf-8"))


def _ln(value: float | None, key: str) -> float:
    """ln de una propiedad estructural; sin ella, la mediana de la calibración."""
    if value is None or value <= 0:
        value = coefficients()["struct_median"][key]
    return math.log(value)


def features(wheel: dict[str, float], fz0: float, c: float) -> dict[str, float]:
    """Regresores de un neumático (``wheel``: coeficientes de rozamiento del JBeam)."""
    nl, fl = wheel["noLoadCoef"], wheel["fullLoadCoef"]
    s, mu0 = wheel["loadSensitivitySlope"], wheel.get("frictionCoef") or 1.0
    q = c * fz0  # carga del nodo N = Fz0/m (c = 1/m)
    v = nl - s * q
    factor = max(v, fl)  # max(noLoad − pendiente·N, fullLoad)
    dl = 0.0 if v <= fl else -s * q / v  # d ln(factor)/d ln(N)
    sliding = wheel.get("slidingFrictionCoef")
    width = wheel.get("tireWidth") or OPTIONAL_KEYS["tireWidth"]
    k_tread = wheel.get("wheelTreadBeamSpring")
    if k_tread is None or k_tread <= 0:
        k_tread = coefficients()["struct_median"]["wheelTreadBeamSpring"]
    c_alpha = math.exp(CORNERING[0]) * width ** CORNERING[1] * k_tread ** CORNERING[2]
    fla0 = math.degrees(math.atan(3 * mu0 * factor * fz0 / c_alpha))
    return {
        "fla0": fla0,
        "ltread": math.log(k_tread),
        "lmu": math.log(max(mu0 * factor, 1e-9)),
        "dl": dl,
        "nl": nl,
        "fl": fl,
        "ratio": fl / nl if nl else 0.0,
        "slope": s * 1e4,
        "sl": (sliding if sliding is not None else mu0) / mu0,
        "se": wheel.get("stribeckExponent") or OPTIONAL_KEYS["stribeckExponent"],
        "sv": wheel.get("stribeckVelMult") or OPTIONAL_KEYS["stribeckVelMult"],
        "lreinf": _ln(wheel.get("wheelReinfBeamSpring"), "wheelReinfBeamSpring"),
        "ltz": _ln(wheel.get("wheelSideTransitionZone"), "wheelSideTransitionZone"),
        "ltrr": _ln(
            wheel.get("wheelTreadReinfBeamSpring"), "wheelTreadReinfBeamSpring"
        ),
        "lper": _ln(wheel.get("wheelPeripheryBeamSpring"), "wheelPeripheryBeamSpring"),
        "width": wheel.get("tireWidth") or OPTIONAL_KEYS["tireWidth"],
        "psi": wheel.get("pressurePSI") or OPTIONAL_KEYS["pressurePSI"],
        "soft": wheel.get("softnessCoef", OPTIONAL_KEYS["softnessCoef"]),
        "tread": wheel.get("treadCoef", OPTIONAL_KEYS["treadCoef"]),
    }


def range_warnings(wheel: dict[str, float], fz0: float) -> list[str]:
    """Avisos si el neumático sale del rango (± 10 %) de los usados en la calibración."""
    cal = coefficients()["calibration_range"]
    vals = {**{k: wheel[k] for k in WHEEL_KEYS}, "reference_load": fz0}
    out = []
    for k, v in vals.items():
        lo, hi = cal[k]
        if not (lo * 0.9 <= v <= hi * 1.1):
            out.append(
                f"{k}={v:.4g} fuera del rango de calibración [{lo:.4g}, {hi:.4g}]"
            )
    ratio = wheel["fullLoadCoef"] / wheel["noLoadCoef"]
    families = coefficients()["calibration_ratio_families"]
    if not any(abs(ratio - r) <= 0.08 for r in families):
        out.append(
            f"fullLoadCoef/noLoadCoef={ratio:.3f}: familia de neumático sin medidas "
            f"(calibrada con {families})"
        )
    return out


def estimate_sctm(wheel: dict[str, float], fz0: float) -> dict[str, float]:
    """Parámetros SCTM estimados (campos de ``SctmParams``)."""
    cal = coefficients()
    f = features(wheel, fz0, cal["node_load_fraction"])
    out: dict[str, float] = {}
    for name, m in cal["models"].items():
        v = m["intercept"] + sum(m["coef"][k] * f[k] for k in m["features"])
        if m.get("log"):
            v = math.exp(v)
        lo, hi = LIMITS[name]
        out[name] = min(max(v, lo), hi)
    return out


def estimate_axle(
    wheel: dict[str, float], fz0: float, radius: float, hub_radius: float,
    pressure_psi: float | None, width: float, rotating_mass: float,
) -> dict[str, Any]:  # fmt: skip
    """Todos los campos que el banco daba por eje (reference_load lo calcula el llamante)."""
    cal = coefficients()
    aux = cal["aux"]
    # huella de contacto neumática: Fz = p·w·2·√(2·r·δ) ⇒ k = dFz/dδ = 4·r·p²·w²/Fz;
    # un solo factor ajustado (RATE medido ≈ 0,79·k; error 14 % dejando un coche fuera)
    p_pa = (pressure_psi or 0.0) * 6894.76
    stiffness = (
        aux["vertical_stiffness"]["scale"] * 4 * radius * p_pa**2 * width**2 / fz0
    )
    return {
        "sctm": estimate_sctm(wheel, fz0),
        "free_rolling_radius": radius * aux["free_rolling_radius_ratio"],
        "vertical_stiffness": max(stiffness, 1e4),
        "rotational_inertia": aux["inertia_factor"] * rotating_mass * radius**2,
        "rolling_resistance": aux["rolling_resistance"],
        "warnings": range_warnings(wheel, fz0)
        + [
            f"{k} no disponible: se usa la mediana de la calibración"
            for k in STRUCT_KEYS
            if wheel.get(k) is None
        ],
        "calibration": {
            "version": cal["version"],
            "records": len(cal["records"]),
            "validation": cal["validation"],
        },
    }
