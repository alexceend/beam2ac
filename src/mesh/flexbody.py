"""Flexbodies de la configuración activa y su transformación a coordenadas BeamNG.

Fila de ``flexbodies``: ``["mesh", ["grupo", …], [nonFlexMaterials], {"pos", "rot",
"scale"}]``. Transformación de un vértice ``v`` del DAE (en unidades del archivo):

    p = pos + Rx(rot.x) · Ry(rot.y) · Rz(rot.z) · diag(scale) · (unit · v)

* ``unit`` = ``<unit meter>`` del DAE (0,01 en la muestra: centímetros).
* Ángulos en grados, rotaciones activas con la regla de la mano derecha, orden
  de matrices X·Y·Z (Z se aplica primero al vértice).

Estado [confirmado con la muestra; docs/research/mallas.md §2]: de las 12
combinaciones de orden/signo, solo esta alinea el eje de las 4 mallas de
neumático con el eje de rueda definido por los nodos (error < 0,06°; las demás
2–4°). Es coherente además con la caja de la carrocería frente a los nodos
``CarBody`` (2–3 cm) y con las pinzas abrazando el disco.

No soportado (``UnsupportedGeometry``): flexbody sin ``pos``/``rot``/``scale``
(valores por defecto de BeamNG no verificados), escalas no positivas (espejos:
no aparecen en la muestra), y piezas con flexbodies cuyo
``nodeOffset``/``nodeMove`` efectivo no es 0 (no se sabe si BeamNG lo aplica a
la malla).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.extract.nodes import UnsupportedGeometry
from src.jbeam import ResolvedConfig, evaluate, parse_table

Vec3 = tuple[float, float, float]
Matrix4 = tuple[tuple[float, float, float, float], ...]  # fila a fila


@dataclass(frozen=True)
class Flexbody:
    part: str
    mesh: str
    groups: tuple[str, ...]
    pos: Vec3
    rot_deg: Vec3
    scale: Vec3


def _vec(raw: Any, variables, what: str) -> Vec3:
    if not isinstance(raw, dict) or set(raw) - {"x", "y", "z"} or len(raw) != 3:
        raise UnsupportedGeometry(f"{what}: se esperaba {{x, y, z}}, no {raw!r}")
    out = []
    for axis in "xyz":
        value = evaluate(raw[axis], variables)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UnsupportedGeometry(f"{what}.{axis} no numérico: {value!r}")
        out.append(float(value))
    return tuple(out)  # type: ignore[return-value]


def collect_flexbodies(resolved: ResolvedConfig) -> list[Flexbody]:
    variables = resolved.variable_values()
    out = []
    for active in resolved.active:
        section = active.part.data.get("flexbodies")
        if section is None:
            continue
        rows = parse_table(section).rows
        if not rows:
            continue
        offset = active.node_offset or {}
        move = active.slot_options.get("nodeMove") or {}
        for raw in (offset, move):
            if any(evaluate(v, variables) not in (0, 0.0, None) for v in raw.values()):
                raise UnsupportedGeometry(
                    f"'{active.name}': flexbodies bajo nodeOffset/nodeMove ≠ 0 "
                    "(no verificado si se aplica a la malla)"
                )
        for row in rows:
            mesh = row.get("mesh")
            opts = row.inline
            where = f"'{active.name}' flexbody {mesh!r}"
            if not isinstance(mesh, str):
                raise UnsupportedGeometry(f"{where}: nombre de malla inválido")
            missing = [k for k in ("pos", "rot", "scale") if k not in opts]
            if missing:
                raise UnsupportedGeometry(f"{where}: faltan {missing}")
            groups = row.get("[group]:") or []
            if isinstance(groups, str):
                groups = [groups]
            scale = _vec(opts["scale"], variables, f"{where}.scale")
            if any(c <= 0 for c in scale):
                raise UnsupportedGeometry(
                    f"{where}: escala no positiva {scale} (espejo no soportado)"
                )
            out.append(
                Flexbody(
                    part=active.name,
                    mesh=mesh,
                    groups=tuple(groups),
                    pos=_vec(opts["pos"], variables, f"{where}.pos"),
                    rot_deg=_vec(opts["rot"], variables, f"{where}.rot"),
                    scale=scale,
                )
            )
    return out


def _mm(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)
    ]


def rotation_xyz(rot_deg: Vec3) -> list[list[float]]:
    """R = Rx · Ry · Rz (grados, mano derecha)."""
    x, y, z = (math.radians(a) for a in rot_deg)
    cx, sx, cy, sy, cz, sz = (
        math.cos(x),
        math.sin(x),
        math.cos(y),
        math.sin(y),
        math.cos(z),
        math.sin(z),
    )
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mm(_mm(rx, ry), rz)


def flexbody_matrix(fb: Flexbody, unit_meter: float) -> Matrix4:
    """Matriz 4×4 (fila a fila, columna = vector) de DAE a coordenadas BeamNG (m)."""
    r = rotation_xyz(fb.rot_deg)
    s = [c * unit_meter for c in fb.scale]
    rows = []
    for i in range(3):
        rows.append((r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], fb.pos[i]))
    rows.append((0.0, 0.0, 0.0, 1.0))
    return tuple(rows)  # type: ignore[return-value]


def apply(m: Matrix4, p: Vec3) -> Vec3:
    x, y, z = p[0], p[1], p[2]
    a, b, c = m[0], m[1], m[2]
    return (
        a[0] * x + a[1] * y + a[2] * z + a[3],
        b[0] * x + b[1] * y + b[2] * z + b[3],
        c[0] * x + c[1] * y + c[2] * z + c[3],
    )
