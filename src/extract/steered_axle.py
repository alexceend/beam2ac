"""Eje rígido DIRECCIONAL (``Camso_liveaxle_*_F``): equivalente DWB para AC.

Caso real (``bisexual_aspiration``, exportador 40340): el eje delantero es un tubo rígido
(``fax1…fax4``) unido a la carrocería por 4 bielas (superior e inferior por lado) y
sostenido por un muelle y un amortiguador por rueda; cada rueda va en una mangueta con
pivote de dirección (``fax2rr``/``fax4rr``, arriba/abajo) y barra de dirección (``fh3``).

AC [C] (coche de AC, suspensión de eje de AC, ver
``docs/research/ac_suspension_coordinates.md`` §1 y §4): ``TYPE=AXLE`` solo existe en
``[REAR]`` (el constructor lee ``[REAR] TRACK/BASEY/HUB_MASS`` y no tiene dirección).
Un eje rígido delantero que gira NO es representable con ``AXLE``.

Equivalente implementado (aproximación, declarada): un DWB por rueda cuyas dos bielas del
triángulo superior (e inferior) son la biela real de ese nivel, tomada desde su pivote
en la carrocería hasta el pivote de dirección de la mangueta, más su simétrica respecto
al plano vertical longitudinal del pivote (un triángulo con el vértice en la rótula):
la rótula describe el mismo arco de heave que la biela real (eje de giro transversal por
los pivotes de la carrocería). La barra de dirección se une a su punto del eje en reposo.

Lo que NO reproduce (limitaciones): la caída que el eje impone en balanceo (el eje gira
con la diferencia de alturas; el DWB de cada rueda es independiente, caída ≈ constante en
el recorrido), la reacción lateral del eje (barra Panhard o cruces) y el acoplamiento de
las dos ruedas en un solo bache. Con ``rigid_axle`` trasero AC sí tiene el eje real.

Los puntos del DWB son equivalentes, no los anclajes físicos (como ``dwb_fitted``).
"""

from __future__ import annotations

from typing import Any

from .components import Beam
from .nodes import Node

Vec = tuple[float, float, float]
MIN_AXLE_LINKS = 3  # un nodo de pivote de dirección tiene muchas vigas al tubo


def _knuckle_of(hub: tuple[str, str], rigid_beams: list[Beam]) -> set[str]:
    hubset = set(hub)
    return {
        a if b in hubset else b
        for a, b in (bm.nodes for bm in rigid_beams if len(bm.nodes) == 2)
        if (a in hubset) != (b in hubset)
    }


def _near(p: Vec, q: Vec) -> float:
    return sum((a - b) ** 2 for a, b in zip(p, q)) ** 0.5


