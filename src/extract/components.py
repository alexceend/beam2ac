"""Lectura genérica de vigas, barras de torsión y propiedades de rueda (fase 5).

Solo estructura lo leído; no interpreta. Reglas heredadas de fases anteriores:

* modificadores de tabla vigentes en cada fila (fase 1);
* los modificadores de ``pressureWheels`` continúan entre piezas en orden de
  recorrido (fase 2, confirmado por la masa).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.jbeam import ResolvedConfig, evaluate, parse_table


@dataclass(frozen=True)
class Beam:
    part: str
    nodes: tuple[str, ...]  # 2 nodos (beams) o 4 (torsionbars)
    name: str | None
    props: dict[str, Any]  # propiedades vigentes, SIN evaluar

    def value(self, key: str, variables, default: Any = None) -> Any:
        return evaluate(self.props[key], variables) if key in self.props else default


def collect_beams(resolved: ResolvedConfig, section: str = "beams") -> list[Beam]:
    """Filas de ``beams`` (o ``torsionbars``) de todas las piezas activas."""
    out: list[Beam] = []
    for active in resolved.active:
        if section not in active.part.data:
            continue
        table = parse_table(active.part.data[section])
        refs = [h for h in table.header if h.endswith(":")]
        for row in table.rows:
            nodes = tuple(row.get(h) for h in refs)
            props = row.properties
            name = props.get("name")
            out.append(
                Beam(active.name, nodes, name if isinstance(name, str) else None, props)
            )
    return out


@dataclass(frozen=True)
class WheelProps:
    name: str
    part: str  # pieza que crea la rueda
    radius_source: str | None  # pieza que fijó por última vez "radius"
    props: dict[str, Any]  # propiedades efectivas de la fila, SIN evaluar


def collect_wheel_props(resolved: ResolvedConfig) -> list[WheelProps]:
    state: dict[str, Any] = {}
    radius_source: str | None = None
    out: list[WheelProps] = []
    for active in resolved.active:
        section = active.part.data.get("pressureWheels")
        if section is None:
            continue
        for item in section:
            if isinstance(item, dict) and "radius" in item:
                radius_source = active.name
        table = parse_table(section, initial_modifiers=state)
        state = table.final_modifiers
        for row in table.rows:
            out.append(
                WheelProps(
                    str(row.get("name")), active.name, radius_source, row.properties
                )
            )
    return out


def information_name(resolved: ResolvedConfig, part: str | None) -> str | None:
    if part is None:
        return None
    active = resolved.get(part)
    info = active.part.data.get("information") if active else None
    name = info.get("name") if isinstance(info, dict) else None
    return name if isinstance(name, str) else None
