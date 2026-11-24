"""G2.25: circuito de validación ``beam2ac_g225_pad`` (nuevo; mismo formato que el de G2.22).

    python -m tools.acbench.track_g225 --out output/<nueva>/content/tracks

Diferencias con ``beam2ac_g222_pad`` (:mod:`tools.acbench.track`, que no cambia):

* explanada de ``SIZE`` = 1200 m (recta de aceleración y frenada de 1,1 km en x = ``LANE_X``);
* dos muros (superficie ``WALL``, mallas ``1WALL`` y ``2WALL``) para medir el collider del coche con una
  colisión lenta y conocida:

  - ``WALL_A``: cara en z = ``WALL_A_Z`` mirando a −Z, x ∈ [250, 350] (choque frontal yendo
    hacia +Z; trasero, en marcha atrás, saliendo de cara a −Z);
  - ``WALL_B``: cara en x = ``WALL_B_X`` mirando a −X, z ∈ [0, 300] (contacto lateral izquierdo
    yendo hacia +Z: en AC +X es la izquierda).

  Alto 1,5 m y 1 m de grueso; caja cerrada con normales hacia fuera.

Misma física de circuito que G2.22: ``ROAD`` con ``FRICTION=1``, ``WAV_PITCH=extended-0``,
``[_SCRIPTING_PHYSICS]`` y ``ALLOW_CUSTOM_AI_MANIPULATION``.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402
from tools.acbench.track import (  # noqa: E402
    SURFACES_INI as _G222_SURFACES,
    _dummy,
    _matrix,
    _png,
    _property,
    _s,
    checker_png,
)

TRACK_ID = "beam2ac_g225_pad"
SIZE = 1200.0  # m
CELL = 10.0
TILE = 4.0
LANE_X = -300.0  # recta de aceleración/frenada (hacia +Z desde z = −550)
LANE_Z0 = -550.0
WALL_A_Z = 400.0  # cara del muro A (mira a −Z)
WALL_A_X = (250.0, 350.0)
WALL_B_X = 450.0  # cara del muro B (mira a −X)
WALL_B_Z = (0.0, 300.0)
WALL_H = 1.5
WALL_T = 1.0

SURFACES_INI = _G222_SURFACES.replace("[_SCRIPTING_PHYSICS]", """[SURFACE_1]
KEY=WALL
FRICTION=0.2
DAMPING=0
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0
IS_VALID_TRACK=0
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0
VIBRATION_LENGTH=0

