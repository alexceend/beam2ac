"""Lectura mínima de archivos COLLADA (.dae), solo biblioteca estándar.

Lee lo necesario para validar transformaciones: unidad, eje vertical,
nodos de escena (nombre → geometría, matriz) y posiciones de vértices de cada
geometría. No lee normales, UV ni materiales (eso lo lee ``src.mesh.pure_kn5``).
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from src.jbeam.sources import ZipMember

Vec3 = tuple[float, float, float]


class DaeError(ValueError):
    """El DAE no tiene la estructura esperada."""


@dataclass
class DaeGeometry:
    id: str
    name: str | None
    positions: list[Vec3]  # en unidades del archivo
    nonfinite: dict[str, int] = field(default_factory=dict)  # fuente -> nº NaN/inf
    # G2.18: índices de posición usados por alguna cara (``<p>`` de ``triangles``,
    # ``polylist`` o ``polygons``, entrada VERTEX); ``None`` si no hay primitivas.
    # Un vértice suelto no llega al KN5 (el escritor solo exporta caras).
    face_vertices: frozenset[int] | None = None


@dataclass
class DaeNode:
    name: str
    geometry_url: str | None
    matrix: tuple[float, ...] | None  # 16 valores, fila a fila, tal cual


@dataclass
class DaeFile:
    path: Path | ZipMember
    unit_meter: float
    up_axis: str
    geometries: dict[str, DaeGeometry] = field(default_factory=dict)
    nodes: dict[str, DaeNode] = field(default_factory=dict)

    def geometry_for_node(self, node_name: str) -> DaeGeometry:
        node = self.nodes.get(node_name)
        if node is None or not node.geometry_url:
            raise DaeError(f"{self.path.name}: no hay nodo con geometría '{node_name}'")
        gid = node.geometry_url.lstrip("#")
        if gid not in self.geometries:
            raise DaeError(f"{self.path.name}: geometría '{gid}' inexistente")
        return self.geometries[gid]


IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_dae(path: str | Path | ZipMember) -> DaeFile:
    """Lee un DAE de una carpeta o de dentro de un zip (``ZipMember``, en memoria y en
    solo lectura: el zip no se extrae ni se modifica)."""
    if isinstance(path, ZipMember):
        tree = ET.parse(io.BytesIO(path.read_bytes()))
    else:
        path = Path(path)
        tree = ET.parse(path)
    root = tree.getroot()
    unit_meter, up_axis = 1.0, "Y_UP"  # valores por defecto de COLLADA
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "unit" and "meter" in el.attrib:
            unit_meter = float(el.attrib["meter"])
        elif tag == "up_axis" and el.text:
            up_axis = el.text.strip()
    dae = DaeFile(path, unit_meter, up_axis)

    for geom in root.iter():
        if _local(geom.tag) != "geometry":
            continue
        gid = geom.attrib.get("id")
        sources = {}
        nonfinite = {}
        position_source = None
        face_vertices: set[int] | None = None
        for el in geom.iter():
            tag = _local(el.tag)
            if tag in ("triangles", "polylist", "polygons"):
                inputs = [c for c in el if _local(c.tag) == "input"]
                voff = [
                    int(c.attrib.get("offset", 0))
                    for c in inputs
                    if c.attrib.get("semantic") == "VERTEX"
                ]
                if voff:
                    stride = max(int(c.attrib.get("offset", 0)) for c in inputs) + 1
                    face_vertices = face_vertices or set()
                    for c in el:
                        if _local(c.tag) == "p" and c.text:
                            idx = c.text.split()
                            face_vertices.update(int(i) for i in idx[voff[0] :: stride])
            if tag == "source":
                arr = next((c for c in el if _local(c.tag) == "float_array"), None)
                if arr is not None and arr.text:
                    sources[el.attrib.get("id")] = arr.text
                    bad = sum(
                        1
                        for tok in arr.text.split()
                        if "nan" in tok.lower() or "inf" in tok.lower()
                    )
                    if bad:
                        nonfinite[el.attrib.get("id")] = bad
            elif tag == "vertices":
                for inp in el:
                    if (
                        _local(inp.tag) == "input"
                        and inp.attrib.get("semantic") == "POSITION"
                    ):
                        position_source = inp.attrib.get("source", "").lstrip("#")
        if gid is None or position_source not in sources:
            raise DaeError(f"{path.name}: geometría {gid!r} sin posiciones")
        if position_source in nonfinite:
            raise DaeError(f"{path.name}: {gid}: posiciones con NaN/inf")
        values = [float(v) for v in sources[position_source].split()]
        if len(values) % 3:
            raise DaeError(f"{path.name}: {gid}: posiciones no múltiplo de 3")
        pts = [tuple(values[i : i + 3]) for i in range(0, len(values), 3)]
        dae.geometries[gid] = DaeGeometry(
            gid,
            geom.attrib.get("name"),
            pts,  # type: ignore[arg-type]
            nonfinite,
            frozenset(face_vertices) if face_vertices is not None else None,
        )

    for node in root.iter():
        if _local(node.tag) != "node":
            continue
        name = node.attrib.get("name") or node.attrib.get("id")
        url = matrix = None
        for child in node:
            tag = _local(child.tag)
            if tag == "instance_geometry":
                url = child.attrib.get("url")
            elif tag == "matrix" and child.text:
                matrix = tuple(float(v) for v in child.text.split())
            elif tag in ("translate", "rotate", "scale"):
                raise DaeError(
                    f"{path.name}: nodo '{name}' usa <{tag}> "
                    "(no soportado; solo <matrix>)"
                )
        if name:
            if name in dae.nodes:
                raise DaeError(f"{path.name}: nodo repetido '{name}'")
            dae.nodes[name] = DaeNode(name, url, matrix)
    return dae


def bounds(points: list[Vec3]) -> tuple[Vec3, Vec3]:
    if not points:
        raise DaeError("sin puntos")
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def material_names(path: str | Path | ZipMember) -> frozenset[str]:
    """Nombres (``name``, o ``id`` si falta) de ``library_materials``: los que se
    dan a los materiales del KN5, antes de recortarlos a 63 bytes (G2.24)."""
    if isinstance(path, ZipMember):
        tree = ET.parse(io.BytesIO(path.read_bytes()))
    else:
        tree = ET.parse(Path(path))
    out = set()
    for el in tree.getroot().iter():
        if _local(el.tag) == "material":
            name = el.attrib.get("name") or el.attrib.get("id")
            if name:
                out.add(name)
    return frozenset(out)
