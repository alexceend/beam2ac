"""Masa y geometría de la configuración activa (fase 2).

Fórmulas (masas puntuales; ejes BeamNG salvo que se indique "AC"):

* Masa total  M = Σ m_nodo + Σ masa_generada_rueda.
* CdG         c = (Σ m_i·p_i) / M, con la masa generada de cada rueda en su centro.
* Ejes        ruedas agrupadas por coordenada longitudinal (|Δ| ≤ ``axle_tolerance``),
              de delante (AC z mayor) a atrás.
* Batalla     L = z_AC(eje delantero) − z_AC(eje trasero)   (solo con 2 ejes).
* Vía         x(rueda izquierda) − x(rueda derecha) de cada eje.
* Reparto     f_del = (z_AC(CdG) − z_AC(trasero)) / L   (equilibrio estático de
              momentos de un sólido rígido sobre dos ejes).
* Suelo       z_suelo = media(z_centro_rueda − radio). [HIPÓTESIS: neumático sin
              carga, pose de definición del JBeam]
* Inercia     I = Σ m_i·(|r_i|²·𝟙 − r_i·r_iᵀ), r_i = p_i − c, en ejes AC.
              [APROXIMACIÓN: masas puntuales; las ruedas como punto en su centro]
* Conjunto rueda (cota inferior de masa no suspendida) = masa generada + nodos
  ``node1``/``node2`` del eje. La partición completa suspendida/no suspendida
  requiere la topología de la suspensión (fase 5).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.coords import Vec3, beamng_to_ac, beamng_tensor_to_ac
from src.jbeam import ResolvedConfig, load
from src.safe_paths import ensure_safe_output_path

from .nodes import Node, NodeOverride, collect_nodes
from .wheels import Wheel, collect_wheels

CONFIRMED = "confirmado"
DERIVED = "derivado"
HYPOTHESIS = "hipótesis"
APPROXIMATION = "aproximación"


@dataclass(frozen=True)
class WheelSummary:
    name: str
    part: str
    side: str  # "left" (x > 0) | "right" (x < 0) | "center"
    center_beamng: Vec3
    center_ac: Vec3
    radius: float
    hub_radius: float
    tire_width: float | None
    generated_mass: float
    assembly_mass: float  # generada + nodos del eje
    spin_inertia: float


@dataclass(frozen=True)
class Axle:
    index: int  # 0 = más adelantado
    longitudinal_ac: float  # z AC medio de sus ruedas
    wheels: tuple[str, ...]
    track: float | None


@dataclass(frozen=True)
class ReferenceCheck:
    source: str
    reference_kg: float
    computed_kg: float
    difference_kg: float
    explanation: str


@dataclass
class MassGeometry:
    node_count: int
    node_mass: float
    wheel_generated_mass: float
    total_mass: float
    mass_by_part: dict[str, float]
    cog_beamng: Vec3
    cog_ac: Vec3
    wheels: list[WheelSummary]
    axles: list[Axle]
    wheelbase: float | None
    front_axle_fraction: float | None
    ground_z_beamng: float | None
    cog_height: float | None
    inertia_ac: tuple[Vec3, Vec3, Vec3]  # kg·m² respecto al CdG, ejes AC
    status: dict[str, str]
    issues: list[str] = field(default_factory=list)
    reference: ReferenceCheck | None = None
    # filas de nodo con id repetido en la misma pieza, reemplazadas por la última
    # (regla de tableSchema.lua; G2.13): se conservan para el diagnóstico
    node_overrides: list[NodeOverride] = field(default_factory=list)

    @property
    def wheel_assembly_mass(self) -> float:
        return sum(w.assembly_mass for w in self.wheels)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["wheel_assembly_mass"] = self.wheel_assembly_mass
        data["inertia_ac_axes"] = {
            "pitch_about_X": self.inertia_ac[0][0],
            "yaw_about_Y": self.inertia_ac[1][1],
            "roll_about_Z": self.inertia_ac[2][2],
        }
        return data


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _side(x: float) -> str:
    return "left" if x > 0 else "right" if x < 0 else "center"


def _group_axles(
    summaries: list[WheelSummary], tolerance: float, issues: list[str]
) -> list[Axle]:
    ordered = sorted(summaries, key=lambda w: -w.center_ac[2])
    groups: list[list[WheelSummary]] = []
    for wheel in ordered:
        if (
            groups
            and abs(groups[-1][-1].center_ac[2] - wheel.center_ac[2]) <= tolerance
        ):
            groups[-1].append(wheel)
        else:
            groups.append([wheel])
    axles = []
    for index, group in enumerate(groups):
        left = [w for w in group if w.side == "left"]
        right = [w for w in group if w.side == "right"]
        track = None
        if len(left) == 1 and len(right) == 1 and len(group) == 2:
            track = left[0].center_ac[0] - right[0].center_ac[0]
        else:
            issues.append(
                f"eje {index}: {len(group)} ruedas ({len(left)} izq., "
                f"{len(right)} der.); vía no calculada"
            )
        axles.append(
            Axle(
                index=index,
                longitudinal_ac=_mean([w.center_ac[2] for w in group]),
                wheels=tuple(w.name for w in group),
                track=track,
            )
        )
    return axles


def _inertia(points: list[tuple[float, Vec3]], cog: Vec3) -> tuple[Vec3, Vec3, Vec3]:
    t = [[0.0] * 3 for _ in range(3)]
    for mass, pos in points:
        r = [pos[i] - cog[i] for i in range(3)]
        r2 = r[0] ** 2 + r[1] ** 2 + r[2] ** 2
        for i in range(3):
            for j in range(3):
                t[i][j] += mass * ((r2 if i == j else 0.0) - r[i] * r[j])
    return tuple(tuple(row) for row in t)  # type: ignore[return-value]


def compute_mass_geometry(
    resolved: ResolvedConfig,
    axle_tolerance: float = 0.05,
) -> MassGeometry:
    overrides: list[NodeOverride] = []
    nodes: list[Node] = collect_nodes(resolved, overrides)
    wheels: list[Wheel] = collect_wheels(resolved, nodes)
    by_id = {n.id: n for n in nodes}
    issues: list[str] = []
    for o in overrides:
        issues.append(
            f"'{o.part}' nodo {o.id!r}: fila {o.replaced_row_index} reemplazada por la "
            f"fila {o.kept_row_index} (mismo id en la misma pieza; vale la última, "
            f"tableSchema.lua); no se cuentan {o.replaced_mass} kg"
        )

    node_mass = sum(n.mass for n in nodes)
    wheel_mass = sum(w.generated_mass for w in wheels)
    total = node_mass + wheel_mass
    if total <= 0:
        raise ValueError("masa total nula: no hay nodos con masa")

    points: list[tuple[float, Vec3]] = [(n.mass, n.pos) for n in nodes]
    points += [(w.generated_mass, w.center) for w in wheels]
    cog = tuple(sum(m * p[i] for m, p in points) / total for i in range(3))

    by_part: dict[str, float] = {}
    for n in nodes:
        by_part[n.part] = by_part.get(n.part, 0.0) + n.mass
    for w in wheels:
        key = f"{w.part} (ruedas generadas)"
        by_part[key] = by_part.get(key, 0.0) + w.generated_mass

    summaries = [
        WheelSummary(
            name=w.name,
            part=w.part,
            side=_side(w.center[0]),
            center_beamng=w.center,
            center_ac=beamng_to_ac(w.center),
            radius=w.radius,
            hub_radius=w.hub_radius,
            tire_width=w.tire_width,
            generated_mass=w.generated_mass,
            assembly_mass=w.generated_mass + by_id[w.node1].mass + by_id[w.node2].mass,
            spin_inertia=w.spin_inertia,
        )
        for w in wheels
    ]
    axles = _group_axles(summaries, axle_tolerance, issues) if summaries else []
    cog_ac = beamng_to_ac(cog)  # type: ignore[arg-type]

    wheelbase = front_fraction = None
    if len(axles) == 2:
        wheelbase = axles[0].longitudinal_ac - axles[1].longitudinal_ac
        front_fraction = (cog_ac[2] - axles[1].longitudinal_ac) / wheelbase
    else:
        issues.append(f"{len(axles)} ejes: batalla y reparto solo se calculan con 2")

    ground = cog_height = None
    if wheels:
        ground = _mean([w.center[2] - w.radius for w in wheels])
        cog_height = cog[2] - ground

    inertia = beamng_tensor_to_ac(_inertia(points, cog))  # type: ignore[arg-type]

    status = {
        "total_mass": CONFIRMED + " (contra info_<config>.json, ver reference)",
        "node_offset_rule": CONFIRMED
        + " (x + sign(x)·off.x, sign(0) = 0: utils.lua y mathlib.lua del juego; G2.13)",
        "duplicate_node_ids": CONFIRMED
        + " (misma pieza: vale la última fila, tableSchema.lua; entre piezas: error)",
        "wheel_generated_mass": CONFIRMED + " (necesaria para cuadrar la masa)",
        "coordinate_axes": CONFIRMED + " (muestras BeamNG y AC)",
        "cog": DERIVED,
        "wheelbase_track_axles": DERIVED,
        "front_axle_fraction": DERIVED + " (estática, sólido rígido)",
        "ground_and_cog_height": HYPOTHESIS + " (neumático sin carga)",
        "wheel_spin_inertia": HYPOTHESIS + " (posición de nodos generados)",
        "inertia_tensor": APPROXIMATION + " (masas puntuales)",
        "wheel_assembly_mass": "cota inferior de la masa no suspendida",
    }
    return MassGeometry(
        node_count=len(nodes),
        node_mass=node_mass,
        wheel_generated_mass=wheel_mass,
        total_mass=total,
        mass_by_part=by_part,
        cog_beamng=cog,  # type: ignore[arg-type]
        cog_ac=cog_ac,
        wheels=summaries,
        axles=axles,
        wheelbase=wheelbase,
        front_axle_fraction=front_fraction,
        ground_z_beamng=ground,
        cog_height=cog_height,
        inertia_ac=inertia,
        status=status,
        issues=issues,
        node_overrides=overrides,
    )


def check_reference_mass(
    result: MassGeometry, info_path: str | Path, rounding: float = 0.5
) -> ReferenceCheck | None:
    """Compara con ``Weight`` de ``info_<config>.json`` (dato de UI, entero).

    No modifica la masa calculada: solo informa de la diferencia.
    """
    path = Path(info_path)
    if not path.is_file():
        return None
    weight = load(path).value.get("Weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool):
        return None
    diff = result.total_mass - float(weight)
    if abs(diff) <= rounding:
        why = (
            f"|Δ| = {abs(diff):.2f} kg ≤ {rounding} kg: compatible con que la "
            "referencia es un entero redondeado"
        )
    else:
        why = (
            f"|Δ| = {abs(diff):.2f} kg > {rounding} kg: diferencia real, revisar "
            "piezas activas, masas generadas o la definición de la referencia"
        )
    check = ReferenceCheck(str(path), float(weight), result.total_mass, diff, why)
    result.reference = check
    return check


def write_report(result: MassGeometry, path: str | Path) -> Path:
    """Escribe el informe JSON (nunca dentro de las muestras)."""
    target = ensure_safe_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return target