[_SCRIPTING_PHYSICS]""", 1)

WALLS = {  # nombre → (min, max) de la caja en ejes AC
    "WALL_A": ((WALL_A_X[0], 0.0, WALL_A_Z), (WALL_A_X[1], WALL_H, WALL_A_Z + WALL_T)),
    "WALL_B": ((WALL_B_X, 0.0, WALL_B_Z[0]), (WALL_B_X + WALL_T, WALL_H, WALL_B_Z[1])),
}

SPAWNS = {
    "AC_PIT_0": (0.0, 0.0, 0.0),
    "AC_START_0": (0.0, 0.0, 0.0),
    "AC_HOTLAP_START_0": (0.0, 0.0, 0.0),
    "AC_TIME_0_L": (-20.0, 300.0, 0.0),
    "AC_TIME_0_R": (20.0, 300.0, 0.0),
}


def _mesh(name: str, verts: list[tuple[float, ...]], idx: list[int], radius: float) -> bytes:
    out = struct.pack("<i", 2) + _s(name) + struct.pack("<i", 0) + b"\x01" + bytes([0, 1, 0])
    out += struct.pack("<i", len(verts))
    for x, y, z, nx, ny, nz, u, v in verts:
        out += struct.pack("<3f3f2f3f", x, y, z, nx, ny, nz, u, v, 1.0, 0.0, 0.0)
    out += struct.pack("<i", len(idx)) + struct.pack(f"<{len(idx)}H", *idx)
    out += struct.pack("<i", 0)  # material 0
    out += struct.pack("<I6f?", 0, 0.0, 0.0, 0.0, 0.0, 0.0, radius, True)
    return out


def ground_mesh() -> bytes:
    n = int(SIZE / CELL)
    half = SIZE / 2
    verts = []
    for j in range(n + 1):
        for i in range(n + 1):
            x, z = -half + i * CELL, -half + j * CELL
            verts.append((x, 0.0, z, 0.0, 1.0, 0.0, x / TILE / 2, z / TILE / 2))
    if len(verts) > 65535:
        raise ValueError("demasiados vértices para índices de 16 bits")
    idx = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            idx += [a, c, b, b, c, d]  # normal +Y (mismo orden que G2.22)
    return _mesh("1ROAD", verts, idx, SIZE * 0.75)


def box_faces(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> tuple[list, list]:
    """Caja cerrada (24 vértices, 12 triángulos) con normales hacia fuera; el orden de los
    índices es el del suelo de G2.22 (``a, c, b``) visto desde fuera de cada cara."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = [  # (normal, cuatro esquinas en sentido antihorario visto desde fuera)
        ((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1, 0, 0), [(x0, y0, z1), (x0, y1, z1), (x0, y1, z0), (x0, y0, z0)]),
        ((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
        ((0, -1, 0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
        ((0, 0, 1), [(x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1)]),
        ((0, 0, -1), [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]),
    ]
    verts, idx = [], []
    for n, quad in faces:
        base = len(verts)
        for k, p in enumerate(quad):
            verts.append((*p, *n, (k in (2, 3)) * 1.0, (k in (1, 2)) * 1.0))
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return verts, idx


def check_winding(verts: list, idx: list) -> bool:
    """Cada triángulo ``(p0, p1, p2)`` con ``(p1 − p0) × (p2 − p0)`` en el sentido de la normal del
    vértice: es lo que cumple el suelo de G2.22 (orden ``a, c, b`` ⇒ +Y), que AC dibuja bien."""
    for t in range(0, len(idx), 3):
        a, b, c = (verts[i] for i in idx[t:t + 3])
        u = [b[k] - a[k] for k in range(3)]
        v = [c[k] - a[k] for k in range(3)]
        cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        if sum(cross[k] * a[3 + k] for k in range(3)) <= 0:
            return False
    return True


# Orden de los triángulos de los muros (G2.25):
# * banco v1 ("exterior", mismo criterio que el suelo): atlanta r01 atravesó ~0,8 m sin collidedWith;
# * banco v2 ("doble"): atlanta r11 y el Kunos ks_toyota_celica_st185 también atraviesan la cara
#   cercana; el contacto llega con la rueda o con la cara lejana;
# * banco v4 ("inverso"): solo el orden contrario.
WALL_WINDING = "inverso"


def wall_indices(idx: list[int]) -> list[int]:
    rev = [k for tri in range(0, len(idx), 3) for k in (idx[tri], idx[tri + 2], idx[tri + 1])]
    return {"exterior": idx, "doble": idx + rev, "inverso": rev}[WALL_WINDING]


def build_kn5() -> bytes:
    tex = checker_png()
    body = b"sc6969" + struct.pack("<I", 5)
    body += struct.pack("<i", 1) + struct.pack("<i", 1) + _s("beam2ac_checker.png") + struct.pack("<i", len(tex)) + tex
    body += struct.pack("<i", 1)
    body += _s("ground") + _s("ksPerPixel") + b"\x00\x00" + struct.pack("<i", 0)
    props = [_property("ksAmbient", 0.45), _property("ksDiffuse", 0.45), _property("ksSpecular", 0.0),
             _property("ksSpecularEXP", 1.0), _property("ksEmissive", 0.0, (0, 0, 0)), _property("ksAlphaRef", 0.0)]
    body += struct.pack("<i", len(props)) + b"".join(props)
    body += struct.pack("<i", 1) + _s("txDiffuse") + struct.pack("<i", 0) + _s("beam2ac_checker.png")
    body += _dummy(TRACK_ID, _matrix(0, 0, 0), 1 + len(WALLS) + len(SPAWNS))
    body += _dummy("GROUND", _matrix(0, 0, 0), 1) + ground_mesh()
    for k, (name, (lo, hi)) in enumerate(WALLS.items(), start=1):
        verts, idx = box_faces(lo, hi)
        if not check_winding(verts, idx):
            raise ValueError(f"orden de caras incorrecto en {name}")
        idx = wall_indices(idx)
        radius = max(hi[k] - lo[k] for k in range(3))
        body += _dummy(name, _matrix(0, 0, 0), 1) + _mesh(f"{k}WALL", verts, idx, radius)  # 1WALL, 2WALL (convención de Kunos)
    for name, (x, z, yaw) in SPAWNS.items():
        body += _dummy(name, _matrix(x, 0.05, z, yaw))
    return body


def build(dest_tracks: Path, track_id: str = TRACK_ID, kn5: bytes | None = None,
          surfaces: str = SURFACES_INI) -> Path:
    """``track_id``/``kn5``/``surfaces``: otro circuito con los mismos archivos auxiliares (G2.27)."""
    root = ensure_safe_output_path(dest_tracks / track_id)
    new_output_dir(root)
    (root / "data").mkdir()
    (root / "ai").mkdir()
    (root / "ui").mkdir()
    (root / f"{track_id}.kn5").write_bytes(kn5 or build_kn5())
    (root / "data" / "surfaces.ini").write_text(surfaces, "ascii")
    (root / "data" / "cameras.ini").write_text(
        "[HEADER]\nVERSION=3\nCAMERA_COUNT=1\nSET_NAME=TV 1\n\n[CAMERA_0]\nNAME=1\nPOSITION=0,40,-60\n"
        "FORWARD=0,-0.55,0.835\nUP=0,0.835,0.55\nMIN_FOV=30\nMAX_FOV=60\nIN_POINT=-1\nOUT_POINT=-1\n"
        "SHADOW_SPLIT0=120\nSHADOW_SPLIT1=250\nSHADOW_SPLIT2=600\nNEAR_PLANE=1\nFAR_PLANE=5000\n"
        "MIN_EXPOSURE=0.35\nMAX_EXPOSURE=0.55\nDOF_FACTOR=0\nDOF_RANGE=10000\nDOF_FOCUS=0\nDOF_MANUAL=0\n"
        "SPLINE=\nSPLINE_ROTATION=0\nFOV_GAMMA=1\nSPLINE_ANIMATION_LENGTH=15\nIS_FIXED=0\n", "ascii")
    (root / "data" / "map.ini").write_text(
        f"[PARAMETERS]\nWIDTH={SIZE:g}\nHEIGHT={SIZE:g}\nMARGIN=20\nSCALE_FACTOR=1\nMAX_SIZE=1600\n"
        f"X_OFFSET={SIZE / 2:g}\nZ_OFFSET={SIZE / 2:g}\nDRAWING_SIZE=10\n", "ascii")
    (root / "data" / "lighting.ini").write_text("[LIGHTING]\nSUN_PITCH_ANGLE=45\nSUN_HEADING_ANGLE=45\n", "ascii")
    (root / "data" / "groove.ini").write_text("[HEADER]\nGROOVES_NUMBER=0\n", "ascii")
    (root / "data" / "crew.ini").write_text("[HEADER]\nSIDE=1\n", "ascii")
    (root / "ai" / "fast_lane.ai").write_bytes(struct.pack("<3i", 3, 0, 0))
    (root / "map.png").write_bytes(_png(64, 64, lambda x, y: (40, 40, 40)))
    (root / "ui" / "outline.png").write_bytes(_png(64, 64, lambda x, y: (0, 0, 0)))
    (root / "ui" / "preview.png").write_bytes(checker_png())
    (root / "ui" / "ui_track.json").write_text(json.dumps({
        "name": "beam2ac G2.25 pad", "description": "Explanada plana de validación con dos muros (generada).",
        "tags": ["test track"], "country": "", "city": "", "length": "0", "width": f"{SIZE:g}",
        "pitboxes": "1", "run": "OPEN"}, indent=1), "utf-8")
    return root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="carpeta content/tracks de destino (bajo output/)")
    a = ap.parse_args(argv)
    print(build(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
