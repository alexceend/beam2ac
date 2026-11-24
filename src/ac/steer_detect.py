"""Volante detectado por geometría en el KN5 (sin perfil del vehículo).

El rig de ``steer_wheel.py`` necesitaba un perfil escrito a mano (materiales del volante). Aquí se obtiene
del propio KN5 base, con los ojos del piloto (``DRIVEREYES``, marco del KN5: x izquierda, y arriba, z adelante):

1. Aro: una malla (material) con casi todos sus vértices a ≤ 1,1 m de los ojos, forman un
   disco delgado con hueco (radio 0,10–0,26 m, grosor ≤ 0,5·R, pocos vértices en el centro), con el eje
   hacia delante (|z| ≥ 0,7) y el centro delante de los ojos (0,15–0,95 m), hasta 0,70 m por debajo y a
   ≤ 0,25 m de lado. Si hay varios candidatos se elige el de distancia al ojo más cercana a 0,6 m y se
   anotan los demás.
2. Volumen del volante: radio ≤ 1,08·R y |distancia axial| ≤ 0,10 m del plano del aro. Otros materiales con
   ≥ 98 % de sus vértices dentro se mueven enteros; con 50–98 % se parten por triángulos (los de sus tres
   vértices dentro giran); con menos del 50 % (salpicadero, columna) no se tocan.

Validado en el Eagle Inca P395 (40340) contra su ``.car`` de Automation: centro x = 0,318 (fixture 0,3186 m),
inclinación 12,5° (fixture −13,0°). El sentido de giro sigue sin probar en AC (igual que en el Ibiza).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_materials import split_kn5
from src.ac.kn5_tree import load, walk
from src.ac.steer_wheel import rim_frame_points

R_MIN, R_MAX = 0.10, 0.26
AHEAD = (0.15, 0.95)
BELOW_MAX, ABOVE_MAX, SIDE_MAX = 0.70, 0.10, 0.25
BALL = 1.1  # m alrededor de los ojos donde se busca el aro
AXIAL = 0.10
RADIAL_MARGIN = 1.08
WHOLE, PART = 0.98, 0.50
NOMINAL = 0.6  # m: distancia ojos–centro del volante habitual (Eagle 0,46; Ibiza 0,87)


def list_materials(kn5: Path, eyes: tuple[float, float, float]) -> list[dict[str, Any]]:
    """Materiales con malla del KN5 (para que el usuario elija el aro): nº de vértices y distancia del
    centroide a los ojos, de menor a mayor."""
    data = Path(kn5).read_bytes()
    k = read_kn5(Path(kn5))
    mats = [m.name for m in split_kn5(data).materials]
    out: dict[str, list] = {}
    for kn in k.nodes:
        if kn.type == 2 and kn.vertices:
            out.setdefault(mats[kn.material], []).extend(k.mesh_world_vertices(kn))
    rows = []
    for name, v in out.items():
        c = [sum(q[i] for q in v) / len(v) for i in range(3)]
        radius = max(math.dist(q, c) for q in v)
        dist = math.dist(c, eyes)
        rows.append(
            {
                "material": name,
                "vertices": len(v),
                "distance_m": round(dist, 3),
                "radius_m": round(radius, 3),
                "likely": radius <= 0.5 and dist <= BALL,  # objeto pequeño cerca de los ojos
            }
        )
    return sorted(rows, key=lambda r: (not r["likely"], r["distance_m"]))


def detect_steering_wheel(
    kn5: Path, eyes: tuple[float, float, float], rim_material: str | None = None
) -> dict[str, Any]:
    """Perfil ``steering_wheel`` (formato de ``rig_steering_wheel``) más ``detected``; ``ValueError`` si no
    hay un volante identificable (nunca se inventa uno). Con ``rim_material`` (elección del usuario) ese
    material es el aro y no se aplican los criterios de forma: solo se mide su marco y el volumen."""
    data = Path(kn5).read_bytes()
    k = read_kn5(Path(kn5))
    mats = [m.name for m in split_kn5(data).materials]
    tree = [(n, p) for n, p in walk(load(data)[1])]
    meshes = []  # (nombre de material, nombre del padre, vértices en mundo)
    for (tn, tp), kn in zip(tree, k.nodes):
        if kn.type == 2 and kn.vertices and tp is not None:
            meshes.append((mats[kn.material], tp.name, list(k.mesh_world_vertices(kn))))
    cands = []
    for name, parent, v in meshes:
        if rim_material is not None and name != rim_material:
            continue
        near = [q for q in v if math.dist(q, eyes) <= BALL]
        if rim_material is None and (len(near) < 0.95 * len(v) or len(v) < 50):
            continue
        if len(v) < 3:
            continue
        try:
            c, z, _ = rim_frame_points(v)
        except Exception:
            continue

        def ra(q):
            d = [q[i] - c[i] for i in range(3)]
            ax = sum(d[i] * z[i] for i in range(3))
            return math.sqrt(max(0.0, sum(x * x for x in d) - ax * ax)), ax

        rs = sorted(ra(q)[0] for q in v)
        axs = [ra(q)[1] for q in v]
        r_big = rs[int(0.97 * (len(rs) - 1))]
        thick = max(axs) - min(axs)
        hollow = sum(r < 0.5 * r_big for r in rs) / len(rs)
        d = [c[0] - eyes[0], c[1] - eyes[1], c[2] - eyes[2]]
        ok = rim_material is not None or (
            R_MIN <= r_big <= R_MAX
            and thick <= 0.5 * r_big
            and hollow <= 0.35
            and abs(z[2]) >= 0.7
            and AHEAD[0] <= d[2] <= AHEAD[1]
            and -BELOW_MAX <= d[1] <= ABOVE_MAX
            and abs(d[0]) <= SIDE_MAX
            and max(math.dist(q, c) for q in v) <= 1.3 * r_big + thick
        )
        if ok:
            cands.append((abs(math.dist(c, eyes) - NOMINAL), name, parent, c, z, r_big, thick, hollow))
    if not cands and rim_material is not None:
        raise ValueError(f"el material {rim_material!r} no tiene malla en el KN5")
    if not cands:
        raise ValueError("ninguna malla tiene forma de aro de volante delante de los ojos")
    cands.sort()
    _, rim, parent, c, z, big_r, thick, hollow = cands[0]
    if rim_material is not None and big_r > 1.5 * R_MAX:
        raise ValueError(
            f"{rim_material!r} mide {big_r:.2f} m de radio: no es un aro de volante (máx. {1.5 * R_MAX:.2f} m)"
        )

    def inside(q):
        d = [q[i] - c[i] for i in range(3)]
        ax = sum(d[i] * z[i] for i in range(3))
        r = math.sqrt(max(0.0, sum(x * x for x in d) - ax * ax))
        return r <= RADIAL_MARGIN * big_r and abs(ax) <= AXIAL

    move, splits, ignored = [], [], []
    for name, par, v in meshes:
        if par != parent:
            continue
        n_in = sum(1 for q in v if inside(q))
        frac = n_in / len(v)
        if name == rim or frac >= WHOLE:
            move.append(name)
        elif frac >= PART:
            splits.append(name)
        elif n_in:
            ignored.append({"material": name, "inside": n_in, "of": len(v)})
    slug = "".join(ch if ch.isalnum() else "_" for ch in parent)
    return {
        "parent_mesh": parent,
        "rim_material_token": rim,
        "move_materials": sorted(set(move)),
        "splits": [
            {"material": m, "mesh_name": f"{slug}_steer_{i}"} for i, m in enumerate(sorted(set(splits)))
        ],
        "split_radius_m": round(RADIAL_MARGIN * big_r, 4),
        "split_axial_m": AXIAL,
        "detected": {
            "method": "elegido por el usuario"
            if rim_material is not None
            else "geometría del KN5 (src/ac/steer_detect.py)",
            "centre": c,
            "axis": z,
            "rim_radius_m": big_r,
            "rim_thickness_m": thick,
            "hollow": hollow,
            "candidates": [{"material": x[1], "distance_to_nominal": x[0]} for x in cands],
            "ignored_partial": ignored,
        },
    }
