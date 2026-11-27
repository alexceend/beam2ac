"""KN5 de coche en Python puro (sin numpy ni programas externos).

Lee los DAE de BeamNG, triangula, aplica las matrices del plan (:mod:`src.mesh.plan`),
construye el árbol de nodos y escribe el KN5 con los bloques de :mod:`src.ac.kn5_materials` y
:mod:`src.ac.kn5_tree`. El formato binario se deriva SOLO de este repositorio y de los KN5 de
referencia construidos antes con otra herramienta.

Uso: ``python -m src.mesh.pure_kn5 <carpeta_vehiculo> <archivo.pc> --out output/<n>``.

Reglas medidas contra los KN5 de referencia; lo que no se pudo reproducir está marcado como
DIFERENCIA CONOCIDA:

* Árbol: raíz ``root`` (identidad) → un nodo (tipo 1) por objeto/Empty, hijos ordenados por
  nombre. Un objeto sin padre lleva matriz identidad; con padre lleva la inversa del mundo del
  padre y sus vértices quedan en coordenadas de mundo. Cada material del objeto es una malla
  (tipo 2) homónima hija del nodo, en el orden de primera aparición en el DAE.
* Ejes: ``AC(x, y, z) = (Bx, Bz, −By)`` sobre vértices, normales y matrices de Empty
  (rotación pura, sin cambio de sentido de las caras). Matrices en vector fila.
* Vértice KN5 = posición, normal, UV, tangente (44 bytes). Tangente (0, 0, 0): es lo que lleva
  la referencia cuando el material no tiene textura (las texturas se añaden después).
* UV: se toma el PRIMER ``TEXCOORD`` y se escribe ``(u, −v)`` (medido). Si la fuente UV trae
  NaN/inf, toda la malla va a (0, 0).
* Normales: las del DAE transformadas con la inversa traspuesta del plan; una normal no
  finita se sustituye por la normal de su cara. Sin ``NORMAL`` en el DAE: normal de cara.
* Triángulos: ``triangles`` tal cual; ``polylist``/``polygons`` en abanico (v0, vi, vi+1)
  (en polígonos cóncavos la referencia triangula distinto). Cada triángulo (a, b, c) se
  escribe con índices rotados (b, c, a), como la referencia.
* Partición de vértices: un vértice KN5 por terna única (índice de posición, de normal, de UV)
  dentro de cada malla de material. DIFERENCIA CONOCIDA: en el Ibiza la referencia escribe
  487199 vértices y este módulo 363675 (la referencia no deduplica registros cuya normal
  difiere ~1e-5). El 4,3 % de las esquinas tiene una normal que difiere > 1e-3 de la de la
  referencia (1,5 % > 0,1). Se mantienen las normales del DAE (son el dato de origen).
* Caras repetidas (mismo conjunto de vértices, cualquier orden): se descartan todas menos la
  primera (medido: 156 en body0 del Ibiza).
* Orden de las mallas de un objeto: por primera aparición del símbolo de material en todo el
  DAE, no por el orden de las primitivas del objeto.
* Materiales: solo los usados, ordenados por nombre (63 bytes, y ``.001``, ``.002`` por cada
  DAE que repite el nombre, en orden de ruta); shader ``ksPerPixel`` con ``ksDiffuse`` =
  ``ksAmbient`` = 0,4, sin texturas ni ranuras: :func:`src.ac.kn5_materials.apply_diffuse` los
  acepta tal cual.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ac.kn5_materials import Kn5Material, Kn5Parts, build_kn5
from src.ac.kn5_tree import MeshData, Node, invert_rigid, mat_mul, mesh_payload
from src.ac.kn5_tree import serialize_tree
from src.jbeam.sources import ZipMember
from src.safe_paths import ensure_safe_output_path

from .plan import ScenePlan, trim_name

Vec3 = tuple[float, float, float]
ROOT_NAME = "root"  # nombre del nodo raíz del KN5
MAX_VERTICES = 65535
SHADER = "ksPerPixel"
KS_DEFAULT = 0.4  # ksDiffuse y ksAmbient de los KN5 de referencia
_ZEROS9 = (0.0,) * 9
# AC = C · BeamNG (rotación pura): (x, y, z) → (x, z, −y)
_C = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))


class PureKn5Error(ValueError):
    """No se puede construir el KN5 (datos del DAE o del plan incoherentes)."""


# ------------------------------------------------------------------ lectura de DAE
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class Primitive:
    material: str  # símbolo de ``material=`` de la primitiva
    corners: list[
        tuple[int, int, int]
    ]  # (posición, normal, uv) por esquina; -1 = no hay
    triangles: int = 0


@dataclass
class MeshSource:
    """Geometría triangulada de un nodo del DAE, en unidades del archivo."""

    positions: list[Vec3]
    normals: list[Vec3] | None
    uvs: list[tuple[float, float]] | None
    uv_finite: bool
    primitives: list[Primitive] = field(default_factory=list)
    # símbolo de material -> nombre de material en ``library_materials``
    bind: dict[str, str] = field(default_factory=dict)
    # símbolo -> orden de primera aparición en TODO el DAE (orden de ranuras de material)
    rank: dict[str, int] = field(default_factory=dict)


def _num(tok: str) -> float:
    try:
        return float(tok)
    except ValueError:  # Automation escribe "-nan(ind)" / "nan(ind)"
        return math.nan


def _floats(text: str | None, stride: int) -> list[tuple[float, ...]]:
    toks = (text or "").split()
    try:
        vals = list(map(float, toks))
    except ValueError:
        vals = [_num(v) for v in toks]
    if len(vals) % stride:
        raise PureKn5Error(f"fuente con {len(vals)} valores, stride {stride}")
    return [tuple(vals[i : i + stride]) for i in range(0, len(vals), stride)]


def _read_geometry(geom: ET.Element) -> MeshSource:
    mesh = next(c for c in geom if _local(c.tag) == "mesh")
    sources: dict[str, ET.Element] = {}
    vertices_pos: dict[str, str] = {}
    for el in mesh:
        tag = _local(el.tag)
        if tag == "source":
            sources[el.attrib["id"]] = el
        elif tag == "vertices":
            for inp in el:
                if inp.attrib.get("semantic") == "POSITION":
                    vertices_pos[el.attrib["id"]] = inp.attrib["source"].lstrip("#")

    def source(ref: str, stride: int) -> list[tuple[float, ...]]:
        arr = next(
            c for c in sources[ref.lstrip("#")] if _local(c.tag) == "float_array"
        )
        return _floats(arr.text, stride)

    positions: list[Vec3] | None = None
    normals = uvs = None
    uv_ref: str | None = None
    prims: list[Primitive] = []
    for el in mesh:
        if _local(el.tag) not in ("triangles", "polylist", "polygons"):
            continue
        inputs = [c for c in el if _local(c.tag) == "input"]
        stride = max(int(c.attrib.get("offset", 0)) for c in inputs) + 1
        off = {"VERTEX": -1, "NORMAL": -1, "TEXCOORD": -1}
        for c in inputs:
            sem = c.attrib.get("semantic")
            if sem not in off or off[sem] >= 0:
                continue  # solo el primer TEXCOORD (multi-UV: ver docstring)
            off[sem] = int(c.attrib.get("offset", 0))
            if sem == "VERTEX":
                positions = positions or [
                    p  # type: ignore[misc]
                    for p in source(vertices_pos[c.attrib["source"].lstrip("#")], 3)
                ]
            elif sem == "NORMAL" and normals is None:
                normals = source(c.attrib["source"], 3)  # type: ignore[assignment]
            elif sem == "TEXCOORD" and uvs is None:
                uv_ref = c.attrib["source"]
                uvs = source(uv_ref, 2)  # type: ignore[assignment]
        if off["VERTEX"] < 0:
            raise PureKn5Error(f"{geom.attrib.get('id')}: primitiva sin VERTEX")
        p_el = [c for c in el if _local(c.tag) == "p"]
        counts = None
        if _local(el.tag) == "polylist":
            vc = next(c for c in el if _local(c.tag) == "vcount")
            counts = [int(v) for v in (vc.text or "").split()]
        polys: list[list[int]] = []
        for p in p_el:
            idx = list(map(int, (p.text or "").split()))
            if _local(el.tag) == "polygons":
                polys.append(idx)  # un <p> = un polígono
            elif counts is None:  # triangles: todo el <p> son triángulos
                polys += [
                    idx[i : i + 3 * stride] for i in range(0, len(idx), 3 * stride)
                ]
            else:  # polylist: cortar por vcount
                pos = 0
                for n in counts:
                    polys.append(idx[pos : pos + n * stride])
                    pos += n * stride
        corners: list[tuple[int, int, int]] = []
        tris = 0
        ov, on, ot = off["VERTEX"], off["NORMAL"], off["TEXCOORD"]
        for poly in polys:
            n = len(poly) // stride
            if n < 3:
                continue
            ring = [
                (
                    poly[b + ov],
                    poly[b + on] if on >= 0 else -1,
                    poly[b + ot] if ot >= 0 else -1,
                )
                for b in range(0, n * stride, stride)
            ]
            for i in range(1, n - 1):  # abanico (v0, vi, vi+1); triángulo: tal cual
                corners += [ring[0], ring[i], ring[i + 1]]  # type: ignore[list-item]
                tris += 1
        prims.append(Primitive(el.attrib.get("material", ""), corners, tris))
    if positions is None:
        raise PureKn5Error(f"{geom.attrib.get('id')}: geometría sin primitivas")
    uv_finite = uvs is None or all(math.isfinite(c) for uv in uvs for c in uv)
    return MeshSource(
        positions, normals, uvs, uv_finite, prims  # type: ignore[arg-type]
    )


def read_dae_meshes(
    path: str | Path | ZipMember, wanted: set[str]
) -> tuple[dict[str, MeshSource], list[str]]:
    """Mallas ``wanted`` (nombres de nodo) de un DAE y los nombres de TODOS los
    materiales de ``library_materials`` (todos cuentan para el sufijo de duplicados)."""
    if isinstance(path, ZipMember):
        root = ET.parse(io.BytesIO(path.read_bytes())).getroot()
    else:
        root = ET.parse(Path(path)).getroot()
    geoms: dict[str, ET.Element] = {}
    mat_names: dict[str, str] = {}  # id -> nombre
    order: list[str] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "geometry":
            geoms[el.attrib["id"]] = el
        elif tag == "material" and "id" in el.attrib:
            name = el.attrib.get("name") or el.attrib["id"]
            mat_names[el.attrib["id"]] = name
            order.append(name)
    rank: dict[str, int] = {}
    for g in geoms.values():  # orden del archivo
        for el in g.iter():
            if _local(el.tag) in ("triangles", "polylist", "polygons"):
                rank.setdefault(el.attrib.get("material", ""), len(rank))
    out: dict[str, MeshSource] = {}
    for node in root.iter():
        if _local(node.tag) != "node":
            continue
        name = node.attrib.get("name") or node.attrib.get("id")
        if name not in wanted:
            continue
        inst = next((c for c in node if _local(c.tag) == "instance_geometry"), None)
        if inst is None:
            continue
        src = _read_geometry(geoms[inst.attrib["url"].lstrip("#")])
        src.rank = rank
        for im in inst.iter():
            if _local(im.tag) == "instance_material":
                target = im.attrib.get("target", "").lstrip("#")
                src.bind[im.attrib["symbol"]] = mat_names.get(target, target)
        out[name] = src
    missing = wanted - set(out)
    if missing:
        raise PureKn5Error(f"{Path(str(path)).name}: sin malla(s) {sorted(missing)}")
    return out, order


# ------------------------------------------------------------------ geometría
def _det3(m: list[list[float]]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _normal_matrix(m: list[list[float]]) -> list[list[float]]:
    """Inversa traspuesta (cofactores / det) de la parte 3×3: transforma normales."""
    d = _det3(m)
    if d == 0:
        raise PureKn5Error("matriz del plan singular")
    cof = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            a, b = [r for r in range(3) if r != i], [c for c in range(3) if c != j]
            cof[i][j] = (-1) ** (i + j) * (
                m[a[0]][b[0]] * m[a[1]][b[1]] - m[a[0]][b[1]] * m[a[1]][b[0]]
            )
    return [[c / d for c in row] for row in cof]


def _to_ac(v: Vec3) -> Vec3:
    return (v[0], v[2], -v[1])  # AC = (Bx, Bz, −By)


def _unit(v: Vec3) -> Vec3 | None:
    x, y, z = v
    n = math.sqrt(sum((x * x, y * y, z * z)))
    if not math.isfinite(n) or n == 0:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


@dataclass
class BuiltMesh:
    name: str
    material: str  # nombre del material en el KN5
    vertices: list[tuple[float, ...]]  # 11 floats
    indices: list[int]


def build_object_meshes(
    name: str,
    src: MeshSource,
    matrix: list[list[float]],
    material_names: dict[str, str],
    warnings: list[str],
) -> list[BuiltMesh]:
    """Mallas KN5 (una por material) de un objeto del plan, en ejes AC y mundo."""
    (a, b, c, d), (e, f, g, h), (i, j, k, m) = (r[:4] for r in matrix[:3])
    pos = [  # matriz · p en BeamNG y después AC = (Bx, Bz, −By)
        (
            a * x + b * y + c * z + d,
            i * x + j * y + k * z + m,
            -(e * x + f * y + g * z + h),
        )
        for x, y, z in src.positions
    ]
    if any(not math.isfinite(c) for p in pos for c in p):
        raise PureKn5Error(f"{name}: posiciones no finitas")
    if _det3([r[:3] for r in matrix[:3]]) < 0:
        warnings.append(f"{name}: matriz con reflexión (caras invertidas)")
    nrm: list[Vec3 | None] = []
    if src.normals is not None:
        nm = _normal_matrix([r[:3] for r in matrix[:3]])
        # sum() y no a+b+c: desde Python 3.12 suma floats con compensación
        r0, r1, r2 = nm
        for x, y, z in src.normals:
            t = (
                sum((r0[0] * x, r0[1] * y, r0[2] * z)),
                sum((r1[0] * x, r1[1] * y, r1[2] * z)),
                sum((r2[0] * x, r2[1] * y, r2[2] * z)),
            )
            u = _unit(t)
            nrm.append(_to_ac(u) if u else None)
    uvs = src.uvs
    if uvs is not None and not src.uv_finite:
        warnings.append(f"{name}: UV no finitas; toda la malla a (0, 0)")
        uvs = None
    # Una malla por material, en orden de primera aparición.
    groups: dict[str, list[tuple[int, int, int]]] = {}
    seen: set[frozenset[int]] = set()
    dropped = 0
    for prim in src.primitives:
        mat = material_names.get(src.bind.get(prim.material, prim.material))
        if mat is None:
            raise PureKn5Error(f"{name}: material '{prim.material}' sin nombre")
        keep = groups.setdefault(mat, [])
        for t in range(0, len(prim.corners), 3):
            tri = prim.corners[t : t + 3]
            ident = frozenset(c[0] for c in tri)
            # las caras repetidas (mismos vértices, cualquier orden) se descartan:
            # medido en body0 del Ibiza, 156 triángulos (caras de doble cara del DAE).
            if len(ident) == 3 and ident in seen:
                dropped += 1
                continue
            seen.add(ident)
            keep.extend(tri)
    if dropped:
        warnings.append(f"{name}: {dropped} triángulos repetidos descartados")
    out: list[BuiltMesh] = []
    repaired = 0
    # Orden de las mallas de un objeto = ranuras de material: por primera aparición del
    # símbolo en todo el DAE (no por la primitiva de este objeto); medido en el Ibiza.
    first: dict[str, int] = {}
    for prim in src.primitives:
        mat = material_names[src.bind.get(prim.material, prim.material)]
        r = src.rank.get(prim.material, 0)
        first[mat] = min(first.get(mat, r), r)
    for mat, corners in sorted(groups.items(), key=lambda kv: first[kv[0]]):
        verts: list[tuple[float, ...]] = []
        remap: dict[tuple[int, int, int, int], int] = {}
        idx: list[int] = []
        for t in range(0, len(corners), 3):
            tri = corners[t : t + 3]
            face = None
            ids = []
            for pi, ni, ti in tri:
                n = nrm[ni] if ni >= 0 and nrm else None
                key = (pi, ni, ti, -1)
                if n is None:  # sin normal o no finita: normal de la cara
                    if face is None:
                        a, b, c = (pos[k[0]] for k in tri)
                        cr = (
                            (b[1] - a[1]) * (c[2] - a[2])
                            - (b[2] - a[2]) * (c[1] - a[1]),
                            (b[2] - a[2]) * (c[0] - a[0])
                            - (b[0] - a[0]) * (c[2] - a[2]),
                            (b[0] - a[0]) * (c[1] - a[1])
                            - (b[1] - a[1]) * (c[0] - a[0]),
                        )
                        face = _unit(cr) or (0.0, 1.0, 0.0)
                        if ni >= 0:
                            repaired += 1
                    n = face
                    key = (pi, ni, ti, t)
                if key not in remap:
                    uv = (uvs[ti][0], -uvs[ti][1]) if uvs and ti >= 0 else (0.0, 0.0)
                    remap[key] = len(verts)
                    verts.append((*pos[pi], *n, *uv, 0.0, 0.0, 0.0))
                ids.append(remap[key])
            idx += [
                ids[1],
                ids[2],
                ids[0],
            ]  # rotación (b, c, a) de los KN5 de referencia
        out += _chunks(BuiltMesh(name, mat, verts, idx))
    if repaired:
        warnings.append(
            f"{name}: {repaired} normales no finitas sustituidas por la de cara"
        )
    return out


def _chunks(m: BuiltMesh) -> list[BuiltMesh]:
    """Parte por triángulos enteros las mallas de más de 65535 vértices."""
    if len(m.vertices) <= MAX_VERTICES:
        return [m]
    parts: list[BuiltMesh] = []
    remap: dict[int, int] = {}
    verts: list[tuple[float, ...]] = []
    idx: list[int] = []
    for t in range(0, len(m.indices), 3):
        tri = m.indices[t : t + 3]
        new = sum(1 for i in tri if i not in remap)
        if len(verts) + new > MAX_VERTICES:
            parts.append(BuiltMesh(m.name, m.material, verts, idx))
            remap, verts, idx = {}, [], []
        for i in tri:
            if i not in remap:
                remap[i] = len(verts)
                verts.append(m.vertices[i])
            idx.append(remap[i])
    parts.append(BuiltMesh(m.name, m.material, verts, idx))
    return parts


# ------------------------------------------------------------------ matrices de nodo
def _row_world_ac(m: list[list[float]]) -> list[list[float]]:
    """Matriz de BeamNG (columna) → mundo AC en vector fila: ``C·M·Cᵀ`` traspuesta."""
    a = [
        [
            sum(_C[i][p] * m[p][q] * _C[j][q] for p in range(3) for q in range(3))
            for j in range(3)
        ]
        for i in range(3)
    ]
    t = [sum(_C[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [
        [a[0][0], a[1][0], a[2][0], 0.0],
        [a[0][1], a[1][1], a[2][1], 0.0],
        [a[0][2], a[1][2], a[2][2], 0.0],
        [t[0], t[1], t[2], 1.0],
    ]


def _null(name: str, matrix: list[list[float]]) -> Node:
    return Node(
        1,
        name,
        1,
        struct.pack("<16f", *[matrix[i][j] for i in range(4) for j in range(4)]),
    )


_IDENT = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


# ------------------------------------------------------------------ nombres de material
def unique_material_names(dae_materials: list[list[str]]) -> list[dict[str, str]]:
    """Nombre final de cada material por DAE (en orden de importación).

    Cada DAE aporta todos los materiales de su ``library_materials``; si el nombre ya existe
    se añade ``.001``, ``.002``… y se recorta a 63 bytes antes del sufijo.
    """
    taken: set[str] = set()
    result = []
    for names in dae_materials:
        mapping: dict[str, str] = {}
        for raw in names:
            if raw in mapping:
                continue
            base = trim_name(raw)
            final, n = base, 0
            while final in taken:
                n += 1
                final = trim_name(base, 4) + f".{n:03d}"
            taken.add(final)
            mapping[raw] = final
        result.append(mapping)
    return result


# ------------------------------------------------------------------ construcción
def _plan_dict(plan: ScenePlan | dict[str, Any]) -> dict[str, Any]:
    return json.loads(plan.to_json()) if isinstance(plan, ScenePlan) else plan


def build_kn5_pure(
    plan: ScenePlan | dict[str, Any],
    out_path: str | Path,
    dae_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Escribe el KN5 del plan y devuelve estadísticas y avisos.

    ``dae_dirs``: carpetas donde buscar un DAE por nombre de archivo si la ruta del plan
    no existe (planes guardados en otro equipo).
    """
    p = _plan_dict(plan)
    warnings: list[str] = []

    def resolve(path: str) -> Path:
        q = Path(path.replace("\\", "/"))
        if q.exists():
            return q
        for d in dae_dirs or []:
            if (d / q.name).exists():
                return d / q.name
        raise PureKn5Error(f"no se encuentra el DAE {q.name}")

    daes = sorted({o["dae"] for o in p["objects"]}, key=lambda s: s.replace("\\", "/"))
    sources: dict[str, dict[str, MeshSource]] = {}
    lib: list[list[str]] = []
    for d in daes:
        wanted = {o["mesh"] for o in p["objects"] if o["dae"] == d}
        sources[d], names = read_dae_meshes(resolve(d), wanted)
        lib.append(names)
    mat_maps = dict(zip(daes, unique_material_names(lib)))

    empties = {e["name"]: e for e in p["empties"]}
    world = {n: _row_world_ac(e["matrix"]) for n, e in empties.items()}
    nodes_by_name: dict[str, Node] = {}
    children: dict[str | None, list[Node]] = {None: []}
    for name, e in empties.items():
        parent = e.get("parent")
        if parent and parent not in empties:
            raise PureKn5Error(f"Empty '{name}': padre '{parent}' inexistente")
        local = (
            mat_mul(world[name], invert_rigid(world[parent])) if parent else world[name]
        )
        node = _null(name, local)
        nodes_by_name[name] = node
        children.setdefault(parent, []).append(node)

    used: set[str] = set()
    built: list[tuple[Node, list[BuiltMesh]]] = []
    for o in p["objects"]:
        meshes = build_object_meshes(
            o["name"], sources[o["dae"]][o["mesh"]], o["matrix"],
            mat_maps[o["dae"]], warnings,
        )  # fmt: skip
        parent = o.get("parent")
        if parent and parent not in empties:
            raise PureKn5Error(f"'{o['name']}': padre '{parent}' inexistente")
        node = _null(o["name"], invert_rigid(world[parent]) if parent else _IDENT)
        children.setdefault(parent, []).append(node)
        built.append((node, meshes))
        used.update(m.material for m in meshes)
    mat_order = sorted(used)
    mat_index = {n: i for i, n in enumerate(mat_order)}

    stats = {"meshes": 0, "vertices": 0, "triangles": 0}
    for node, meshes in built:
        for bm in meshes:
            md = MeshData(
                bytes([1, 1, 0]),
                [struct.pack("<11f", *v) for v in bm.vertices],
                bm.indices,
                mat_index[bm.material],
                0, 0.0, 0.0, True,
            )  # fmt: skip
            node.children.append(Node(2, bm.name, 1, mesh_payload(md)))
            stats["meshes"] += 1
            stats["vertices"] += len(bm.vertices)
            stats["triangles"] += len(bm.indices) // 3
    for key, lst in children.items():
        lst.sort(key=lambda n: n.name)
        if key:
            nodes_by_name[key].children = lst
    root = Node(1, ROOT_NAME, 1, struct.pack("<16f", *[c for r in _IDENT for c in r]))
    root.children = children[None]
    props = [(n, (KS_DEFAULT,) + _ZEROS9) for n in ("ksDiffuse", "ksAmbient")]
    parts = Kn5Parts(
        5, b"", [], [Kn5Material(n, SHADER, 0, 0, 0, list(props), []) for n in mat_order],
        serialize_tree(root),
    )  # fmt: skip
    target = ensure_safe_output_path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_kn5(parts))
    stats.update(materials=len(mat_order), warnings=warnings, path=str(target))
    return stats


