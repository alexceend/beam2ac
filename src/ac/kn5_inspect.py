"""Lector de KN5 (v5/v6) para verificar modelos.

Formato tomado de ``tools/kn5-obj-converter-main/convert.py`` (lectura) y de
los KN5 de referencia (escritura); comprobado con el KN5 de
la muestra de AC.

Matrices: 16 floats por fila con la traslación en la fila 4 (convención de
vector fila): ``p_padre = p_local · M``. Mundo de un nodo = ``M_local ·
M_mundo_padre``. Los vértices de una malla están en el marco de su nodo padre.
"""

from __future__ import annotations

import math
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

Vec3 = tuple[float, float, float]
Mat = tuple[tuple[float, ...], ...]  # 4x4 filas

IDENTITY: Mat = tuple(tuple(1.0 if i == j else 0.0 for j in range(4)) for i in range(4))


class Kn5Error(ValueError):
    """Archivo KN5 no válido o no soportado."""


@dataclass
class Kn5Node:
    name: str
    type: int  # 1 dummy, 2 mesh, 3 skinned
    parent: int
    local: Mat = IDENTITY
    world: Mat = IDENTITY
    vertices: list[Vec3] = field(default_factory=list)  # marco del padre
    material: int | None = None
    children: list[int] = field(default_factory=list)
    nonfinite_normals: int = 0
    indices: list[int] = field(default_factory=list)
    flags: dict[str, float] = field(default_factory=dict)  # malla: sombras, visible…

    @property
    def translation(self) -> Vec3:
        return self.world[3][0], self.world[3][1], self.world[3][2]


@dataclass
class Kn5File:
    version: int
    textures: list[str]
    materials: list[tuple[str, str]]  # (nombre, shader)
    nodes: list[Kn5Node]

    def find(self, name: str) -> list[Kn5Node]:
        return [n for n in self.nodes if n.name == name]

    def mesh_world_vertices(self, node: Kn5Node) -> list[Vec3]:
        parent = self.nodes[node.parent].world if node.parent >= 0 else IDENTITY
        a, b, c, d = parent  # transform_point desenrollado (cientos de miles de puntos)
        a0, a1, a2 = a[0], a[1], a[2]
        b0, b1, b2 = b[0], b[1], b[2]
        c0, c1, c2 = c[0], c[1], c[2]
        d0, d1, d2 = d[0], d[1], d[2]
        return [
            (
                x * a0 + y * b0 + z * c0 + d0,
                x * a1 + y * b1 + z * c1 + d1,
                x * a2 + y * b2 + z * c2 + d2,
            )
            for x, y, z in node.vertices
        ]


def mat_mul(a: Mat, b: Mat) -> Mat:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def transform_point(p: Vec3, m: Mat) -> Vec3:
    x, y, z = p[0], p[1], p[2]
    a, b, c, d = m
    return (
        x * a[0] + y * b[0] + z * c[0] + d[0],
        x * a[1] + y * b[1] + z * c[1] + d[1],
        x * a[2] + y * b[2] + z * c[2] + d[2],
    )


def invert_rigid(m: Mat) -> Mat:
    """Inversa de una matriz de rotación + traslación (vector fila)."""
    r = [[m[j][i] for j in range(3)] for i in range(3)]  # transpuesta
    t = [-sum(m[3][k] * r[k][j] for k in range(3)) for j in range(3)]
    return tuple(tuple(r[i]) + (0.0,) for i in range(3)) + ((t[0], t[1], t[2], 1.0),)


class _Reader:
    def __init__(self, f: BinaryIO):
        self.f = f

    def raw(self, n: int) -> bytes:
        data = self.f.read(n)
        if len(data) != n:
            raise Kn5Error("fin de archivo inesperado")
        return data

    def i32(self) -> int:
        return struct.unpack("<i", self.raw(4))[0]

    def string(self) -> str:
        return self.raw(self.i32()).decode("utf-8", "replace")


