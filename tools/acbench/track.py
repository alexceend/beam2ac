"""G2.22: generador del circuito de pruebas ``beam2ac_g222_pad`` (nuevo, no copia de ninguno).

    python -m tools.acbench.track --out output/<nueva>/content/tracks

Explanada plana de ``SIZE`` × ``SIZE`` m a y = 0, una sola superficie (``1ROAD`` → ``ROAD``,
``FRICTION=1`` como ``TARMAC-DRIFT`` del circuito ``drift`` donde se hizo G2.21), sin muros ni
objetos. Textura de damero de 4 m para ver el movimiento. Puntos de salida en el centro.

``data/surfaces.ini``:

* ``WAV_PITCH=extended-0`` en ``[SURFACE_0]`` activa la física extendida del circuito (SDK de
  CSP, ``ac_apps/lib.lua`` «To activate extended physics»), requisito de ``physics.*``;
* ``[_SCRIPTING_PHYSICS]`` con las cuatro claves documentadas y ``ALLOW_APPS`` (no
  documentada; si CSP la ignora, F0 lo dirá con ``ACT_PROBE``);
* ``[_EXTRA_PERMISSIONS] ALLOW_CUSTOM_AI_MANIPULATION=1`` (documentación de Custom AI).

Formato KN5 v5 como :mod:`src.ac.kn5_write` (mismo lector :mod:`src.ac.kn5_inspect`), con
nodos de transformación para los puntos ``AC_*`` (matriz de vector fila, traslación en la
fila 3) y una textura PNG incrustada.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

TRACK_ID = "beam2ac_g222_pad"
SIZE = 600.0  # m
CELL = 10.0  # m por celda de la malla
TILE = 4.0  # m por casilla del damero
FRICTION = 1.0

SURFACES_INI = f"""[SURFACE_0]
KEY=ROAD
FRICTION={FRICTION:g}
DAMPING=0
WAV=
WAV_PITCH=extended-0
FF_EFFECT=NULL
DIRT_ADDITIVE=0
IS_VALID_TRACK=1
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0
VIBRATION_LENGTH=0

[_SCRIPTING_PHYSICS]
ALLOW_TRACK_SCRIPTS=1
ALLOW_DISPLAY_SCRIPTS=1
ALLOW_NEW_MODE_SCRIPTS=1
ALLOW_TOOLS=1
ALLOW_APPS=1

[_EXTRA_PERMISSIONS]
ALLOW_CUSTOM_AI_MANIPULATION=1
"""


def _png(width: int, height: int, pixel) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def checker_png() -> bytes:
    return _png(64, 64, lambda x, y: (96, 96, 96) if ((x // 32) + (y // 32)) % 2 else (128, 128, 128))


def _s(text: str) -> bytes:
    d = text.encode("utf-8")
    return struct.pack("<i", len(d)) + d


def _matrix(tx: float, ty: float, tz: float, yaw_deg: float = 0.0) -> tuple[float, ...]:
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return (c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, tx, ty, tz, 1)


def _dummy(name: str, m: tuple[float, ...], children: int = 0) -> bytes:
    return struct.pack("<i", 1) + _s(name) + struct.pack("<i", children) + b"\x01" + struct.pack("<16f", *m)


def _property(name: str, a: float = 0.0, v3: tuple[float, float, float] = (0, 0, 0)) -> bytes:
    return _s(name) + struct.pack("<f2f3f4f", a, 0, 0, *v3, 0, 0, 0, 0)


def ground_mesh() -> bytes:
    n = int(SIZE / CELL)
    half = SIZE / 2
    verts = []
    for j in range(n + 1):
        for i in range(n + 1):
            x, z = -half + i * CELL, -half + j * CELL
            verts.append((x, 0.0, z, 0.0, 1.0, 0.0, x / TILE / 2, z / TILE / 2))
    idx = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            idx += [a, c, b, b, c, d]  # normal +Y (sentido horario visto desde arriba en AC)
    out = struct.pack("<i", 2) + _s("1ROAD") + struct.pack("<i", 0) + b"\x01" + bytes([0, 1, 0])
    out += struct.pack("<i", len(verts))
    for x, y, z, nx, ny, nz, u, v in verts:
        out += struct.pack("<3f3f2f3f", x, y, z, nx, ny, nz, u, v, 1.0, 0.0, 0.0)
    out += struct.pack("<i", len(idx)) + struct.pack(f"<{len(idx)}H", *idx)
    out += struct.pack("<i", 0)  # material 0
    out += struct.pack("<I6f?", 0, 0.0, 0.0, 0.0, 0.0, 0.0, SIZE * 0.75, True)
    return out


SPAWNS = {  # nombre → (x, z, rumbo°); salida en el centro, mirando a +Z
    "AC_PIT_0": (0.0, 0.0, 0.0),
    "AC_START_0": (0.0, 0.0, 0.0),
    "AC_HOTLAP_START_0": (0.0, 0.0, 0.0),
    "AC_TIME_0_L": (-20.0, 150.0, 0.0),
    "AC_TIME_0_R": (20.0, 150.0, 0.0),
}


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
    body += _dummy(TRACK_ID, _matrix(0, 0, 0), 1 + len(SPAWNS))
    body += _dummy("GROUND", _matrix(0, 0, 0), 1) + ground_mesh()
    for name, (x, z, yaw) in SPAWNS.items():
        body += _dummy(name, _matrix(x, 0.05, z, yaw))
    return body


def build(dest_tracks: Path) -> Path:
    root = ensure_safe_output_path(dest_tracks / TRACK_ID)
    new_output_dir(root)
    (root / "data").mkdir()
    (root / "ai").mkdir()
    (root / "ui").mkdir()
    (root / f"{TRACK_ID}.kn5").write_bytes(build_kn5())
    (root / "data" / "surfaces.ini").write_text(SURFACES_INI, "ascii")
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
    (root / "ai" / "fast_lane.ai").write_bytes(struct.pack("<3i", 3, 0, 0))  # vacío, como drift
    (root / "map.png").write_bytes(_png(64, 64, lambda x, y: (40, 40, 40)))
    (root / "ui" / "outline.png").write_bytes(_png(64, 64, lambda x, y: (0, 0, 0)))
    (root / "ui" / "preview.png").write_bytes(checker_png())
    (root / "ui" / "ui_track.json").write_text(json.dumps({
        "name": "beam2ac G2.22 pad", "description": "Explanada plana de pruebas del banco LSD (generada).",
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
