"""Condiciones verificables sobre el IR (arquitecturas, capacidades del destino).

Una condición es ``{"path": ..., "op": ..., "value": ...}``. La ruta admite ``*`` para
recorrer los ejes, roles de diferencial o elementos de una lista
(``drivetrain.differentials.*.kind``). Operadores:

* ``eq`` / ``in`` / ``ne``: algún valor PRESENTE cumple (``ne``: ninguno es igual);
* ``present`` / ``absent``: hay (o no hay) algún valor presente;
* ``has_key``: el diccionario de la ruta contiene la clave ``value``
  (p. ej. ``formats.code_versions`` con ``camso_activeDampers``);
* ``differs``: los valores presentes de dos rutas (``value`` = otra ruta) difieren.

Un dato ausente nunca cumple ``eq``/``in``: la condición no se supone.
"""

from __future__ import annotations

from typing import Any

from .param import IRError, Param
from .vehicle import VehicleIR

OPS = ("eq", "in", "ne", "present", "absent", "has_key", "differs")


def _expand(obj: Any, parts: list[str]) -> list[Any]:
    if not parts:
        return [obj]
    head, rest = parts[0], parts[1:]
    if head == "*":
        if isinstance(obj, dict):
            children = [obj[k] for k in sorted(obj)]
        elif isinstance(obj, list):
            children = list(obj)
        else:
            return []
        return [x for c in children for x in _expand(c, rest)]
    if isinstance(obj, dict):
        return _expand(obj[head], rest) if head in obj else []
    if isinstance(obj, list):
        i = int(head) - 1
        return _expand(obj[i], rest) if 0 <= i < len(obj) else []
    if hasattr(obj, head):
        return _expand(getattr(obj, head), rest)
    raise IRError(f"ruta inexistente en el IR: {head!r}")


def values(ir: VehicleIR, path: str) -> list[Any]:
    """Valores presentes en la ruta (con comodines)."""
    found = _expand(ir, path.split("."))
    return [p.value for p in found if isinstance(p, Param) and p.present]


def holds(ir: VehicleIR, cond: dict[str, Any]) -> bool:
    op, path, ref = cond["op"], cond["path"], cond.get("value")
    if op not in OPS:
        raise IRError(f"operador desconocido {op!r}")
    if op == "has_key":
        found = _expand(ir, path.split("."))
        return any(isinstance(d, dict) and ref in d for d in found)
    vals = values(ir, path)
    if op == "eq":
        return any(v == ref for v in vals)
    if op == "in":
        return any(v in ref for v in vals)
    if op == "ne":
        return bool(vals) and all(v != ref for v in vals)
    if op == "present":
        return bool(vals)
    if op == "absent":
        return not vals
    other = values(ir, ref)
    return bool(vals) and bool(other) and vals[0] != other[0]


def all_hold(ir: VehicleIR, conds: list[dict[str, Any]]) -> bool:
    return all(holds(ir, c) for c in conds)