_KN5_MEMO: dict = {}
_KN5_LOCK = threading.Lock()


def read_kn5(path: str | Path, keep_vertices: bool = True) -> Kn5File:
    """Lee un KN5. Memoriza los 2 últimos (ruta, fecha, tamaño): una petición del editor de cámaras
    lo lee ~5 veces (0,7 s cada una). El resultado es de solo lectura: no modificarlo.
    """
    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_mtime_ns, st.st_size, keep_vertices)
    with (
        _KN5_LOCK
    ):  # el servidor tiene hilos; leer dentro del cerrojo evita lecturas duplicadas
        if key not in _KN5_MEMO:
            if len(_KN5_MEMO) >= 2:
                _KN5_MEMO.pop(next(iter(_KN5_MEMO)))
            _KN5_MEMO[key] = _read_kn5(p, keep_vertices)
        return _KN5_MEMO[key]


def _read_kn5(path: str | Path, keep_vertices: bool = True) -> Kn5File:
    with open(path, "rb") as f:
        r = _Reader(f)
        magic, version = struct.unpack("<6sI", r.raw(10))
        if magic != b"sc6969":
            raise Kn5Error(f"cabecera inválida {magic!r}")
        if version > 5:
            r.raw(4)
        textures = []
        for _ in range(r.i32()):
            r.i32()
            textures.append(r.string())
            f.seek(r.i32(), 1)
        materials = []
        for _ in range(r.i32()):
            name, shader = r.string(), r.string()
            r.raw(2)
            if version > 4:
                r.raw(4)
            for _ in range(r.i32()):
                r.string()
                r.raw(40)
            for _ in range(r.i32()):
                r.string()
                r.raw(4)
                r.string()
            materials.append((name, shader))
        nodes: list[Kn5Node] = []
        _read_node(r, nodes, -1, keep_vertices)
        return Kn5File(version, textures, materials, nodes)


def _read_node(r: _Reader, nodes: list[Kn5Node], parent: int, keep: bool) -> None:
    ntype = r.i32()
    node = Kn5Node(r.string(), ntype, parent)
    count = r.i32()
    r.raw(1)
    if ntype == 1:
        vals = struct.unpack("<16f", r.raw(64))
        node.local = tuple(tuple(vals[i * 4 : i * 4 + 4]) for i in range(4))
    elif ntype in (2, 3):
        cast, visible, transparent = r.raw(3)
        node.flags = {
            "cast_shadows": cast,
            "visible": visible,
            "transparent": transparent,
        }
        if ntype == 3:
            for _ in range(r.i32()):
                r.string()
                r.raw(64)
        stride = 44 if ntype == 2 else 76
        vcount = r.i32()
        data = r.raw(vcount * stride)
        if keep:
            node.vertices = list(struct.iter_unpack(f"<3f{stride - 12}x", data))
        finite = math.isfinite
        node.nonfinite_normals += sum(
            not (finite(x) and finite(y) and finite(z))
            for x, y, z in struct.iter_unpack(f"<12x3f{stride - 24}x", data)
        )
        icount = r.i32()
        idata = r.raw(icount * 2)
        if keep:
            node.indices = list(struct.unpack(f"<{icount}H", idata))
        node.material = r.i32()
        if ntype == 2:
            layer, lod_in, lod_out, cx, cy, cz, radius, renderable = struct.unpack(
                "<I6f?", r.raw(29)
            )
            node.flags.update(
                layer=layer,
                lod_in=lod_in,
                lod_out=lod_out,
                sphere_radius=radius,
                renderable=renderable,
            )
        else:
            r.raw(12)
    else:
        raise Kn5Error(f"tipo de nodo desconocido {ntype}")
    parent_world = nodes[parent].world if parent >= 0 else IDENTITY
    node.world = mat_mul(node.local, parent_world)
    nodes.append(node)
    index = len(nodes) - 1
    if parent >= 0:
        nodes[parent].children.append(index)
    for _ in range(count):
        _read_node(r, nodes, index, keep)