def production_plan(plan: ScenePlan) -> ScenePlan:
    """El plan que construye producción (origen Kunos + nulos COCKPIT/STEER vacíos)."""
    from .plan import (
        add_cockpit_lr_nulls,
        add_steer_hr_diagnostic_nulls,
        kunos_origin_offset,
        translate_plan,
    )

    moved = translate_plan(plan, kunos_origin_offset(plan))
    return add_steer_hr_diagnostic_nulls(add_cockpit_lr_nulls(moved))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m src.mesh.pure_kn5", description=__doc__
    )
    ap.add_argument("vehicle_dir")
    ap.add_argument("pc")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--kn5", default=None, help="nombre del KN5 (por defecto <car>.kn5)"
    )
    args = ap.parse_args(argv)
    from .build import plan_for_vehicle, verify_kn5
    from src.ac.kn5_inspect import read_kn5

    out = ensure_safe_output_path(args.out)
    plan = production_plan(plan_for_vehicle(args.vehicle_dir, args.pc))
    kn5 = out / (args.kn5 or f"{Path(args.vehicle_dir).name}.kn5")
    stats = build_kn5_pure(plan, kn5)
    print(
        f"KN5: {kn5}  mallas {stats['meshes']}  vértices {stats['vertices']}  "
        f"triángulos {stats['triangles']}  materiales {stats['materials']}"
    )
    for w in stats["warnings"][:20]:
        print(f"  aviso: {w}")
    result = verify_kn5(read_kn5(kn5), plan)
    print(f"Verificación frente al plan: {'OK' if result.ok else 'FALLOS'}")
    for err in result.errors[:20]:
        print(f"  - {err}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
