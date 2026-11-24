"""Suspensión de la configuración activa (fase 5).

Ver docs/research/suspension_frenos_neumaticos_aero.md.

Capas:

* LEÍDO: tipo (nombre de la pieza de suspensión), rigidez y precarga de muelles,
  amortiguación (4 coeficientes + velocidad de corte), topes (recorrido a lo
  largo de la viga y rigidez límite), barras de torsión (Nm/rad).
* DERIVADO (geometría sin carga, ejes AC): eje del muelle, inclinación respecto a
  la vertical, longitud; caída y convergencia de cada rueda a partir de los
  nodos del eje (``node1``/``node2`` de ``pressureWheels``).
* HIPÓTESIS (solo en el informe, nunca en AC):
  - relación de palanca MR ≈ |componente vertical del eje del muelle|, es decir,
    el punto de anclaje inferior se mueve como el centro de rueda (traslación
    vertical pura). Sin cinemática completa no se puede afirmar;
  - rigidez/amortiguación en rueda = valor de la viga · MR².
* CONFIRMADO en el juego (G2.14, ``lua/vehicle/jbeam/stage2.lua``
  ``pushToPhysics``): con ``precompressionRange`` la longitud de reposo es
  ``L + precompressionRange`` (``beamPrecompression = (L + r)/L``), así que la fuerza
  del muelle lineal en la pose del JBeam es ``beamSpring · precompressionRange``.

Identificación de vigas por PROPIEDADES (G2.14; antes, por el nombre
``spring_<rueda>``/``damper_<rueda>``, que el exportador 200409 no escribe), dentro
del subárbol de slots de la pieza de suspensión del eje y del lado de la rueda
(signo de x de los dos extremos = signo de x del centro de la rueda):

* muelle: ``beamType`` NORMAL o BOUNDED (muelle progresivo: ``beamLimitSpring`` fuera
  de los límites, p. ej. el Porsche), ``beamSpring`` > 0 y ``precompressionRange``;
* amortiguador: ``beamType`` BOUNDED, ``beamDamp`` > 0 y ``beamSpring`` = 0 (una NORMAL
  con rigidez 0 también amortigua, pero en los 38022/40340 hay otras así junto a las
  ruedas, p. ej. ``a18–rw1r``; ``stage2.lua`` solo pasa rebote y rápidos a las BOUNDED);
* tope: ``beamType`` BOUNDED con ``longBoundRange`` o ``shortBoundRange``.

Cada viga se asigna a la rueda cuyo centro está más cerca de su punto medio (así un eje
con las dos suspensiones en una misma pieza no mezcla las ruedas de un lado).

Debe haber exactamente un muelle por rueda (amortiguador y tope: si no, aviso). Si
existen vigas con el nombre antiguo, deben ser LAS MISMAS (si no, error): en los 10
vehículos con nombres del corpus coinciden (G2.14, ``inventario.py``).

Compatibilidad con la regla anterior (no cambia ningún vehículo del corpus): si las
propiedades no identifican ningún amortiguador pero existe ``damper_<rueda>``, vale el
nombrado; si no hay ningún tope BOUNDED, vale la regla anterior (viga con
``*BoundRange`` que comparte nodo con el muelle).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.coords import Vec3, beamng_to_ac
from src.jbeam import ResolvedConfig

from src.jbeam.tables import parse_table

from .components import Beam, collect_beams
from .linkage import LinkageResult, analyse_corner, rigid_link_beams
from .mass_geometry import MassGeometry, compute_mass_geometry
from .nodes import Node, collect_nodes
from .rigid_axle import analyse_rigid_axle, with_linkage
from .coupled_axle import virtualize_torsion_axle
from .steered_axle import virtualize_steered_axle
from .unsprung import UnsprungBody, UnsprungPartition, partition
from .wheels import collect_wheels

GRAVITY = 9.81  # m/s² (valor por defecto de BeamNG; no está en la muestra)
TYPE_BY_LABEL = (("macpherson", "STRUT"), ("strut", "STRUT"))


class SuspensionDataError(ValueError):
    """Datos de suspensión no soportados o incompletos."""


@dataclass(frozen=True)
class DamperData:
    bump: float  # N·s/m (beamDamp), a lo largo de la viga
    rebound: float | None
    bump_fast: float | None
    rebound_fast: float | None
    velocity_split: float | None  # m/s a lo largo de la viga
    # G2.17: umbral propio de la extensión (beamDampVelocitySplitRebound; stage2.lua: si
    # falta, el de beamDampVelocitySplit) y frecuencia del filtro (dampCutoffHz)
    velocity_split_rebound: float | None = None
    cutoff_hz: float | None = None


@dataclass(frozen=True)
class BumpStopData:
    """G2.17: viga de tope leída tal cual (a lo largo de la viga)."""

    nodes: tuple[str, str]
    short_bound_range: float | None  # m desde la longitud de creación (compresión)
    long_bound_range: float | None  # m desde la longitud de creación (extensión)
    bound_zone: float | None  # m de transición (stage2.lua: 1 si falta)
    limit_spring: float | None  # N/m (beamLimitSpring)
    limit_damp: float | None  # N·s/m (beamLimitDamp)
    limit_damp_rebound: float | None  # N·s/m (beamLimitDampRebound)
    spawn_length: float  # m, distancia de los nodos en la pose del JBeam


@dataclass(frozen=True)
class CornerSuspension:
    wheel: str
    side: str
    spring_rate: float  # N/m a lo largo de la viga (leído)
    spring_preload_range: float | None  # m (precompressionRange, leído)
    spring_axis_ac: Vec3  # unitario, de abajo arriba (derivado)
    spring_length: float  # m, sin carga (derivado)
    spring_inclination_deg: float  # respecto a la vertical (derivado)
    damper: DamperData | None
    bumpstop_travel: float | None  # m a lo largo de la viga del tope (leído)
    bumpstop_rate: float | None  # N/m (beamLimitSpring, leído)
    camber_deg: float  # negativo = parte alta hacia dentro (derivado)
    toe_deg: float  # positivo = convergencia (toe-in) (derivado)
    motion_ratio_hyp: float  # hipótesis (ver módulo)
    wheel_rate_hyp: float  # hipótesis
    preload_at_wheel_hyp: float | None  # N, hipótesis
    sprung_corner_load: float  # N, cota superior (masa no suspendida = cota inferior)
    # G2.16: enlaces de la mangueta y equivalente DWB (src/extract/linkage.py); None si
    # no se analizó (p. ej. llamada sin la lista de vigas rígidas)
    linkage: LinkageResult | None = None
    # G2.17: viga de tope completa (solo si se identificó una)
    bumpstop: BumpStopData | None = None
    # G2.44: nodos del muelle y del amortiguador y ``beamPrecompression`` del muelle (eje
    # rígido, :mod:`src.extract.rigid_axle`)
    spring_nodes: tuple[str, str] | None = None
    damper_nodes: tuple[str, str] | None = None
    spring_precompression: float | None = None


@dataclass
class AxleSuspension:
    index: int
    label: str | None  # nombre de la pieza de suspensión
    ac_type: str | None  # solo si la etiqueta lo respalda
    corners: list[CornerSuspension]
    torsionbars: list[dict[str, Any]]
    # G2.14: cuerpos no suspendidos de las ruedas del eje (cierre rígido, unsprung.py)
    unsprung: list[UnsprungBody] = field(default_factory=list)


@dataclass
class Suspension:
    axles: list[AxleSuspension]
    issues: list[str] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)
    partition: UnsprungPartition | None = None  # G2.14


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def camber_toe(inner: Vec3, outer: Vec3) -> tuple[float, float]:
    """Caída y convergencia (grados) del eje de rueda inner→outer, ejes BeamNG.

    * caída < 0 si el extremo exterior está más alto (parte alta de la rueda
      inclinada hacia dentro);
    * convergencia > 0 (toe-in) si el extremo exterior está más adelantado
      (−Y en BeamNG). Válido para ambos lados.
    """
    a = _sub(outer, inner)
    lateral = abs(a[0])
    if lateral == 0:
        raise SuspensionDataError("eje de rueda sin componente lateral")
    camber = -math.degrees(math.atan2(a[2], lateral))
    toe = math.degrees(math.atan2(-a[1], lateral))
    return camber, toe


def _float(beam: Beam, key: str, variables) -> float | None:
    value = beam.value(key, variables)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SuspensionDataError(f"viga {beam.nodes} {key}={value!r} no numérico")
    return float(value)


def _btype(beam: Beam) -> str:
    """``beamType`` vigente sin ``|`` ni espacios (``| BOUNDED`` → ``BOUNDED``)."""
    t = beam.props.get("beamType", "|NORMAL")
    return str(t).replace(" ", "").lstrip("|").upper() or "NORMAL"


def classify_beam(beam: Beam, variables) -> set[str]:
    """Papel de una viga de suspensión por sus propiedades (ver docstring del módulo).

    No mira el nombre. Un valor no numérico se trata como ausente (no clasifica).
    """

    def num(key: str) -> float | None:
        v = beam.value(key, variables)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        return float(v) if ok else None

    out: set[str] = set()
    kind = _btype(beam)
    spring, damp = num("beamSpring"), num("beamDamp")
    if (
        kind in ("NORMAL", "BOUNDED")
        and spring
        and spring > 0
        and num("precompressionRange") is not None
    ):
        out.add("spring")
    if kind == "BOUNDED":
        if damp and damp > 0 and spring == 0:
            out.add("damper")
        if "longBoundRange" in beam.props or "shortBoundRange" in beam.props:
            out.add("bumpstop")
    return out


def _subtree(resolved: ResolvedConfig, root: str) -> set[str]:
    """Pieza ``root`` y todas sus descendientes activas (árbol de slots)."""
    out = {root}
    for active in resolved.active:
        p = active.parent
        while p is not None:
            if p == root:
                out.add(active.name)
                break
            pa = resolved.get(p)
            p = pa.parent if pa else None
    return out


def _same_side(beam: Beam, nodes: dict[str, Node], sign: int) -> bool:
    xs = [nodes[n].pos[0] for n in beam.nodes if n in nodes]
    return len(xs) == len(beam.nodes) and all((x > 0) - (x < 0) == sign for x in xs)


def _nearest_wheel(beam: Beam, nodes: dict[str, Node], centres: dict) -> str | None:
    """Rueda cuyo centro está más cerca del punto medio de la viga (G2.14)."""
    pts = [nodes[n].pos for n in beam.nodes if n in nodes]
    if len(pts) != len(beam.nodes) or not centres:
        return None
    mid = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    return min(centres, key=lambda w: _norm(_sub(mid, centres[w])))


def collect_rails(resolved: ResolvedConfig) -> list[tuple[str, str, str]]:
    """G2.19: ``(nodo deslizante, extremo 1, extremo 2)`` de cada fila de
    ``slidenodes`` con ``attached`` y ``fixToRail`` verdaderos cuyo raíl (``rails``)
    tiene exactamente 2 nodos y no es cerrado (``looped``).

    Formato (comentarios de los JBeam del exportador: «rails form a line between 2
    nodes that slidenodes will slide on … strut shafts»): ``rails`` es un objeto
    ``{nombre: {"links:": [n1, n2], "looped": …}}``; ``slidenodes`` una tabla
    ``["id:", "railName", "attached", "fixToRail", "tolerance", "spring", …]``. Los
    raíles de todas las piezas activas se unen por nombre (el último gana, como el
    resto de secciones objeto); no se interpreta ``tolerance`` (los del corpus valen 0).
    """
    rails: dict[str, Any] = {}
    slides: list[tuple[str, str]] = []
    for active in resolved.active:
        sec = active.part.data.get("rails")
        if isinstance(sec, dict):
            rails.update(sec)
        tab = active.part.data.get("slidenodes")
        if tab is None:
            continue
        for row in parse_table(tab).rows:
            node, rail = row.get("id:"), row.get("railName")
            if (
                isinstance(node, str)
                and isinstance(rail, str)
                and row.get("attached") is True
                and row.get("fixToRail") is True
            ):
                slides.append((node, rail))
    out = []
    for node, rail in slides:
        spec = rails.get(rail)
        if not isinstance(spec, dict) or spec.get("looped"):
            continue
        links = spec.get("links:")
        if (
            isinstance(links, list)
            and len(links) == 2
            and all(isinstance(n, str) for n in links)
        ):
            out.append((node, links[0], links[1]))
    return out


def build_suspension(
    resolved: ResolvedConfig, geometry: MassGeometry | None = None
) -> Suspension:
    geometry = geometry or compute_mass_geometry(resolved)
    variables = resolved.variable_values()
    nodes: dict[str, Node] = {n.id: n for n in collect_nodes(resolved)}
    wheels = {w.name: w for w in collect_wheels(resolved, list(nodes.values()))}
    beams = collect_beams(resolved)
    by_name: dict[str, list[Beam]] = {}
    for beam in beams:
        if beam.name:
            by_name.setdefault(beam.name, []).append(beam)
    issues: list[str] = []
    if len(geometry.axles) != 2:
        raise SuspensionDataError("se necesitan exactamente 2 ejes (fase 2)")

    torsionbars = collect_beams(resolved, "torsionbars")
    rigid = rigid_link_beams(beams, variables)  # G2.16
    rails = collect_rails(resolved)  # G2.19
    split = partition(resolved, geometry, nodes, wheels)
    centres = {
        w.name: tuple(
            (a + b) / 2 for a, b in zip(nodes[w.node1].pos, nodes[w.node2].pos)
        )
        for w in wheels.values()
    }
    summaries = {w.name: w for w in geometry.wheels}
    front_fraction = geometry.front_axle_fraction

    axles = []
    for axle in geometry.axles:
        # Pieza de suspensión = padre de la pieza que crea las ruedas del eje
        # (estructura del árbol de slots, no convención de nombres).
        owners = {resolved.get(wheels[w].part).parent for w in axle.wheels}
        if len(owners) != 1:
            raise SuspensionDataError(f"eje {axle.index}: ruedas de piezas distintas")
        owner = resolved.get(owners.pop())
        info = owner.part.data.get("information", {}) if owner else {}
        label = info.get("name") if isinstance(info, dict) else None
        ac_type = None
        if label:
            low = label.lower()
            ac_type = next((t for key, t in TYPE_BY_LABEL if key in low), None)
        if ac_type is None:
            issues.append(f"eje {axle.index} ('{label}'): sin tipo AC respaldado")
        parts = _subtree(resolved, owner.name) if owner else set()
        axle_beams = [b for b in beams if b.part in parts]
        share = front_fraction if axle.index == 0 else 1 - front_fraction
        # eje rígido direccional: DWB equivalente (src/extract/steered_axle.py)
        c_nodes, c_rigid = nodes, rigid
        virt = _steered_axle(axle, wheels, nodes, rigid, axle_beams, split, variables)
        if virt is not None:
            c_nodes, c_rigid = virt[0], virt[1]
            issues.append(
                f"eje {axle.index} ('{label}'): eje rígido direccional; DWB "
                "equivalente (AC no tiene eje rígido delantero con dirección)"
            )

        def build_corners(c_nodes, c_rigid, torsion_info=None):
            return [
                _corner(
                    wheel_name,
                    wheels[wheel_name],
                    summaries[wheel_name],
                    c_nodes,
                    [
                        b
                        for b in axle_beams
                        if _nearest_wheel(b, nodes, centres) == wheel_name
                    ],
                    by_name,
                    variables,
                    geometry.total_mass * GRAVITY * share / len(axle.wheels),
                    issues,
                    c_rigid,
                    torsionbars,
                    rails,
                    (torsion_info or {}).get(wheel_name),
                )
                for wheel_name in axle.wheels
            ]

        corners = build_corners(c_nodes, c_rigid)
        if virt is not None:
            for c in corners:
                if c.linkage is not None:
                    c.linkage.reason += (
                        "; EQUIVALENTE de eje rígido direccional (puntos virtuales, "
                        "src/extract/steered_axle.py): no son los anclajes físicos"
                    )
        axle_bars = []
        for bar in torsionbars:
            ys = [nodes[n].pos[1] for n in bar.nodes if n in nodes]
            if not ys:
                continue
            mean_long = -sum(ys) / len(ys)  # z AC
            nearest = min(
                geometry.axles, key=lambda a: abs(a.longitudinal_ac - mean_long)
            )
            if nearest.index == axle.index:
                axle_bars.append(
                    {
                        "part": bar.part,
                        "name": bar.name,
                        "nodes": list(bar.nodes),
                        "spring_Nm_per_rad": _float(bar, "spring", variables),
                        # G2.17: stage2.lua: spring2 = spring si falta
                        "spring2_Nm_per_rad": _float(bar, "spring2", variables),
                        "damp": _float(bar, "damp", variables),
                    }
                )
        bodies = []
        for w in axle.wheels:
            body = split.body_of(w)
            if body is not None and body not in bodies:
                bodies.append(body)
        # G2.44: eje rígido con enlaces de barra (equivalente a TYPE=AXLE de AC)
        if (
            len(bodies) == 1
            and bodies[0].coupled
            and corners
            and all(
                c.linkage is not None and c.linkage.kind == "coupled" for c in corners
            )
        ):
            res_axle = analyse_rigid_axle(
                wheels=wheels,
                axle_wheels=list(axle.wheels),
                nodes=nodes,
                body=bodies[0],
                rigid_beams=rigid,
                corners=corners,
                variables=variables,
            )
            if res_axle is not None:
                corners = [with_linkage(c, r) for c, r in zip(corners, res_axle)]
            else:
                # eje torsional (src/extract/coupled_axle.py): DWB equivalente por rueda
                tube = set(bodies[0].nodes) | {
                    n for n, _ in bodies[0].adjacent_mechanism
                }
                exclude = {
                    frozenset(b.nodes)
                    for b in axle_beams
                    if len(b.nodes) == 2
                    and classify_beam(b, variables) & {"spring", "damper", "bumpstop"}
                }
                tvirt = virtualize_torsion_axle(
                    hubs={w: (wheels[w].node1, wheels[w].node2) for w in axle.wheels},
                    axle_nodes=tube,
                    nodes=nodes,
                    rigid_beams=rigid,
                    exclude=exclude,
                )
                if tvirt is not None:
                    tcorners = build_corners(tvirt[0], tvirt[1], tvirt[2])
                    if all(
                        c.linkage is not None and c.linkage.kind == "dwb_coupled"
                        for c in tcorners
                    ):
                        corners = tcorners
                        issues.append(
                            f"eje {axle.index} ('{label}'): eje torsional; DWB "
                            "equivalente por semieje (AC no tiene viga de torsión; "
                            "src/extract/coupled_axle.py)"
                        )
        axles.append(
            AxleSuspension(axle.index, label, ac_type, corners, axle_bars, bodies)
        )

    status = {
        "spring_damper_bumpstop_values": "leído (a lo largo de la viga)",
        "geometry": "derivado, pose del JBeam sin carga",
        "camber_toe": "derivado de los nodos del eje de rueda",
        "ac_type": "leído de la etiqueta de la pieza "
        "(solo 'MacPherson/Strut' -> STRUT)",
        "motion_ratio": "HIPÓTESIS: traslación vertical pura del anclaje inferior",
        "wheel_rates": "HIPÓTESIS (dependen de la relación de palanca)",
        "preload": "fuerza del muelle en la pose del JBeam = beamSpring · "
        "precompressionRange (CONFIRMADO, stage2.lua); en rueda: HIPÓTESIS (MR)",
        "gravity": f"{GRAVITY} m/s² (por defecto de BeamNG; no está en la muestra)",
        "unsprung": "medido: cierre rígido de nodos (src/extract/unsprung.py); "
        "los nodos de mecanismo no se asignan",
    }
    return Suspension(axles, issues, status, split)


def _steered_axle(axle, wheels, nodes, rigid, axle_beams, split, variables):
    """Nodos y vigas con el eje rígido direccional sustituido por su DWB equivalente,
    o ``None`` si el eje no es de ese tipo (dos mangutas no suspendidas separadas que
    comparten tubo)."""
    bodies = [split.body_of(w) for w in axle.wheels]
    if (
        len(axle.wheels) != 2
        or any(b is None for b in bodies)
        or bodies[0] is bodies[1]
    ):
        return None
    tube = set()
    for b in bodies:
        tube |= set(b.nodes) | {n for n, _ in b.adjacent_mechanism}
    if not set(a for a, _ in bodies[0].adjacent_mechanism) & set(
        a for a, _ in bodies[1].adjacent_mechanism
    ):
        return None  # sin tubo común: ruedas independientes
    exclude = {
        frozenset(b.nodes)
        for b in axle_beams
        if len(b.nodes) == 2
        and classify_beam(b, variables) & {"spring", "damper", "bumpstop"}
    }
    hubs = {w: (wheels[w].node1, wheels[w].node2) for w in axle.wheels}
    return virtualize_steered_axle(
        hubs=hubs, axle_nodes=tube, nodes=nodes, rigid_beams=rigid, exclude=exclude
    )


def _pick(role: str, wheel_name: str, found: list[Beam], named: list[Beam]):
    """Vigas de un papel; si hay vigas con el nombre antiguo, deben ser las mismas.

    Excepción de compatibilidad (solo ``damper``): sin ninguna identificada por
    propiedades, valen las nombradas (regla anterior a G2.14)."""
    if named and not found and role == "damper":
        return list(named)
    if named:
        key = sorted(sorted(b.nodes) for b in named)
        if sorted(sorted(b.nodes) for b in found) != key:
            raise SuspensionDataError(
                f"rueda {wheel_name}: las vigas '{role}_{wheel_name}' "
                f"{[b.nodes for b in named]} no son las identificadas por propiedades "
                f"{[b.nodes for b in found]}"
            )
    return found


def _corner(
    wheel_name,
    wheel,
    summary,
    nodes,
    beams,
    by_name,
    variables,
    corner_load,
    issues,
    rigid=None,
    torsionbars=(),
    rails=(),
    torsion=None,
) -> CornerSuspension:
    a1, a2 = nodes[wheel.node1].pos, nodes[wheel.node2].pos
    cx = (a1[0] + a2[0]) / 2
    sign = (cx > 0) - (cx < 0)
    if sign == 0:
        raise SuspensionDataError(f"rueda {wheel_name}: centro en el plano medio")
    side = [b for b in beams if len(b.nodes) == 2 and _same_side(b, nodes, sign)]
    roles = {id(b): classify_beam(b, variables) for b in side}
    springs = _pick(
        "spring",
        wheel_name,
        [b for b in side if "spring" in roles[id(b)]],
        by_name.get(f"spring_{wheel_name}", []),
    )
    dampers = _pick(
        "damper",
        wheel_name,
        [b for b in side if "damper" in roles[id(b)]],
        by_name.get(f"damper_{wheel_name}", []),
    )
    if len(springs) != 1:
        raise SuspensionDataError(
            f"rueda {wheel_name}: se esperaba un muelle (viga con beamSpring > 0 "
            f"y precompressionRange) en la suspensión de su lado, hay {len(springs)}"
        )
    spring = springs[0]
    rate = _float(spring, "beamSpring", variables)
    if not rate or rate <= 0:
        raise SuspensionDataError(f"spring_{wheel_name}: beamSpring no positivo")
    a, b = (nodes[n].pos for n in spring.nodes)
    lower, upper = (a, b) if a[2] <= b[2] else (b, a)
    axis_b = _sub(upper, lower)
    length = _norm(axis_b)
    axis_ac = beamng_to_ac(tuple(c / length for c in axis_b))  # type: ignore[arg-type]
    inclination = math.degrees(math.acos(min(1.0, abs(axis_ac[1]))))
    mr = abs(axis_ac[1])

    damper = None
    if len(dampers) == 1:
        d = dampers[0]
        damper = DamperData(
            bump=_float(d, "beamDamp", variables) or 0.0,
            rebound=_float(d, "beamDampRebound", variables),
            bump_fast=_float(d, "beamDampFast", variables),
            rebound_fast=_float(d, "beamDampReboundFast", variables),
            velocity_split=_float(d, "beamDampVelocitySplit", variables),
            velocity_split_rebound=_float(d, "beamDampVelocitySplitRebound", variables),
            cutoff_hz=_float(d, "dampCutoffHz", variables),
        )
    else:
        issues.append(
            f"rueda {wheel_name}: {len(dampers)} amortiguadores identificados"
        )

    # tope: viga BOUNDED con *BoundRange del lado de la rueda; si hay varias, la que
    # comparte nodo con el muelle (regla anterior a G2.14)
    stops = [b for b in side if "bumpstop" in roles[id(b)]]
    if len(stops) > 1:
        stops = [b for b in stops if set(b.nodes) & set(spring.nodes)]
    if not stops:  # regla anterior a G2.14 (compatibilidad)
        stops = [
            b
            for b in beams
            if ("longBoundRange" in b.props or "shortBoundRange" in b.props)
            and set(b.nodes) & set(spring.nodes)
        ]
    travel = rate_limit = None
    stop_data = None
    if len(stops) == 1:
        travel = _float(stops[0], "longBoundRange", variables) or _float(
            stops[0], "shortBoundRange", variables
        )
        rate_limit = _float(stops[0], "beamLimitSpring", variables)
        s0 = stops[0]
        stop_data = BumpStopData(
            nodes=(s0.nodes[0], s0.nodes[1]),
            short_bound_range=_float(s0, "shortBoundRange", variables),
            long_bound_range=_float(s0, "longBoundRange", variables),
            bound_zone=_float(s0, "boundZone", variables),
            limit_spring=rate_limit,
            limit_damp=_float(s0, "beamLimitDamp", variables),
            limit_damp_rebound=_float(s0, "beamLimitDampRebound", variables),
            spawn_length=math.dist(nodes[s0.nodes[0]].pos, nodes[s0.nodes[1]].pos),
        )
    else:
        issues.append(f"rueda {wheel_name}: {len(stops)} topes identificados")

    n1, n2 = nodes[wheel.node1].pos, nodes[wheel.node2].pos
    inner, outer = (n1, n2) if abs(n1[0]) < abs(n2[0]) else (n2, n1)
    camber, toe = camber_toe(inner, outer)

    preload_range = _float(spring, "precompressionRange", variables)
    sprung = corner_load - summary.assembly_mass * GRAVITY
    linkage = None
    if rigid is not None:  # G2.16
        try:
            linkage = analyse_corner(
                wheel=wheel_name,
                hub=(wheel.node1, wheel.node2),
                side=sign,
                nodes=nodes,
                rigid_beams=rigid,
                spring=spring,
                damper=dampers[0] if len(dampers) == 1 else None,
                variables=variables,
                bumpstop=stops[0] if len(stops) == 1 else None,
                torsionbars=torsionbars,
                rails=rails,
                torsion=torsion,
            )
        except (ValueError, ArithmeticError) as exc:
            linkage = LinkageResult(
                wheel_name, "unsupported", f"análisis de enlaces fallido: {exc}"
            )
    return CornerSuspension(
        wheel=wheel_name,
        side=summary.side,
        spring_rate=rate,
        spring_preload_range=preload_range,
        spring_axis_ac=axis_ac,
        spring_length=length,
        spring_inclination_deg=inclination,
        damper=damper,
        bumpstop_travel=travel,
        bumpstop_rate=rate_limit,
        camber_deg=camber,
        toe_deg=toe,
        motion_ratio_hyp=mr,
        wheel_rate_hyp=rate * mr**2,
        preload_at_wheel_hyp=(
            None if preload_range is None else rate * preload_range * mr
        ),
        sprung_corner_load=sprung,
        linkage=linkage,
        bumpstop=stop_data,
        spring_nodes=(spring.nodes[0], spring.nodes[1]),
        damper_nodes=(
            (dampers[0].nodes[0], dampers[0].nodes[1]) if len(dampers) == 1 else None
        ),
        spring_precompression=_float(spring, "beamPrecompression", variables),
    )
