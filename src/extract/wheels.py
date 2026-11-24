"""Ruedas definidas por ``pressureWheels``.

Reglas y su estado:

* Los modificadores de ``pressureWheels`` continúan de una pieza activa a la
  siguiente, en orden de recorrido. [confirmado: la pieza "wheeldata" que crea
  las ruedas no define radio ni pesos; vienen de la pieza de ruedas. Sin esta
  continuidad la masa total sería 1149 kg en vez de 1226]
* Masa generada por rueda = ``numRays · 2 · (hubNodeWeight + nodeWeight)``
  (dos anillos de ``numRays`` nodos para la llanta y dos para el neumático).
  [confirmado: con esta fórmula la masa total del coche = 1226,3 kg frente a
  1226 kg de la referencia]
* Centro de rueda = punto medio de ``node1`` y ``node2`` (nodos del eje).
  [derivado de los datos; coincide con la posición de los flexbodies]
* Inercia de giro ≈ anillos de llanta en ``hubRadius`` + anillos de neumático en
  ``radius``: ``I = numRays·2·(hubNodeWeight·hubRadius² + nodeWeight·radius²)``.
  [HIPÓTESIS: supone dónde coloca BeamNG los nodos generados; excluye nodos del
  eje (≈ sobre el eje de giro) y disco de freno]
* Para el CdG, la masa generada se sitúa en el centro de la rueda. [HIPÓTESIS:
  anillos simétricos respecto al centro del neumático]

``wheelOffset`` y ``offsetFromNode`` desplazan el centro como ``addPressureWheel``
del juego (ver ``_wheel``).

No soportado (→ UnsupportedGeometry): ``hasTire`` falso, propiedades necesarias ausentes, nodos del eje inexistentes, y las otras
secciones que generan ruedas (``wheels``, ``hubWheels``, ``monoHubWheels``…).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.jbeam import ResolvedConfig, evaluate, parse_table

from .nodes import Node, UnsupportedGeometry, Vec3, _number

OTHER_WHEEL_SECTIONS = (
    "wheels",
    "hubWheels",
    "hubWheelsTSV",
    "hubWheelsTSI",
    "monoHubWheels",
)
REQUIRED = ("numRays", "hubNodeWeight", "nodeWeight", "radius", "hubRadius")


@dataclass(frozen=True)
class Wheel:
    name: str
    part: str  # pieza que crea la rueda
    node1: str
    node2: str
    center: Vec3  # ejes BeamNG
    radius: float
    hub_radius: float
    tire_width: float | None
    hub_width: float | None
    num_rays: int
    hub_node_weight: float
    tire_node_weight: float
    generated_mass: float  # kg
    spin_inertia: float  # kg·m² (hipótesis, ver módulo)
    brake_torque_raw: Any  # leído, sin interpretar (fase 5)


def collect_wheels(resolved: ResolvedConfig, nodes: list[Node]) -> list[Wheel]:
    variables = resolved.variable_values()
    by_id = {n.id: n for n in nodes}
    state: dict[str, Any] = {}
    wheels: list[Wheel] = []
    for active in resolved.active:
        for section in OTHER_WHEEL_SECTIONS:
            if section in active.part.data:
                raise UnsupportedGeometry(
                    f"'{active.name}' usa '{section}' "
                    "(solo pressureWheels está verificado)"
                )
        if "pressureWheels" not in active.part.data:
            continue
        table = parse_table(active.part.data["pressureWheels"], initial_modifiers=state)
        state = table.final_modifiers
        for row in table.rows:
            wheels.append(_wheel(active.name, row, by_id, variables))
    return wheels


def _wheel(part: str, row, by_id: dict[str, Node], variables) -> Wheel:
    name = row.get("name")
    where = f"'{part}' rueda {name!r}"
    used = (
        *REQUIRED,
        "hasTire",
        "wheelOffset",
        "tireWidth",
        "hubWidth",
        "offsetFromNode",
    )
    props = {
        k: evaluate(row.properties[k], variables) for k in used if k in row.properties
    }
    missing = [k for k in REQUIRED if props.get(k) is None]
    if missing:
        raise UnsupportedGeometry(f"{where}: faltan {missing}")
    if props.get("hasTire", True) is not True:
        raise UnsupportedGeometry(f"{where}: hasTire = {props.get('hasTire')!r}")
    n1, n2 = row.get("node1:"), row.get("node2:")
    for node_id in (n1, n2):
        if node_id not in by_id:
            raise UnsupportedGeometry(f"{where}: nodo de eje {node_id!r} inexistente")
    p1, p2 = by_id[n1].pos, by_id[n2].pos
    center = tuple((a + b) / 2 for a, b in zip(p1, p2))
    # addPressureWheel (BeamNG 0.38, sections/wheels.lua:1298): eje = normalizado
    # (node2 - node1); offsetFromNode (1|2) coloca el centro a hubWidth/2 de ese nodo;
    # despues, centro += eje * wheelOffset (sin intercambio de nodos por z, que es de
    # ``wheels``). [confirmado leyendo el codigo del juego]
    axis_vec = tuple(b - a for a, b in zip(p1, p2))
    length = sum(c * c for c in axis_vec) ** 0.5
    if length == 0:
        raise UnsupportedGeometry(f"{where}: nodos de eje coincidentes")
    axis = tuple(c / length for c in axis_vec)
    from_node = props.get("offsetFromNode")
    if isinstance(from_node, (int, float)) and not isinstance(from_node, bool):
        if props.get("hubWidth") is None:
            raise UnsupportedGeometry(f"{where}: offsetFromNode sin hubWidth")
        half = 0.5 * _number(props["hubWidth"], f"{where}.hubWidth")
        if from_node == 1:
            center = tuple(a + half * x for a, x in zip(p1, axis))
        elif from_node == 2:
            center = tuple(b - half * x for b, x in zip(p2, axis))
    offset = props.get("wheelOffset")
    if offset not in (None, 0, 0.0):
        center = tuple(
            c + _number(offset, f"{where}.wheelOffset") * x
            for c, x in zip(center, axis)
        )
    rays_f = _number(props["numRays"], f"{where}.numRays")
    if rays_f != int(rays_f) or rays_f <= 0:
        raise UnsupportedGeometry(f"{where}: numRays = {rays_f}")
    rays = int(rays_f)
    hub_w = _number(props["hubNodeWeight"], f"{where}.hubNodeWeight")
    tire_w = _number(props["nodeWeight"], f"{where}.nodeWeight")
    radius = _number(props["radius"], f"{where}.radius")
    hub_r = _number(props["hubRadius"], f"{where}.hubRadius")
    width = props.get("tireWidth")
    hub_width = props.get("hubWidth")
    return Wheel(
        name=str(name),
        part=part,
        node1=n1,
        node2=n2,
        center=center,  # type: ignore[arg-type]
        radius=radius,
        hub_radius=hub_r,
        tire_width=None if width is None else _number(width, f"{where}.tireWidth"),
        hub_width=(
            None if hub_width is None else _number(hub_width, f"{where}.hubWidth")
        ),
        num_rays=rays,
        hub_node_weight=hub_w,
        tire_node_weight=tire_w,
        generated_mass=rays * 2 * (hub_w + tire_w),
        spin_inertia=rays * 2 * (hub_w * hub_r**2 + tire_w * radius**2),
        brake_torque_raw=row.properties.get("brakeTorque"),
    )
