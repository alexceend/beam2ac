"""``driver_base_pos.knh``: pose del piloto en el asiento y manos ante el aro.

Reproduce las variantes validadas test8dp (pose correcta en el juego) y v12 (manos
fuera del aro, pendiente de confirmar en el juego):

1. **Asiento (test8dp).** Parte de la pose de un coche de Kunos de la instalación
   local del usuario (``content/cars/<coche>/driver_base_pos.knh``, solo lectura) y
   traslada ``DRIVER:RIG_Center`` para que ``DRIVER:RIG_Hips`` quede en
   ``hips_target`` (marco del KN5). Sus antecesores deben ser identidad, así que el
   desplazamiento en mundo es el desplazamiento local.
2. **Manos (v12).** IK de dos huesos por lado: la muñeca se desplaza a lo largo de
   −eje del aro hasta que la media de los nudillos (``HAND_*1`` / ``*4``) queda a
   ``knuckle_target_m`` del plano del aro. El hombro queda fijo, el codo se recoloca
   en el plano original del brazo, las longitudes de hueso se conservan y la mano
   mantiene su orientación. Solo cambian las matrices de ``RIG_Arm``,
   ``RIG_ForeArm`` y ``RIG_HAND`` de cada lado.

Formato .knh (comprobado con 466 archivos): nodo = int32 longitud + nombre, 16 float
(matriz local de vector fila, p_padre = p_local · M), int32 nº hijos; recursivo.
Mundo = local · mundo_padre.

**No distribuible:** el resultado deriva de un archivo de Kunos. Se genera solo en
local, con la instalación del usuario, y el informe lo marca así.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any

from src.safe_paths import ensure_safe_output_path

Mat = list[list[float]]
FINGERS = {
    "L": [
        "DRIVER:HAND_Index1",
        "DRIVER:HAND_Middle1",
        "DRIVER:HAND_Ring1",
        "DRIVER:HAND_Pinkie1",
    ],
    "R": [
        "DRIVER:HAND_Index4",
        "DRIVER:HAND_Middle4",
        "DRIVER:HAND_Ring4",
        "DRIVER:HAND_Pinkie4",
    ],
}
ARM_BONES = ("Arm", "ForeArm", "HAND")


class DriverPoseError(RuntimeError):
    """La pose no se puede generar con seguridad."""


# ------------------------------------------------------------------ lectura
class Knh:
    def __init__(self, raw: bytes):
        self.raw = bytearray(raw)
        self.nodes: list[str] = []
        self.parent: dict[str, str | None] = {}
        self.off: dict[str, int] = {}
        self.local: dict[str, Mat] = {}
        if self._parse(0, None) != len(self.raw):
            raise DriverPoseError("knh: sobran o faltan bytes")

    def _parse(self, p: int, par: str | None) -> int:
        n = struct.unpack_from("<i", self.raw, p)[0]
        name = bytes(self.raw[p + 4 : p + 4 + n]).decode("latin1")
        if name in self.parent:
            raise DriverPoseError(f"knh: nombre repetido {name}")
        o = p + 4 + n
        m = struct.unpack_from("<16f", self.raw, o)
        kids = struct.unpack_from("<i", self.raw, o + 64)[0]
        self.nodes.append(name)
        self.parent[name] = par
        self.off[name] = o
        self.local[name] = [list(m[i * 4 : i * 4 + 4]) for i in range(4)]
        p = o + 68
        for _ in range(kids):
            p = self._parse(p, name)
        return p

    def worlds(self, local: dict[str, Mat] | None = None) -> dict[str, Mat]:
        loc = self.local if local is None else local
        w: dict[str, Mat] = {}
        for n in self.nodes:
            par = self.parent[n]
            w[n] = loc[n] if par is None else _mm(loc[n], w[par])
        return w


def _mm(a, b) -> Mat:
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)
    ]


def _identity_worlds(raw: bytes) -> dict[str, Mat]:
    """Mundo de cada nodo multiplicando desde la identidad (como el lector de v8)."""
    out: dict[str, Mat] = {}
    ident = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

    def node(p, parent_world):
        n = struct.unpack_from("<i", raw, p)[0]
        name = raw[p + 4 : p + 4 + n].decode("latin1")
        p += 4 + n
        m = struct.unpack_from("<16f", raw, p)
        p += 64
        kids = struct.unpack_from("<i", raw, p)[0]
        p += 4
        rows = [m[i * 4 : i * 4 + 4] for i in range(4)]
        w = [
            [sum(rows[i][k] * parent_world[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)
        ]
        out[name] = w
        for _ in range(kids):
            p = node(p, w)
        return p

    if node(0, ident) != len(raw):
        raise DriverPoseError("knh: sobran o faltan bytes")
    return out


def _is_identity(m) -> bool:
    flat = [m[i][j] for i in range(4) for j in range(4)]
    return all(
        abs(flat[i] - (1.0 if i in (0, 5, 10, 15) else 0.0)) < 1e-6 for i in range(16)
    )


# ------------------------------------------------------------------ 1. asiento
def place_hips(
    raw: bytes, hips_target: tuple[float, float, float]
) -> tuple[bytes, dict]:
    knh = Knh(raw)
    if "DRIVER:RIG_Center" not in knh.parent or "DRIVER:RIG_Hips" not in knh.parent:
        raise DriverPoseError("knh sin DRIVER:RIG_Center o DRIVER:RIG_Hips")
    chain, p = [], knh.parent["DRIVER:RIG_Center"]
    while p is not None:
        chain.append(p)
        p = knh.parent[p]
    if not all(_is_identity(knh.local[a]) for a in chain):
        raise DriverPoseError("antecesores de RIG_Center no identidad")
    hips = _identity_worlds(bytes(raw))["DRIVER:RIG_Hips"][3][:3]
    delta = tuple(t - h for t, h in zip(hips_target, hips))
    off = knh.off["DRIVER:RIG_Center"]
    m = struct.unpack_from("<16f", raw, off)
    new_t = (m[12] + delta[0], m[13] + delta[1], m[14] + delta[2])
    out = bytearray(raw)
    struct.pack_into("<3f", out, off + 48, *new_t)
    after = _identity_worlds(bytes(out))
    return bytes(out), {
        "node_modified": "DRIVER:RIG_Center",
        "translation_before": list(m[12:15]),
        "translation_after": list(new_t),
        "delta": list(delta),
        "hips_before": list(hips),
        "hips_after": after["DRIVER:RIG_Hips"][3][:3],
    }


# ------------------------------------------------------------------ 2. manos
def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _add(a, b):
    return [x + y for x, y in zip(a, b)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _scl(a, s):
    return [x * s for x in a]


def _nrm(a):
    return math.sqrt(_dot(a, a))


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _inv(m) -> Mat:
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(m[3][k] * r[k][j] for k in range(3)) for j in range(3)]
    return [r[0] + [0.0], r[1] + [0.0], r[2] + [0.0], t + [1.0]]


def _rot_between(a, b):
    """Rotación mínima (forma de vector fila) que lleva la dirección a a la b."""
    a, b = _scl(a, 1 / _nrm(a)), _scl(b, 1 / _nrm(b))
    k = _cross(a, b)
    s, c = _nrm(k), _dot(a, b)
    if s < 1e-12:
        return [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    k = _scl(k, 1 / s)
    kk = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
    r = [
        [
            (i == j)
            + s * kk[i][j]
            + (1 - c) * sum(kk[i][m] * kk[m][j] for m in range(3))
            for j in range(3)
        ]
        for i in range(3)
    ]
    return [[r[j][i] for j in range(3)] for i in range(3)]


def _rotate_about(w, q, p) -> Mat:
    """Mundo w (4×4) girado rígidamente por q (3×3, vector fila) alrededor de p."""
    r = [[sum(w[i][k] * q[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    t = _add([sum((w[3][k] - p[k]) * q[k][j] for k in range(3)) for j in range(3)], p)
    return [r[0] + [0.0], r[1] + [0.0], r[2] + [0.0], t + [1.0]]


def hands_to_rim(
    raw: bytes,
    rim_centre: tuple[float, float, float],
    rim_axis: tuple[float, float, float],
    knuckle_target_m: float,
    strict: bool = True,
) -> tuple[bytes, dict]:
    """``strict=False`` (solo la vista previa): si el objetivo no se alcanza, la mano queda lo más
    cerca posible con el brazo extendido y ``report[side]["unreachable_m"]`` dice cuánto falta.
    """
    knh = Knh(raw)
    local = {k: [row[:] for row in v] for k, v in knh.local.items()}

    def pos(w, n):
        return w[n][3][:3]

    def axial(p):
        return _dot(_sub(p, rim_centre), rim_axis)

    report: dict[str, Any] = {}
    for side, fingers in FINGERS.items():
        arm, fore, end, hand, clave = (
            f"DRIVER:RIG_{b}_{side}"
            for b in ("Arm", "ForeArm", "ForeArm_END", "HAND", "Clave")
        )
        try:
            ok_chain = (
                knh.parent[fore] == arm
                and knh.parent[end] == fore
                and knh.parent[hand] == end
                and knh.parent[arm] == clave
                and all(f in knh.parent for f in fingers)
            )
        except KeyError:
            ok_chain = False
        if not ok_chain:
            raise DriverPoseError(f"cadena de brazo {side} distinta de la esperada")
        w = knh.worlds(local)
        mcp = sum(axial(pos(w, f)) for f in fingers) / 4
        d = mcp - knuckle_target_m
        delta = _scl(rim_axis, -d)
        s, e, wr = pos(w, arm), pos(w, fore), pos(w, hand)
        t = _add(wr, delta)
        l1, l2, dd = _nrm(_sub(e, s)), _nrm(_sub(wr, e)), _nrm(_sub(t, s))
        short = 0.0
        if not abs(l1 - l2) < dd < l1 + l2:
            if strict:
                raise DriverPoseError(f"{side}: objetivo inalcanzable")
            dd2 = max(abs(l1 - l2) + 1e-4, min(l1 + l2 - 1e-4, dd))
            short = abs(dd - dd2)
            t = _add(s, _scl(_sub(t, s), dd2 / dd))
            dd = dd2
        u = _scl(_sub(t, s), 1 / dd)
        v = _sub(_sub(e, s), _scl(u, _dot(_sub(e, s), u)))
        v = _scl(v, 1 / _nrm(v))
        a = (l1 * l1 - l2 * l2 + dd * dd) / (2 * dd)
        e2 = _add(_add(s, _scl(u, a)), _scl(v, math.sqrt(l1 * l1 - a * a)))
        q1 = _rot_between(_sub(e, s), _sub(e2, s))
        warm = _rotate_about(w[arm], q1, s)
        wfore = _rotate_about(w[fore], q1, s)
        w1 = _add(
            [sum((wr[k] - s[k]) * q1[k][j] for k in range(3)) for j in range(3)], s
        )
        q2 = _rot_between(_sub(w1, e2), _sub(t, e2))
        wfore = _rotate_about(wfore, q2, e2)
        wend = _mm(local[end], wfore)
        whand = [w[hand][0][:], w[hand][1][:], w[hand][2][:], t + [1.0]]
        local[arm] = _mm(warm, _inv(w[clave]))
        local[fore] = _mm(wfore, _inv(warm))
        local[hand] = _mm(whand, _inv(wend))
        report[side] = {
            "unreachable_m": short,
            "knuckles_axial_before": mcp,
            "knuckles_axial_after": mcp - d,
            "shift_m": d,
            "delta_xyz": delta,
            "elbow_moved_m": _nrm(_sub(e2, e)),
            "elbow_angle_before_deg": math.degrees(
                math.acos(_dot(_sub(s, e), _sub(wr, e)) / (l1 * l2))
            ),
            "elbow_angle_after_deg": math.degrees(
                math.acos(_dot(_sub(s, e2), _sub(t, e2)) / (l1 * l2))
            ),
        }

    out = bytearray(raw)
    changed = [f"DRIVER:RIG_{b}_{s}" for s in FINGERS for b in ARM_BONES]
    for n in changed:
        struct.pack_into(
            "<16f",
            out,
            knh.off[n],
            *[local[n][i][j] for i in range(4) for j in range(4)],
        )
    _check_hands(bytes(raw), bytes(out), changed, report, strict)
    return bytes(out), report


def _check_hands(
    src: bytes, dst: bytes, changed: list[str], report: dict, strict: bool = True
) -> None:
    a, b = Knh(src), Knh(dst)
    wa, wb = a.worlds(), b.worlds()

    def pos(w, n):
        return w[n][3][:3]

    under = {"L": [], "R": []}
    for n in a.nodes:
        p = n
        while p is not None:
            if p in ("DRIVER:RIG_Arm_L", "DRIVER:RIG_Arm_R"):
                under[p[-1]].append(n)
                break
            p = a.parent[p]
    moved = [
        n
        for n in a.nodes
        if any(x != y for x, y in zip(sum(wa[n], []), sum(wb[n], [])))
    ]
    allowed = [range(a.off[n], a.off[n] + 64) for n in changed]
    diff = [i for i, (x, y) in enumerate(zip(src, dst)) if x != y]
    ok = {
        "nodos y orden idénticos": a.nodes == b.nodes,
        "fuera de los brazos: matrices de mundo idénticas": all(
            n in under["L"] + under["R"] for n in moved
        ),
        "solo cambian bytes de las 6 matrices": len(src) == len(dst)
        and all(any(i in r for r in allowed) for i in diff),
    }
    for side in ("L", "R"):
        arm, fore, hand = (f"DRIVER:RIG_{x}_{side}" for x in ARM_BONES)
        s0, s1 = pos(wa, arm), pos(wb, arm)
        ok[f"{side}: hombro fijo"] = _nrm(_sub(s0, s1)) < 1e-6
        ok[f"{side}: longitudes conservadas (1e-5)"] = (
            abs(_nrm(_sub(pos(wb, fore), s1)) - _nrm(_sub(pos(wa, fore), s0))) < 1e-5
            and abs(
                _nrm(_sub(pos(wb, hand), pos(wb, fore)))
                - _nrm(_sub(pos(wa, hand), pos(wa, fore)))
            )
            < 1e-5
        )
        dl = report[side]["delta_xyz"]
        fings = [n for n in under[side] if "HAND" in n]
        ok[f"{side}: mano y dedos desplazados Δ con la misma orientación"] = all(
            _nrm(_sub(_sub(pos(wb, n), pos(wa, n)), dl)) < 1e-5
            and all(
                abs(wb[n][i][j] - wa[n][i][j]) < 1e-5
                for i in range(3)
                for j in range(3)
            )
            for n in fings
        )
        ok[f"{side}: traslaciones locales sin cambios"] = all(
            _nrm(_sub(b.local[n][3][:3], a.local[n][3][:3])) < 1e-5
            for n in (arm, fore, hand)
        )
    report["checks"] = ok
    if strict and not all(ok.values()):
        raise DriverPoseError(
            "comprobación fallida: " + ", ".join(k for k, v in ok.items() if not v)
        )


# ------------------------------------------------------------------ conjunto
def build_driver_pose(
    source_knh: Path,
    dst: Path,
    hips_target: tuple[float, float, float],
    rim_centre: tuple[float, float, float],
    rim_axis: tuple[float, float, float],
    knuckle_target_m: float,
    strict: bool = True,
) -> dict[str, Any]:
    raw = Path(source_knh).read_bytes()
    seated, seat = place_hips(raw, hips_target)
    final, hands = hands_to_rim(
        seated, rim_centre, rim_axis, knuckle_target_m, strict=strict
    )
    target = ensure_safe_output_path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(final)
    return {
        "source_knh": str(source_knh),
        "distributable": False,
        "licence_note": "deriva de un driver_base_pos.knh de Kunos de la instalación "
        "local; solo para uso local",
        "seat": seat,
        "hands": hands,
        "rim_centre_used": list(rim_centre),
        "rim_axis_used": list(rim_axis),
    }
