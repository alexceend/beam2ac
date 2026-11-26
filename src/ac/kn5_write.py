"""Escritor KN5 mínimo (v5) para mallas sin texturas, como ``collider.kn5``.

Formato idéntico al del KN5 de coche (``src.mesh.pure_kn5``) y al que lee
:mod:`src.ac.kn5_inspect`. Estructura replicada de los ``collider.kn5`` oficiales
de Kunos (abarth500, ks_toyota_celica_st185, ks_audi_r8_lms):

    nodo raíz (matriz identidad)
    └── nodo <nombre> (identidad)
        └── malla <nombre> (mismo nombre: patrón de exportación del editor)

Banderas de malla de esos colliders: sombras 1, visible 1, transparente 0,
capa 0, LOD 0/0, renderizable. Material con shader ``GL`` y sin texturas.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from src.safe_paths import ensure_safe_output_path

Vec3 = tuple[float, float, float]
IDENTITY16 = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


@dataclass
class SimpleMesh:
    name: str
    vertices: list[Vec3]  # ejes AC
    normals: list[Vec3]
    indices: list[int]
    material: int = 0
    cast_shadows: bool = True
    visible: bool = True
    transparent: bool = False
    renderable: bool = True
    uvs: list[tuple[float, float]] = field(default_factory=list)


def _s(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<i", len(data)) + data


def _node(name: str, children: int) -> bytes:
    return (
        struct.pack("<i", 1)
        + _s(name)
        + struct.pack("<i", children)
        + b"\x01"
        + struct.pack("<16f", *IDENTITY16)
    )


def _mesh(m: SimpleMesh) -> bytes:
    if len(m.vertices) != len(m.normals) or len(m.vertices) > 65535:
        raise ValueError(f"malla '{m.name}': vértices/normales inválidos")
    if len(m.indices) % 3 or any(i >= len(m.vertices) for i in m.indices):
        raise ValueError(f"malla '{m.name}': índices inválidos")
    out = struct.pack("<i", 2) + _s(m.name) + struct.pack("<i", 0) + b"\x01"
    out += bytes([int(m.cast_shadows), int(m.visible), int(m.transparent)])
    out += struct.pack("<i", len(m.vertices))
    uvs = m.uvs or [(0.0, 0.0)] * len(m.vertices)
    for v, n, uv in zip(m.vertices, m.normals, uvs):
        out += struct.pack("<3f3f2f3f", *v, *n, *uv, 1.0, 0.0, 0.0)
    out += struct.pack("<i", len(m.indices)) + struct.pack(
        f"<{len(m.indices)}H", *m.indices
    )
    out += struct.pack("<i", m.material)
    centre = tuple(
        (min(v[i] for v in m.vertices) + max(v[i] for v in m.vertices)) / 2
        for i in range(3)
    )
    radius = max(math.dist(centre, v) for v in m.vertices)
    out += struct.pack("<I6f?", 0, 0.0, 0.0, *centre, radius, m.renderable)
    return out


def write_simple_kn5(
    path: str | Path,
    meshes: list[SimpleMesh],
    materials: list[tuple[str, str]],
    root: str = "beam2ac",
) -> Path:
    """Escribe un KN5 con un nodo + malla homónimos por cada malla."""
    body = b"sc6969" + struct.pack("<I", 5) + struct.pack("<i", 0)  # sin texturas
    body += struct.pack("<i", len(materials))
    for name, shader in materials:
        # nombre, shader, alphaBlendMode (byte), alphaTested (bool), depthMode (int),
        # 0 propiedades, 0 texturas
        body += _s(name) + _s(shader) + b"\x00\x00" + struct.pack("<iII", 0, 0, 0)
    body += _node(root, len(meshes))
    for m in meshes:
        body += _node(m.name, 1) + _mesh(m)
    target = ensure_safe_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def box_mesh(name: str, lo: Vec3, hi: Vec3, material: int = 0) -> SimpleMesh:
    """Caja cerrada (8 vértices, 12 triángulos) en ejes AC, normales hacia fuera y
    triángulos con volumen firmado positivo, como los colliders oficiales."""
    if any(h <= low for low, h in zip(lo, hi)):
        raise ValueError(f"caja degenerada {lo} {hi}")
    xs, ys, zs = (lo[0], hi[0]), (lo[1], hi[1]), (lo[2], hi[2])
    verts = [(x, y, z) for x in xs for y in ys for z in zs]  # índice = 4x + 2y + z
    centre = tuple((a + b) / 2 for a, b in zip(lo, hi))
    normals = []
    for v in verts:
        d = tuple(c - m for c, m in zip(v, centre))
        n = math.sqrt(sum(c * c for c in d))
        normals.append(tuple(c / n for c in d))
    faces = [
        (0, 1, 3, 2),  # x = lo
        (4, 6, 7, 5),  # x = hi
        (0, 4, 5, 1),  # y = lo
        (2, 3, 7, 6),  # y = hi
        (0, 2, 6, 4),  # z = lo
        (1, 5, 7, 3),  # z = hi
    ]
    indices: list[int] = []
    for a, b, c, d in faces:
        indices += [a, b, c, a, c, d]
    return SimpleMesh(name, verts, normals, indices, material)  # type: ignore[arg-type]


def profile_prism_mesh(
    name: str,
    stations: list[tuple[float, float, float]],
    half_width: float,
    material: int = 0,
) -> SimpleMesh:
    """Prisma cerrado que sigue un perfil lateral medido.

    ``stations`` = (z, y_bajo, y_alto)
    con z creciente; anchura constante ±``half_width`` en x (ejes AC).

    Entre estaciones, el fondo y el techo son rectas. Triángulos: 8·(N−1) + 4
    (≤ 60 con N ≤ 8, regla del SDK). Normales de vértice radiales desde el centro, como
    en :func:`box_mesh`; la orientación se fija para que el volumen firmado
    sea positivo.
    """
    n = len(stations)
    if n < 2 or any(b[0] <= a[0] for a, b in zip(stations, stations[1:])):
        raise ValueError("se necesitan ≥ 2 estaciones con z creciente")
    if any(top <= low for _, low, top in stations) or half_width <= 0:
        raise ValueError("estación degenerada")
    xs = (-half_width, half_width)
    verts: list[Vec3] = []
    for z, low, top in stations:  # índice = 4i + 2·lado + (0 bajo | 1 alto)
        for x in xs:
            verts += [(x, low, z), (x, top, z)]

    def v(i: int, side: int, up: int) -> int:
        return 4 * i + 2 * side + up

    quads = []
    for i in range(n - 1):
        j = i + 1
        quads += [
            (v(i, 0, 0), v(j, 0, 0), v(j, 0, 1), v(i, 0, 1)),  # lado x−
            (v(i, 1, 0), v(i, 1, 1), v(j, 1, 1), v(j, 1, 0)),  # lado x+
            (v(i, 0, 0), v(i, 1, 0), v(j, 1, 0), v(j, 0, 0)),  # fondo
            (v(i, 0, 1), v(j, 0, 1), v(j, 1, 1), v(i, 1, 1)),  # techo
        ]
    quads += [
        (v(0, 0, 0), v(0, 0, 1), v(0, 1, 1), v(0, 1, 0)),  # z mínima
        (v(n - 1, 0, 0), v(n - 1, 1, 0), v(n - 1, 1, 1), v(n - 1, 0, 1)),  # z máxima
    ]
    indices: list[int] = []
    for a, b, c, d in quads:
        indices += [a, b, c, a, c, d]
    if signed_volume(verts, indices) < 0:
        indices = [
            k
            for t in range(0, len(indices), 3)
            for k in (indices[t], indices[t + 2], indices[t + 1])
        ]
    centre = tuple(
        (min(p[i] for p in verts) + max(p[i] for p in verts)) / 2 for i in range(3)
    )
    normals = []
    for p in verts:
        d = tuple(a - c for a, c in zip(p, centre))
        m = math.sqrt(sum(c * c for c in d)) or 1.0
        normals.append(tuple(c / m for c in d))
    return SimpleMesh(name, verts, normals, indices, material)  # type: ignore[arg-type]


def signed_volume(vertices: list[Vec3], indices: list[int]) -> float:
    total = 0.0
    for i in range(0, len(indices), 3):
        a, b, c = (vertices[indices[i + j]] for j in range(3))
        total += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6
    return total
