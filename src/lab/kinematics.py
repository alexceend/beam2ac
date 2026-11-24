"""Cinemática de sólido rígido de una mangueta unida al chasis por enlaces fijos.

Laboratorio aislado (como ``src/lab/drivetrain_lab.py``): nada de producción lo importa.

Modelo: la mangueta es un sólido rígido con 6 grados de libertad (rotación ``rv`` como
vector de Rodrigues y traslación ``t``, alrededor de su posición de reposo). Cada enlace
(chasis ``C`` – mangueta ``K``) conserva su longitud de reposo; el recorrido se impone
fijando la altura del centro de rueda. Se resuelve por mínimos cuadrados (Gauss-Newton
amortiguado con jacobiano numérico), así que admite ejes sobredeterminados (p. ej. los
8 enlaces de dos brazos trapezoidales de BeamNG) y devuelve el residuo, que es la
deformación que las vigas tendrían que absorber.

Ejes AC (``+X`` izquierda, ``+Y`` arriba, ``+Z`` adelante), metros; rueda derecha
(x < 0). Convenios de salida:

* caída (°): negativa = parte alta del neumático hacia el interior del coche;
* convergencia (°): positiva = parte delantera hacia el interior (toe-in);
* relación de palanca ``MR = |dL/dh|`` (L = longitud del muelle, h = altura de rueda).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec = tuple[float, float, float]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _norm(a: Vec) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def rotate(rv: Vec, v: Vec) -> Vec:
    """Rotación de Rodrigues de ``v`` por el vector de rotación ``rv`` (rad)."""
    th = _norm(rv)
    if th < 1e-15:
        return v
    k = (rv[0] / th, rv[1] / th, rv[2] / th)
    c, s = math.cos(th), math.sin(th)
    kv = k[0] * v[0] + k[1] * v[1] + k[2] * v[2]
    cross = (
        k[1] * v[2] - k[2] * v[1],
        k[2] * v[0] - k[0] * v[2],
        k[0] * v[1] - k[1] * v[0],
    )
    out = tuple(v[i] * c + cross[i] * s + k[i] * kv * (1 - c) for i in range(3))
    return out  # type: ignore[return-value]


@dataclass
class Knuckle:
    points: dict[
        str, Vec
    ]  # puntos de la mangueta en reposo (incluye el centro de rueda)
    centre: str  # nombre del centro de rueda
    axis: tuple[str, str]  # (exterior, interior) del eje de la rueda
    links: list[tuple[Vec, str]]  # (punto del chasis, punto de la mangueta)
    lengths: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.lengths = [_norm(_sub(self.points[k], c)) for c, k in self.links]

    def moved(self, p: list[float], name: str) -> Vec:
        c0 = self.points[self.centre]
        rel = _sub(self.points[name], c0)
        return _add(_add(c0, rotate((p[0], p[1], p[2]), rel)), (p[3], p[4], p[5]))

    def residuals(self, p: list[float], dy: float) -> list[float]:
        out = [
            _norm(_sub(self.moved(p, k), c)) - l0
            for (c, k), l0 in zip(self.links, self.lengths)
        ]
        out.append(self.moved(p, self.centre)[1] - (self.points[self.centre][1] + dy))
        return out


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-18:
            raise ValueError("sistema singular: el eje no está bien restringido")
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def solve(
    k: Knuckle, dy: float, p0: list[float] | None = None
) -> tuple[list[float], float]:
    """Pose de la mangueta para un recorrido ``dy`` (m, + = compresión). Devuelve
    ``(p, residuo_máximo_de_longitud_m)``."""
    p = list(p0) if p0 else [0.0] * 6
    lam = 1e-9
    for _ in range(60):
        r = k.residuals(p, dy)
        cost = sum(x * x for x in r)
        jac = []
        for j in range(6):
            q = p[:]
            q[j] += 1e-7
            rj = k.residuals(q, dy)
            jac.append([(a - b) / 1e-7 for a, b in zip(rj, r)])
        jt = jac  # 6 × m
        jtj = [
            [sum(jt[i][m] * jt[j][m] for m in range(len(r))) for j in range(6)]
            for i in range(6)
        ]
        jtr = [sum(jt[i][m] * r[m] for m in range(len(r))) for i in range(6)]
        for i in range(6):
            jtj[i][i] += lam * (jtj[i][i] + 1e-12)
        step = _solve_linear(jtj, [-x for x in jtr])
        q = [a + b for a, b in zip(p, step)]
        if sum(x * x for x in k.residuals(q, dy)) <= cost:
            p, lam = q, lam * 0.3
        else:
            lam *= 10
        if max(abs(s) for s in step) < 1e-12:
            break
    res = k.residuals(p, dy)
    return p, max(abs(x) for x in res[:-1])


def wheel_angles(k: Knuckle, p: list[float]) -> tuple[float, float]:
    """(caída, convergencia) en grados para una rueda derecha (x < 0)."""
    out, inn = (k.moved(p, n) for n in k.axis)
    a = _sub(inn, out)  # del exterior al interior: +X para la rueda derecha
    # Parte alta hacia dentro ⇒ el extremo interior del eje baja (a[1] < 0) ⇒ negativa.
    camber = math.degrees(math.atan2(a[1], a[0]))
    # Giro ψ de la dirección de marcha hacia +X: el eje pasa a (cos ψ, 0, −sen ψ).
    toe = math.degrees(math.atan2(-a[2], a[0]))
    return camber, toe


def sweep(
    k: Knuckle,
    travels: list[float],
    spring: tuple[Vec, str] | None = None,
    extra: list[str] = (),
) -> list[dict]:
    """Recorre ``travels`` (m) y devuelve caída, convergencia, longitud del muelle y
    posiciones de los puntos ``extra`` en cada uno."""
    rows = []
    p = None
    base = wheel_angles(k, [0.0] * 6)
    for dy in sorted(travels, key=abs):
        p, res = solve(k, dy, p)
        cam, toe = wheel_angles(k, p)
        row = {
            "dy": dy,
            "camber_deg": cam,
            "toe_deg": toe,
            "dcamber_deg": cam - base[0],
            "dtoe_deg": toe - base[1],
            "track_dx": k.moved(p, k.centre)[0] - k.points[k.centre][0],
            "residual_m": res,
            "points": {n: k.moved(p, n) for n in extra},
        }
        if spring:
            row["spring_len"] = _norm(_sub(k.moved(p, spring[1]), spring[0]))
        rows.append(row)
    return sorted(rows, key=lambda r: r["dy"])


def motion_ratio(k: Knuckle, spring: tuple[Vec, str], h: float = 0.001) -> float:
    """``|dL/dh|`` en reposo por diferencias centrales."""
    up, _ = solve(k, h)
    dn, _ = solve(k, -h)
    lu = _norm(_sub(k.moved(up, spring[1]), spring[0]))
    ld = _norm(_sub(k.moved(dn, spring[1]), spring[0]))
    return abs(lu - ld) / (2 * h)
