"""Frenos y neumáticos de la configuración activa (fase 5).

Frenos — semántica tomada de los comentarios de la propia muestra
(``camso_suspension_F/R_35b84.jbeam``):

* ``brakeTorque``: "Rate of brake torque (Nm) increase per input (input is 0 to 1).
  If there is no knee point, it also indicates the maximum brake torque."
* ``brakeInputSplit``: "Creates a knee point in brake torque rate".
* ``brakeSplitCoef``: "Reduces or increases the brake torque rate after the knee
  point".

⇒ T(e) = bt·min(e, s) + bt·c·max(0, e − s), con e la entrada (0–1); sin codo
(s ausente o 1) T(1) = bt. [derivado de esos comentarios]

Neumáticos: geometría y presión leídas; los coeficientes de rozamiento de BeamNG
se leen pero NO se convierten (modelos incompatibles).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.jbeam import ResolvedConfig, evaluate

from .components import collect_wheel_props, information_name

INCH = 0.0254
TYRE_LABEL = re.compile(r"\b(\d{3})/(\d{2})/(\d{2})\b")  # "215/45/17" en etiquetas
FRICTION_KEYS = (
    "frictionCoef",
    "slidingFrictionCoef",
    "noLoadCoef",
    "fullLoadCoef",
    "loadSensitivitySlope",
    "softnessCoef",
    "treadCoef",
    "stribeckExponent",
    "stribeckVelMult",
)


class BrakeTyreDataError(ValueError):
    """Datos de freno o neumático no soportados o incompletos."""


def brake_torque(
    rate: float, split: float | None, coef: float | None, e: float
) -> float:
    """Par de freno (Nm) para una entrada ``e`` (0–1)."""
    if not 0 <= e <= 1:
        raise BrakeTyreDataError(f"entrada de freno fuera de rango: {e}")
    s = 1.0 if split is None else split
    c = 1.0 if coef is None else coef
    if not 0 < s <= 1:
        raise BrakeTyreDataError(f"brakeInputSplit fuera de (0, 1]: {s}")
    return rate * min(e, s) + rate * c * max(0.0, e - s)


@dataclass(frozen=True)
class WheelBrake:
    wheel: str
    rate: float  # brakeTorque leído (Nm por unidad de entrada)
    input_split: float | None
    split_coef: float | None
    max_torque: float  # derivado, entrada 1
    parking_torque: float | None  # leído
    raw: dict[str, Any]  # diámetro, masa, tipo, material, ABS…


@dataclass(frozen=True)
class TyreNominal:
    width_mm: int
    aspect: int
    rim_in: int

    @property
    def radius(self) -> float:
        return self.width_mm * self.aspect / 100 / 1000 + self.rim_in * INCH / 2

    @property
    def rim_radius(self) -> float:
        return self.rim_in * INCH / 2


@dataclass(frozen=True)
class WheelTyre:
    wheel: str
    radius: float  # m, radio de los nodos del neumático (sin carga)
    hub_radius: float  # m
    width: float | None  # m (tireWidth)
    pressure_psi: float | None
    label: str | None  # etiqueta de la pieza que fija "radius"
    nominal: TyreNominal | None  # interpretado de la etiqueta (solo contraste)
    friction_raw: dict[str, Any]


@dataclass
class BrakesTyres:
    brakes: list[WheelBrake]
    tyres: list[WheelTyre]
    issues: list[str] = field(default_factory=list)


def _num(value: Any, what: str, required: bool = True) -> float | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrakeTyreDataError(f"{what}: se esperaba un número, no {value!r}")
    return float(value)


def build_brakes_tyres(resolved: ResolvedConfig) -> BrakesTyres:
    variables = resolved.variable_values()
    issues: list[str] = []
    brakes, tyres = [], []
    for wp in collect_wheel_props(resolved):

        def ev(key, _wp=wp):
            return evaluate(_wp.props[key], variables) if key in _wp.props else None

        where = f"rueda {wp.name}"
        rate = _num(ev("brakeTorque"), f"{where}.brakeTorque", required=False)
        if rate is None:
            issues.append(f"{where}: sin brakeTorque")
        else:
            split = _num(ev("brakeInputSplit"), f"{where}.brakeInputSplit", False)
            coef = _num(ev("brakeSplitCoef"), f"{where}.brakeSplitCoef", False)
            brakes.append(
                WheelBrake(
                    wheel=wp.name,
                    rate=rate,
                    input_split=split,
                    split_coef=coef,
                    max_torque=brake_torque(rate, split, coef, 1.0),
                    parking_torque=_num(
                        ev("parkingTorque"), f"{where}.parkingTorque", False
                    ),
                    raw={
                        k: ev(k)
                        for k in (
                            "brakeDiameter",
                            "brakeMass",
                            "brakeType",
                            "rotorMaterial",
                            "absSlipRatioTarget",
                            "brakeSpring",
                            # G2.15: modelo térmico y ABS (wheels.lua)
                            "enableBrakeThermals",
                            "padMaterial",
                            "enableABS",
                            "enableABSactuator",
                            "absHz",
                        )
                        if k in wp.props
                    },
                )
            )
        label = information_name(resolved, wp.radius_source)
        nominal = None
        if label:
            matches = TYRE_LABEL.findall(label)
            if len(matches) == 1:
                nominal = TyreNominal(*(int(x) for x in matches[0]))
        tyres.append(
            WheelTyre(
                wheel=wp.name,
                radius=_num(ev("radius"), f"{where}.radius"),
                hub_radius=_num(ev("hubRadius"), f"{where}.hubRadius"),
                width=_num(ev("tireWidth"), f"{where}.tireWidth", False),
                pressure_psi=_num(ev("pressurePSI"), f"{where}.pressurePSI", False),
                label=label,
                nominal=nominal,
                friction_raw={k: ev(k) for k in FRICTION_KEYS if k in wp.props},
            )
        )
    return BrakesTyres(brakes, tyres, issues)


def front_share(
    front: list[WheelBrake], rear: list[WheelBrake], e: float = 1.0
) -> float:
    """Fracción del par total en el eje delantero para una entrada ``e``."""
    f = sum(brake_torque(b.rate, b.input_split, b.split_coef, e) for b in front)
    r = sum(brake_torque(b.rate, b.input_split, b.split_coef, e) for b in rear)
    if f + r <= 0:
        raise BrakeTyreDataError("par de freno total nulo")
    return f / (f + r)
