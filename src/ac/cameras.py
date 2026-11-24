"""Cámaras de a bordo: ``dash_cam.ini``, ``BONNET_CAMERA_POS`` y ``BUMPER_CAMERA_POS``.

Integra en el ensamblador las reglas con las que se construyeron ``pkg_v17cam`` y
``pkg_v18bu`` (validadas en el juego: las seis vistas funcionan). Procedencia:
``tools/variants/{cam_geom,measure_cameras,measure_bumper,build_v17cam,build_v18bu}.py``
y ``output/seat_ibiza_acfr/camera_work_2026-10-25/{cameras,bumper}_measurement.json``.
No se recalibra nada: las medianas de las referencias son constantes de este módulo.

Marcos (análisis de AC):

* ``DRIVEREYES`` y ``[DASH_CAM] POS``: marco del KN5.
* ``BONNET_CAMERA_POS`` y ``BUMPER_CAMERA_POS``: marco del
  cuerpo físico. Con ``GRAPHICS_PITCH_ROTATION`` = 0: KN5 = cuerpo − GRAPHICS_OFFSET.

Reglas (heurísticas medidas en coches de referencia, no normas de AC):

* **Dash**: ``POS`` = ``DRIVEREYES`` (como ``sample_assetto``), ``EXP`` = 26
  (``ks_toyota_ae86``).
* **Bonnet** (5 referencias: AE86, Abarth 500, Celica, R8, S13): x = 0;
  z = borde trasero del capó − 0,222 m; y = superficie en esa z + 0,055 m.
* **Bumper** (4 Kunos: AE86, Abarth 500, Celica, R8): x = 0; y de cuerpo = 0,575;
  z del KN5 = frontal − 0,185 m. Debe quedar ≥ 0,10 m sobre la carrocería en todo el
  rango de z de las referencias (Bumper dibuja el coche, como Bonnet).
  Si la carrocería lo incumple (coche alto, capó alto), y sube lo justo
  hasta cumplirlo.

Perfil superior: para una vertical (x, z), la y más alta de los triángulos de la
carrocería (sin ruedas, suspensión, piloto ni volante) que la cortan.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.ac.ini_format import sanitize_ini_comments
from src.ac.kn5_inspect import read_kn5

Vec3 = tuple[float, float, float]
Tri = tuple[Vec3, Vec3, Vec3]
SKIP = ("WHEEL", "SUSP", "TYRE", "RIM", "DISC", "DRIVER", "HUB", "CALIPER", "STEER")

DASH_EXP = 26  # ks_toyota_ae86 data/dash_cam.ini
# Bonnet: medianas de las 5 referencias (cameras_measurement.json, regla)
BONNET_BEHIND_HOOD_EDGE_M = 0.222  # [0.237, 0.17, 0.138, 0.306, 0.222]
BONNET_ABOVE_SURFACE_M = 0.055  # [0.05, 0.055, 0.131, 0.12, 0.018]
# Bumper: medianas de los 4 Kunos (bumper_measurement.json, regla)
BUMPER_Y_BODY = 0.575  # [0.55, 0.6, 0.55, 0.6]
BUMPER_Z_MINUS_FRONT_M = -0.185  # [-0.217, -0.644, 0.261, -0.154]
BUMPER_Z_RANGE_M = (-0.644, 0.261)  # rango de las referencias, para la holgura
BUMPER_MIN_CLEARANCE_M = 0.10


class CameraError(ValueError):
    """No se puede aplicar la regla de cámara a este KN5 o car.ini."""


# ------------------------------------------------------------------ geometría
def _ancestry(k, n) -> list[str]:
    out = [n.name.upper()]
    while n.parent >= 0:
        n = k.nodes[n.parent]
        out.append(n.name.upper())
    return out


def body_triangles(kn5_path: Path, x0: float) -> tuple[float, list[Tri]]:
    """(z más adelantada con |x| < 0,3, triángulos cuyo rango x contiene x0)."""
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


def profile(
    tris: list[Tri], x0: float, z_from: float, z_to: float, step: float = 0.02
) -> list[tuple[float, float]]:
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
    """z + 0,04 donde el salto y(z+0,04) − y(z) es máximo, entre DRIVEREYES z + 0,5 y
    + 1,6 m (escalón entre la base del parabrisas y el capó)."""
    best, zbest = -9.0, None
    z = z_eyes + 0.5
    while z <= z_eyes + 1.6:
        a, b = y_at(p, z), y_at(p, z + 0.04)
        if a is not None and b is not None and b - a > best:
            best, zbest = b - a, z + 0.04
        z += 0.02
    return None if zbest is None else round(zbest, 3)


# ------------------------------------------------------------------ reglas
@dataclass(frozen=True)
class Cameras:
    dash_pos: Vec3
    dash_exp: int
    bonnet_body: Vec3
    bonnet_kn5: Vec3
    bumper_body: Vec3
    bumper_kn5: Vec3
    front_z: float
    hood_rear_edge_z: float
    bonnet_surface_y: float
    bumper_min_clearance: float


def compute_cameras(
    front: float,
    tris: list[Tri],
    eyes: Vec3,
    graphics_offset: Vec3,
    graphics_pitch_rotation: float = 0.0,
) -> Cameras:
    """Aplica las tres reglas. ``eyes`` = DRIVEREYES tal como se escribe (3 decimales);
    ``front``/``tris`` = :func:`body_triangles` en x = 0."""
    if abs(graphics_pitch_rotation) > 1e-9:
        raise CameraError(
            "GRAPHICS_PITCH_ROTATION ≠ 0: la conversión KN5 = cuerpo − GRAPHICS_OFFSET "
            "solo está comprobada sin giro"
        )
    go = graphics_offset
    front_z = round(front, 4)
    p = profile(tris, 0.0, -1.0, front)
    edge = hood_rear_edge(p, eyes[2])
    if edge is None:
        raise CameraError("no se encuentra el borde trasero del capó en el perfil")
    z = edge - BONNET_BEHIND_HOOD_EDGE_M
    surface = y_at(p, z)
    if surface is None:
        raise CameraError(f"sin superficie del capó en z = {z:.3f}")
    bonnet_kn5 = (0.0, round(surface + BONNET_ABOVE_SURFACE_M, 3), round(z, 3))
    if not (bonnet_kn5[1] > surface and z < front_z):
        raise CameraError("la cámara Bonnet quedaría dentro de la carrocería")
    bonnet_body = tuple(round(bonnet_kn5[i] + go[i], 3) for i in range(3))

    z_kn5 = front_z + BUMPER_Z_MINUS_FRONT_M
    # Coches altos o con capó alto: la y de los Kunos (bajos) puede quedar
    # dentro de la carrocería.
    # La cámara sube lo justo para respetar la holgura mínima en todo el rango.
    tops = []
    zz = front_z + BUMPER_Z_RANGE_M[0]
    while zz <= front_z + BUMPER_Z_RANGE_M[1] + 1e-9:
        sy = top_y(tris, 0.0, zz)
        if sy is not None:
            tops.append(sy)
        zz += 0.02
    if not tops:
        raise CameraError("la cámara Bumper no encuentra carrocería en su rango de z")
    # shortcut: cabina adelantada, la cámara subiría sobre el parabrisas
    y_kn5 = max(BUMPER_Y_BODY - go[1], max(tops) + BUMPER_MIN_CLEARANCE_M + 0.001)
    bumper_body = (0.0, round(y_kn5 + go[1], 3), round(z_kn5 + go[2], 3))
    bumper_kn5 = (0.0, round(y_kn5, 3), round(z_kn5, 3))
    clear = [round(y_kn5 - sy, 3) for sy in tops]
    if min(clear) < BUMPER_MIN_CLEARANCE_M:
        raise CameraError(
            f"la cámara Bumper queda a {min(clear)} m de la "
            f"carrocería (mínimo {BUMPER_MIN_CLEARANCE_M})"
        )
    return Cameras(
        tuple(eyes),  # type: ignore[arg-type]
        DASH_EXP,
        bonnet_body,  # type: ignore[arg-type]
        bonnet_kn5,
        bumper_body,
        bumper_kn5,
        front_z,
        edge,
        round(surface, 3),
        min(clear),
    )


def cameras_from_kn5(
    kn5_path: Path, eyes: Vec3, graphics_offset: Vec3, pitch: float = 0.0
) -> Cameras:
    front, tris = body_triangles(kn5_path, 0.0)
    return compute_cameras(front, tris, eyes, graphics_offset, pitch)


# ------------------------------------------------------------------ texto
def fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v != 0 else "0"


def _vec(v) -> str:
    return ", ".join(fmt(c) for c in v)


def bonnet_line(c: Cameras) -> str:
    return sanitize_ini_comments(
        f"BONNET_CAMERA_POS={_vec(c.bonnet_body)}\t\t; DIAG test17cam: "
        "marco del cuerpo "
        f"(AC); KN5 {_vec(c.bonnet_kn5)} + GRAPHICS_OFFSET; "
        f"regla medida en 5 referencias: {BONNET_BEHIND_HOOD_EDGE_M} m por detras del "
        f"borde trasero del capo y {BONNET_ABOVE_SURFACE_M} m sobre la superficie "
        "(camera_work_2026-10-25)"
    )


def bumper_line(c: Cameras) -> str:
    raised = (
        f" (y cuerpo final {c.bumper_body[1]}: subida por la carroceria o editada)"
        if c.bumper_body[1] != BUMPER_Y_BODY
        else ""
    )
    return sanitize_ini_comments(
        f"BUMPER_CAMERA_POS={_vec(c.bumper_body)}		; DIAG test18bu: marco del cuerpo "
        f"(AC, como BONNET); KN5 {_vec(c.bumper_kn5)}; convencion "
        f"Kunos (ae86, abarth500, celica, r8): y cuerpo mediana {BUMPER_Y_BODY}{raised}, z = "
        f"frontal {BUMPER_Z_MINUS_FRONT_M} m (camera_work_2026-10-25)"
    )


def render_dash_cam_ini(c: Cameras) -> bytes:
    """``data/dash_cam.ini`` en CRLF, como el validado en ``pkg_v17cam``."""
    text = sanitize_ini_comments(
        "; DIAG test17cam: POS = DRIVEREYES (marco del KN5, AC; "
        "misma convencion que sample_assetto); EXP de ks_toyota_ae86\r\n"
        f"[DASH_CAM]\r\nPOS={_vec(c.dash_pos)}\r\nEXP={c.dash_exp}\r\n"
    )
    return text.encode("utf-8")


def median(values: list[float]) -> float:
    """Ayuda para comprobar las constantes contra las mediciones guardadas."""
    return statistics.median(values)
