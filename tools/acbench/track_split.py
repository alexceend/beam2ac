"""G2.27 (B0): circuito ``beam2ac_g227_split`` para saturar un diferencial 2WD en recta.

    python -m tools.acbench.track_split --out output/<nueva>/content/tracks

La explanada de G2.25 (:mod:`tools.acbench.track_g225`) sin muros, con una franja de bajo agarre
(superficie ``LOWMU``, malla ``1LOWMU``) a un lado de la recta ``LANE_X``: x en ``STRIP_X``,
z en ``STRIP_Z``. Un coche centrado en la recta lleva las ruedas de un lado sobre la franja y las del
otro sobre ``ROAD``. La franja sustituye a las celdas del suelo (misma cota, sin mallas superpuestas).
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.acbench import track_g225 as G  # noqa: E402
from tools.acbench.track import _dummy, _matrix, _property, _s, checker_png  # noqa: E402

TRACK_ID = "beam2ac_g227_split"
LOW_FRICTION = 0.1
STRIP_X = (G.LANE_X, G.LANE_X + G.CELL)
STRIP_Z = (-400.0, 400.0)

_LOWMU = f"""[SURFACE_2]
KEY=LOWMU
FRICTION={LOW_FRICTION:g}
DAMPING=0
WAV=
WAV_PITCH=0
FF_EFFECT=NULL
DIRT_ADDITIVE=0
IS_VALID_TRACK=1
BLACK_FLAG_TIME=0
SIN_HEIGHT=0
SIN_LENGTH=0
IS_PITLANE=0
VIBRATION_GAIN=0
VIBRATION_LENGTH=0

[_SCRIPTING_PHYSICS]"""
SURFACES_INI = G.SURFACES_INI.replace("[_SCRIPTING_PHYSICS]", _LOWMU, 1)


def in_strip(x: float, z: float) -> bool:
    """Celda cuyo vértice de menor (x, z) es este."""
    return STRIP_X[0] <= x < STRIP_X[1] and STRIP_Z[0] <= z < STRIP_Z[1]


def ground_meshes() -> tuple[bytes, bytes]:
    n = int(G.SIZE / G.CELL)
    half = G.SIZE / 2
    verts = []
    for j in range(n + 1):
        for i in range(n + 1):
            x, z = -half + i * G.CELL, -half + j * G.CELL
            verts.append((x, 0.0, z, 0.0, 1.0, 0.0, x / G.TILE / 2, z / G.TILE / 2))
    road: list[int] = []
    low_v: list[tuple[float, ...]] = []
    low: list[int] = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b, c, d = a + 1, a + n + 1, a + n + 2
            if in_strip(verts[a][0], verts[a][2]):
                k = len(low_v)
                low_v += [verts[a], verts[b], verts[c], verts[d]]
                low += [k, k + 2, k + 1, k + 1, k + 2, k + 3]  # orden del suelo (normal +Y)
            else:
                road += [a, c, b, b, c, d]
    assert low, "franja vacía"
    radius = G.SIZE * 0.75
    return G._mesh("1ROAD", verts, road, radius), G._mesh("1LOWMU", low_v, low, radius)


def build_kn5() -> bytes:
    tex = checker_png()
    name = "beam2ac_checker.png"
    body = b"sc6969" + struct.pack("<I", 5)
    body += struct.pack("<i", 1) + struct.pack("<i", 1) + _s(name) + struct.pack("<i", len(tex)) + tex
    body += struct.pack("<i", 1)
    body += _s("ground") + _s("ksPerPixel") + b"\x00\x00" + struct.pack("<i", 0)
    props = [_property("ksAmbient", 0.45), _property("ksDiffuse", 0.45), _property("ksSpecular", 0.0),
             _property("ksSpecularEXP", 1.0), _property("ksEmissive", 0.0, (0, 0, 0)),
             _property("ksAlphaRef", 0.0)]
    body += struct.pack("<i", len(props)) + b"".join(props)
    body += struct.pack("<i", 1) + _s("txDiffuse") + struct.pack("<i", 0) + _s(name)
    body += _dummy(TRACK_ID, _matrix(0, 0, 0), 1 + len(G.SPAWNS))
    road, low = ground_meshes()
    body += _dummy("GROUND", _matrix(0, 0, 0), 2) + road + low
    for spawn, (x, z, yaw) in G.SPAWNS.items():
        body += _dummy(spawn, _matrix(x, 0.05, z, yaw))
    return body


def build(dest_tracks: Path) -> Path:
    return G.build(dest_tracks, TRACK_ID, build_kn5(), SURFACES_INI)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    print(build(ap.parse_args(argv).out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
