"""Reparto suspendido / no suspendido por cierre rígido de nodos (G2.14).

Base física (BeamNG): toda la masa está en los nodos; las vigas no tienen masa
(G2.13: la masa física que instancia el juego es Σ ``nodeWeight``, más los 2 nodos de
cámara). Si cada nodo se mueve rígidamente con la carrocería o con un buje, el reparto
de la masa entre la carrocería y los bujes es exacto.

Grafo rígido: vigas ``|NORMAL`` con ``beamSpring`` ≥ :data:`RIGID_SPRING` (o sin valor
numérico: rigidez por defecto), EXCLUIDOS los muelles de la suspensión
(:func:`src.extract.suspension.classify_beam`). No unen: ``BOUNDED`` (amortiguadores,
topes), ``SUPPORT`` (limitadores), ``hydros`` ni ``torsionbars``.

Cierre rígido de un grupo: se le añade todo nodo con ≥ 3 vigas rígidas a nodos del
grupo (tres distancias fijas a puntos no alineados fijan un punto en 3D), hasta que no
cambie. No mira nombres ni grupos de nodo.

* rueda: semilla = ``node1``/``node2`` de ``pressureWheels`` y los nodos unidos
  rígidamente a los dos (mangueta). Si dos ruedas acaban en el mismo grupo (eje rígido o
  torsional), es un único cuerpo **acoplado**;
* carrocería: semilla = los nodos de la fila de ``refNodes`` (ref, back, left, up…),
  sin los nodos de las ruedas;
* «mecanismo»: nodos que no entran en ningún grupo (p. ej. los extremos de la
  cremallera, unidos al chasis por ``hydros`` y a las manguetas por las bieletas; los
  nodos de unión de los brazos de un eje rígido). Su masa es la ambigüedad del reparto y
  se informa siempre; no se asigna.

Masa no suspendida de un grupo = Σ ``nodeWeight`` del grupo + masa generada de sus
ruedas (``pressureWheels``; :mod:`src.extract.wheels`). Contrastado: Ibiza delantera
49,15 kg (= ``HUB_MASS`` de la base física aprobada) y Porsche 48,68 / 57,16 kg
(= fase C5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.jbeam import ResolvedConfig, parse_table

from .components import collect_beams
from .mass_geometry import MassGeometry
from .nodes import Node

RIGID_SPRING = 1000.0  # N/m (mismo umbral que candidates_dwb.MIN_LINK_SPRING)


@dataclass(frozen=True)
class UnsprungBody:
    wheels: tuple[str, ...]  # 1 rueda (independiente) o varias (eje acoplado)
    nodes: tuple[str, ...]
    node_mass: float  # kg, Σ nodeWeight del grupo
    generated_mass: float  # kg, masa generada de sus ruedas
    adjacent_mechanism: tuple[tuple[str, float], ...]  # (nodo, kg) unidos al grupo

    @property
    def coupled(self) -> bool:
        return len(self.wheels) > 1

    @property
    def mass(self) -> float:
        return self.node_mass + self.generated_mass


@dataclass
class UnsprungPartition:
    bodies: list[UnsprungBody]
    sprung_nodes: int
    sprung_node_mass: float
    mechanism: dict[str, float] = field(default_factory=dict)  # nodo → kg
    seed: tuple[str, ...] = ()

    def body_of(self, wheel: str) -> UnsprungBody | None:
        return next((b for b in self.bodies if wheel in b.wheels), None)


def _ref_nodes(resolved: ResolvedConfig) -> tuple[str, ...]:
    for active in resolved.active:
        sec = active.part.data.get("refNodes")
        if not sec:
            continue
        table = parse_table(sec)
        for row in table.rows:
            vals = [row.get(h) for h in table.header if h.endswith(":")]
            vals = [v for v in vals if isinstance(v, str)]
            if vals:
                return tuple(vals)
    return ()


def _closure(seed: set[str], adj: dict[str, set[str]], forbid: set[str]) -> set[str]:
    group = set(seed)
    changed = True
    while changed:
        changed = False
        for n, nb in adj.items():
            if n not in group and n not in forbid and len(nb & group) >= 3:
                group.add(n)
                changed = True
    return group


def partition(
    resolved: ResolvedConfig,
    geometry: MassGeometry,
    nodes: dict[str, Node],
    wheels: dict,  # nombre → src.extract.wheels.Wheel
) -> UnsprungPartition:
    """Reparto por cierre rígido (ver docstring del módulo)."""
    from .suspension import _btype, classify_beam  # import diferido: ciclo

    variables = resolved.variable_values()
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for b in collect_beams(resolved):
        if len(b.nodes) != 2 or _btype(b) != "NORMAL":
            continue
        if "spring" in classify_beam(b, variables):
            continue
        s = b.value("beamSpring", variables)
        if isinstance(s, (int, float)) and not isinstance(s, bool) and s < RIGID_SPRING:
            continue
        a, c = b.nodes
        if a in adj and c in adj and a != c:
            adj[a].add(c)
            adj[c].add(a)
    gen = {w.name: w.generated_mass for w in geometry.wheels}
    groups: list[tuple[list[str], set[str]]] = []
    for name in sorted(wheels):
        w = wheels[name]
        hub = {w.node1, w.node2}
        seed = hub | {n for n, nb in adj.items() if hub <= nb}
        g = _closure(seed, adj, set())
        for ws, gg in groups:
            if gg & g:
                ws.append(name)
                gg |= g
                break
        else:
            groups.append(([name], g))
    unsprung = set().union(*(g for _, g in groups)) if groups else set()
    seed = _ref_nodes(resolved)
    sprung = _closure({n for n in seed if n in adj} - unsprung, adj, unsprung)
    mech = {n: nodes[n].mass for n in nodes if n not in sprung and n not in unsprung}
    bodies = []
    for ws, g in groups:
        touching = sorted(
            (n, mech[n]) for n in mech if adj[n] & g
        )  # nodos de mecanismo unidos al grupo
        bodies.append(
            UnsprungBody(
                wheels=tuple(ws),
                nodes=tuple(sorted(g)),
                node_mass=sum(nodes[n].mass for n in g),
                generated_mass=sum(gen.get(w, 0.0) for w in ws),
                adjacent_mechanism=tuple(touching),
            )
        )
    return UnsprungPartition(
        bodies=bodies,
        sprung_nodes=len(sprung),
        sprung_node_mass=sum(nodes[n].mass for n in sprung),
        mechanism=mech,
        seed=seed,
    )
