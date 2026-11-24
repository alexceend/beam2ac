"""``collider.kn5`` con perfil lateral medido en el KN5 (variante v7).

Sustituye a la caja envolvente, cuyo fondo quedaba a 0,069 m en toda la longitud
(informe_prueba_manual_2026-10-24.md §B2). El método es el de v7 (validado en el
juego), en ejes AC y con el origen de Kunos ya aplicado:

* puntos «techo»: vértices en mundo de las mallas bajo la malla de carrocería del
  perfil (``body_mesh``);
* puntos «fondo»: vértices de todas las mallas salvo las que tienen en su cadena de
  nombres (ella misma o un antecesor) alguno de los ``exclude_tokens`` (ruedas,
  frenos, suspensión, cabina), limitados a ``|x| ≤`` semianchura de la carrocería;
* estaciones en ``z = [z_min] + z_cuts + [z_max]``; en cada tramo, fondo = mínimo y
  techo = máximo de ``y``; cada estación toma el mínimo/máximo de sus dos tramos;
* prisma de anchura constante ``±semianchura`` (:func:`profile_prism_mesh`).

Los cortes ``z_cuts`` son propios del coche (cambios de perfil medidos a mano para el
Ibiza): vienen del perfil del vehículo, no se suponen para otros coches.
El collider se mide en el KN5 BASE (antes de mover el volante), como en v7.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.ac.kn5_inspect import Kn5File, read_kn5
from src.ac.kn5_write import profile_prism_mesh, write_simple_kn5

Vec3 = tuple[float, float, float]
EPS = 1e-9


class ColliderProfileError(ValueError):
    """No se puede derivar el perfil con los datos dados."""


def _chain(kn5: Kn5File, node) -> list[str]:
    names = [node.name]
    while node.parent >= 0:
        node = kn5.nodes[node.parent]
        names.append(node.name)
    return names


def body_points(
    kn5: Kn5File, body_mesh: str, exclude_tokens: list[str]
) -> tuple[list[Vec3], list[Vec3]]:
    """(puntos de la carrocería, puntos de todo salvo lo excluido), en mundo."""
    body: list[Vec3] = []
    rest: list[Vec3] = []
    for n in kn5.nodes:
        if n.type != 2 or not n.vertices:
            continue
        chain = _chain(kn5, n)
        if any(t in x for x in chain for t in exclude_tokens):
            continue
        vs = kn5.mesh_world_vertices(n)
        rest += vs
        if body_mesh in chain:
            body += vs
    if not body:
        raise ColliderProfileError(f"sin vértices bajo {body_mesh}")
    return body, rest


def stations_from_points(
    body: list[Vec3], rest: list[Vec3], z_cuts: list[float]
) -> tuple[float, list[tuple[float, float, float]], dict[str, Any]]:
    half = max(abs(v[0]) for v in body)
    low_pts = [v for v in rest if abs(v[0]) <= half + EPS]
    top_pts = body
    zmin = min(v[2] for v in low_pts + top_pts)
    zmax = max(v[2] for v in low_pts + top_pts)
    zs = [zmin, *z_cuts, zmax]
    if any(b <= a for a, b in zip(zs, zs[1:])):
        raise ColliderProfileError(f"cortes fuera de [{zmin}, {zmax}] o desordenados")

    def interval(pts, a, b):
        return [v[1] for v in pts if a - EPS <= v[2] <= b + EPS]

    lows, tops = [], []
    for a, b in zip(zs, zs[1:]):
        lo, hi = interval(low_pts, a, b), interval(top_pts, a, b)
        if not lo or not hi:
            raise ColliderProfileError(f"tramo vacío entre z={a} y z={b}")
        lows.append(min(lo))
        tops.append(max(hi))
    stations = []
    for i, z in enumerate(zs):
        adj = [j for j in (i - 1, i) if 0 <= j < len(lows)]
        stations.append((z, min(lows[j] for j in adj), max(tops[j] for j in adj)))

    def line(z, k):
        for (z0, *a), (z1, *b) in zip(stations, stations[1:]):
            if z0 - EPS <= z <= z1 + EPS:
                t = (z - z0) / (z1 - z0)
                return a[k] + t * (b[k] - a[k])
        raise AssertionError(z)

    below = sum(v[1] < line(v[2], 0) - 1e-6 for v in low_pts)
    above = sum(v[1] > line(v[2], 1) + 1e-6 for v in top_pts)
    stats = {
        "points_floor": len(low_pts),
        "points_roof": len(top_pts),
        "floor_points_below_profile": below,
        "roof_points_above_profile": above,
    }
    return half, stations, stats


def write_profile_collider(
    base_kn5: Path, path: Path, profile: dict[str, Any]
) -> dict[str, Any]:
    kn5 = read_kn5(base_kn5)
    body, rest = body_points(kn5, profile["body_mesh"], profile["exclude_tokens"])
    half, stations, stats = stations_from_points(body, rest, profile["z_cuts"])
    mesh = profile_prism_mesh("GEO_collider", stations, half)
    write_simple_kn5(path, [mesh], [("GL", "GL")], root="collider")
    return {
        "method": "perfil medido (v7): fondo/techo por tramos, anchura constante",
        "source_kn5": str(base_kn5),
        "half_width": half,
        "stations": stations,
        "triangles": len(mesh.indices) // 3,
        **stats,
        "status": "integrado (validado en el juego con v7)",
    }
