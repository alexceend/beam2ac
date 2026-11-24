"""Combinación CONSERVADORA de una sección repartida entre varias piezas activas.

Qué se sabe (ver docs/research/motor.md §1):

* Varias piezas activas escriben en la misma sección con nombre (en ``35b84``,
  ``mainEngine`` aparece en el motor, en la gestión electrónica y en el turbo).
* El código de BeamNG que las fusiona NO está en la muestra, así que no hay
  evidencia de qué pieza gana cuando dos dan valores distintos a la misma clave,
  ni de cómo actúan ``"$+clave"`` / ``"$*clave"`` (sobre qué valor base y en qué
  orden).

Por eso esta función solo combina lo que no depende de esas reglas:

* clave definida por una sola pieza → se toma;
* clave definida por varias piezas con el MISMO valor → se toma;
* clave con valores distintos → :attr:`MergedSection.conflicts` (no se elige);
* clave con prefijo ``$+``/``$*`` (u otro ``$`` + símbolo) → :attr:`operators`
  (no se aplica).

Quien consuma el resultado decide qué hacer con conflictos y operadores, y
debe justificarlo (o rechazarlo) en su propio dominio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .resolver import ResolvedConfig


@dataclass(frozen=True)
class Contribution:
    part: str
    depth: int
    value: Any


@dataclass
class MergedSection:
    name: str
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, list[str]] = field(default_factory=dict)
    conflicts: dict[str, list[Contribution]] = field(default_factory=dict)
    operators: list[tuple[str, str, Any]] = field(
        default_factory=list
    )  # (pieza, clave, valor)
    non_dict: list[Contribution] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _is_operator(key: str) -> bool:
    return len(key) >= 2 and key[0] == "$" and not (key[1].isalnum() or key[1] == "_")


def merge_section(resolved: ResolvedConfig, name: str) -> MergedSection:
    merged = MergedSection(name=name)
    seen: dict[str, list[Contribution]] = {}
    for active, value in resolved.section_contributions(name):
        merged.parts.append(active.name)
        if not isinstance(value, dict):
            merged.non_dict.append(Contribution(active.name, active.depth, value))
            continue
        for key, item in value.items():
            if _is_operator(key):
                merged.operators.append((active.name, key, item))
                continue
            seen.setdefault(key, []).append(
                Contribution(active.name, active.depth, item)
            )
    for key, contributions in seen.items():
        first = contributions[0].value
        if all(c.value == first for c in contributions):
            merged.values[key] = first
            merged.sources[key] = [c.part for c in contributions]
        else:
            merged.conflicts[key] = contributions
    return merged
