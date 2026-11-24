"""Comparación de un valor calculado con un valor ESCRITO en un INI, con la precisión
escrita.

Un INI guarda ``1119.6`` o ``1.475``: el valor calculado (1119.57…, 1.474996) es igual
si la diferencia es ≤ ½ de la última cifra decimal escrita. Para vectores se compara
componente a componente con la tolerancia más gruesa. El texto se compara literalmente.
"""

from __future__ import annotations

from typing import Any


def parse_value(text: str) -> Any:
    parts = [p.strip() for p in str(text).split(",")]
    try:
        nums = [float(p) if any(c in p for c in ".eE") else int(p) for p in parts]
    except ValueError:
        return text
    return nums if len(nums) > 1 else nums[0]


def tolerance(text: str) -> float:
    tols = []
    for p in str(text).split(","):
        p = p.strip().lower().split("e")[0]
        dec = len(p.split(".")[1]) if "." in p else 0
        tols.append(0.5 * 10 ** (-dec))
    return max(tols) + 1e-12


def same_as_written(written: str, value: Any) -> bool:
    expected = parse_value(written)
    tol = tolerance(written)
    if isinstance(expected, list) or isinstance(value, list):
        if not (
            isinstance(expected, list)
            and isinstance(value, list)
            and len(expected) == len(value)
        ):
            return False
        return all(abs(a - b) <= tol for a, b in zip(expected, value))
    if (
        isinstance(expected, (int, float))
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return abs(expected - value) <= tol
    return str(expected) == str(value)
