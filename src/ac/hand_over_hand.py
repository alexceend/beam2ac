"""Clip de dirección mano sobre mano (EXPERIMENTAL; no lo usa el ensamblador).

RESULTADO (informe §2.6): con el cruce de manos, los segmentos de antebrazo llegan a
< 2 cm (se atravesarían); no se ha generado ninguna variante con cruce. La variante sin
cruce (agarre alterno) es geométricamente viable.

Geometría (plano del aro visto desde el piloto; ángulo 0° = lado izquierdo del coche,
90° = arriba; un giro de volante de +deg gira lo agarrado +deg):

* Cada mano alterna **agarre** (gira rígidamente con el aro) y **traslado** (vuelve por
  el aire hasta su nuevo punto de agarre). Es una función periódica del ángulo de
  volante con periodo ``P = G + T`` (G grados de agarre, T de traslado), igual para
  los dos sentidos: girar al revés recorre el mismo ciclo hacia atrás.
* Los traslados de las dos manos no se solapan: siempre hay al menos una mano en el
  aro. A 0° las dos agarran en la pose del ``.knh``.
* Traslado: ángulo con un Hermite cúbico cuyas pendientes en los extremos son la
  velocidad del aro (C1 en el ángulo) y despegue hacia el piloto ``H·sin²(πs)``
  (nulo con derivada nula en los extremos). La mano mantiene su radio de agarre y no
  avanza nunca más allá de su profundidad de agarre: no puede entrar en el aro.
* Hombro: la clavícula gira hasta ``clave_max_deg`` hacia la mano según lo estirado
  que esté el brazo; después, IK de dos huesos con el codo en el plano del brazo.
* ``LOCK`` = P: el ``fmodf`` de AC (modelo de piloto de AC) lleva |deg| > LOCK a
  ``deg − LOCK·k``, que es la misma pose por periodicidad: continuo en cualquier ángulo.

No usa datos de animación de Kunos (se genera desde el ``.knh`` del coche, que sí
deriva del AE86 y por eso no es distribuible).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
from src.ac.steer_anim import (
    Frame,
    SteerAnimError,
    _axis_rotation,
    ac_frame_angle,
    clip_bones,
    matrix_to_frame,
)

Vec = list[float]


@dataclass(frozen=True)
class HandCycle:
    """Ciclo de una mano: agarra entre ``a_min`` y ``a_min + grip`` (grados absolutos
    en el plano del aro) y vuelve en ``transfer`` grados de volante."""

    a_min: float
    grip: float
    transfer: float
    base: float  # ángulo de la mano en la pose del .knh (a 0°)

    @property
    def period(self) -> float:
        return self.grip + self.transfer

    def state(self, deg: float) -> tuple[float, float, bool]:
        """(ángulo absoluto, fracción de despegue 0..1, agarrando)."""
        tau = (deg + self.base - self.a_min) % self.period
        if tau < self.grip:
            return self.a_min + tau, 0.0, True
        s = (tau - self.grip) / self.transfer
        p0, p1, m = self.a_min + self.grip, self.a_min, self.transfer
        h00, h10 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s
        h01, h11 = -2 * s**3 + 3 * s**2, s**3 - s**2
        return (
            h00 * p0 + h10 * m + h01 * p1 + h11 * m,
            math.sin(math.pi * s) ** 2,
            False,
        )


@dataclass(frozen=True)
class HohParams:
    left: HandCycle
    right: HandCycle
    lift_m: float = 0.10
    clave_max_deg: float = 15.0
    # giro del codo alrededor de la recta hombro→mano según el ángulo de la mano:
    # (ángulo de inicio, ángulo de fin, giro máximo en grados), suavizado entre ambos.
    # Separa los antebrazos cuando las manos se cruzan (uno pasa por encima del otro).
    swivel_l: tuple[float, float, float] = (0.0, 0.0, 0.0)
    swivel_r: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def period(self) -> float:
        if abs(self.left.period - self.right.period) > 1e-9:
            raise SteerAnimError("las dos manos deben tener el mismo periodo")
        return self.left.period


def rim_basis(axis: Vec) -> tuple[Vec, Vec]:
    """(x, arriba) del plano del aro: arriba = +Y proyectado; x = arriba × eje."""
    up = _sub([0.0, 1.0, 0.0], _scl(axis, _dot([0.0, 1.0, 0.0], axis)))
    up = _scl(up, 1 / _nrm(up))
    x = [
        up[1] * axis[2] - up[2] * axis[1],
        up[2] * axis[0] - up[0] * axis[2],
        up[0] * axis[1] - up[1] * axis[0],
    ]
    return x, up


def rim_angle(point: Vec, centre: Vec, axis: Vec) -> float:
    x, up = rim_basis(axis)
    d = _sub(point, centre)
    return math.degrees(math.atan2(_dot(d, up), _dot(d, x)))


def _parent(knh: Knh, name: str) -> str:
    p = knh.parent[name]
    if p is None:
        raise SteerAnimError(f"{name} sin padre")
    return p


def swivel_deg(angle: float, spec: tuple[float, float, float]) -> float:
    """Giro del codo: 0 en ``a0``, ``psi`` en ``a1`` (smoothstep; vale con a1 < a0)."""
    a0, a1, psi = spec
    if psi == 0 or a0 == a1:
        return 0.0
    x = max(0.0, min(1.0, (angle - a0) / (a1 - a0)))
    return psi * x * x * (3 - 2 * x)


def _solve_arm(
    knh: Knh, w0, local, side, hand_target, clave_max_deg, swivel=0.0, strict=True
):
    """Coloca clavícula, brazo, antebrazo y mano para que la mano quede en
    ``hand_target`` (mundo 4×4). Devuelve datos para las comprobaciones."""
    clave, arm, fore, end, hand = (
        f"DRIVER:RIG_{b}_{side}"
        for b in ("Clave", "Arm", "ForeArm", "ForeArm_END", "HAND")
    )
    c0 = w0[clave][3][:3]
    s, e, wr = w0[arm][3][:3], w0[fore][3][:3], w0[hand][3][:3]
    t = hand_target[3][:3]
    l1, l2 = _nrm(_sub(e, s)), _nrm(_sub(wr, e))
    d0 = _nrm(_sub(wr, s))
    stretch = max(0.0, min(1.0, (_nrm(_sub(t, s)) - d0) / (l1 + l2 - d0)))
    # clavícula: gira hacia la mano según lo estirado (continuo en t)
    ax = [
        (s[1] - c0[1]) * (t[2] - c0[2]) - (s[2] - c0[2]) * (t[1] - c0[1]),
        (s[2] - c0[2]) * (t[0] - c0[0]) - (s[0] - c0[0]) * (t[2] - c0[2]),
        (s[0] - c0[0]) * (t[1] - c0[1]) - (s[1] - c0[1]) * (t[0] - c0[0]),
    ]
    ang = math.radians(clave_max_deg) * stretch * stretch * (3 - 2 * stretch)
    if _nrm(ax) < 1e-9 or ang == 0:
        qc = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    else:
        qc = _axis_rotation(ax, ang)
    wclave = _rotate_about(w0[clave], qc, c0)
    warm0 = _rotate_about(w0[arm], qc, c0)
    wfore0 = _rotate_about(w0[fore], qc, c0)
    s2, e1 = warm0[3][:3], wfore0[3][:3]
    wr1 = _add(
        [sum((wr[k] - c0[k]) * qc[k][j] for k in range(3)) for j in range(3)], c0
    )
    dd = _nrm(_sub(t, s2))
    margin = min(dd - abs(l1 - l2), l1 + l2 - dd)
    if margin <= 0:
        if strict:
            raise SteerAnimError(f"{side}: mano inalcanzable")
        # vista previa: brazo extendido hacia el objetivo (la mano se queda corta; margin < 0 lo dice)
        dd2 = max(abs(l1 - l2) + 1e-4, min(l1 + l2 - 1e-4, dd))
        t = _add(s2, _scl(_sub(t, s2), dd2 / dd))
        hand_target = [*hand_target[:3], t + [1.0]]
        dd = dd2
    u = _scl(_sub(t, s2), 1 / dd)
    v = _sub(_sub(e1, s2), _scl(u, _dot(_sub(e1, s2), u)))
    if _nrm(v) < 1e-9:
        raise SteerAnimError(f"{side}: codo indeterminado")
    v = _scl(v, 1 / _nrm(v))
    a = (l1 * l1 - l2 * l2 + dd * dd) / (2 * dd)
    e2 = _add(_add(s2, _scl(u, a)), _scl(v, math.sqrt(l1 * l1 - a * a)))
    if swivel:
        e2 = _add(
            [
                sum(
                    (e2[k] - s2[k]) * _axis_rotation(u, math.radians(swivel))[k][j]
                    for k in range(3)
                )
                for j in range(3)
            ],
            s2,
        )
    q1 = _rot_between(_sub(e1, s2), _sub(e2, s2))
    warm = _rotate_about(warm0, q1, s2)
    wfore = _rotate_about(wfore0, q1, s2)
    w1 = _add(
        [sum((wr1[k] - s2[k]) * q1[k][j] for k in range(3)) for j in range(3)], s2
    )
    q2 = _rot_between(_sub(w1, e2), _sub(t, e2))
    wfore = _rotate_about(wfore, q2, e2)
    wend = _mm(local[end], wfore)
    local[clave] = _mm(wclave, _inv(w0[_parent(knh, clave)]))
    local[arm] = _mm(warm, _inv(wclave))
    local[fore] = _mm(wfore, _inv(warm))
    local[hand] = _mm(hand_target, _inv(wend))
    elbow = math.degrees(
        math.acos(max(-1.0, min(1.0, _dot(_sub(s2, e2), _sub(t, e2)) / (l1 * l2))))
    )
    return {"margin": margin, "elbow_deg": elbow, "clave_deg": math.degrees(ang)}


def hand_target(w0, side, angle_abs, lift, centre, axis, base_angle, lift_m):
    hand = f"DRIVER:RIG_HAND_{side}"
    q = _axis_rotation(axis, math.radians(angle_abs - base_angle))
    m = _rotate_about(w0[hand], q, list(centre))
    off = _scl(axis, -lift_m * lift)
    return [m[0], m[1], m[2], _add(m[3][:3], off) + [1.0]]


def pose_locals(
    knh: Knh, params: HohParams, deg: float, centre, axis, sign: int = 1, strict=True
):
    """Locales de todos los nodos para un ángulo de volante, más datos de control."""
    w0 = knh.worlds()
    local = {k: [row[:] for row in v] for k, v in knh.local.items()}
    info = {}
    for side, cyc in (("L", params.left), ("R", params.right)):
        a, lift, grip = cyc.state(sign * deg)
        tgt = hand_target(w0, side, a, lift, centre, axis, cyc.base, params.lift_m)
        info[side] = {
            "angle": a,
            "lift": lift,
            "grip": grip,
            **_solve_arm(
                knh,
                w0,
                local,
                side,
                tgt,
                params.clave_max_deg,
                swivel_deg(a, params.swivel_l if side == "L" else params.swivel_r),
                strict,
            ),
        }
    return local, info


AFFECTED = tuple(
    f"DRIVER:RIG_{b}_{s}" for s in FINGERS for b in ("Clave", "Arm", "ForeArm", "HAND")
)


def hoh_clip(
    knh: Knh, params: HohParams, centre, axis, frames: int = 600, sign: int = 1
):
    """Clip de ``frames`` fotogramas con LOCK = periodo (ver docstring del módulo)."""
    lock = params.period
    if frames % 2:
        raise SteerAnimError("frames debe ser par (fotograma central = 0°)")
    bones = clip_bones(knh)
    per: dict[str, list[Frame]] = {b: [] for b in bones}
    worst = math.inf
    for f in range(frames):
        local, info = pose_locals(
            knh, params, ac_frame_angle(f, frames, lock), centre, axis, sign
        )
        worst = min(worst, info["L"]["margin"], info["R"]["margin"])
        for b in bones:
            per[b].append(matrix_to_frame(local[b]))
    return [(b, per[b]) for b in bones], {
        "lock_deg": lock,
        "frames": frames,
        "reach_margin_m": worst,
    }
