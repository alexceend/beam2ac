"""Caras traseras reales para piezas finas de doble cara (alerón, G2.55).

BeamNG pinta ``doubleSided`` los materiales de Automation y las piezas finas (hojas del alerón, rejillas) son
superficies de UNA capa: en AC sin CSP los triángulos se descartan por su cara trasera y la pieza desaparece desde un
lado (alerón del Ibiza: solo se veían las placas laterales). Aquí se añade a cada malla pequeña y ABIERTA de un material
``doubleSided`` una copia con el giro de los triángulos invertido y las normales negadas (geometría real: no depende de
CSP). No se tocan las mallas cerradas (neumáticos, cajas) ni las grandes (carrocería: ``MAX_TRIANGLES``).
"""

from __future__ import annotations

import re
import struct
from collections import Counter

from src.ac.kn5_tree import MeshData, dump, load, mesh_payload, read_mesh, walk

MAX_TRIANGLES = (
    4000  # carrocería y piezas grandes quedan igual (duplicarlas no compensa)
)
# piezas aerodinámicas finas de Automation (``<id>_efixturetype__eft_wing1016``); el resto de mallas no se duplica
AERO_NODE = re.compile(r"eft_(wing|spoiler|splitter|diffuser|canard)", re.I)
_Q = 1e-4  # tolerancia para soldar vértices al contar aristas


def is_open(m: MeshData) -> bool:
    """¿Tiene aristas de borde (usadas por un solo triángulo) tras soldar vértices por posición?"""
    key = [tuple(round(c / _Q) for c in p) for p in m.positions()]
    edges: Counter = Counter()
    for t in range(0, len(m.indices), 3):
        a, b, c = (key[i] for i in m.indices[t : t + 3])
        for e in ((a, b), (b, c), (c, a)):
            edges[frozenset(e)] += 1
    return any(n == 1 for n in edges.values())


def with_back_faces(m: MeshData) -> MeshData:
    """Malla + copia reversada (vértices duplicados con la normal negada; mismos UV)."""
    n = len(m.vertices)
    back = []
    for v in m.vertices:
        nx, ny, nz = struct.unpack_from("<3f", v, 12)
        back.append(v[:12] + struct.pack("<3f", -nx, -ny, -nz) + v[24:])
    idx = list(m.indices)
    for t in range(0, len(m.indices), 3):
        a, b, c = m.indices[t : t + 3]
        idx += [a + n, c + n, b + n]
    return MeshData(
        m.flags,
        list(m.vertices) + back,
        idx,
        m.material,
        m.layer,
        m.lod_in,
        m.lod_out,
        m.renderable,
    )


def add_back_faces(
    kn5: bytes,
    material_names: set[str],
    max_triangles: int = MAX_TRIANGLES,
    node_filter: re.Pattern[str] = AERO_NODE,
) -> tuple[bytes, list[str]]:
    """``(KN5 nuevo, nombres de las mallas duplicadas)``; sin cambios si no hay ninguna."""
    parts, root = load(kn5)
    names = [m.name for m in parts.materials]
    done: list[str] = []
    for node, _ in walk(root):
        if node.type != 2 or not node_filter.search(node.name):
            continue
        m = read_mesh(node)
        tris = len(m.indices) // 3
        if (
            names[m.material] not in material_names
            or not 0 < tris <= max_triangles
            or len(m.vertices) * 2 > 65535
            or not is_open(m)
        ):
            continue
        node.payload = mesh_payload(with_back_faces(m))
        done.append(node.name)
    return (dump(parts, root) if done else kn5), done
