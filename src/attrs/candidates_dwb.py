"""Puntos candidatos de una suspensión ``DWB`` de AC medidos en la estructura de BeamNG.

Adaptador aislado (fase 5): solo LEE la configuración resuelta y devuelve candidatos con
su evidencia. No escribe nada y no decide: los valores son propuestas para una decisión
humana.

Identificación ESTRUCTURAL (vigas y conexiones; los nombres de nodo no se usan), para la
rueda derecha de un eje (``x`` < 0 en BeamNG, ``+X`` = izquierda):

1. Buje: nodos de eje de la rueda (``node1``/``node2`` de ``pressureWheels``).
2. Mangueta: nodos unidos al buje por vigas ``|NORMAL`` con rigidez ≥
``MIN_LINK_SPRING`` (sin
   contar los del buje; una viga de rigidez 0 es un amortiguador, no une rígidamente).
3. Enlaces: vigas ``|NORMAL`` de rigidez ≥ ``MIN_LINK_SPRING`` de un nodo de la mangueta
a un nodo
   que no es de la mangueta ni del buje. Se descartan los enlaces nominales (p. ej.
   estabilizadora
   a 1 N/m), las ``|SUPPORT``/``|BOUNDED`` (topes) y el **muelle**: el par de nodos que
   también
   tiene una viga ``|BOUNDED`` con ``beamDamp`` > 0 (amortiguador coaxial) es el
   conjunto muelle-amortiguador, no un brazo.
4. Un nodo de la mangueta con exactamente 2 enlaces es el vértice de un **triángulo**;
el de
   mayor altura es el superior. El nodo con 1 enlace es la **bieleta** (dirección o
   convergencia). Cualquier otra forma (p. ej. eje torsional, MacPherson) →
   ``StructureError``: no es un DWB.

Conversión (semántica de AC confirmada en ``docs/research/ac_suspension_coordinates.md``
§2.1–2.2 y §7): cada punto es relativo a la base de la rueda, que es el centro de diseño
del buje, en ejes de AC (``AC(x, y, z) = (Bx, Bz, −By)``), escrito para la rueda derecha
con ``+X`` hacia dentro (que en ejes AC es ``+X`` para la rueda derecha, sin cambio de
signo). Como base y puntos se miden en el mismo marco, el CdG se cancela: no depende de
la hipótesis del suelo ni del reparto de masas. ``WBCAR_*_FRONT`` es el pivote más
adelantado (mayor ``z`` en AC).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.coords import beamng_to_ac
from src.extract.components import collect_beams
from src.extract.nodes import collect_nodes
from src.jbeam import ResolvedConfig, parse_table

MIN_LINK_SPRING = 1000.0  # N/m: por debajo, el enlace es nominal (no estructural)


class StructureError(ValueError):
    """La estructura del eje no es la de un doble triángulo identificable."""


@dataclass
class DwbCandidate:
    axle: str  # FRONT | REAR
    wheel: str
    points: dict[
        str, tuple[float, float, float]
    ]  # clave AC -> punto relativo a la base (m)
    nodes: dict[str, Any]  # clave AC -> nodo(s) de BeamNG usados
    hub_nodes: tuple[str, str]
    knuckle: list[str]
    evidence: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axle": self.axle,
            "wheel": self.wheel,
            "points": {k: [round(c, 5) for c in v] for k, v in self.points.items()},
            "nodes": self.nodes,
            "hub_nodes": list(self.hub_nodes),
            "knuckle": self.knuckle,
            "evidence": self.evidence,
            "caveats": self.caveats,
            "status": "candidato medido; NO aplicado (requiere decisión)",
        }


def _beam_type(props: dict[str, Any]) -> str:
    t = str(props.get("beamType", "|NORMAL"))
    return t.strip("|").upper() or "NORMAL"


def _wheel_rows(resolved: ResolvedConfig) -> list[dict[str, Any]]:
    rows, state = [], {}
    for a in resolved.active:
        if "pressureWheels" not in a.part.data:
            continue
        t = parse_table(a.part.data["pressureWheels"], initial_modifiers=state)
        state = t.final_modifiers
        for row in t.rows:
            rows.append(
                {
                    "name": row.get("name"),
                    "node1": row.get("node1:"),
                    "node2": row.get("node2:"),
                }
            )
    return rows


def measure_dwb(
    resolved: ResolvedConfig, wheel_name: str, axle: str, side: str = "right"
) -> DwbCandidate:
    """``side='left'`` mide la rueda izquierda expresando +X hacia dentro (−x de AC),
    para
    comprobar la simetría que AC supone al reflejar la rueda derecha."""
    variables = resolved.variable_values()
    nodes = {n.id: n for n in collect_nodes(resolved)}
    wheel = next((w for w in _wheel_rows(resolved) if w["name"] == wheel_name), None)
    if wheel is None:
        raise StructureError(f"rueda {wheel_name!r} inexistente")
    hub = {wheel["node1"], wheel["node2"]}
    links: dict[str, set[str]] = {}
    normal: list[tuple[str, str, float]] = []
    damped_pairs: set[frozenset[str]] = set()
    for b in collect_beams(resolved):
        if len(b.nodes) != 2:
            continue
        btype = _beam_type(b.props)
        damp = b.value("beamDamp", variables, 0)
        if btype == "BOUNDED" and isinstance(damp, (int, float)) and damp > 0:
            damped_pairs.add(frozenset(b.nodes))
        if btype != "NORMAL":
            continue
        spring = b.value("beamSpring", variables, None)
        spring = float(spring) if isinstance(spring, (int, float)) else float("inf")
        normal.append((b.nodes[0], b.nodes[1], spring))
    rigid = [(a, b) for a, b, s in normal if s >= MIN_LINK_SPRING]
    knuckle = sorted(
        {a if b in hub else b for a, b in rigid if (a in hub) != (b in hub)}
    )
    if not knuckle:
        raise StructureError(
            f"{wheel_name}: no hay nodos unidos al buje por vigas NORMAL rígidas"
        )
    coilover = sorted(sorted(p) for p in damped_pairs if p & set(knuckle))
    for a, b in rigid:
        if frozenset((a, b)) in damped_pairs:
            continue  # muelle coaxial con un amortiguador
        for k, other in ((a, b), (b, a)):
            if k in knuckle and other not in knuckle and other not in hub:
                links.setdefault(k, set()).add(other)
    arms = {k: sorted(v) for k, v in links.items() if len(v) == 2}
    rods = {k: sorted(v) for k, v in links.items() if len(v) == 1}
    others = {k: sorted(v) for k, v in links.items() if len(v) > 2}
    shared = {}
    for k, piv in arms.items():
        shared.setdefault(tuple(piv), []).append(k)
    doubled = {piv: ks for piv, ks in shared.items() if len(ks) > 1}
    if doubled:
        raise StructureError(
            f"{wheel_name}: cada triángulo se une a la mangueta por dos rótulas "
            f"{doubled}; un DWB de AC "
            "solo admite un punto por triángulo (WBTYRE_TOP/BOTTOM) "
            "y una bieleta. Representarlo exige "
            "elegir un punto equivalente o el tipo ML: decisión humana"
        )
    if len(arms) != 2 or len(rods) != 1 or others:
        raise StructureError(
            f"{wheel_name}: estructura no DWB (triángulos {arms}, "
            f"bieletas {rods}, otros {others})"
        )
    center = tuple(
        (a + b) / 2
        for a, b in zip(nodes[wheel["node1"]].pos, nodes[wheel["node2"]].pos)
    )
    if (center[0] >= 0) == (side == "right"):
        raise StructureError(
            f"{wheel_name}: no es una rueda {side} (x = {center[0]:+.3f} en BeamNG)"
        )
    c_ac = beamng_to_ac(center)
    sx = 1.0 if side == "right" else -1.0

    def rel(node_id: str) -> tuple[float, float, float]:
        p = beamng_to_ac(nodes[node_id].pos)
        return (sx * (p[0] - c_ac[0]), p[1] - c_ac[1], p[2] - c_ac[2])

    (top, top_piv), (bot, bot_piv) = sorted(
        arms.items(), key=lambda kv: -nodes[kv[0]].pos[2]
    )
    rod_k, (rod_c,) = next(iter(rods.items()))

    def front_rear(pivots: list[str]) -> tuple[str, str]:
        a, b = sorted(pivots, key=lambda n: -beamng_to_ac(nodes[n].pos)[2])
        return a, b  # (más adelantado, más atrasado)

    tf, tr = front_rear(top_piv)
    bf, br = front_rear(bot_piv)
    mapping = {
        "WBCAR_TOP_FRONT": tf,
        "WBCAR_TOP_REAR": tr,
        "WBCAR_BOTTOM_FRONT": bf,
        "WBCAR_BOTTOM_REAR": br,
        "WBCAR_STEER": rod_c,
        "WBTYRE_TOP": top,
        "WBTYRE_BOTTOM": bot,
        "WBTYRE_STEER": rod_k,
    }
    cand = DwbCandidate(
        axle=axle,
        wheel=wheel_name,
        points={k: rel(n) for k, n in mapping.items()},
        nodes=mapping,
        hub_nodes=(wheel["node1"], wheel["node2"]),
        knuckle=knuckle,
        evidence=[
            "identificación estructural: vigas NORMAL buje–mangueta–chasis "
            "(src/attrs/candidates_dwb.py)",
            f"muelle-amortiguador (excluido de los enlaces): {coilover}",
            "marco y significado de los puntos: "
            "docs/research/ac_suspension_coordinates.md §2.1-2.2, "
            "§7 [Confirmed]",
        ],
        caveats=[
            "posiciones en reposo de diseño (sin carga); BeamNG no se ha ejecutado",
            "la bieleta interior (WBCAR_STEER) es un nodo "
            "de la cremallera; su recorrido no se representa",
            "estabilidad en AC sin probar (ac_suspension_coordinates.md "
            "§7, incógnita 2)",
        ],
    )
    return cand


def axle_wheels(resolved: ResolvedConfig, side: str = "right") -> dict[str, str]:
    """Rueda de un lado de cada eje, por posición (delante = menor y en BeamNG)."""
    nodes = {n.id: n for n in collect_nodes(resolved)}
    found = []
    for w in _wheel_rows(resolved):
        c = [(a + b) / 2 for a, b in zip(nodes[w["node1"]].pos, nodes[w["node2"]].pos)]
        if (c[0] < 0) == (side == "right"):
            found.append((c[1], w["name"]))
    found.sort()
    if len(found) != 2:
        raise StructureError(f"se esperaban 2 ruedas del lado {side}, hay {len(found)}")
    return {"FRONT": found[0][1], "REAR": found[1][1]}


def symmetry_error(resolved: ResolvedConfig, axle: str) -> float:
    """Máxima diferencia (m) entre los puntos de la rueda derecha y los de la izquierda
    reflejada."""
    right = measure_dwb(resolved, axle_wheels(resolved, "right")[axle], axle, "right")
    left = measure_dwb(resolved, axle_wheels(resolved, "left")[axle], axle, "left")
    return max(
        abs(a - b)
        for k in right.points
        for a, b in zip(right.points[k], left.points[k])
    )


def dwb_candidates(resolved: ResolvedConfig) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for axle, wheel in axle_wheels(resolved).items():
        try:
            out[axle] = measure_dwb(resolved, wheel, axle).to_dict()
        except StructureError as exc:
            out[axle] = {"axle": axle, "wheel": wheel, "error": str(exc)}
    return out
