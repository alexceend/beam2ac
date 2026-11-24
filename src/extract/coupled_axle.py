"""Eje trasero torsional (``Camso_torsionbeam_*``): equivalente DWB para AC.

BeamNG: las dos ruedas del eje son un único cuerpo rígido (cierre rígido de nodos,
:mod:`src.extract.unsprung`): brazos longitudinales y viga transversal. Lo une a la
carrocería un PIVOTE por lado (un nodo del brazo sujeto por ≥ 3 vigas rígidas a la
carrocería = rótula) y lo suspenden un muelle y un amortiguador por lado. La torsión de
la viga la da una ``torsionbar`` (no una viga rígida), de modo que cada brazo puede
girar casi libremente respecto al otro alrededor del eje que une los dos pivotes.

No es un eje de barras (:mod:`src.extract.rigid_axle` lo rechaza: < 3 montajes) y AC no
tiene un tipo propio: el precedente oficial (Abarth 500 Assetto Corse, viga de torsión
real) usa ``DWB`` (``docs/PLAN.md``). Se traduce cada brazo como una rueda
independiente con un DWB equivalente medido sobre la cinemática del semieje.

Método (genérico, sin datos de ningún coche concreto), por rueda:

1. Semieje = nodos del cuerpo rígido del lado de la rueda (``x`` del mismo signo).
2. Pivote = nodo del semieje con ≥ :data:`MIN_PIVOT_LINKS` vigas rígidas a nodos fuera
   del cuerpo (la carrocería): una rótula fija durante el recorrido.
3. Se une cada nodo del semieje al buje con una viga virtual para que
   :func:`src.extract.linkage.analyse_corner` lo trate como parte de la mangueta.
4. Una viga del semieje al pivote del OTRO lado se conserva como anclaje (el pivote es
   un punto de la carrocería: la rótula no se mueve), apuntando a un nodo virtual fijo
   en su posición. Una viga a un nodo MÓVIL del otro lado se quita: con el recorrido
   simétrico el reflejado de ese nodo se mueve con el semieje. Quedan en ``dropped``;
   ``analyse_corner`` comprueba que la distancia al reflejado se conserva (≤
   ``EQUIV_M``) y, si no, devuelve ``coupled`` (bloqueo, nunca un valor inventado).

Aproximación declarada: el DWB de cada rueda es el de recorrido SIMÉTRICO (ambas ruedas
suben a la vez). La torsión de la viga (balanceo diferencial) no entra en la cinemática:
su rigidez va a la barra estabilizadora equivalente (``torsionbars``).
"""

from __future__ import annotations

from typing import Any

from .components import Beam
from .nodes import Node

MIN_PIVOT_LINKS = 3  # una rótula = 3 distancias a puntos no alineados de la carrocería
MIDPLANE = 1e-3  # m: un nodo del cuerpo en el plano medio no pertenece a ningún lado


def _side(nodes: dict[str, Node], n: str) -> int:
    return 1 if nodes[n].pos[0] > 0 else -1


def virtualize_torsion_axle(
    *,
    hubs: dict[str, tuple[str, str]],
    axle_nodes: set[str],
    nodes: dict[str, Node],
    rigid_beams: list[Beam],
    exclude: set[frozenset[str]],
) -> tuple[dict[str, Node], list[Beam], dict[str, dict[str, Any]]] | None:
    """``(nodos con virtuales, vigas rígidas con el eje virtualizado, info por rueda)``
    o ``None`` si el eje no es torsional (no hay exactamente un pivote por lado, un nodo
    del cuerpo cae en el plano medio o las dos ruedas no están a lados opuestos).

    ``hubs``: rueda → (nodo, nodo) de su eje; ``axle_nodes``: cuerpo rígido acoplado y
    su mecanismo adyacente; ``exclude``: vigas de muelle/amortiguador/tope."""
    if len(hubs) != 2:
        return None
    sides = {w: _side(nodes, h[0]) for w, h in hubs.items()}
    if sorted(sides.values()) != [-1, 1]:
        return None
    body = {n for n in axle_nodes if n in nodes}
    if any(abs(nodes[n].pos[0]) <= MIDPLANE for n in body):
        return None
    half = {s: {n for n in body if _side(nodes, n) == s} for s in (-1, 1)}
    usable = [
        bm
        for bm in rigid_beams
        if len(bm.nodes) == 2
        and frozenset(bm.nodes) not in exclude
        and all(n in nodes for n in bm.nodes)
    ]
    usable_ids = {id(bm) for bm in usable}
    outside: dict[str, set[str]] = {}
    for bm in usable:
        a, b = bm.nodes
        for k, o in ((a, b), (b, a)):
            if k in body and o not in body:
                outside.setdefault(k, set()).add(o)
    pivots = {
        s: sorted(n for n in half[s] if len(outside.get(n, ())) >= MIN_PIVOT_LINKS)
        for s in (-1, 1)
    }
    if any(len(p) != 1 for p in pivots.values()):
        return None
    pivot = {s: p[0] for s, p in pivots.items()}
    out_nodes = dict(nodes)
    for p in pivot.values():
        out_nodes[f"_vp_{p}"] = Node(
            f"_vp_{p}", nodes[p].pos, nodes[p].pos, 0.0, "_virtual", None
        )
    pivset = set(pivot.values())
    hubnodes = {n for h in hubs.values() for n in h}
    keep: list[Beam] = []
    added: list[Beam] = []
    dropped: dict[int, list[tuple[str, str]]] = {-1: [], 1: []}
    for bm in rigid_beams:
        if id(bm) not in usable_ids or not all(n in body for n in bm.nodes):
            keep.append(bm)
            continue
        a, b = bm.nodes
        if a in pivset and b in pivset:
            continue  # pivote–pivote: ambos son puntos fijos de la carrocería
        if a in pivset or b in pivset:
            # el pivote es un punto fijo: sus vigas al semieje son distancias del brazo
            # a un punto de la carrocería (las del buje son redundantes: cuerpo rígido)
            pv, x = (a, b) if a in pivset else (b, a)
            if x not in hubnodes:
                added.append(Beam("_virtual", (x, f"_vp_{pv}"), None, {}))
            continue
        if _side(nodes, a) == _side(nodes, b):
            keep.append(bm)
            continue
        # viga entre semiejes con un nodo móvil en cada extremo: se descarta y se
        # comprueba la simetría (``dropped``)
        dropped[_side(nodes, a)].append((a, b))
        dropped[_side(nodes, b)].append((b, a))
    info: dict[str, dict[str, Any]] = {}
    for wheel, hub in hubs.items():
        s = sides[wheel]
        hubset = set(hub)
        touching = {
            (a if b in hubset else b)
            for bm in keep
            if len(bm.nodes) == 2
            for a, b in (bm.nodes,)
            if (a in hubset) != (b in hubset)
        }
        linked = {n for bm in keep + added for n in bm.nodes}
        for n in sorted(half[s] - hubset - touching - pivset):
            if n in linked:
                added.append(Beam("_virtual", (n, hub[0]), None, {}))
        info[wheel] = {
            "pivot": pivot[s],
            "other_pivot": pivot[-s],
            "half": sorted(half[s] - pivset),
            "dropped": dropped[s],
        }
    return out_nodes, keep + added, info


__all__ = ["virtualize_torsion_axle"]
