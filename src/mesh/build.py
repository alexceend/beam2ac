"""Flujo reproducible de la fase 6: plan → KN5 (Python puro) → verificación.

Uso:
    python -m src.mesh.build <carpeta_vehiculo> <archivo.pc> --out output/<car>/mesh [--plan-only]

* Siempre escribe ``scene_plan.json``.
* Sin ``--plan-only`` construye el KN5 con :mod:`src.mesh.pure_kn5` y después lo verifica con
  :func:`verify_kn5`, dejando ``verification.json``. No necesita ningún programa externo.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ac.kn5_inspect import Kn5File, invert_rigid, read_kn5, transform_point
from src.coords import beamng_to_ac
from src.jbeam import PartIndex, load_pc, resolve
from src.safe_paths import ensure_safe_output_path

from .dae import bounds, read_dae
from .plan import ScenePlan, build_plan, write_plan

POSITION_TOLERANCE = 0.01  # m (criterio de la fase 6)


@dataclass
class Verification:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def plan_for_vehicle(vehicle_dir: str | Path, pc: str | Path) -> ScenePlan:
    vehicle_dir = Path(vehicle_dir)
    resolved = resolve(PartIndex.from_directories(vehicle_dir), load_pc(pc))
    daes = {str(p): read_dae(p) for p in sorted(vehicle_dir.rglob("*.dae"))}
    if not daes:
        raise FileNotFoundError(f"no hay archivos .dae en {vehicle_dir}")
    return build_plan(resolved, daes)


def plan_for_zip(
    vehicle_zip: str | Path,
    common_zip: str | Path | None = None,
    pc_name: str | None = None,
    include_common_daes: bool = False,
) -> ScenePlan:
    """Plan de escena de un vehículo en un zip (y ``common.zip``), sin extraer nada.

    Lee JBeam, ``.pc`` y ``.dae`` en memoria con ``src.jbeam.sources``. Los DAE de
    ``common.zip`` solo se leen con ``include_common_daes`` (son muchos y pesados); si
    una malla no aparece, el plan lo señala como en la lectura de carpetas.
    """
    from src.jbeam import ZipSource, vehicle_dirs_in_zip

    vehicle_zip = Path(vehicle_zip)
    prefixes = vehicle_dirs_in_zip(vehicle_zip)
    if len(prefixes) != 1:
        raise ValueError(f"el zip contiene {len(prefixes)} vehículos: {prefixes}")
    prefix = prefixes[0]
    pcs = ZipSource(vehicle_zip, prefix).members(".pc")
    if pc_name:
        available = sorted(m.name for m in pcs)
        pcs = [m for m in pcs if m.name == pc_name]
        if not pcs:
            raise ValueError(
                f"la configuración {pc_name!r} no existe en {vehicle_zip.name} "
                f"(¿otra versión del juego?); hay {len(available)}: "
                f"{', '.join(available[:12])}{'…' if len(available) > 12 else ''}"
            )
    if len(pcs) != 1:
        raise ValueError(f"se necesita exactamente un .pc: {[m.member for m in pcs]}")
    sources = [ZipSource(vehicle_zip, prefix)]
    if common_zip:
        sources.append(ZipSource(Path(common_zip), "vehicles/common/"))
    resolved = resolve(PartIndex.from_sources(*sources), load_pc(pcs[0]))
    dae_sources = sources if include_common_daes else sources[:1]
    daes = {str(m): read_dae(m) for s in dae_sources for m in s.members(".dae")}
    if not daes:
        raise FileNotFoundError(f"no hay archivos .dae en {vehicle_zip}!/{prefix}")
    return build_plan(resolved, daes)


def verify_kn5(kn5: Kn5File, plan: ScenePlan) -> Verification:
    """Comprueba el KN5 exportado contra el plan, en coordenadas AC."""
    v = Verification(ok=True)
    names = [n.name for n in kn5.nodes]
    if any(n.type == 3 for n in kn5.nodes):
        v.errors.append("hay mallas skinned")
    for node in kn5.nodes:
        if node.type == 2 and node.children:
            v.errors.append(f"la malla '{node.name}' tiene hijos")
        if node.nonfinite_normals:
            v.errors.append(f"'{node.name}': {node.nonfinite_normals} normales NaN/inf")
    for empty in plan.empties:
        found = [n for n in kn5.nodes if n.name == empty.name and n.type == 1]
        if len(found) != 1:
            v.errors.append(f"nodo {empty.name}: {len(found)} apariciones")
            continue
        node = found[0]
        parent_name = kn5.nodes[node.parent].name if node.parent >= 0 else None
        wanted = getattr(empty, "parent", None)
        if wanted and parent_name != wanted:
            v.errors.append(f"{empty.name}: padre {parent_name!r}, esperado {wanted!r}")
        expected = beamng_to_ac(
            (empty.matrix[0][3], empty.matrix[1][3], empty.matrix[2][3])
        )
        dist = math.dist(node.translation, expected)
        v.checks.append(
            {
                "node": empty.name,
                "ac_position": node.translation,
                "expected": expected,
                "error_m": dist,
            }
        )
        if dist > POSITION_TOLERANCE:
            v.errors.append(f"{empty.name}: {dist:.4f} m del centro de rueda")
    for obj in plan.objects:
        meshes = _meshes_of(kn5, obj.name)
        if not meshes:
            v.errors.append(f"objeto '{obj.name}' ausente del KN5")
            continue
        pts = [p for m in meshes for p in kn5.mesh_world_vertices(m)]
        lo, hi = bounds(pts)
        # G2.18: el KN5 solo lleva los vértices con cara (los sueltos del DAE no se
        # exportan): se compara con esa caja si el plan la tiene
        box = obj.bbox_faces_beamng or obj.bbox_beamng
        exp_lo = beamng_to_ac(box[0])
        exp_hi = beamng_to_ac(box[1])
        exp = tuple((min(a, b), max(a, b)) for a, b in zip(exp_lo, exp_hi))
        dev = max(max(abs(lo[i] - exp[i][0]), abs(hi[i] - exp[i][1])) for i in range(3))
        parent_name = _top_named_ancestor(kn5, meshes[0])
        check = {"object": obj.name, "bbox_error_m": dev, "parent_in_kn5": parent_name}
        if obj.parent and obj.parent.startswith("WHEEL_"):
            check["local_extent_in_wheel_frame"] = _local_extent(kn5, obj.parent, pts)
        v.checks.append(check)
        if dev > 1e-3:
            v.errors.append(f"'{obj.name}': caja a {dev:.4f} m de la esperada")
        if (obj.parent or None) != parent_name:
            v.errors.append(
                f"'{obj.name}': padre {parent_name!r}, esperado {obj.parent!r}"
            )
    if "COCKPIT_HR" not in names:
        v.checks.append({"note": "sin COCKPIT_HR (no hay interior en la fuente)"})
    v.ok = not v.errors
    return v


def _meshes_of(kn5: Kn5File, name: str):
    """Mallas exportadas para un objeto: el nodo con su nombre o sus hijos."""
    out = []
    for i, node in enumerate(kn5.nodes):
        if node.name != name:
            continue
        if node.type == 2:
            out.append(node)
        else:
            out.extend(kn5.nodes[c] for c in node.children if kn5.nodes[c].type == 2)
    return out


def _top_named_ancestor(kn5: Kn5File, node) -> str | None:
    i = node.parent
    while i >= 0:
        name = kn5.nodes[i].name
        if name.startswith(("WHEEL_", "SUSP_")):
            return name
        i = kn5.nodes[i].parent
    return None


def _local_extent(kn5: Kn5File, wheel: str, pts) -> dict[str, float]:
    node = next(n for n in kn5.nodes if n.name == wheel)
    inv = invert_rigid(node.world)
    local = [transform_point(p, inv) for p in pts]
    ext = {}
    for i, axis in enumerate("xyz"):
        lo, hi = min(p[i] for p in local), max(p[i] for p in local)
        ext[f"{axis}_min"], ext[f"{axis}_max"] = lo, hi
    return ext


def check_kn5_name(kn5_name: str) -> str:
    """El nombre del KN5 debe ser un nombre de archivo simple (sin rutas) y acabar en .kn5."""
    ok = (
        kn5_name.lower().endswith(".kn5")
        and kn5_name not in (".kn5", "..")
        and not any(c in kn5_name for c in "/" + chr(92) + ":" + chr(0))
        and ".." not in kn5_name
        and Path(kn5_name).name == kn5_name
    )
    if not ok:
        raise RuntimeError(
            f"nombre de KN5 no válido (solo un nombre .kn5 simple): {kn5_name!r}"
        )
    return kn5_name


def build_kn5(
    plan: ScenePlan, out_dir: str | Path, kn5_name: str
) -> tuple[Path, Verification]:
    """Escribe el plan, construye el KN5 en Python puro y lo verifica contra el plan.

    Escribe en ``out_dir``: scene_plan.json, verification.json y el KN5.
    """
    from .pure_kn5 import build_kn5_pure

    out = ensure_safe_output_path(out_dir)
    check_kn5_name(kn5_name)
    write_plan(plan, out / "scene_plan.json")
    kn5_path = ensure_safe_output_path(out / kn5_name)
    build_kn5_pure(plan, kn5_path)
    result = verify_kn5(read_kn5(kn5_path), plan)
    target = ensure_safe_output_path(out / "verification.json")
    target.write_text(
        json.dumps(
            {"ok": result.ok, "errors": result.errors, "checks": result.checks},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return kn5_path, result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m src.mesh.build", description=__doc__
    )
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--kn5", default=None, help="nombre del KN5 (por defecto <car>.kn5)"
    )
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)

    out = ensure_safe_output_path(args.out)
    plan = plan_for_vehicle(args.vehicle_dir, args.pc)
    plan_path = write_plan(plan, out / "scene_plan.json")
    print(
        f"Plan: {len(plan.objects)} mallas, {len(plan.empties)} empties -> {plan_path}"
    )
    for issue in plan.issues:
        print(f"  aviso: {issue}")
    if args.plan_only:
        return 0
    kn5_name = args.kn5 or f"{Path(args.vehicle_dir).name}.kn5"
    try:
        kn5_path, result = build_kn5(plan, out, kn5_name)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    kn5 = read_kn5(kn5_path)
    print(f"KN5: {kn5_path}  nodos {len(kn5.nodes)}  materiales {len(kn5.materials)}")
    print(
        f"Verificación: {'OK' if result.ok else 'FALLOS'} "
        f"({len(result.errors)} errores)"
    )
    for err in result.errors[:20]:
        print(f"  - {err}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
