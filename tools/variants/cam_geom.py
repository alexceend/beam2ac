"""Geometría de cámaras de AC a partir de un KN5 (solo lectura).

Marcos de referencia (análisis de AC):
- ``DRIVEREYES`` y ``[DASH_CAM] POS`` se multiplican por el marco del KN5
  (cámara de salpicadero) (origen en el suelo, centro del coche).
- ``BONNET_CAMERA_POS`` y ``BUMPER_CAMERA_POS`` se multiplican por el marco del cuerpo físico.
- Entre ambos, con ``GRAPHICS_PITCH_ROTATION`` = 0: punto_KN5 = punto_cuerpo − GRAPHICS_OFFSET
  (comprobado con 5 coches de referencia: con esa relación las cámaras Bonnet quedan
  0,02–0,13 m por encima del capó; con el signo contrario o sin desfase quedan dentro de la
  carrocería).

Perfil superior: para una línea vertical (x, z), la y más alta de los triángulos de la
carrocería que la cortan (coordenadas baricéntricas en el plano x-z). Se excluyen ruedas,
suspensión, piloto y volante y sus hijos.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.ac.kn5_inspect import read_kn5  # noqa: E402

Vec3 = tuple[float, float, float]
Tri = tuple[Vec3, Vec3, Vec3]
SKIP = ("WHEEL", "SUSP", "TYRE", "RIM", "DISC", "DRIVER", "HUB", "CALIPER", "STEER")


def _ancestry(k, n) -> list[str]:
    out = [n.name.upper()]
    while n.parent >= 0:
        n = k.nodes[n.parent]
        out.append(n.name.upper())
    return out


def body_triangles(kn5_path: Path, x0: float) -> tuple[float, list[Tri]]:
    """(z más adelantada de la carrocería con |x| < 0,3, triángulos cuyo rango x contiene x0)."""
    k = read_kn5(kn5_path)
    tris: list[Tri] = []
    front = -9.0
    for n in k.nodes:
        if n.type != 2 or any(s in a for a in _ancestry(k, n) for s in SKIP):
            continue
        v = k.mesh_world_vertices(n)
        for p in v:
            if abs(p[0]) < 0.3:
                front = max(front, p[2])
        idx = n.indices
        for i in range(0, len(idx) - 2, 3):
            a, b, c = v[idx[i]], v[idx[i + 1]], v[idx[i + 2]]
            if min(a[0], b[0], c[0]) <= x0 <= max(a[0], b[0], c[0]):
                tris.append((a, b, c))
    return front, tris


def top_y(tris: list[Tri], x0: float, z0: float) -> Optional[float]:
    """y más alta de los triángulos que cortan la vertical (x0, z0)."""
    best = None
    for a, b, c in tris:
        if not (min(a[2], b[2], c[2]) <= z0 <= max(a[2], b[2], c[2])):
            continue
        d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
        if abs(d) < 1e-12:
            continue
        l1 = ((b[2] - c[2]) * (x0 - c[0]) + (c[0] - b[0]) * (z0 - c[2])) / d
        l2 = ((c[2] - a[2]) * (x0 - c[0]) + (a[0] - c[0]) * (z0 - c[2])) / d
        l3 = 1 - l1 - l2
        if min(l1, l2, l3) < -1e-9:
            continue
        y = l1 * a[1] + l2 * b[1] + l3 * c[1]
        best = y if best is None else max(best, y)
    return best


def profile(tris: list[Tri], x0: float, z_from: float, z_to: float, step: float = 0.02) -> list[tuple[float, float]]:
    out = []
    n = int(round((z_to - z_from) / step))
    for i in range(n + 1):
        z = round(z_from + i * step, 4)
        y = top_y(tris, x0, z)
        if y is not None:
            out.append((z, y))
    return out


def y_at(p: list[tuple[float, float]], z: float) -> Optional[float]:
    for (z0, y0), (z1, y1) in zip(p, p[1:]):
        if z0 <= z <= z1:
            return y0 + (y1 - y0) * (z - z0) / (z1 - z0) if z1 > z0 else y0
    return None


def hood_rear_edge(p: list[tuple[float, float]], z_eyes: float) -> Optional[float]:
    """Borde trasero del capó: z + 0,04 donde el salto y(z+0,04) − y(z) es máximo, buscando
    entre DRIVEREYES z + 0,5 y + 1,6 m (escalón entre la base del parabrisas y el capó)."""
    best, zbest = -9.0, None
    z = z_eyes + 0.5
    while z <= z_eyes + 1.6:
        a, b = y_at(p, z), y_at(p, z + 0.04)
        if a is not None and b is not None and b - a > best:
            best, zbest = b - a, z + 0.04
        z += 0.02
    return None if zbest is None else round(zbest, 3)


def f3(s: str) -> list[float]:
    return [float(v) for v in s.split(";")[0].split(",")]
