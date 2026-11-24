"""Árbol de nodos de un KN5 (v5) editable sin perder bytes.

Se usa para operaciones que no deben tocar la geometría: mover mallas completas bajo
otro nulo y partir una
malla por triángulos enteros. El formato es el mismo que leen :mod:`src.ac.kn5_inspect`
y
:mod:`src.ac.kn5_materials`:

    nodo = int32 tipo, cadena nombre, int32 nº hijos, byte activo, carga útil
    tipo 1 (nulo): 16 float (matriz local, vector fila: p_padre = p_local · M)
    tipo 2 (malla): 3 bytes (sombras, visible, transparente), int32 nº vértices,
    vértices de 44 bytes
                    (pos 3f, normal 3f, uv 2f, tangente 3f), int32 nº índices, uint16
                    índices,
                    int32 material, uint32 capa, 6 float (lodIn, lodOut, centro x/y/z,
                    radio), bool renderizable
Los nodos de tipo 3 (con piel) no se admiten (los KN5 de coche del proyecto no los
tienen).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from src.ac.kn5_materials import Kn5FormatError, Kn5Parts, build_kn5, split_kn5

VERTEX_SIZE = 44
Mat = list[list[float]]


@dataclass
class Node:
    type: int
    name: str
    active: int
    payload: (
        bytes  # tipo 1: 64 bytes de matriz; tipo 2: todo lo que sigue al byte activo
    )
    children: list["Node"] = field(default_factory=list)

    # --- nulos
    @property
    def matrix(self) -> Mat:
        if self.type != 1:
            raise Kn5FormatError(f"{self.name} no es un nulo")
        m = struct.unpack("<16f", self.payload)
        return [list(m[i * 4 : i * 4 + 4]) for i in range(4)]

    def set_matrix(self, m: Mat) -> None:
        if self.type != 1:
            raise Kn5FormatError(f"{self.name} no es un nulo")
        self.payload = struct.pack(
            "<16f", *[m[i][j] for i in range(4) for j in range(4)]
        )


@dataclass
class MeshData:
    flags: bytes  # 3 bytes
    vertices: list[bytes]  # 44 bytes cada uno, sin interpretar
    indices: list[int]
    material: int
    layer: int
    lod_in: float
    lod_out: float
    renderable: bool

    def positions(self) -> list[tuple[float, float, float]]:
        return [struct.unpack_from("<3f", v, 0) for v in self.vertices]


def _read_string(data: bytes, p: int) -> tuple[str, int]:
    n = struct.unpack_from("<i", data, p)[0]
    return data[p + 4 : p + 4 + n].decode("utf-8"), p + 4 + n


def _parse(data: bytes, p: int) -> tuple[Node, int]:
    ntype = struct.unpack_from("<i", data, p)[0]
    name, p = _read_string(data, p + 4)
    count = struct.unpack_from("<i", data, p)[0]
    active = data[p + 4]
    p += 5
    start = p
    if ntype == 1:
        p += 64
    elif ntype == 2:
        p += 3
        vc = struct.unpack_from("<i", data, p)[0]
        p += 4 + vc * VERTEX_SIZE
        ic = struct.unpack_from("<i", data, p)[0]
        p += 4 + ic * 2 + 4 + 29
    else:
        raise Kn5FormatError(f"tipo de nodo no admitido: {ntype} ({name})")
    node = Node(ntype, name, active, data[start:p])
    for _ in range(count):
        child, p = _parse(data, p)
        node.children.append(child)
    return node, p


def parse_tree(nodes_bytes: bytes) -> Node:
    root, end = _parse(nodes_bytes, 0)
    if end != len(nodes_bytes):
        raise Kn5FormatError(f"sobran {len(nodes_bytes) - end} bytes tras el árbol")
    return root


def serialize_tree(root: Node) -> bytes:
    out = bytearray()

    def emit(n: Node) -> None:
        name = n.name.encode("utf-8")
        out.extend(struct.pack("<i", n.type) + struct.pack("<i", len(name)) + name)
        out.extend(struct.pack("<i", len(n.children)) + bytes([n.active]) + n.payload)
        for c in n.children:
            emit(c)

    emit(root)
    return bytes(out)


def read_mesh(node: Node) -> MeshData:
    if node.type != 2:
        raise Kn5FormatError(f"{node.name} no es una malla")
    b = node.payload
    flags = b[:3]
    vc = struct.unpack_from("<i", b, 3)[0]
    p = 7
    verts = [b[p + i * VERTEX_SIZE : p + (i + 1) * VERTEX_SIZE] for i in range(vc)]
    p += vc * VERTEX_SIZE
    ic = struct.unpack_from("<i", b, p)[0]
    idx = list(struct.unpack_from(f"<{ic}H", b, p + 4))
    p += 4 + ic * 2
    material = struct.unpack_from("<i", b, p)[0]
    layer, lod_in, lod_out, _cx, _cy, _cz, _r, rend = struct.unpack_from(
        "<I6f?", b, p + 4
    )
    return MeshData(flags, verts, idx, material, layer, lod_in, lod_out, rend)


def mesh_payload(m: MeshData) -> bytes:
    """Carga útil de una malla; la esfera envolvente se recalcula como en
    :mod:`src.ac.kn5_write`.
    """
    if (
        len(m.vertices) > 65535
        or any(i >= len(m.vertices) for i in m.indices)
        or len(m.indices) % 3
    ):
        raise Kn5FormatError("malla inválida")
    pos = m.positions()
    centre = tuple(
        (min(v[i] for v in pos) + max(v[i] for v in pos)) / 2 for i in range(3)
    )
    radius = max(math.dist(centre, v) for v in pos)
    return (
        m.flags
        + struct.pack("<i", len(m.vertices))
        + b"".join(m.vertices)
        + struct.pack("<i", len(m.indices))
        + struct.pack(f"<{len(m.indices)}H", *m.indices)
        + struct.pack("<i", m.material)
        + struct.pack(
            "<I6f?", m.layer, m.lod_in, m.lod_out, *centre, radius, m.renderable
        )
    )


def split_mesh(m: MeshData, keep_triangle) -> tuple[MeshData, MeshData]:
    """Parte una malla por triángulos enteros.

    ``keep_triangle(t)`` decide si el triángulo t va a la primera parte.

    Cada vértice usado por una parte se copia byte a byte (los compartidos se duplican);
    no se crea ni se mueve
    ningún vértice.
    """

    def subset(tris: list[int]) -> MeshData:
        remap: dict[int, int] = {}
        verts: list[bytes] = []
        idx: list[int] = []
        for t in tris:
            for i in m.indices[3 * t : 3 * t + 3]:
                if i not in remap:
                    remap[i] = len(verts)
                    verts.append(m.vertices[i])
                idx.append(remap[i])
        return MeshData(
            m.flags, verts, idx, m.material, m.layer, m.lod_in, m.lod_out, m.renderable
        )

    n = len(m.indices) // 3
    a = [t for t in range(n) if keep_triangle(t)]
    b = [t for t in range(n) if not keep_triangle(t)]
    if not a or not b:
        raise Kn5FormatError("la partición deja una parte vacía")
    return subset(a), subset(b)


def mat_mul(a: Mat, b: Mat) -> Mat:
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)
    ]


def invert_rigid(m: Mat) -> Mat:
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(m[3][k] * r[k][j] for k in range(3)) for j in range(3)]
    return [r[0] + [0.0], r[1] + [0.0], r[2] + [0.0], t + [1.0]]


def frame_matrix(origin, z_axis, x_hint=(1.0, 0.0, 0.0)) -> Mat:
    """Matriz rígida (vector fila) con Z local = ``z_axis``.

    X queda lo más parecido posible a ``x_hint``.

    Mano derecha: Z = X × Y (como el ``STEER_HR`` de los coches de Kunos).
    """
    z = list(z_axis)
    nz = math.sqrt(sum(c * c for c in z))
    z = [c / nz for c in z]
    d = sum(a * b for a, b in zip(x_hint, z))
    x = [a - d * b for a, b in zip(x_hint, z)]
    nx = math.sqrt(sum(c * c for c in x))
    x = [c / nx for c in x]
    y = [
        z[1] * x[2] - z[2] * x[1],
        z[2] * x[0] - z[0] * x[2],
        z[0] * x[1] - z[1] * x[0],
    ]  # Y = Z × X
    return [x + [0.0], y + [0.0], z + [0.0], list(origin) + [1.0]]


def walk(root: Node, parent: Node | None = None):
    yield root, parent
    for c in root.children:
        yield from walk(c, root)


def load(data: bytes) -> tuple[Kn5Parts, Node]:
    parts = split_kn5(data)
    return parts, parse_tree(parts.nodes)


def dump(parts: Kn5Parts, root: Node) -> bytes:
    parts.nodes = serialize_tree(root)
    return build_kn5(parts)
