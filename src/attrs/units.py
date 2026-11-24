"""Unidades con dimensión explícita (sin dependencias externas).

Cada unidad tiene una dimensión y un factor a la unidad SI de esa dimensión. Dos
unidades son compatibles solo si comparten dimensión; la conversión es ``valor ·
factor_origen / factor_destino``. Una conversión exacta de unidades NO implica que dos
atributos tengan el mismo significado físico (eso lo decide una regla con evidencia, no
este módulo).

Unidades especiales:

* ``"1"``: adimensional (relaciones, fracciones).
* ``"text"``, ``"enum"``: sin dimensión numérica; solo compatibles consigo mismas.
* ``"table:<x>-><y>"``: tabla de pares; compatible si lo son ``x`` e ``y`` por separado.
* ``"undocumented"``: unidad de AC no documentada en las fuentes del proyecto. Nunca es
  compatible con otra cosa (obliga a una decisión explícita).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# unidad -> (dimensión, factor a SI)
_UNITS: dict[str, tuple[str, float]] = {
    "1": ("dimensionless", 1.0),
    "%": ("dimensionless", 0.01),
    "m": ("length", 1.0),
    "mm": ("length", 1e-3),
    "cm": ("length", 1e-2),
    "in": ("length", 0.0254),
    # km virtuales del desgaste de AC (registro: "MAX WEAR AT: 25.5 vKM"); fase B4
    "km": ("length", 1000.0),
    "kg": ("mass", 1.0),
    "s": ("time", 1.0),
    "N": ("force", 1.0),
    "N·m": ("torque", 1.0),
    "N/m": ("stiffness", 1.0),
    # G2.17: término progresivo de un tope (F = k·x + c·x²)
    "N/m²": ("stiffness_per_length", 1.0),
    "N·m/rad": ("angular_stiffness", 1.0),
    "N·s/m": ("damping", 1.0),
    "kg·m²": ("inertia", 1.0),
    "m²": ("area", 1.0),
    "L": ("volume", 1e-3),
    "Pa": ("pressure", 1.0),
    "kPa": ("pressure", 1e3),
    "psi": ("pressure", 6894.757293168361),
    "bar": ("pressure", 1e5),
    "rpm": ("angular_speed", 2 * math.pi / 60),
    "Hz": ("frequency", 1.0),  # G2.50: frecuencia del ABS
    "rad/s": ("angular_speed", 1.0),
    "deg": ("angle", math.pi / 180),
    "rad": ("angle", 1.0),
    "m/s": ("speed", 1.0),
    # G2.16: desplazamiento lineal por ángulo (barra de dirección por grado de rueda)
    "m/rad": ("length_per_angle", 1.0),
    "m/deg": ("length_per_angle", 180 / math.pi),
    "km/h": ("speed", 1 / 3.6),
    "W": ("power", 1.0),
    "kW": ("power", 1e3),
    "hp": ("power", 745.69987158227),
    # Temperatura en °C. Es la única unidad de temperatura: no hay conversión con
    # desplazamiento (K, °F), que esta tabla de factores no sabría hacer.
    "°C": ("temperature", 1.0),
}
# G2.20: "dataset" = datos medidos de varias columnas que se describen a sí mismos
# ({"columns": [[nombre, unidad], …], "rows": [[…], …]}); p. ej. las muestras
# fuerza–deslizamiento del neumático (no caben en una tabla x→y)
_SYMBOLIC = {"text", "enum", "undocumented", "dataset"}


class UnitError(ValueError):
    """Unidad desconocida o conversión entre dimensiones distintas."""


@dataclass(frozen=True)
class UnitInfo:
    unit: str
    dimension: str
    factor: float


def known_units() -> list[str]:
    return sorted(_UNITS) + sorted(_SYMBOLIC)


def is_table(unit: str) -> bool:
    return unit.startswith("table:") and "->" in unit


def table_axes(unit: str) -> tuple[str, str]:
    if not is_table(unit):
        raise UnitError(f"no es una unidad de tabla: {unit!r}")
    x, y = unit[len("table:") :].split("->", 1)
    return x, y


def validate_unit(unit: str) -> None:
    if unit in _UNITS or unit in _SYMBOLIC:
        return
    if is_table(unit):
        for axis in table_axes(unit):
            validate_unit(axis)
        return
    raise UnitError(f"unidad desconocida: {unit!r}")


def info(unit: str) -> UnitInfo:
    validate_unit(unit)
    if unit in _SYMBOLIC or is_table(unit):
        return UnitInfo(unit, unit, float("nan"))
    dim, factor = _UNITS[unit]
    return UnitInfo(unit, dim, factor)


def compatible(a: str, b: str) -> bool:
    """¿Se puede convertir ``a`` en ``b`` sin cambiar de dimensión?"""
    validate_unit(a)
    validate_unit(b)
    if "undocumented" in (a, b):
        return False
    if is_table(a) or is_table(b):
        if not (is_table(a) and is_table(b)):
            return False
        (ax, ay), (bx, by) = table_axes(a), table_axes(b)
        return compatible(ax, bx) and compatible(ay, by)
    if a in _SYMBOLIC or b in _SYMBOLIC:
        return a == b
    return _UNITS[a][0] == _UNITS[b][0]


def convert(value: float, src: str, dst: str) -> float:
    if not compatible(src, dst):
        raise UnitError(f"unidades incompatibles: {src!r} -> {dst!r}")
    if src == dst:
        return value
    return value * _UNITS[src][1] / _UNITS[dst][1]
