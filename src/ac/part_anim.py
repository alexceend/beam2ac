"""Animaciones de piezas del coche (puertas, limpiaparabrisas, palanca) a partir de una configuración del usuario (G2.55).

Automation NO exporta estas piezas (la carrocería sale en mallas por material, sin bisagras ni palancas): los datos
los pone el usuario. Cada entrada nombra un nodo que YA existe en el KN5 (malla o dummy), un pivote (marco del padre
del nodo, el del KN5 si cuelga de la raíz) y un eje; el resultado es un ``.ksanim`` con 100 fotogramas.

Formato [obs, ``ks_toyota_ae86``]: ``steer_anim.write_ksanim``; cada fotograma = transformación LOCAL del hueso:
cuaternión (x, y, z, w) de ``R_col`` = traspuesta de las 3×3 de la matriz por filas del nodo, traslación = fila 4
(comprobado con ``COCKPIT_HR``: filas [1,0,0],[0,0,−1],[0,1,0] → (−0,707, 0, 0, 0,707)), escala 1.
Archivos de AC [obs, nombres de los AE86/Celica/R8]: ``car_door_L``/``car_door_R``/``car_wiper``/``car_shift``.
Semántica del perfil [hip, sin probar en AC]: puerta = 0 → ángulo (suavizado); limpia = 0 → ángulo → 0; palanca = 0 → ángulo.
"""

from __future__ import annotations

import math
from typing import Any

from src.ac.kn5_inspect import (
    IDENTITY,
    Mat,
    invert_rigid,
    mat_mul,
    read_kn5,
    transform_point,
)
from src.ac.steer_anim import write_ksanim

FRAMES = 100
KINDS = {
    "door_L": "car_door_L.ksanim",
    "door_R": "car_door_R.ksanim",
    "wiper": "car_wiper.ksanim",
    "shift": "car_shift.ksanim",
}


AXES = {  # ejes del marco del KN5 (+X izquierda, +Y arriba, +Z adelante)
    "vertical": (0.0, 1.0, 0.0),
    "longitudinal": (0.0, 0.0, 1.0),
    "lateral": (1.0, 0.0, 0.0),
}
HINGES = ("front", "back", "left", "right", "top", "bottom", "center")
DEFAULT_AXIS = {
    "door_L": "vertical",
    "door_R": "vertical",
    "wiper": "longitudinal",
    "shift": "lateral",
}


class PartAnimError(ValueError):
    pass


def node_refs(kn) -> list[str]:
    """Referencia de cada nodo: su nombre si es único y ``nombre#k`` (k = 1, 2… en orden del KN5) si se repite."""
    total: dict[str, int] = {}
    for n in kn.nodes:
        total[n.name] = total.get(n.name, 0) + 1
    seen: dict[str, int] = {}
    out = []
    for n in kn.nodes:
        seen[n.name] = seen.get(n.name, 0) + 1
        out.append(n.name if total[n.name] == 1 else f"{n.name}#{seen[n.name]}")
    return out


def resolve_node(kn, ref) -> list:
    """Nodos que cumplen ``ref`` (``nombre`` exacto o ``nombre#k``); ``[]`` si no existe."""
    if not isinstance(ref, str):
        return []
    refs = node_refs(kn)
    hits = [n for n, r in zip(kn.nodes, refs) if r == ref]
    return hits or [
        n
        for n in kn.nodes
        if n.name == ref and sum(m.name == ref for m in kn.nodes) == 1
    ]


def _descendants(kn, i: int) -> list[int]:
    out, stack = [], [i]
    while stack:
        j = stack.pop()
        out.append(j)
        stack.extend(kn.nodes[j].children)
    return out


def pivot_from_hinge(kn, node, hinge: str) -> tuple[float, float, float]:
    """Pivote (en el marco del PADRE del nodo) en el lado ``hinge`` de la caja de la pieza: ``front`` = +Z,
    ``back`` = −Z, ``left`` = +X (izquierda de AC), ``right`` = −X, ``top`` = +Y, ``bottom`` = −Y, ``center``.
    """
    if hinge not in HINGES:
        raise PartAnimError(f"bisagra {hinge!r}: use {list(HINGES)}")
    i = kn.nodes.index(node)
    pts = [
        p
        for j in _descendants(kn, i)
        if kn.nodes[j].type == 2
        for p in kn.mesh_world_vertices(kn.nodes[j])
    ]
    if not pts:
        raise PartAnimError(f"{node.name}: sin geometría para situar la bisagra")
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    k, v = {
        "front": (2, hi[2]),
        "back": (2, lo[2]),
        "left": (0, hi[0]),
        "right": (0, lo[0]),
        "top": (1, hi[1]),
        "bottom": (1, lo[1]),
        "center": (0, c[0]),
    }[hinge]
    world = list(c)
    world[k] = v
    parent = kn.nodes[node.parent].world if node.parent >= 0 else IDENTITY
    inv = invert_rigid(parent)
    return transform_point(tuple(world), inv)


def _translation(p: tuple[float, float, float]) -> Mat:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (p[0], p[1], p[2], 1.0),
    )


def _rotation_row(axis: tuple[float, float, float], angle: float) -> Mat:
    n = math.sqrt(sum(c * c for c in axis))
    if n == 0:
        raise PartAnimError("eje de giro nulo")
    x, y, z = (c / n for c in axis)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    col = (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )
    return tuple(  # por filas = traspuesta de la de columnas
        tuple(col[j][i] for j in range(3)) + (0.0,) for i in range(3)
    ) + (
        (0.0, 0.0, 0.0, 1.0),
    )


