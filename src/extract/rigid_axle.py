"""Eje rígido (``linkage = rigid_axle``) con enlaces de barra: equivalente al
``TYPE=AXLE`` de AC (G2.44).

BeamNG: las dos ruedas del eje son un único cuerpo rígido (cierre rígido de nodos,
:mod:`src.extract.unsprung`); lo une a la carrocería un conjunto de vigas ``NORMAL``
rígidas
(brazos y barra Panhard) y lo suspenden un muelle y un amortiguador por lado.

AC [C] (suspensión de eje de AC,
``output/g217_…/acs_suspension/c2``):
cada ``J<n>_CAR``/``J<n>_AXLE`` crea una ``DistanceJoint`` entre un punto de la
carrocería y otro
del eje; ambos se expresan RELATIVOS AL CENTRO DEL EJE en reposo (``axle->localToWorld``
y
después ``body->worldToLocal``). El muelle (suspensión de eje de AC) va en el punto
``centro + semivía·ATTACH_REL_POS`` con su eje vertical, de modo que ``SPRING_RATE`` es
la
rigidez VERTICAL en ese punto.

Equivalencia en heave puro (el eje sube sin girar; primer orden):

* muelle y amortiguador: palanca = componente vertical de su eje (``cos`` de la
inclinación);
* topes: recorrido vertical = rango a lo largo de la viga / palanca de la viga;
* enlaces: coordenadas de los nodos menos el centro del eje (media de los centros de
rueda),
  con ``(x, y, z)_AC = (Bx, Bz, −By)``.

Sin cinemática: no se mide la trayectoria del eje con las barras (el modelo de AC lleva
las
mismas barras como distancias, por lo que esa trayectoria sale del propio motor de AC).
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from src.coords import beamng_to_ac

from .linkage import LinkageResult

Vec = tuple[float, float, float]
MAX_LINKS = (
    8  # J0…J7 en el catálogo (G2.44: composed_fox tiene 7 con los cruces del montaje)
)
MIN_LINKS = (
    3  # menos de tres distancias no fijan la orientación del eje (hay 4–5 en AC)
)


def _mid(nodes: dict[str, Any], hub: tuple[str, str]) -> Vec:
    a, b = nodes[hub[0]].pos, nodes[hub[1]].pos
    return tuple((p + q) / 2 for p, q in zip(a, b))  # type: ignore[return-value]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def axle_nodes(body: Any) -> set[str]:
    """Nodos del eje: cuerpo rígido + nodos de unión adyacentes (montajes)."""
    return set(body.nodes) | {n for n, _ in body.adjacent_mechanism}


def find_links(
    nodes: dict[str, Any],
    rigid_beams: list[Any],
    axle: set[str],
    exclude: set[frozenset[str]],
) -> list[tuple[str, str]]:
    """Vigas rígidas con un extremo en el eje y otro fuera (carrocería, eje)."""
    out: list[tuple[str, str]] = []
    for b in rigid_beams:
        if len(b.nodes) != 2:
            continue
        x, y = b.nodes
        if frozenset((x, y)) in exclude or x not in nodes or y not in nodes:
            continue
        if (x in axle) == (y in axle):
            continue
        pair = (y, x) if x in axle else (x, y)
        if pair not in out:
            out.append(pair)
    return out


def _cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _thin_to_five_dof(
    nodes: dict[str, Any], links: list[tuple[str, str]], centre: Vec
) -> list[tuple[str, str]]:
    """Descarta los enlaces que subirían el rango de las restricciones a 6.

    Cada distancia fija una fila ``[u, P×u]`` del jacobiano del cuerpo rígido (u =
    dirección de
    la viga, P = punto en el eje respecto al centro). Con rango 6 el eje queda bloqueado
    en AC
    (composed_fox: dos vigas de «rigidez lateral» del montaje cruzan los brazos y suben
    el rango
    de 5 a 6, G2.44). Un eje de barras necesita 1 GDL (el heave): se mantienen, en el
    orden de las
    vigas, los enlaces que no pasan de rango 5; los dependientes de los ya aceptados se
    conservan
    (no restringen más). Tolerancia relativa 1e-3."""
    basis: list[list[float]] = []
    kept: list[tuple[str, str]] = []
    for car_n, ax_n in links:
        a, b = nodes[ax_n].pos, nodes[car_n].pos
        d = [q - p for p, q in zip(a, b)]
        norm = math.sqrt(sum(x * x for x in d))
        if norm == 0:
            continue
        u = tuple(x / norm for x in d)
        p = tuple(q - c for q, c in zip(a, centre))
        row = list(u) + list(_cross(p, u))  # type: ignore[arg-type]
        res = row[:]
        for e in basis:
            k = sum(x * y for x, y in zip(res, e))
            res = [x - k * y for x, y in zip(res, e)]
        n = math.sqrt(sum(x * x for x in res))
        if n < 1e-3 * math.sqrt(sum(x * x for x in row)):  # dependiente: no añade rango
            kept.append((car_n, ax_n))
            continue
        if len(basis) >= 5:
            continue
        basis.append([x / n for x in res])
        kept.append((car_n, ax_n))
    return kept


def analyse_rigid_axle(
    *,
    wheels: dict[str, Any],
    axle_wheels: list[str],
    nodes: dict[str, Any],
    body: Any,
    rigid_beams: list[Any],
    corners: list[Any],
    variables: dict,
) -> list[LinkageResult] | None:
    """Un ``LinkageResult`` (``rigid_axle``) por rueda, o ``None`` si no hay un eje
    rígido
    con enlaces de barra reconocible (menos de ``MIN_LINKS`` enlaces, muelle ausente…).
    """
    if len(axle_wheels) != 2 or len(corners) != 2:
        return None
    axle = axle_nodes(body)
    centres = [_mid(nodes, (wheels[w].node1, wheels[w].node2)) for w in axle_wheels]
    centre = tuple((a + b) / 2 for a, b in zip(*centres))
    half_track = abs(centres[0][0] - centres[1][0]) / 2
    if half_track <= 0:
        return None
    exclude: set[frozenset[str]] = set()
    for c in corners:
        for nn in (c.spring_nodes, c.damper_nodes, getattr(c.bumpstop, "nodes", None)):
            if nn:
                exclude.add(frozenset(nn))
    links = find_links(nodes, rigid_beams, axle, exclude)
    links = _thin_to_five_dof(nodes, links, centre)
    # un eje con barras tiene 3–8 enlaces (el catálogo define J0…J7) sobre ≥ 3 montajes
    # distintos del
    # eje; un eje torsional (Ibiza: 14 vigas sobre 2 montajes) NO es un eje de barras
    if (
        not MIN_LINKS <= len(links) <= MAX_LINKS
        or len({a for _, a in links}) < MIN_LINKS
    ):
        return None
    link_data = []
    c_ac = beamng_to_ac(centre)
    for car_n, ax_n in links:
        jc = _sub(beamng_to_ac(nodes[car_n].pos), c_ac)
        ja = _sub(beamng_to_ac(nodes[ax_n].pos), c_ac)
        link_data.append((car_n, ax_n, jc, ja))
    out: list[LinkageResult] = []
    for c in corners:
        r = _corner_result(c, nodes, axle, centre, half_track, link_data, variables)
        if r is None:
            return None
        out.append(r)
    return out


def _vertical_ratio(nodes: dict[str, Any], pair: tuple[str, str]) -> float:
    a, b = (nodes[n].pos for n in pair)
    d = [q - p for p, q in zip(a, b)]
    n = math.sqrt(sum(x * x for x in d))
    return abs(d[2]) / n if n else 0.0  # z de BeamNG = arriba


def _corner_result(
    c: Any,
    nodes: dict[str, Any],
    axle: set[str],
    centre: Vec,
    half_track: float,
    links: list[tuple[str, str, Vec, Vec]],
    variables: dict,
) -> LinkageResult | None:
    if not c.spring_nodes:
        return None
    on = [n for n in c.spring_nodes if n in axle]
    if len(on) != 1:
        return None
    r = LinkageResult(
        c.wheel,
        "rigid_axle",
        f"eje rígido con {len(links)} enlaces de barra "
        "(distancias entre carrocería y eje)",
    )
    r.axle_links = links
    r.attach_rel_pos = round(abs(nodes[on[0]].pos[0] - centre[0]) / half_track, 6)
    mr = _vertical_ratio(nodes, tuple(c.spring_nodes))
    r.spring_motion_ratio = mr
    r.spring_motion_ratio_range = (mr, mr)
    r.rest_camber_deg, r.rest_toe_deg = c.camber_deg, c.toe_deg
    if c.damper_nodes:
        d_on = [n for n in c.damper_nodes if n in axle]
        if len(d_on) == 1:
            other = next(n for n in c.damper_nodes if n != d_on[0])
            r.damper_motion_ratio = _vertical_ratio(nodes, tuple(c.damper_nodes))
            # el eje sube ⇒ se acorta si el extremo del eje está por debajo del de la
            # carrocería
            r.damper_shortens_on_bump = nodes[d_on[0]].pos[2] < nodes[other].pos[2]
    s0, s1 = c.spring_nodes
    jl = math.dist(nodes[s0].pos, nodes[s1].pos)
    rng = c.spring_preload_range
    pc = None
    if rng is None:  # beamPrecompression sin precompressionRange (stage2.lua)
        pc = c.spring_precompression
    if isinstance(rng, (int, float)):
        r.spring_rest_compression = float(rng)  # (L + r) − L
    elif isinstance(pc, (int, float)):
        r.spring_rest_compression = jl * (float(pc) - 1.0)
    bs = c.bumpstop
    if bs is not None:
        b_on = [n for n in bs.nodes if n in axle]
        if len(b_on) == 1:
            bmr = _vertical_ratio(nodes, tuple(bs.nodes))
            other = next(n for n in bs.nodes if n != b_on[0])
            shortens_up = nodes[b_on[0]].pos[2] < nodes[other].pos[2]
            short, long_ = bs.short_bound_range, bs.long_bound_range
            up, dn = (short, long_) if shortens_up else (long_, short)
            if bmr > 1e-6:
                if up:
                    r.bumpstop_travel_up = up / bmr
                    r.bumpstop_motion_ratio_up = bmr
                if dn:
                    r.bumpstop_travel_down = dn / bmr
                    r.bumpstop_motion_ratio_down = bmr
            r.bumpstop_reason = ""
        else:
            r.bumpstop_reason = f"el tope {bs.nodes} no va del eje a la carrocería"
    else:
        r.bumpstop_reason = "sin viga de tope identificada"
    return r


def with_linkage(corner: Any, result: LinkageResult) -> Any:
    return dataclasses.replace(corner, linkage=result)


__all__ = ["analyse_rigid_axle", "axle_nodes", "find_links", "with_linkage"]
