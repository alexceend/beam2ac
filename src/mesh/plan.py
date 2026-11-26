"""Plan de escena (fase 6): qué mallas, dónde y bajo qué nodo.

Todo en coordenadas BeamNG y metros. El addon de exportación convierte después
con ``(x, z, −y)``, que es la transformación BeamNG→AC documentada (fase 2), así
que el plan no aplica ninguna conversión ni traslada el origen.

Reglas de clasificación (docs/research/mallas.md §3):

* **WHEEL_xx** — flexbody cuyos grupos incluyen ``hubGroup`` o ``group`` de la fila
  ``pressureWheels`` de la rueda (nodos que giran con ella).
* **SUSP_xx** — flexbody que no gira, cuya caja transformada cabe entera en la
  esfera de radio = radio del neumático alrededor del centro de UNA rueda (p. ej.
  pinzas; en la muestra de AC las pinzas cuelgan de ``SUSP_xx``).
* **raíz** — el resto (carrocería, chasis, motor, brazos de suspensión de eje
  completo).

Nombres AC: ``xx`` = lado (L si x > 0, R si x < 0) + eje (F = el más adelantado,
R = el trasero), a partir de la geometría de la fase 2, no del nombre de rueda.

Empties de rueda: en el centro de rueda, con eje X = eje de rueda (de los nodos
``node1``/``node2``) orientado hacia +X de AC, Z ortogonalizado con la vertical.
Así la caída/convergencia quedan en el nodo y la malla queda alineada en su
marco local, como en el KN5 de muestra (``WHEEL_LF`` lleva la caída en su matriz).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.extract.mass_geometry import compute_mass_geometry
from src.extract.nodes import collect_nodes
from src.extract.wheels import collect_wheels
from src.jbeam import ResolvedConfig, parse_table
from src.safe_paths import ensure_safe_output_path

from .dae import DaeFile, bounds
from .flexbody import Matrix4, Vec3, apply, collect_flexbodies, flexbody_matrix

PLAN_VERSION = 1


class MeshPlanError(ValueError):
    """No se puede construir un plan de escena coherente."""


# G2.18: los nombres de objeto y de material del KN5 caben en 63 bytes UTF-8 (convención de los
# KN5 de referencia: 64 bytes con el nulo final) y los más largos se recortan. Exportaciones del
# 200409 (prothean, portland, omega) tienen nodos DAE de 65 caracteres.
NAME_BYTES_MAX = 63


def trim_name(name: str, reserve: int = 0) -> str:
    """``name`` recortado a 63 bytes UTF-8 (sin partir un
    carácter), dejando ``reserve`` bytes libres para un sufijo."""
    limit = NAME_BYTES_MAX - reserve
    raw = name.encode("utf-8")
    if len(raw) <= limit:
        return name
    return raw[:limit].decode("utf-8", errors="ignore")


@dataclass
class PlanObject:
    name: str
    mesh: str  # nombre del nodo en el DAE
    dae: str  # ruta del DAE
    part: str
    groups: list[str]
    matrix: Matrix4  # DAE -> BeamNG (m), incluye la unidad
    parent: str | None  # WHEEL_xx / SUSP_xx / None
    reason: str
    vertex_count: int
    bbox_beamng: tuple[Vec3, Vec3]
    bbox_dae: tuple[Vec3, Vec3]  # sin transformar, unidades del DAE
    nonfinite_values: dict[str, int]  # NaN/inf en fuentes no posicionales
    # G2.18: caja de los vértices que usa alguna cara (BeamNG, m): la que puede tener
    # el KN5 (los vértices sueltos no se exportan). ``None``: sin datos de caras.
    bbox_faces_beamng: tuple[Vec3, Vec3] | None = None


@dataclass
class PlanEmpty:
    name: str
    wheel: str | None
    matrix: Matrix4  # marco del nodo en BeamNG (mundo)
    parent: str | None = None  # otro Empty del plan (None = raíz)
    note: str = ""


@dataclass
class ScenePlan:
    version: int
    unit_meter: dict[str, float]
    up_axis: dict[str, str]
    objects: list[PlanObject]
    empties: list[PlanEmpty]
    wheel_centers: dict[str, Vec3]  # nombre AC -> centro BeamNG
    issues: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def _wheel_groups(resolved: ResolvedConfig) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for active in resolved.active:
        for row in parse_table(active.part.data.get("pressureWheels", [])).rows:
            name = row.get("name")
            names = {
                g for g in (row.get("hubGroup"), row.get("group")) if isinstance(g, str)
            }
            groups.setdefault(str(name), set()).update(names)
    return groups


def _unit(v: Vec3) -> Vec3:
    n = math.sqrt(sum(c * c for c in v))
    if n == 0:
        raise MeshPlanError("vector nulo")
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def wheel_frame(inner: Vec3, outer: Vec3, center: Vec3, left: bool) -> Matrix4:
    """Marco del nodo de rueda: X = eje de rueda hacia +X, Z ≈ arriba."""
    axle = _unit(tuple(o - i for o, i in zip(outer, inner)))  # type: ignore[arg-type]
    x = axle if left else tuple(-c for c in axle)
    up = (0.0, 0.0, 1.0)
    dot = sum(a * b for a, b in zip(up, x))
    z = _unit(tuple(u - dot * c for u, c in zip(up, x)))  # type: ignore[arg-type]
    y = _cross(z, x)
    return (
        (x[0], y[0], z[0], center[0]),
        (x[1], y[1], z[1], center[1]),
        (x[2], y[2], z[2], center[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def build_plan(resolved: ResolvedConfig, daes: dict[str, DaeFile]) -> ScenePlan:
    """``daes``: ruta -> DaeFile ya leído (todos los del vehículo)."""
    geometry = compute_mass_geometry(resolved)
    if len(geometry.axles) != 2:
        raise MeshPlanError("se necesitan exactamente 2 ejes para nombrar las ruedas")
    nodes = {n.id: n for n in collect_nodes(resolved)}
    wheels = {w.name: w for w in collect_wheels(resolved, list(nodes.values()))}
    summaries = {w.name: w for w in geometry.wheels}
    groups = _wheel_groups(resolved)
    issues: list[str] = []

    ac_name: dict[str, str] = {}
    for axle in geometry.axles:
        for w in axle.wheels:
            side = summaries[w].side
            if side == "center":
                raise MeshPlanError(f"rueda {w} en x = 0: lado indefinido")
            ac_name[w] = ("L" if side == "left" else "R") + (
                "F" if axle.index == 0 else "R"
            )
    if len(set(ac_name.values())) != len(ac_name):
        raise MeshPlanError(f"nombres AC de rueda repetidos: {ac_name}")

    empties, centers = [], {}
    for w, suffix in sorted(ac_name.items(), key=lambda kv: kv[1]):
        wheel = wheels[w]
        p1, p2 = nodes[wheel.node1].pos, nodes[wheel.node2].pos
        inner, outer = (p1, p2) if abs(p1[0]) < abs(p2[0]) else (p2, p1)
        frame = wheel_frame(inner, outer, wheel.center, summaries[w].side == "left")
        empties.append(PlanEmpty(f"WHEEL_{suffix}", w, frame))
        empties.append(PlanEmpty(f"SUSP_{suffix}", w, frame))
        centers[f"WHEEL_{suffix}"] = wheel.center

    mesh_index: dict[str, tuple[str, DaeFile]] = {}
    for path, dae in daes.items():
        for node_name in dae.nodes:
            if node_name in mesh_index:
                raise MeshPlanError(f"malla '{node_name}' en dos DAE")
            mesh_index[node_name] = (path, dae)

    objects: list[PlanObject] = []
    used: dict[str, int] = {}
    final_names: set[str] = set()
    for fb in collect_flexbodies(resolved):
        if fb.mesh not in mesh_index:
            raise MeshPlanError(
                f"malla '{fb.mesh}' de '{fb.part}' no está en ningún DAE"
            )
        path, dae = mesh_index[fb.mesh]
        m = flexbody_matrix(fb, dae.unit_meter)
        geom = dae.geometry_for_node(fb.mesh)
        pts = [apply(m, p) for p in geom.positions]
        lo, hi = bounds(pts)
        faces_box = None
        if geom.face_vertices is not None:
            used_pts = [pts[i] for i in sorted(geom.face_vertices) if i < len(pts)]
            if used_pts:
                faces_box = bounds(used_pts)
            loose = len(pts) - len(used_pts)
            if loose:
                issues.append(
                    f"'{fb.mesh}': {loose} vértices sin cara en el DAE (no llegan al "
                    "KN5; verify_kn5 compara la caja de los vértices con cara)"
                )
        if geom.nonfinite:
            issues.append(
                f"'{fb.mesh}': valores NaN/inf en {geom.nonfinite} (no posiciones); "
                "el constructor de KN5 sustituye las normales no finitas por la de la cara y "
                "verify_kn5 comprueba que el KN5 no tenga ninguna"
            )
        spinning = [w for w in ac_name if groups.get(w, set()) & set(fb.groups)]
        if len(spinning) > 1:
            raise MeshPlanError(f"'{fb.mesh}' ligada a varias ruedas: {spinning}")
        if spinning:
            parent = f"WHEEL_{ac_name[spinning[0]]}"
            reason = f"grupos {list(fb.groups)} incluyen los de la rueda {spinning[0]}"
        else:
            inside = [
                w
                for w in ac_name
                if all(
                    math.dist(corner, wheels[w].center) <= summaries[w].radius
                    for corner in _corners(lo, hi)
                )
            ]
            if len(inside) == 1:
                parent = f"SUSP_{ac_name[inside[0]]}"
                reason = f"no gira y cabe en la esfera del neumático de {inside[0]}"
            elif len(inside) > 1:
                raise MeshPlanError(f"'{fb.mesh}' cabe en varias ruedas: {inside}")
            else:
                parent, reason = None, "no gira ni pertenece a una sola rueda"
        base = fb.mesh if parent is None else f"{fb.mesh}_{parent.split('_')[1]}"
        used[base] = used.get(base, 0) + 1
        suffix = "" if used[base] == 1 else f".{used[base]}"
        name = trim_name(base, len(suffix)) + suffix
        if name in final_names:
            raise MeshPlanError(f"nombre de objeto repetido tras el recorte: '{name}'")
        final_names.add(name)
        if name != base + suffix:
            issues.append(
                f"'{base + suffix}': nombre de objeto recortado a '{name}' (el KN5 "
                f"admite {NAME_BYTES_MAX} bytes)"
            )
        objects.append(
            PlanObject(
                name,
                fb.mesh,
                path,
                fb.part,
                list(fb.groups),
                m,
                parent,
                reason,
                len(pts),
                (lo, hi),
                bounds(geom.positions),
                dict(geom.nonfinite),
                faces_box,
            )
        )

    unused = sorted(set(mesh_index) - {o.mesh for o in objects})
    if unused:
        issues.append(f"mallas del DAE sin flexbody activo (no se exportan): {unused}")
    return ScenePlan(
        PLAN_VERSION,
        {p: d.unit_meter for p, d in daes.items()},
        {p: d.up_axis for p, d in daes.items()},
        objects,
        empties,
        centers,
        issues,
    )


def _corners(lo: Vec3, hi: Vec3) -> list[Vec3]:
    return [
        (x, y, z)
        for x in (lo[0], hi[0])
        for y in (lo[1], hi[1])
        for z in (lo[2], hi[2])
    ]


def write_plan(plan: ScenePlan, path: str | Path) -> Path:
    target = ensure_safe_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.to_json(), encoding="utf-8")
    return target


def load_plan(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def kunos_origin_offset(plan: ScenePlan) -> Vec3:
    """Traslación (ejes BeamNG) para el origen que pide el SDK de Kunos.

    ``AC_Pipeline_PUB_Rev2.0.pdf`` (sdk/dev de Assetto Corsa), pág. 17: "The model
    must be placed with the wheels touching the ground on the 0 coordinate (Y)" y
    "The model bounding box must be centered". Interpretación: caja centrada en X
    y Z de AC (= x, y de BeamNG) y punto más bajo de las mallas de rueda en Y = 0
    de AC (= z de BeamNG). Solo traslada; no gira ni escala.
    """
    if not plan.objects:
        raise MeshPlanError("plan sin objetos")
    lo = [min(o.bbox_beamng[0][i] for o in plan.objects) for i in range(3)]
    hi = [max(o.bbox_beamng[1][i] for o in plan.objects) for i in range(3)]
    wheel_objects = [o for o in plan.objects if (o.parent or "").startswith("WHEEL_")]
    if not wheel_objects:
        raise MeshPlanError("sin mallas de rueda: no se puede fijar el suelo")
    ground = min(o.bbox_beamng[0][2] for o in wheel_objects)
    return (-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -ground)


def translate_plan(plan: ScenePlan, offset: Vec3) -> ScenePlan:
    """Copia del plan desplazada ``offset`` (ejes BeamNG). No modifica el original."""

    def move_matrix(m: Matrix4) -> Matrix4:
        return tuple(  # type: ignore[return-value]
            tuple(row[:3]) + (row[3] + (offset[i] if i < 3 else 0.0),)
            for i, row in enumerate(m)
        )

    def move(p: Vec3) -> Vec3:
        return (p[0] + offset[0], p[1] + offset[1], p[2] + offset[2])

    objects = [
        PlanObject(
            o.name,
            o.mesh,
            o.dae,
            o.part,
            list(o.groups),
            move_matrix(o.matrix),
            o.parent,
            o.reason,
            o.vertex_count,
            (move(o.bbox_beamng[0]), move(o.bbox_beamng[1])),
            o.bbox_dae,
            dict(o.nonfinite_values),
            (
                (move(o.bbox_faces_beamng[0]), move(o.bbox_faces_beamng[1]))
                if o.bbox_faces_beamng is not None
                else None
            ),
        )
        for o in plan.objects
    ]
    empties = [
        PlanEmpty(e.name, e.wheel, move_matrix(e.matrix), e.parent, e.note)
        for e in plan.empties
    ]
    centers = {k: move(v) for k, v in plan.wheel_centers.items()}
    return ScenePlan(
        plan.version,
        dict(plan.unit_meter),
        dict(plan.up_axis),
        objects,
        empties,
        centers,
        list(plan.issues) + [f"origen trasladado {offset} (ejes BeamNG)"],
    )


IDENTITY4: Matrix4 = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def add_cockpit_lr_nulls(plan: ScenePlan) -> ScenePlan:
    """Añade ``COCKPIT_LR`` (raíz, en el origen) y ``STEER_LR`` (hijo), vacíos.

    Evidencia: el SDK de Kunos los lista entre los "Nulls that MUST be present in
    ALL LODs" (pág. 13) y están en todos los KN5 inspeccionados (abarth500,
    ks_toyota_celica_st185, ks_audi_r8_lms y el mod de muestra), siempre con
    ``STEER_LR`` hijo de ``COCKPIT_LR``; en los tres oficiales ``COCKPIT_LR`` está
    en (0, 0, 0). No se añade geometría (no hay interior en la fuente).
    HIPÓTESIS: la posición de ``STEER_LR`` (en el origen) no tiene efecto visual
    porque no tiene mallas. Aplicar DESPUÉS de fijar el origen del modelo.
    """
    names = {e.name for e in plan.empties}
    if names & {"COCKPIT_LR", "STEER_LR"}:
        raise MeshPlanError("el plan ya tiene COCKPIT_LR/STEER_LR")
    empties = list(plan.empties) + [
        PlanEmpty("COCKPIT_LR", None, IDENTITY4, None, "nulo vacío (SDK)"),
        PlanEmpty(
            "STEER_LR",
            None,
            IDENTITY4,
            "COCKPIT_LR",
            "nulo vacío (SDK); posición: hipótesis",
        ),
    ]
    return ScenePlan(
        plan.version,
        dict(plan.unit_meter),
        dict(plan.up_axis),
        list(plan.objects),
        empties,
        dict(plan.wheel_centers),
        list(plan.issues) + ["añadidos COCKPIT_LR/STEER_LR vacíos (SDK de Kunos)"],
    )


def add_steer_hr_diagnostic_nulls(plan: ScenePlan) -> ScenePlan:
    """Añade ``COCKPIT_HR`` (raíz, origen) y ``STEER_HR`` (hijo, origen), VACÍOS.

    SOLUCIÓN DIAGNÓSTICA (fase 7.1), no un volante: sirve para comprobar en el
    juego si la ausencia de ``STEER_HR`` era lo que impedía cargar.
    Evidencia (ensamblado.md §8.3 y §9.1): AC contiene
    "ERROR: CANNOT FIND STEER_HR IN FILE:"; ``STEER_HR`` está en 419/449 KN5
    instalados (faltan solo coches de tráfico); en 126/128 coches ``ks_*`` es un
    nodo dummy hijo de ``COCKPIT_HR``, y ``COCKPIT_HR`` es un dummy colgado de la
    raíz (con matriz identidad en 193 de 384 KN5).
    HIPÓTESIS: la posición (origen, sin inclinación) no importa sin mallas. En los
    oficiales ``STEER_HR`` está en el volante y lleva sus mallas como hijas; aquí
    no hay fuente para ninguna de las dos cosas.
    """
    names = {e.name for e in plan.empties}
    if names & {"COCKPIT_HR", "STEER_HR"}:
        raise MeshPlanError("el plan ya tiene COCKPIT_HR/STEER_HR")
    empties = list(plan.empties) + [
        PlanEmpty("COCKPIT_HR", None, IDENTITY4, None, "nulo vacío (diagnóstico)"),
        PlanEmpty(
            "STEER_HR",
            None,
            IDENTITY4,
            "COCKPIT_HR",
            "nulo vacío (diagnóstico); posición: hipótesis",
        ),
    ]
    return ScenePlan(
        plan.version,
        dict(plan.unit_meter),
        dict(plan.up_axis),
        list(plan.objects),
        empties,
        dict(plan.wheel_centers),
        list(plan.issues)
        + ["añadidos COCKPIT_HR/STEER_HR vacíos (diagnóstico de carga, fase 7.1)"],
    )