def _quaternion(r_col: list[list[float]]) -> tuple[float, float, float, float]:
    t = r_col[0][0] + r_col[1][1] + r_col[2][2]
    if t > 0:
        s = math.sqrt(t + 1) * 2
        q = (
            (r_col[2][1] - r_col[1][2]) / s,
            (r_col[0][2] - r_col[2][0]) / s,
            (r_col[1][0] - r_col[0][1]) / s,
            s / 4,
        )
    elif r_col[0][0] > r_col[1][1] and r_col[0][0] > r_col[2][2]:
        s = math.sqrt(1 + r_col[0][0] - r_col[1][1] - r_col[2][2]) * 2
        q = (
            s / 4,
            (r_col[0][1] + r_col[1][0]) / s,
            (r_col[0][2] + r_col[2][0]) / s,
            (r_col[2][1] - r_col[1][2]) / s,
        )
    elif r_col[1][1] > r_col[2][2]:
        s = math.sqrt(1 + r_col[1][1] - r_col[0][0] - r_col[2][2]) * 2
        q = (
            (r_col[0][1] + r_col[1][0]) / s,
            s / 4,
            (r_col[1][2] + r_col[2][1]) / s,
            (r_col[0][2] - r_col[2][0]) / s,
        )
    else:
        s = math.sqrt(1 + r_col[2][2] - r_col[0][0] - r_col[1][1]) * 2
        q = (
            (r_col[0][2] + r_col[2][0]) / s,
            (r_col[1][2] + r_col[2][1]) / s,
            s / 4,
            (r_col[1][0] - r_col[0][1]) / s,
        )
    return q


def frame_of(m: Mat) -> tuple[float, ...]:
    """Fotograma ksanim (10 floats) de una matriz local por filas (rotación uniforme + traslación)."""
    rows = [list(m[i][:3]) for i in range(3)]
    sc = [math.sqrt(sum(c * c for c in r)) or 1.0 for r in rows]
    r_row = [[rows[i][j] / sc[i] for j in range(3)] for i in range(3)]
    r_col = [[r_row[j][i] for j in range(3)] for i in range(3)]
    q = _quaternion(r_col)
    return (*q, m[3][0], m[3][1], m[3][2], sc[0], sc[1], sc[2])


def profile(kind: str, i: int) -> float:
    """Fracción del ángulo en el fotograma ``i`` (0..FRAMES-1)."""
    u = i / (FRAMES - 1)
    if kind == "wiper":
        return math.sin(math.pi * u)
    return u * u * (3 - 2 * u)  # puerta y palanca: 0 → 1 suavizado


def clip_for(
    local: Mat, kind: str, pivot, axis, angle_deg: float
) -> list[tuple[float, ...]]:
    frames = []
    p = tuple(float(c) for c in pivot)
    for i in range(FRAMES):
        rot = _rotation_row(
            tuple(float(c) for c in axis), math.radians(angle_deg) * profile(kind, i)
        )
        m = mat_mul(
            mat_mul(mat_mul(local, _translation((-p[0], -p[1], -p[2]))), rot),
            _translation(p),
        )
        frames.append(frame_of(m))
    return frames


def build_files(kn5_path, items: list[dict[str, Any]]) -> dict[str, bytes]:
    """``{nombre de archivo: bytes}``; un ``PartAnimError`` explica la primera entrada inválida."""
    kn = read_kn5(kn5_path, keep_vertices=any("hinge" in it for it in items))
    out: dict[str, bytes] = {}
    for it in items:
        kind = it.get("kind")
        if kind not in KINDS:
            raise PartAnimError(f"tipo {kind!r}: use {sorted(KINDS)}")
        nodes = resolve_node(kn, it.get("node"))
        if len(nodes) != 1:
            raise PartAnimError(
                f"{kind}: el nodo {it.get('node')!r} existe {len(nodes)} veces en el KN5 (se necesita exactamente 1; "
                "Automation no separa puertas/limpias: deben existir como nodo propio)"
            )
        fname = KINDS[kind]
        if fname in out:
            raise PartAnimError(f"{kind}: ya hay otra entrada de este tipo")
        if (
            "hinge" in it
        ):  # forma simple (sin números): lado de la pieza y eje por nombre
            axis_name = it.get("axis_name") or DEFAULT_AXIS[kind]
            if axis_name not in AXES:
                raise PartAnimError(f"{kind}: eje {axis_name!r}: use {sorted(AXES)}")
            it = {
                **it,
                "pivot": list(pivot_from_hinge(kn, nodes[0], it["hinge"])),
                "axis": list(AXES[axis_name]),
            }
        for key in ("pivot", "axis"):
            v = it.get(key)
            if (
                not isinstance(v, (list, tuple))
                or len(v) != 3
                or not all(
                    isinstance(c, (int, float))
                    and not isinstance(c, bool)
                    and math.isfinite(c)
                    and abs(c) < 1e6
                    for c in v
                )
            ):
                raise PartAnimError(f"{kind}: {key} debe ser 3 números finitos")
        if not any(it["axis"]):
            raise PartAnimError(f"{kind}: eje de giro nulo")
        ang = it.get("angle_deg")
        if (
            not isinstance(ang, (int, float))
            or isinstance(ang, bool)
            or not -180 <= ang <= 180
        ):
            raise PartAnimError(f"{kind}: angle_deg entre -180 y 180")
        local = nodes[0].local or IDENTITY
        out[fname] = write_ksanim(
            [
                (
                    nodes[0].name,
                    clip_for(local, kind, it["pivot"], it["axis"], float(ang)),
                )
            ]
        )
    return out