def virtualize_steered_axle(
    *,
    hubs: dict[str, tuple[str, str]],
    axle_nodes: set[str],
    nodes: dict[str, Node],
    rigid_beams: list[Beam],
    exclude: set[frozenset[str]],
) -> tuple[dict[str, Node], list[Beam], dict[str, dict[str, Any]]] | None:
    """``(nodos con virtuales, vigas rígidas con el eje sustituido, info por rueda)``.

    ``hubs``: rueda → (nodo, nodo) de su eje de rueda; ``axle_nodes``: nodos del tubo
    (cuerpos no suspendidos de las dos ruedas y su mecanismo adyacente);
    ``exclude``: vigas de muelle/amortiguador/tope (no son bielas). ``None`` si no
    reconoce un eje direccional (cada rueda: rótula superior e inferior con ≥ 3 vigas al
    tubo, una barra de dirección con 1, y una biela a la carrocería por nivel y lado).
    """
    # tubo propiamente dicho: sin las mangutas ni los nodos de rueda
    wheel_side: set[str] = set()  # mangutas y nodos de rueda (ni tubo ni carrocería)
    for hub in hubs.values():
        wheel_side |= set(hub) | _knuckle_of(hub, rigid_beams)
    axle_nodes = axle_nodes - wheel_side
    pairs: list[tuple[str, str]] = []  # (nodo fuera del tubo, nodo del tubo)
    for bm in rigid_beams:
        if len(bm.nodes) != 2 or frozenset(bm.nodes) in exclude:
            continue
        x, y = bm.nodes
        if x not in nodes or y not in nodes or (x in axle_nodes) == (y in axle_nodes):
            continue
        if x in wheel_side or y in wheel_side:
            continue
        pair = (y, x) if x in axle_nodes else (x, y)
        if pair not in pairs:
            pairs.append(pair)
    out_nodes = dict(nodes)
    info: dict[str, dict[str, Any]] = {}
    remove: set[tuple[str, str]] = set()
    added: list[Beam] = []
    for wheel, hub in hubs.items():
        knuckle = _knuckle_of(hub, rigid_beams)
        sign = 1 if nodes[hub[0]].pos[0] > 0 else -1
        # vigas de la mangueta a nodos del tubo
        to_axle: dict[str, list[str]] = {}
        for bm in rigid_beams:
            if len(bm.nodes) != 2 or frozenset(bm.nodes) in exclude:
                continue
            x, y = bm.nodes
            for k, o in ((x, y), (y, x)):
                if k in knuckle and o in axle_nodes and o not in knuckle:
                    to_axle.setdefault(k, []).append(o)
        pivots = sorted(
            (k for k, v in to_axle.items() if len(v) >= MIN_AXLE_LINKS),
            key=lambda k: -nodes[k].pos[2],
        )
        rods = [k for k, v in to_axle.items() if len(v) == 1]
        if len(pivots) != 2 or len(rods) != 1:
            return None
        top, bot = pivots
        if nodes[top].pos[2] <= nodes[bot].pos[2]:
            return None
        rod_up = rods[0]
        rod_chassis = to_axle[rod_up][0]
        wheel_info: dict[str, Any] = {"top": top, "bottom": bot, "rod": rod_up}
        virt: list[tuple[str, str, Vec]] = []
        for level, pivot in (("top", top), ("bottom", bot)):
            tp = nodes[pivot].pos
            # biela del nivel: tubo en el lado de la rueda, a la altura del pivote,
            # y carrocería en el mismo lado (los cruces de rigidez lateral no cuentan)
            cands = [
                (c, a)
                for c, a in pairs
                if nodes[a].pos[0] * sign > 1e-3
                and nodes[c].pos[0] * sign > 1e-3
                and abs(nodes[a].pos[2] - tp[2]) < 0.15
            ]
            if not cands:
                return None
            c, a = min(
                cands,
                key=lambda ca: (
                    abs(nodes[ca[1]].pos[2] - tp[2]),
                    abs(abs(nodes[ca[1]].pos[0]) - abs(tp[0])),
                ),
            )
            pc = nodes[c].pos
            mirror = (2 * tp[0] - pc[0], pc[1], pc[2])
            wheel_info[f"{level}_link"] = (c, a)
            virt.append((f"_v_{wheel}_{level}_1", pivot, pc))
            virt.append((f"_v_{wheel}_{level}_2", pivot, mirror))
        for vid, upright, pos in virt:
            out_nodes[vid] = Node(vid, pos, pos, 0.0, "_virtual", None)
            added.append(Beam("_virtual", (vid, upright), None, {}))
        # la barra de dirección conserva su nodo real (extremo de la cremallera)
        added.append(Beam("_virtual", (rod_chassis, rod_up), None, {}))
        for k, o_list in to_axle.items():
            for o in o_list:
                remove.add((k, o))
        info[wheel] = wheel_info
    kept = [
        bm
        for bm in rigid_beams
        if not (
            len(bm.nodes) == 2
            and (bm.nodes in remove or (bm.nodes[1], bm.nodes[0]) in remove)
        )
    ]
    return out_nodes, kept + added, info


__all__ = ["virtualize_steered_axle"]
