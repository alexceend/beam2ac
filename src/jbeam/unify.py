"""Fusión de una sección clave→valor entre piezas con la semántica del juego (lectura).

Port de ``unifyParts`` y del diario de fusión de ``lua/common/jbeam/slotSystem.lua`` de
BeamNG (instalación local, solo lectura; licencia bCDDL):

* El diario se llena en **postorden**: al elegir la pieza hija de un slot se recorren
  primero sus propios slots y después se apunta ``(padre, hija)`` (l. 386-414). Se
  reproduce
  en ese orden (``unifyPartJournal``, l. 500-506), así que una pieza nieta se fusiona en
  su
  hija antes de que la hija se fusione en su padre, y las hermanas en el orden de los
  slots.
* ``unifyParts(destino=padre, origen=hija)`` (l. 73-176), para secciones clave→valor:
  - valor normal: **sobrescribe** el del destino (gana la hija);
  - clave ``"$+k"``/``"$*k"``/``"$<k"``/``"$>k"`` con valor NUMÉRICO: si el destino
  tiene
    ``k`` numérico, suma/multiplica/mínimo/máximo; si el destino ya tiene el mismo
    modificador numérico, se combinan y siguen siendo modificador; si no hay valor base,
    el modificador se guarda tal cual para que lo aplique un antepasado (l. 119-150);
  - modificador con valor no numérico (p. ej. una expresión ``"$=…"``): se guarda tal
    cual con su clave, sin operar (la comprobación es ``type(v3) == "number"``).

``src/jbeam/merge.py`` (combinación conservadora) NO cambia: lo usa el adaptador
``camsoEngine`` validado. Este módulo es una API aparte para quien necesite la semántica
del juego (adaptador ``combustionEngine``), y devuelve una traza por clave.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .resolver import ActivePart, ResolvedConfig

OPS = {"+": "suma", "*": "producto", "<": "mínimo", ">": "máximo"}


@dataclass
class UnifiedSection:
    name: str
    values: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, list[str]] = field(default_factory=dict)  # clave -> operaciones
    parts: list[str] = field(default_factory=list)  # piezas que aportan la sección
    non_dict: list[str] = field(default_factory=list)

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _unify(
    target: dict[str, Any],
    source: dict[str, Any],
    part: str,
    trace: dict[str, list[str]],
) -> None:
    for k, v in source.items():
        if (
            _num(v)
            and len(k) >= 2
            and k[0] == "$"
            and not (k[1].isalnum() or k[1] == "_")
        ):
            actual = k[2:]
            existing = target.get(actual)
            if _num(target.get(k)):  # modificador sobre modificador
                existing, actual = target[k], k
            if _num(existing):
                op = k[1]
                if op == "+":
                    target[actual] = existing + v
                elif op == "*":
                    target[actual] = existing * v
                elif op == "<":
                    target[actual] = min(existing, v)
                elif op == ">":
                    target[actual] = max(existing, v)
                else:
                    target[k] = v
                    trace.setdefault(k, []).append(
                        f"{part}: {k}={v!r} (operador desconocido, guardado)"
                    )
                    continue
                trace.setdefault(actual, []).append(
                    f"{part}: {OPS[op]} {v!r} → {target[actual]!r}"
                )
            else:
                target[k] = v
                trace.setdefault(k, []).append(
                    f"{part}: modificador {k}={v!r} sin valor base (se guarda)"
                )
        else:
            if k in target and target[k] != v:
                trace.setdefault(k, []).append(
                    f"{part}: sobrescribe {target[k]!r} → {v!r}"
                )
            elif k not in target:
                trace.setdefault(k, []).append(f"{part}: {v!r}")
            target[k] = v


def unify_dict_section(resolved: ResolvedConfig, name: str) -> UnifiedSection:
    """Valores de la sección ``name`` tras fusionar las piezas activas como el juego."""
    out = UnifiedSection(name=name)
    children: dict[str | None, list[ActivePart]] = {}
    for a in resolved.active:  # orden de recorrido = orden de slots
        children.setdefault(a.parent, []).append(a)

    def merged(part: ActivePart) -> dict[str, Any]:
        own = part.part.data.get(name)
        data: dict[str, Any] = {}
        if isinstance(own, dict):
            data = dict(own)
            out.parts.append(part.name)
            for k, v in own.items():
                out.trace.setdefault(k, []).append(f"{part.name}: {v!r} (propia)")
        elif own is not None:
            out.non_dict.append(part.name)
        for child in children.get(part.name, []):
            sub = merged(child)
            if sub:
                _unify(data, sub, child.name, out.trace)
        return data

    roots = children.get(None, [])
    if len(roots) != 1:
        raise ValueError(f"se esperaba una pieza raíz, hay {[r.name for r in roots]}")
    out.values = merged(roots[0])
    return out
