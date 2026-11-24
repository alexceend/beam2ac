"""Nodos de la configuración activa, con posición final y masa.

Reglas aplicadas (y su estado de verificación):

* Fuente: sección ``nodes`` (tabla ``["id", "posX", "posY", "posZ"]``) de cada
  pieza activa; masa = propiedad ``nodeWeight`` vigente en la fila (modificador o
  propia). [confirmado: la masa total coincide con la referencia]
* ``nodeOffset`` efectivo de la pieza (heredado o reemplazado, fase 1):
  ``x' = x + sign(x)·off.x``, ``y' = y + off.y``, ``z' = z + off.z``, con
  ``sign(0) = 0`` (un nodo en el plano medio no se desplaza en x, sí en y/z).
  [confirmado en el código del juego (BeamNG 0.38, G2.13):
  ``lua/common/jbeam/utils.lua`` ``updateNodeTransformCache`` (rama sin rotaciones,
  ``cachePosX + cachePosXSign * op.x`` con ``cachePosXSign = sign(posX)``) y
  ``lua/common/mathlib.lua:1134`` ``sign(x) = max(min((x·1e200)·1e200, 1), −1)``
  (−1, 0, 1); y contra las posiciones que el juego instancia en 9 vehículos
  (``output/g213_chassis_geometry_2026-11-03/banco``). Antes de G2.13 x = 0 se
  rechazaba por ambiguo.]
* id de nodo repetido DENTRO de la misma pieza: vale la última fila; las anteriores
  no existen en el vehículo (ni su masa). [confirmado en el juego:
  ``lua/common/jbeam/tableSchema.lua`` ``processJbeamTableRow``,
  ``ctx.newList[newID] = newRow`` con ``newID = id``: la fila posterior reemplaza la
  entrada entera; y en el volcado del juego (patagonia: 420 nodos de carrocería, no
  423).] Las filas reemplazadas no se descartan en silencio: se devuelven como
  :class:`NodeOverride` (``collect_nodes(..., overrides=lista)``).

* id de nodo repetido en piezas activas DISTINTAS: la misma regla sobre la tabla ya
  fusionada, así que vale la fila de la pieza que se fusiona la ÚLTIMA.
  [confirmado leyendo el código del juego (BeamNG 0.38): ``slotSystem.lua``
  ``findParts``/``fillSlots_rec`` apuntan en ``unifyJournal`` cada pieza DESPUÉS de
  recorrer sus subslots (postorden: los hijos antes que su padre), ``unifyParts``
  añade las filas de cada pieza al final de la tabla de la raíz (que ya trae las
  suyas), y ``tableSchema.lua`` procesa esa tabla única con ``newList[id] = fila``.
  Orden de fusión: raíz, luego postorden del árbol de slots (``merge_rank``).]
  Aparece en vehículos oficiales de BeamNG (pessima, etk800).

Casos NO verificados → :class:`UnsupportedGeometry` (no se adivina):

* ``nodeMove`` con algún componente distinto de 0;
* ``scalenodeWeight`` distinto de 1 (no se sabe a qué nodos afecta);
* nodo sin ``nodeWeight`` (el valor por defecto de BeamNG no está verificado);
* coordenada o masa no numérica tras evaluar expresiones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.jbeam import ResolvedConfig, evaluate, parse_table

Vec3 = tuple[float, float, float]


class UnsupportedGeometry(ValueError):
    """Los datos usan una regla de geometría o masa que no está verificada."""


@dataclass(frozen=True)
class Node:
    id: str
    pos: Vec3  # ejes BeamNG, nodeOffset aplicado
    raw_pos: Vec3  # posición leída (expresiones evaluadas), sin offset
    mass: float  # kg
    part: str
    group: Any  # valor leído de "group" (sin interpretar)


@dataclass(frozen=True)
class NodeOverride:
    """Fila de nodo reemplazada por otra posterior con el mismo id en la misma pieza.

    El dato se conserva (posición leída y masa) para el diagnóstico; el vehículo solo
    tiene el nodo de ``kept_row_index`` (regla de ``tableSchema.lua``).
    """

    id: str
    part: str  # pieza de la fila que se conserva
    replaced_row_index: int
    replaced_raw_pos: Vec3
    replaced_mass: float
    kept_row_index: int
    kept_raw_pos: Vec3
    kept_mass: float
    replaced_part: str = ""  # pieza de la fila reemplazada ("" = la misma pieza)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnsupportedGeometry(f"{what}: se esperaba un número, no {value!r}")
    if not math.isfinite(value):
        raise UnsupportedGeometry(f"{what}: valor no finito {value!r}")
    return float(value)


def _offset_vector(raw: dict[str, Any] | None, variables, where: str) -> Vec3:
    if not raw:
        return (0.0, 0.0, 0.0)
    return tuple(  # type: ignore[return-value]
        _number(evaluate(raw.get(axis, 0), variables) or 0, f"{where}.{axis}")
        for axis in ("x", "y", "z")
    )


def _sign(x: float) -> int:
    """``sign`` de ``mathlib.lua`` del juego: −1, 0 o 1 (``sign(0) = 0``)."""
    return (x > 0) - (x < 0)


def apply_node_offset(pos: Vec3, offset: Vec3, where: str = "") -> Vec3:
    """Aplica ``nodeOffset`` como el juego: ``x + sign(x)·off.x`` (ver docstring).

    ``where`` se conserva por compatibilidad de la firma (ya no hay caso ambiguo).
    """
    x, y, z = pos
    ox, oy, oz = offset
    return (x + _sign(x) * ox, y + oy, z + oz)


def merge_rank(resolved: ResolvedConfig) -> dict[str, int]:
    """Orden en que el juego fusiona las piezas en la tabla de la raíz (menor = antes).

    Raíz primero (es el destino y ya trae sus filas); después, postorden del árbol de
    slots (``unifyJournal`` de ``slotSystem.lua``: se apunta cada pieza tras visitar
    sus hijos). ``resolved.active`` viene en preorden con ``parent``.
    """
    children: dict[str | None, list[str]] = {}
    for a in resolved.active:
        children.setdefault(a.parent, []).append(a.name)
    rank: dict[str, int] = {}

    def post(name: str) -> None:
        for c in children.get(name, []):
            post(c)
        rank[name] = len(rank) + 1

    roots = [a.name for a in resolved.active if a.parent is None]
    for r in roots:
        for c in children.get(r, []):
            post(c)
        rank[r] = 0
    return rank


def collect_nodes(
    resolved: ResolvedConfig, overrides: list[NodeOverride] | None = None
) -> list[Node]:
    """Todos los nodos de las piezas activas, en orden de recorrido.

    Un id repetido en la misma pieza deja solo la última fila (en su posición); si
    se pasa ``overrides``, se le añade un :class:`NodeOverride` por cada reemplazo.
    """
    variables = resolved.variable_values()
    rank = merge_rank(resolved)
    nodes: list[Node] = []
    owner: dict[str, str] = {}
    row_of: dict[str, int] = {}
    for active in resolved.active:
        data = active.part.data
        scale = evaluate(data.get("scalenodeWeight", 1), variables)
        if scale != 1:
            raise UnsupportedGeometry(
                f"'{active.name}': scalenodeWeight = {scale!r} "
                "(semántica no verificada)"
            )
        move = active.slot_options.get("nodeMove")
        if move and any(_offset_vector(move, variables, f"{active.name}.nodeMove")):
            raise UnsupportedGeometry(
                f"'{active.name}': nodeMove distinto de 0 (semántica no verificada)"
            )
        offset = _offset_vector(
            active.node_offset, variables, f"{active.name}.nodeOffset"
        )
        for row in parse_table(data.get("nodes", [])).rows:
            node_id = row.get("id")
            where = f"'{active.name}' nodo {node_id!r}"
            if not isinstance(node_id, str):
                raise UnsupportedGeometry(f"{where}: id no válido")
            props = row.properties
            if "nodeWeight" not in props:
                raise UnsupportedGeometry(f"{where}: sin nodeWeight")
            raw = tuple(
                _number(evaluate(row.get(c), variables), f"{where}.{c}")
                for c in ("posX", "posY", "posZ")
            )
            mass = _number(evaluate(props["nodeWeight"], variables), where)
            if mass < 0:
                raise UnsupportedGeometry(f"{where}: nodeWeight negativo {mass}")
            node = Node(
                id=node_id,
                pos=apply_node_offset(raw, offset, where),  # type: ignore[arg-type]
                raw_pos=raw,  # type: ignore[arg-type]
                mass=mass,
                part=active.name,
                group=props.get("group"),
            )
            if node_id in owner:
                # la fila de la pieza fusionada después reemplaza a la otra
                # (tableSchema.lua; entre piezas, orden de ``merge_rank``)
                old = next(n for n in nodes if n.id == node_id)
                if rank[active.name] >= rank[owner[node_id]]:
                    nodes.remove(old)
                    winner, loser = node, old
                    loser_row, kept_row = row_of[node_id], row.index
                    loser_part, kept_part = owner[node_id], active.name
                else:  # esta fila pierde: no entra en el vehículo
                    winner, loser = old, node
                    loser_row, kept_row = row.index, row_of[node_id]
                    loser_part, kept_part = active.name, owner[node_id]
                if overrides is not None:
                    overrides.append(
                        NodeOverride(
                            id=node_id,
                            part=kept_part,
                            replaced_row_index=loser_row,
                            replaced_raw_pos=loser.raw_pos,
                            replaced_mass=loser.mass,
                            kept_row_index=kept_row,
                            kept_raw_pos=winner.raw_pos,
                            kept_mass=winner.mass,
                            replaced_part=loser_part,
                        )
                    )
                if winner is old:
                    continue
            owner[node_id] = active.name
            row_of[node_id] = row.index
            nodes.append(node)
    return nodes
