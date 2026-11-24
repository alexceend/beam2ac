"""Aerodinámica de la configuración activa (fase 5), adaptador ``cefaero``.

``lua/controller/cefaero.lua`` está en la muestra (exportaciones de Automation):

* arrastre longitudinal: ``F = vehicleArea · ½ρv²`` (líneas 65-66 y 117-119) ⇒
  ``vehicleArea`` es Cd·A en m²; lateral ×dragModX y vertical ×dragModZ. El Lua
  lee ``dragNodes.dragModX``, pero el JBeam escribe ``"dragModX:"`` (con dos
  puntos, sintaxis de referencia a nodos: ``"dragNodes:"`` llega al Lua como
  ``dragNodes_nodes``, línea 233). Probablemente se usan los valores por
  defecto 1,25 / 1,5 (líneas 239-240); en cualquier caso coinciden con el JBeam.
* carga: ``factor = F_obj / (½·ρ₀·(v_obj/3,6)²)`` (líneas 157-163) con ρ₀ = la
  densidad del módulo (1,225 al cargar, línea 17), NO ``targetAirDensity``
  (se pasa pero no se usa). En marcha: ``F = ½·factor·ρ·v²`` en los nodos
  ``ref`` de cada grupo, en la dirección ref→up, con signo de carga hacia abajo
  (líneas 136-143).

⇒ Cl·A = F_obj / (½·1,225·v_obj²) [derivado; HIPÓTESIS: ρ₀ = 1,225 en el
momento de calcular el factor]. A la densidad del entorno ρ:
F(v) = F_obj·(ρ/1,225)·(v/v_obj)².

Otros modelos aerodinámicos (coches oficiales: triángulos) → no soportado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.coords import Vec3, beamng_to_ac
from src.jbeam import ResolvedConfig, evaluate, parse_table
from src.jbeam.merge import merge_section

from .nodes import collect_nodes

RHO_FACTOR = 1.225  # cefaero.lua:17 (densidad del módulo al cargar)


class AeroDataError(ValueError):
    """Modelo aerodinámico no soportado o incompleto."""


@dataclass(frozen=True)
class DownforcePoint:
    is_front: bool
    ref_node: str
    up_node: str
    position_ac: Vec3


@dataclass
class Aero:
    cda: float  # m², arrastre longitudinal (leído: vehicleArea)
    drag_mod_lateral: float
    drag_mod_vertical: float
    drag_node_ac: Vec3 | None
    target_front_n: float
    target_rear_n: float
    target_speed_kmh: float
    target_air_density_unused: float | None
    cla_front: float  # m², derivado con ρ₀ = 1,225
    cla_rear: float
    constant_front_n: float
    constant_rear_n: float
    points: list[DownforcePoint]
    status: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def _uses_cefaero(resolved: ResolvedConfig) -> bool:
    for active in resolved.active:
        for row in parse_table(active.part.data.get("controller", [])).rows:
            if row.get("fileName") == "cefaero":
                return True
    return False


def _triangle_aero(resolved: ResolvedConfig) -> int:
    """Número de triángulos con ``liftCoef`` (G2.15): aerodinámica por triángulos, que
    calcula el C++ del juego (sin Lua)."""
    n = 0
    for _, section in resolved.section_contributions("triangles"):
        for row in parse_table(section).rows:
            if "liftCoef" in row.properties:
                n += 1
    return n


def aero_model(resolved: ResolvedConfig) -> str:
    """``cefaero`` (Lua del exportador), ``triangles`` (solo triángulos con
    ``dragCoef``/``liftCoef``, calculados por el C++ del juego) o ``none`` (G2.15)."""
    if _uses_cefaero(resolved):
        return "cefaero"
    return "triangles" if _triangle_aero(resolved) else "none"


def axle_equivalent(aero: Aero, geometry: Any) -> tuple[float, float, float]:
    """``cefaero`` como arrastre en el centro del eje trasero + cargas en los centros
    de los dos ejes: ``(CdA, ClA_del, ClA_tras)`` en m², + = carga (G2.29).

    Es la misma representación que la aerodinámica medida del 200409
    (``src/extract/aero_bench.py``). ``cefaero.lua`` (l. 117-142) aplica, por unidad de
    ½ρv²: el arrastre ``vehicleArea`` hacia atrás en el nodo de arrastre (altura y_d) y
    una carga ``cla`` hacia abajo en CADA grupo de nodos (posición z_i). Un sistema de
    fuerzas sobre un sólido equivale a otro con la misma resultante y el mismo momento;
    con el arrastre trasladado al centro del eje trasero (z_t, altura y_t):

        ClA_del = [Σ (z_i − z_t)·cla_i − (y_d − y_t)·CdA] / batalla
        ClA_tras = Σ cla_i − ClA_del

    No representable (se rechaza): fuerza constante (``constantForce*``) y puntos fuera
    del plano medio. No se incluye el arrastre lateral/vertical (×1,25/×1,5) del Lua.
    """
    if aero.constant_front_n or aero.constant_rear_n:
        raise AeroDataError("cefaero con fuerza constante: sin equivalente ∝ v² en AC")
    if aero.drag_node_ac is None or not aero.points:
        raise AeroDataError("cefaero sin nodo de arrastre o sin grupos de carga")
    if len(geometry.axles) != 2 or not geometry.wheelbase:
        raise AeroDataError("se necesitan 2 ejes para la equivalencia por ejes")
    pts = [aero.drag_node_ac] + [p.position_ac for p in aero.points]
    if any(abs(p[0]) > 1e-3 for p in pts):
        raise AeroDataError("puntos aerodinámicos fuera del plano medio del vehículo")
    centers = {w.name: w.center_ac for w in geometry.wheels}
    rear = geometry.axles[1]
    y_t = sum(centers[n][1] for n in rear.wheels) / len(rear.wheels)
    z_t = rear.longitudinal_ac
    loads = [
        (p.position_ac[2] - z_t, aero.cla_front if p.is_front else aero.cla_rear)
        for p in aero.points
    ]
    moment = sum(z * c for z, c in loads) - (aero.drag_node_ac[1] - y_t) * aero.cda
    front = moment / (geometry.axles[0].longitudinal_ac - z_t)
    return aero.cda, front, sum(c for _, c in loads) - front


TRIANGLE_AERO_REASON = (
    "aerodinámica por triángulos (dragCoef/liftCoef/stallAngle): la calcula el C++ del "
    "juego, sin fórmula publicada; no reconstruible fuera de línea (ajuste de G2.15: "
    "residuo hasta ≈ 8 % del CdA). Medida en el banco del juego (G2.15, "
    "output/g215_direction_brakes_aero_2026-11-03/banco/resultados.md)"
)


def build_aero(resolved: ResolvedConfig) -> Aero:
    if not _uses_cefaero(resolved):
        if _triangle_aero(resolved):
            raise AeroDataError(TRIANGLE_AERO_REASON)
        raise AeroDataError(
            "sin controlador 'cefaero': modelo aerodinámico no soportado"
        )
    variables = resolved.variable_values()
    drag = merge_section(resolved, "dragModel")
    down = merge_section(resolved, "downforceModel")
    for sec in (drag, down):
        if sec.conflicts or sec.operators or sec.non_dict:
            raise AeroDataError(
                f"'{sec.name}' con conflictos u operadores entre piezas"
            )

    def num(sec, key, default=None):
        value = evaluate(sec.get(key), variables) if key in sec else default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AeroDataError(f"{sec.name}.{key} = {value!r} no numérico")
        return float(value)

    cda = num(drag, "vehicleArea", 1.0)  # cefaero.lua:242 "or 1"
    issues = []
    if "vehicleArea" not in drag:
        issues.append("vehicleArea ausente: el Lua usa 1")
    front = num(down, "downforceTargetFront", 0.0)
    rear = num(down, "downforceTargetRear", 0.0)
    speed = num(down, "targetSpeedKMH", 200.0)
    if speed <= 0:
        raise AeroDataError("targetSpeedKMH no positiva")
    q = 0.5 * RHO_FACTOR * (speed / 3.6) ** 2
    density = down.get("targetAirDensity")

    nodes = {n.id: n for n in collect_nodes(resolved)}
    drag_nodes = drag.get("dragNodes:")
    drag_node_ac = None
    if isinstance(drag_nodes, list) and drag_nodes and drag_nodes[0] in nodes:
        drag_node_ac = beamng_to_ac(nodes[drag_nodes[0]].pos)
    points = []
    for _, section in resolved.section_contributions("downforceNodeGroups"):
        for row in parse_table(section).rows:
            ref, up = row.get("ref:"), row.get("up:")
            if ref not in nodes or up not in nodes:
                raise AeroDataError(f"nodo de carga inexistente: {ref!r}/{up!r}")
            points.append(
                DownforcePoint(
                    bool(row.get("isFront")), ref, up, beamng_to_ac(nodes[ref].pos)
                )
            )
    status = {
        "cda": "leído (vehicleArea; cefaero.lua 117-119: F = vehicleArea·½ρv²)",
        "cla": "derivado con ρ₀ = 1,225 (cefaero.lua:17, 157-163); HIPÓTESIS sobre ρ₀",
        "target_air_density": "leída pero no usada por el Lua (línea 157-163)",
        "drag_mods": "1,25 / 1,5 (por defecto del Lua = valores del JBeam)",
    }
    return Aero(
        cda=cda,
        drag_mod_lateral=1.25,
        drag_mod_vertical=1.5,
        drag_node_ac=drag_node_ac,
        target_front_n=front,
        target_rear_n=rear,
        target_speed_kmh=speed,
        target_air_density_unused=(
            density if isinstance(density, (int, float)) else None
        ),
        cla_front=front / q,
        cla_rear=rear / q,
        constant_front_n=num(down, "constantForceFront", 0.0) * 9.80665,
        constant_rear_n=num(down, "constantForceRear", 0.0) * 9.80665,
        points=points,
        status=status,
        issues=issues,
    )
