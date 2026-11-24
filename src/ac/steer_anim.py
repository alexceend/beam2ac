"""``animations/steer.ksanim``: clip de dirección del piloto (diagnóstico).

Formato (versión 2; descifrado en v12 y comprobado con el AE86 instalado): int32
versión, int32 nº huesos; por hueso: int32 longitud + nombre (latin1), int32 nº
fotogramas y, por fotograma, 10 float = cuaternión (x, y, z, w), traslación (3) y
escala (3) en el marco local del padre. La matriz local de vector fila es::

    M[i][j] = R[j][i] · s_i    (R = matriz de rotación estándar del cuaternión)
    M[3]    = (tx, ty, tz, 1)

Con esa convención el fotograma 50 del ``steer.ksanim`` del AE86 coincide con su
``driver_base_pos.knh`` (error máximo 0,048); con R sin trasponer, no (1,94).

Cómo usa AC el clip (análisis estático de AC, modelo de piloto de AC)::

    deg = ángulo gráfico del volante (grados)
    x   = −deg / LOCK            (LOCK = [STEER_ANIMATION] LOCK de driver3d.ini)
    si x sale de [−1, 1]: x = fmodf(x, 1)      (el clip se repite: mano sobre mano)
    pos = (x + 1) / 2            (0..1 a lo largo del clip; 0,5 = volante recto)

Los clips de Kunos (AE86, Abarth) son de 100 fotogramas y animan el cuerpo entero,
así que el clip sustituye a la pose del ``.knh`` en los huesos que lleva. Por eso
cada fotograma generado aquí parte de la pose del ``.knh`` del coche y solo cambia
los brazos.

Incertidumbres (se resuelven solo con la prueba en el juego):

* signo: si un ``deg`` positivo gira ``STEER_HR`` en el sentido que aquí se llama +;
* escala: qué unidades devuelve el ángulo gráfico del volante (hipótesis: grados del
  volante gráfico);
* más allá de ±LOCK el clip vuelve a empezar (``fmodf``): no hay mano sobre mano.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from src.ac.driver_pose import (
    FINGERS,
    Knh,
    _add,
    _dot,
    _inv,
    _mm,
    _nrm,
    _rot_between,
    _rotate_about,
    _scl,
    _sub,
)
from src.safe_paths import ensure_safe_output_path

Mat = list[list[float]]
Frame = tuple[float, ...]  # 10 float
KSANIM_VERSION = 2
FRAMES = 100


class SteerAnimError(ValueError):
    """Clip inválido o no generable."""


# ------------------------------------------------------------------ formato
def read_ksanim(data: bytes) -> tuple[int, list[tuple[str, list[Frame]]]]:
    ver, nb = struct.unpack_from("<ii", data, 0)
    p, bones = 8, []
    for _ in range(nb):
        n = struct.unpack_from("<i", data, p)[0]
        name = data[p + 4 : p + 4 + n].decode("latin1")
        p += 4 + n
        nf = struct.unpack_from("<i", data, p)[0]
        p += 4
        frames = [struct.unpack_from("<10f", data, p + 40 * i) for i in range(nf)]
        p += 40 * nf
        bones.append((name, frames))
    if p != len(data):
        raise SteerAnimError(f"ksanim: sobran o faltan {len(data) - p} bytes")
    return ver, bones


def write_ksanim(bones: list[tuple[str, list[Frame]]]) -> bytes:
    if not bones or len({len(f) for _, f in bones}) != 1:
        raise SteerAnimError("todos los huesos deben tener el mismo nº de fotogramas")
    out = bytearray(struct.pack("<ii", KSANIM_VERSION, len(bones)))
    for name, frames in bones:
        b = name.encode("latin1")
        out += struct.pack("<i", len(b)) + b + struct.pack("<i", len(frames))
        for fr in frames:
            out += struct.pack("<10f", *fr)
    return bytes(out)


def frame_to_matrix(fr: Frame) -> Mat:
    x, y, z, w, tx, ty, tz, sx, sy, sz = fr
    r = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    s = (sx, sy, sz)
    rows = [[r[j][i] * s[i] for j in range(3)] + [0.0] for i in range(3)]
    return rows + [[tx, ty, tz, 1.0]]


def matrix_to_frame(m: Mat) -> Frame:
    """Inversa de :func:`frame_to_matrix` (escala positiva, sin cizalla)."""
    s = [math.sqrt(sum(m[i][j] ** 2 for j in range(3))) for i in range(3)]
    if min(s) <= 0:
        raise SteerAnimError("escala nula")
    # R[j][i] = M[i][j] / s_i
    r = [[m[i][j] / s[i] for i in range(3)] for j in range(3)]
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        k = math.sqrt(tr + 1.0) * 2
        w, x = 0.25 * k, (r[2][1] - r[1][2]) / k
        y, z = (r[0][2] - r[2][0]) / k, (r[1][0] - r[0][1]) / k
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        k = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        w, x = (r[2][1] - r[1][2]) / k, 0.25 * k
        y, z = (r[0][1] + r[1][0]) / k, (r[0][2] + r[2][0]) / k
    elif r[1][1] > r[2][2]:
        k = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        w, x = (r[0][2] - r[2][0]) / k, (r[0][1] + r[1][0]) / k
        y, z = 0.25 * k, (r[1][2] + r[2][1]) / k
    else:
        k = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        w, x = (r[1][0] - r[0][1]) / k, (r[0][2] + r[2][0]) / k
        y, z = (r[1][2] + r[2][1]) / k, 0.25 * k
    n = math.sqrt(x * x + y * y + z * z + w * w)
    return (x / n, y / n, z / n, w / n, m[3][0], m[3][1], m[3][2], *s)


def max_error(a: Mat, b: Mat) -> float:
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


# ------------------------------------------------------------------ clips
def clip_bones(knh: Knh) -> list[str]:
    """Huesos del clip: los nodos ``DRIVER:*`` del ``.knh`` en su orden (preorden)."""
    return [n for n in knh.nodes if n.startswith("DRIVER:")]


def static_clip(knh: Knh, frames: int = FRAMES) -> list[tuple[str, list[Frame]]]:
    """Todos los fotogramas = pose del ``.knh`` (el clip no debe mover al piloto)."""
    return [(b, [matrix_to_frame(knh.local[b])] * frames) for b in clip_bones(knh)]


def _axis_rotation(axis, angle_rad: float) -> list[list[float]]:
    """Rotación de ``angle_rad`` alrededor de ``axis`` (regla de la mano derecha), en
    forma de vector fila (p' = p · Q)."""
    k = _scl(axis, 1 / _nrm(axis))
    c, s = math.cos(angle_rad), math.sin(angle_rad)
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


def clip_angle(frame: int, frames: int, lock_deg: float) -> float:
    """Ángulo de volante (grados) al que AC lleva el fotograma ``frame``.

    De ``pos = (1 − deg/LOCK)/2`` con ``pos = frame / (frames − 1)`` (hipótesis: el
    reproductor reparte ``pos`` uniformemente entre el primer y el último fotograma).
    """
    pos = frame / (frames - 1)
    return lock_deg * (1 - 2 * pos)


def steer_clip(
    knh: Knh,
    rim_centre,
    rim_axis,
    lock_deg: float,
    sign: int,
    frames: int = FRAMES,
    angle_fn=None,
    hand_fn=None,
) -> tuple[list[tuple[str, list[Frame]]], dict]:
    """Clip con las manos girando con el aro ``sign · hand_fn(deg)`` alrededor del eje
    del aro.

    Solo cambian ``RIG_Arm``, ``RIG_ForeArm`` y ``RIG_HAND`` de cada lado (IK de dos
    huesos con el hombro fijo y el codo en el plano original del brazo, como
    :func:`src.ac.driver_pose.hands_to_rim`). Los dedos siguen a la mano.

    ``angle_fn(frame, frames, lock_deg)`` da el ángulo de volante de cada fotograma
    (por defecto :func:`clip_angle`, el de v14hp); ``hand_fn(deg)`` el giro de las
    manos (por defecto, igual al del volante).
    """
    if sign not in (1, -1):
        raise SteerAnimError("sign debe ser 1 o -1")
    angle_fn = angle_fn or clip_angle
    w0 = knh.worlds()
    bones = clip_bones(knh)
    per_bone: dict[str, list[Frame]] = {b: [] for b in bones}
    reach_margin = math.inf
    for f in range(frames):
        deg = angle_fn(f, frames, lock_deg)
        phi = hand_fn(deg) if hand_fn else deg
        q = _axis_rotation(rim_axis, math.radians(sign * phi))
        local = {k: [row[:] for row in v] for k, v in knh.local.items()}
        for side in FINGERS:
            arm, fore, end, hand, clave = (
                f"DRIVER:RIG_{b}_{side}"
                for b in ("Arm", "ForeArm", "ForeArm_END", "HAND", "Clave")
            )
            w = w0
            s, e, wr = w[arm][3][:3], w[fore][3][:3], w[hand][3][:3]
            whand_t = _rotate_about(w[hand], q, list(rim_centre))
            t = whand_t[3][:3]
            l1, l2, dd = _nrm(_sub(e, s)), _nrm(_sub(wr, e)), _nrm(_sub(t, s))
            margin = min(dd - abs(l1 - l2), l1 + l2 - dd)
            reach_margin = min(reach_margin, margin)
            if margin <= 0:
                raise SteerAnimError(f"{side}: mano inalcanzable a {deg:.1f}°")
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
            local[arm] = _mm(warm, _inv(w[clave]))
            local[fore] = _mm(wfore, _inv(warm))
            local[hand] = _mm(whand_t, _inv(wend))
        for b in bones:
            per_bone[b].append(matrix_to_frame(local[b]))
    return [(b, per_bone[b]) for b in bones], {
        "lock_deg": lock_deg,
        "sign": sign,
        "frames": frames,
        "reach_margin_m": reach_margin,
    }


def rim_angle_of(point, ref, centre, axis) -> float:
    """Ángulo (grados, mano derecha alrededor de ``axis``) de ``point`` respecto a
    ``ref``, proyectados en el plano del aro."""

    def proj(p):
        d = _sub(p, centre)
        return _sub(d, _scl(axis, _dot(d, axis)))

    u, v = proj(ref), proj(point)
    cr = [
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    ]
    return math.degrees(math.atan2(_dot(cr, axis), _dot(u, v)))


def clip_worlds(knh: Knh, clip, frame: int) -> dict[str, Mat]:
    local = dict(knh.local)
    for name, frames in clip:
        local[name] = frame_to_matrix(frames[frame])
    return knh.worlds(local)


def write_clip(path: Path, clip) -> bytes:
    data = write_ksanim(clip)
    target = ensure_safe_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return data


# --------------------------------------------------- correspondencia exacta de AC (v14)
# modelo de piloto de AC y reproductor de animación de AC:
#   x   = −deg / LOCK;  si x ∉ [−1, 1]: x = fmodf(x, 1)      ← salto de v14hp a ±75°
#   pos = (x + 1) / 2, recortado a [0, 1]
#   t   = n · pos (n = nº de fotogramas); i = ⌊t⌋, frac = t − i; interpola i → i + 1;
#   si i ≥ n − 1 se queda en el último fotograma; si pos ≤ 0, en el primero.


def ac_frame_angle(frame: int, frames: int, lock_deg: float) -> float:
    """Ángulo de volante del fotograma ``frame`` según setCurrentPos (t = n·pos)."""
    return lock_deg * (1 - 2 * frame / frames)


def ac_clip_pos(deg: float, lock_deg: float) -> float:
    """``pos`` que AC da al reproductor para un ángulo de volante (con ``fmodf``)."""
    x = -deg / lock_deg
    if x < -1 or x > 1:
        x = math.fmod(x, 1.0)  # fmodf de C: conserva el signo, como math.fmod
    return min(1.0, max(0.0, (x + 1) * 0.5))


def ac_frame_blend(pos: float, frames: int) -> tuple[int, int, float]:
    """(fotograma, siguiente, fracción) como reproductor de animación de AC."""
    pos = min(1.0, max(0.0, pos))
    t = frames * pos
    i = int(t)
    frac = min(1.0, max(0.0, t - i))
    nxt = i + 1
    if i >= frames - 1:
        i, nxt, frac = frames - 1, frames - 1, 0.0
    if pos <= 0:
        i, nxt, frac = 0, 1, 0.0
    return i, nxt, frac


def saturating_follow(linear_deg: float, max_deg: float):
    """Giro de las manos: igual al del volante hasta ``linear_deg`` y después se acerca
    sin pasarse a ``max_deg`` (continuo y con derivada continua en ``linear_deg``)::

        |φ| = a + (A − a) · (1 − exp(−(|deg| − a) / (A − a)))   si |deg| > a
    """
    if not 0 < linear_deg < max_deg:
        raise SteerAnimError("se necesita 0 < linear_deg < max_deg")
    a, span = linear_deg, max_deg - linear_deg

    def fn(deg: float) -> float:
        m = abs(deg)
        if m <= a:
            return deg
        return math.copysign(a + span * (1 - math.exp(-(m - a) / span)), deg)

    return fn


def blend_frames(fa: Frame, fb: Frame, t: float) -> Frame:
    """Interpolación entre dos fotogramas (cuaternión normalizado + lineal).

    Aproximación para las pruebas: no se ha comprobado si AC usa slerp o nlerp; con
    pasos de 1,5° la diferencia es despreciable.
    """
    qa, qb = fa[:4], fb[:4]
    if sum(x * y for x, y in zip(qa, qb)) < 0:
        qb = tuple(-x for x in qb)
    q = [x + (y - x) * t for x, y in zip(qa, qb)]
    n = math.sqrt(sum(x * x for x in q))
    rest = [x + (y - x) * t for x, y in zip(fa[4:], fb[4:])]
    return (*[x / n for x in q], *rest)


def ac_pose_at(knh: Knh, clip, deg: float, lock_deg: float) -> dict[str, Mat]:
    """Mundos de los huesos que AC mostraría con el volante a ``deg`` (simulación)."""
    frames = len(clip[0][1])
    i, j, frac = ac_frame_blend(ac_clip_pos(deg, lock_deg), frames)
    local = dict(knh.local)
    for name, fr in clip:
        local[name] = frame_to_matrix(blend_frames(fr[i], fr[j], frac))
    return knh.worlds(local)
