"""Enlaces de una suspensión independiente y su equivalente de doble triángulo (G2.16).

Solo lee la configuración resuelta; no decide nada del destino. Para cada rueda:

1. **Mangueta rígida:** buje (``node1``/``node2`` de la rueda) + nodos unidos al
   buje por vigas ``NORMAL`` de rigidez ≥ ``RIGID_LINK`` (o sin ``beamSpring``,
   rígidas por defecto). **Enlaces:** vigas ``NORMAL`` rígidas de un nodo de la
   mangueta a un nodo que no es de la mangueta, salvo el muelle y el amortiguador
   (identificados por propiedades en ``src/extract/suspension.py``).
2. **Longitud de reposo** de cada enlace = L·``beamPrecompression`` (``stage2.lua``;
   confirmado en el juego con el volcado del banco de G2.15: las barras precomprimidas
   miden L·p a ≤ 0,07 mm, ``output/g216_suspension_2026-11-04/precompresion_banco.md``).
   Así fijan los exportadores de Automation parte de la caída y la convergencia.
3. **Pose de reposo:** mínimos cuadrados ponderados por la rigidez de cada viga
   (energía ½·Σk·ΔL², sin cargas) con el centro de rueda a la altura de la pose del
   JBeam. Con enlaces compatibles es la solución rígida exacta; con más de 5 enlaces y
   longitudes incompatibles es el equilibrio elástico.
4. **Rango** de las restricciones en reposo (autovalores de JᵀJ): 5 = un grado de
   libertad guiado por barras; < 5 = la mangueta no la guían solo barras (puntal en
   raíl…); eje acoplado (un enlace llega al otro lado o al plano medio) = no es
   independiente.
5. **Equivalente DWB** (dos vértices con dos barras cada uno + una barra): cada
   subconjunto de rango 5, con las longitudes medidas en la pose de reposo, se recorre
   en ±``TRAVEL`` y se compara con el conjunto completo (variación de caída,
   convergencia y vía respecto al reposo). Se elige el de menor desviación (y, a
   igualdad, el mejor condicionado). ``kind``:

   * ``dwb_5link``: exactamente 5 enlaces que forman un doble triángulo;
   * ``dwb_equivalent``: más de 5 enlaces y algún DWB dentro de
     ``EQUIV_DEG``/``EQUIV_M``;
   * ``dwb_fitted`` (G2.30): más de 5 enlaces de rango 6 sin DWB dentro de la tolerancia
     (BeamNG sigue el equilibrio elástico del conjunto completo, no un subconjunto): un
     doble triángulo con los 5 puntos del chasis AJUSTADOS (regularizados) a esa
     trayectoria, más la rigidez y la fuerza de las barras sobrantes
     (:func:`_redundant_links`);
   * ``overconstrained``: más de 5 enlaces sin DWB ni ajuste dentro de la tolerancia;
   * ``underconstrained``, ``coupled``, ``unsupported``.

Modelo (supuestos, no del juego): enlaces de longitud fija; elasticidad solo para el
reparto en reposo de un conjunto incompatible; sin cargas. Cinemática copiada de
``src/lab/kinematics.py`` (el laboratorio no se toca ni se importa) con pesos y
longitudes de reposo explícitas; ``tests/test_g216_linkage.py`` comprueba que da lo
mismo.

Marco de salida (``anchors``): relativo al centro de rueda en la pose de reposo, ejes
``(lateral, arriba, adelante)`` con el lateral **positivo hacia dentro del coche**,
para la rueda derecha (la izquierda se refleja). Ángulos: caída negativa = parte alta
hacia dentro; convergencia positiva = parte delantera hacia dentro.
"""

from __future__ import annotations

import itertools
import math
from array import array
from dataclasses import dataclass, field
from operator import mul
from typing import Any, Iterable

from src.coords import beamng_to_ac

Vec = tuple[float, float, float]

RIGID_LINK = 1000.0  # N/m: por debajo, el enlace es nominal (como candidates_dwb)
TRAVEL = 0.06  # m de recorrido de rueda en cada sentido para comparar
TRAVEL_STEPS = 5  # puntos por sentido (paso 12 mm)
MR_TRAVEL = 0.04  # m: rango de la palanca informado
EQUIV_DEG = 0.01  # tolerancia de equivalencia (variación de caída/convergencia)
EQUIV_M = 1e-4  # tolerancia de equivalencia (variación de vía)
RANK_TOL = (
    1e-6  # autovalor relativo por debajo del cual la dirección no está restringida
)
HEIGHT_WEIGHT = 1e4  # peso de la restricción de altura del centro de rueda
FIT_REG = (
    1.0  # G2.30: peso de la regularización del ajuste (5 cm de desplazamiento = 1 ud.)
)
# fracción de EQUIV_* que puede quedar de desviación tras el ajuste. 2026-11-21: 0,5 → 1,0 (el mismo
# criterio que ``dwb_equivalent``: dentro de la tolerancia); turbot_aster (40340) queda a 0,66·EQUIV_DEG
# con puntos movidos ≤ 26 mm y con 0,5 caía a ``overconstrained``. Los ajustes ya aceptados (≤ 0,31) no cambian.
FIT_ACCEPT = 1.0
FIT_MAX_MOVE = (
    0.15  # m: un punto del chasis que se mueva más no es un equivalente razonable
)

DWB_KINDS = ("dwb_5link", "dwb_equivalent", "dwb_fitted", "dwb_coupled")
ANCHORS = (
    "upper_chassis_front",
    "upper_chassis_rear",
    "lower_chassis_front",
    "lower_chassis_rear",
    "upper_upright",
    "lower_upright",
    "rod_chassis",
    "rod_upright",
)


# --------------------------------------------------------------- sólido rígido
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
    return (
        v[0] * c + cross[0] * s + k[0] * kv * (1 - c),
        v[1] * c + cross[1] * s + k[1] * kv * (1 - c),
        v[2] * c + cross[2] * s + k[2] * kv * (1 - c),
    )


@dataclass
class Knuckle:
    """Mangueta rígida (6 gdl alrededor de su pose inicial) unida por barras.

    ``lengths``/``weights`` por defecto: longitud de la pose inicial y peso 1."""

    points: dict[str, Vec]
    centre: str
    axis: tuple[str, str]  # (exterior, interior)
    links: list[tuple[Vec, str]]  # (punto del chasis, punto de la mangueta)
    lengths: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    # G2.19: raíles (``rails`` + ``slidenodes`` con ``fixToRail``): (nodo deslizante de
    # la mangueta, extremo del raíl EN la mangueta, extremo del raíl en el chasis). El
    # nodo deslizante debe estar en la recta de los dos extremos: 2 restricciones.
    rails: list[tuple[str, str, Vec]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.lengths:
            self.lengths = [_norm(_sub(self.points[k], c)) for c, k in self.links]
        if not self.weights:
            self.weights = [1.0] * len(self.links)

    def moved(self, p: list[float], name: str) -> Vec:
        c0 = self.points[self.centre]
        rel = _sub(self.points[name], c0)
        return _add(_add(c0, rotate((p[0], p[1], p[2]), rel)), (p[3], p[4], p[5]))

    def link_errors(self, p: list[float]) -> list[float]:
        return [
            _norm(_sub(self.moved(p, k), c)) - l0
            for (c, k), l0 in zip(self.links, self.lengths)
        ]

    def rail_errors(self, p: list[float]) -> list[float]:
        """Distancia (vector, m) del nodo deslizante a la recta de su raíl: componentes
        de (P − A) × û, con A el extremo en la mangueta y û la dirección A → B."""
        out: list[float] = []
        for slide, end_k, end_c in self.rails:
            a = self.moved(p, end_k)
            d = _sub(end_c, a)
            n = _norm(d)
            u = (d[0] / n, d[1] / n, d[2] / n)
            r = _sub(self.moved(p, slide), a)
            out += [
                r[1] * u[2] - r[2] * u[1],
                r[2] * u[0] - r[0] * u[2],
                r[0] * u[1] - r[1] * u[0],
            ]
        return out

    def constraint_errors(self, p: list[float]) -> list[float]:
        """Barras (ΔL) y raíles (distancia a la recta), sin pesos (G2.19)."""
        return self.link_errors(p) + self.rail_errors(p)

    def residuals(self, p: list[float], dy: float) -> list[float]:
        out = [e * w for e, w in zip(self.link_errors(p), self.weights)]
        out += self.rail_errors(p)  # raíl: rígido (peso 1, como la barra más rígida)
        height = self.moved(p, self.centre)[1] - (self.points[self.centre][1] + dy)
        out.append(height * HEIGHT_WEIGHT)
        return out


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-30:
            raise ValueError("sistema singular")
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def solve(
    k: Knuckle, dy: float, p0: list[float] | None = None
) -> tuple[list[float], float]:
    """Pose para un recorrido ``dy`` (m, + = compresión) por Gauss-Newton amortiguado
    (como ``src/lab/kinematics.py``). Devuelve ``(p, máximo |ΔL| sin ponderar)``."""
    p = list(p0) if p0 else [0.0] * 6
    lam = 1e-9
    for _ in range(80):
        r = k.residuals(p, dy)
        cost = sum(x * x for x in r)
        jac = []
        for j in range(6):
            q = p[:]
            q[j] += 1e-7
            jac.append([(a - b) / 1e-7 for a, b in zip(k.residuals(q, dy), r)])
        jtj = [
            [sum(jac[i][m] * jac[j][m] for m in range(len(r))) for j in range(6)]
            for i in range(6)
        ]
        jtr = [sum(jac[i][m] * r[m] for m in range(len(r))) for i in range(6)]
        for i in range(6):
            jtj[i][i] += lam * (jtj[i][i] + 1e-12)
        step = _solve_linear(jtj, [-x for x in jtr])
        q = [a + b for a, b in zip(p, step)]
        if sum(x * x for x in k.residuals(q, dy)) <= cost:
            p, lam = q, lam * 0.3
        else:
            lam *= 10
        if max(abs(s) for s in step) < 1e-13:
            break
    return p, max((abs(e) for e in k.constraint_errors(p)), default=0.0)


def wheel_angles(k: Knuckle, p: list[float]) -> tuple[float, float]:
    """(caída, convergencia) en grados de una rueda derecha (o izquierda reflejada)."""
    out, inn = (k.moved(p, n) for n in k.axis)
    a = _sub(inn, out)
    return math.degrees(math.atan2(a[1], a[0])), math.degrees(math.atan2(-a[2], a[0]))


_JACOBI_ITERATIONS = 200


def _jacobi_eigen(a: list[list[float]]) -> list[float]:
    """Autovalores (ordenados) de una matriz simétrica: 200 rotaciones de Jacobi.

    El umbral de salida casi nunca se alcanza (queda ruido de redondeo), pero la
    iteración suele caer en un ciclo exacto: como cada estado determina el siguiente,
    al repetirse uno el estado final se deduce sin seguir iterando (mismo resultado
    bit a bit)."""
    n = len(a)
    m = [row[:] for row in a]
    seen: dict[bytes, int] = {}
    diags: list[list[float]] = []
    for it in range(_JACOBI_ITERATIONS):
        flat = tuple(itertools.chain.from_iterable(m))
        # clave en bytes: -0.0 y 0.0 son estados distintos
        start = seen.setdefault(array("d", flat).tobytes(), it)
        if start != it:
            end = start + (_JACOBI_ITERATIONS - start) % (it - start)
            return sorted(diags[end])
        diags.append(list(flat[:: n + 1]))
        mags = list(map(abs, flat))
        mags[:: n + 1] = [-1.0] * n  # sin la diagonal
        off = max(mags)
        if off < 1e-30:
            break
        # a igualdad de módulo, el último en orden de filas
        p, q = divmod(len(mags) - 1 - mags[::-1].index(off), n)
        th = 0.5 * math.atan2(2 * m[p][q], m[q][q] - m[p][p])
        c, s = math.cos(th), math.sin(th)
        for row in m:
            x, y = row[p], row[q]
            row[p], row[q] = c * x - s * y, s * x + c * y
        rp, rq = m[p], m[q]
        m[p] = [c * x - s * y for x, y in zip(rp, rq)]
        m[q] = [s * x + c * y for x, y in zip(rp, rq)]
    return sorted(m[i][i] for i in range(n))


def constraint_eigen(k: Knuckle, p: list[float] | None = None) -> list[float]:
    """Autovalores de JᵀJ de las restricciones de longitud y de raíl (sin pesos ni
    altura); la rotación se escala por 0,3 m para comparar metros con radianes."""
    p0 = list(p) if p else [0.0] * 6
    r0 = k.constraint_errors(p0)
    jac = []
    for j in range(6):
        q = p0[:]
        q[j] += 1e-7
        scale = 1 / 0.3 if j < 3 else 1.0
        jac.append([(a - b) / 1e-7 * scale for a, b in zip(k.constraint_errors(q), r0)])
    jtj = [[0.0] * 6 for _ in range(6)]
    for i in range(6):
        for j in range(i, 6):  # simétrica bit a bit
            jtj[i][j] = jtj[j][i] = sum(map(mul, jac[i], jac[j]))
    return _jacobi_eigen(jtj)


def rank(eig: list[float]) -> int:
    top = eig[-1] if eig else 0.0
    return sum(1 for x in eig if top > 0 and x > RANK_TOL * top)


def _travels() -> list[float]:
    step = TRAVEL / TRAVEL_STEPS
    return [i * step for i in range(-TRAVEL_STEPS, TRAVEL_STEPS + 1)]


def sweep(
    k: Knuckle, p_rest: list[float], travels: Iterable[float]
) -> dict[float, tuple[float, float, float, list[float]]]:
    """``dy`` → (caída, convergencia, x del centro, pose), partiendo del reposo."""
    out: dict[float, tuple[float, float, float, list[float]]] = {}
    ups = sorted(t for t in travels if t > 0)
    dns = sorted((t for t in travels if t < 0), reverse=True)
    cam, toe = wheel_angles(k, p_rest)
    out[0.0] = (cam, toe, k.moved(p_rest, k.centre)[0], p_rest)
    for seq in (ups, dns):
        p = p_rest
        for dy in seq:
            p, _ = solve(k, dy, p)
            cam, toe = wheel_angles(k, p)
            out[dy] = (cam, toe, k.moved(p, k.centre)[0], p)
    return out


def length_along(k: Knuckle, p: list[float], spring: tuple[Vec, str]) -> float:
    return _norm(_sub(k.moved(p, spring[1]), spring[0]))


def motion_ratio(
    k: Knuckle,
    spring: tuple[Vec, str],
    p_start: list[float],
    dy0: float = 0.0,
    h: float = 0.001,
) -> float:
    """``|dL/dh|`` a la altura ``dy0`` (m desde la inicial), diferencias centrales."""
    up, _ = solve(k, dy0 + h, p_start)
    dn, _ = solve(k, dy0 - h, p_start)
    return abs(length_along(k, up, spring) - length_along(k, dn, spring)) / (2 * h)


# --------------------------------------------------------------- DWB ajustado (G2.30)
def _centre_z(k: Knuckle, pose: list[float]) -> float:
    return k.moved(pose, k.centre)[2]


def _fit_dwb(
    kn: Knuckle, full: Knuckle, ref: dict, travels: list[float]
) -> tuple[Knuckle, float, float, list[float]] | None:
    """Doble triángulo de 5 barras con los 5 puntos del chasis LIBRES (15 coordenadas)
    ajustados a la trayectoria del conjunto completo ``full`` (equilibrio elástico).

    Gauss-Newton amortiguado sobre la variación, respecto al reposo, de caída,
    convergencia, vía y avance del centro de rueda (normalizadas por ``EQUIV_DEG`` /
    ``EQUIV_M``), con regularización ``FIT_REG`` hacia los puntos reales (la solución
    sin
    regularizar no es única). Aceptado solo si la desviación máxima ≤ ``FIT_ACCEPT``
    veces la tolerancia y ningún punto se mueve más de ``FIT_MAX_MOVE``. Devuelve
    ``(mangueta ajustada, desv. máx. caída/convergencia °, desv. máx. vía/avance m,
    desplazamientos m)`` o ``None``. Determinista (sin azar)."""
    args = (kn.points, kn.centre, kn.axis)
    base = [c for c, _ in kn.links]
    ups = [u for _, u in kn.links]
    n = 3 * len(base)
    z_ref = {
        d: _centre_z(full, ref[d][3]) - _centre_z(full, ref[0.0][3]) for d in travels
    }

    def links_of(x: list[float]) -> list[tuple[Vec, str]]:
        return [
            ((c[0] + x[3 * i], c[1] + x[3 * i + 1], c[2] + x[3 * i + 2]), ups[i])
            for i, c in enumerate(base)
        ]

    def resid(x: list[float]) -> list[float]:
        k2 = Knuckle(*args, links_of(x))
        sw = sweep(k2, [0.0] * 6, travels)
        out: list[float] = []
        for d in travels:
            out += [
                ((sw[d][0] - sw[0.0][0]) - (ref[d][0] - ref[0.0][0])) / EQUIV_DEG,
                ((sw[d][1] - sw[0.0][1]) - (ref[d][1] - ref[0.0][1])) / EQUIV_DEG,
                ((sw[d][2] - sw[0.0][2]) - (ref[d][2] - ref[0.0][2])) / EQUIV_M,
                ((_centre_z(k2, sw[d][3]) - _centre_z(k2, sw[0.0][3])) - z_ref[d])
                / EQUIV_M,
            ]
        return out + [math.sqrt(FIT_REG) * v / 0.05 for v in x]

    x = [0.0] * n
    try:
        r = resid(x)
    except ValueError:
        return None
    lam = 1e-2
    for _ in range(40):
        jac = []
        for j in range(n):
            xp = x[:]
            xp[j] += 1e-5
            try:
                rp = resid(xp)
            except ValueError:  # sistema singular: sin ajuste, sigue overconstrained
                return None
            jac.append([(a - b) / 1e-5 for a, b in zip(rp, r)])
        a_mat = [
            [sum(jac[i][m] * jac[j][m] for m in range(len(r))) for j in range(n)]
            for i in range(n)
        ]
        g = [-sum(jac[i][m] * r[m] for m in range(len(r))) for i in range(n)]
        improved = False
        for _try in range(8):
            mat = [
                [
                    a_mat[i][j] + (lam * (a_mat[i][i] + 1e-6) if i == j else 0.0)
                    for j in range(n)
                ]
                for i in range(n)
            ]
            try:
                xn = [a + b for a, b in zip(x, _solve_linear(mat, g))]
                rn = resid(xn)
            except ValueError:
                lam *= 10
                continue
            if sum(v * v for v in rn) < sum(v * v for v in r):
                x, r, lam, improved = xn, rn, lam / 3, True
                break
            lam *= 10
        if not improved or sum(v * v for v in r) < 1e-4:
            break
    nd = 4 * len(travels)
    dev = [max(abs(v) for v in r[i:nd:4]) for i in range(4)]
    move = [
        math.sqrt(sum(v * v for v in x[3 * i : 3 * i + 3])) for i in range(len(base))
    ]
    if max(dev) > FIT_ACCEPT or max(move) > FIT_MAX_MOVE:
        return None
    return (
        Knuckle(*args, links_of(x)),
        max(dev[0], dev[1]) * EQUIV_DEG,
        max(dev[2], dev[3]) * EQUIV_M,
        move,
    )


def _redundant_links(
    full: Knuckle, links: list[LinkData], p_rest: list[float]
) -> tuple[float, float]:
    """Fuerza (N, hacia arriba sobre la rueda, en la pose del JBeam) y rigidez (N/m, en
    rueda) del conjunto de barras: energía elástica E = ½·Σ k·ΔL² con las longitudes de
    reposo (L·``beamPrecompression``) en el equilibrio de ``full`` a cada altura;
    F = −dE/dh, rigidez = d²E/dh² (h > 0 = compresión), diferencias centrales a ±2 mm.
    Una viga sin ``beamSpring`` pesa como la más rígida del conjunto (como en ``full``).
    """
    top = max((lk.stiffness for lk in links if lk.stiffness), default=1.0)
    stiff = [lk.stiffness or top for lk in links]
    e = {}
    for h in (-0.002, 0.0, 0.002):
        pose, _ = solve(full, h, p_rest)
        e[h] = 0.5 * sum(k * d * d for k, d in zip(stiff, full.link_errors(pose)))
    return (
        -(e[0.002] - e[-0.002]) / 0.004,
        (e[0.002] - 2 * e[0.0] + e[-0.002]) / 0.002**2,
    )


# --------------------------------------------------------------- resultado
@dataclass(frozen=True)
class LinkData:
    chassis: str
    upright: str
    jbeam_length: float  # m, pose del JBeam
    precompression: float  # beamPrecompression (1 si no hay)
    stiffness: float | None  # beamSpring (None = rígida por defecto)

    @property
    def rest_length(self) -> float:
        return self.jbeam_length * self.precompression


@dataclass
class LinkageResult:
    wheel: str
    # dwb_5link | dwb_equivalent | dwb_fitted | dwb_coupled | overconstrained
    # | underconstrained | coupled | unsupported | strut_rail | rigid_axle
    kind: str
    reason: str
    links: list[LinkData] = field(default_factory=list)
    rank: int | None = None
    rest_residual: float | None = None  # m: máx. |ΔL| del conjunto completo en reposo
    deviation_deg: float | None = None  # máx. Δ(caída), Δ(convergencia) del DWB elegido
    deviation_m: float | None = None  # máx. Δ(vía) del DWB elegido
    dwb_links: list[tuple[str, str]] = field(default_factory=list)
    anchors: dict[str, Vec] = field(default_factory=dict)
    anchor_nodes: dict[str, str] = field(default_factory=dict)
    rest_camber_deg: float | None = None
    rest_toe_deg: float | None = None
    spring_motion_ratio: float | None = None
    spring_motion_ratio_range: tuple[float, float] | None = None
    damper_motion_ratio: float | None = None
    spring_rest_compression: float | None = None  # m a lo largo del muelle (reposo)
    rod_ahead_of_axle: bool | None = None
    # G2.30 (``dwb_fitted``): desplazamiento (m) de cada punto del chasis ajustado
    # respecto
    # al nodo real; fuerza (N, hacia arriba en la rueda, a la altura del JBeam) y
    # rigidez
    # (N/m, en rueda) del conjunto de barras redundantes (energía elástica ½Σk·ΔL²)
    fit_displacement: list[float] = field(default_factory=list)
    redundant_link_force: float | None = None
    redundant_link_rate: float | None = None
    rod_shift_per_toe_deg: float | None = None  # m de la barra (chasis, lateral) / °
    # G2.17: lo mismo CON SIGNO: desplazamiento del punto del chasis de la barra HACIA
    # DENTRO por grado de convergencia (toe-in) en el reposo del DWB elegido
    rod_inward_shift_per_toe_in: float | None = None  # m/°
    rod_inward_shift_exact: float | None = None  # m: gira la convergencia de reposo
    damper_shortens_on_bump: bool | None = None
    # G2.17: topes (viga BOUNDED con *BoundRange de la mangueta a la carrocería):
    # recorrido VERTICAL de la rueda desde el reposo hasta el contacto en compresión
    # (arriba) y en extensión (abajo), y la palanca |dL/dh| de la viga del tope en
    # cada contacto
    bumpstop_travel_up: float | None = None  # m (> 0)
    bumpstop_travel_down: float | None = None  # m (> 0)
    bumpstop_motion_ratio_up: float | None = None
    bumpstop_motion_ratio_down: float | None = None
    bumpstop_reason: str = ""
    # G2.17: barras de torsión con un extremo en esta mangueta: |dθ/dh| (rad por m de
    # recorrido vertical de ESTA rueda, con el resto de nodos quietos) en el reposo
    torsion_angle_rates: list[dict[str, Any]] = field(default_factory=list)
    # G2.19: puntal en raíl (``strut_rail``): palanca del eje del puntal de AC
    # (|d(longitud STRUT_CAR–STRUT_TYRE)/dh|) y distancia del nodo deslizante a su raíl
    # en la pose del JBeam (el juego lo lleva al raíl al crear el vehículo)
    strut_motion_ratio: float | None = None
    strut_motion_ratio_range: tuple[float, float] | None = None
    rail_offset_jbeam: float | None = None
    rail: tuple[str, str, str] | None = None  # (deslizante, extremo mangueta, chasis)
    # G2.44 (``rigid_axle``): enlaces (nodo carrocería, nodo eje, J_CAR, J_AXLE) con los
    # puntos relativos al centro del eje en ejes AC, y posición lateral del muelle como
    # fracción de la semivía (ATTACH_REL_POS)
    axle_links: list[tuple[str, str, Vec, Vec]] = field(default_factory=list)
    attach_rel_pos: float | None = None

    @property
    def is_axle(self) -> bool:
        return self.kind == "rigid_axle"

    @property
    def is_dwb(self) -> bool:
        return self.kind in DWB_KINDS

    @property
    def is_strut(self) -> bool:
        return self.kind == "strut_rail"


def _num(beam: Any, key: str, variables: dict, default: Any = None) -> Any:
    v = beam.value(key, variables, default)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


# --------------------------------------------------------------- G2.17: sondas
BUMP_SEARCH = 0.30  # m: recorrido máximo buscado hasta un tope
BUMP_STEP = 0.002  # m: paso de la búsqueda (después, bisección)


def dihedral(p1: Vec, p2: Vec, p3: Vec, p4: Vec) -> float:
    """Ángulo diedro (rad) entre los planos (p1, p2, p3) y (p2, p3, p4) alrededor
    del eje p2→p3 (fórmula estándar con atan2). Es el ángulo de una barra de
    torsión de 4 nodos (``torsionbars`` de BeamNG: id1–id2 brazo, id2–id3 eje,
    id3–id4 brazo)."""
    b1, b2, b3 = _sub(p2, p1), _sub(p3, p2), _sub(p4, p3)

    def cross(a: Vec, b: Vec) -> Vec:
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def dot(a: Vec, b: Vec) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    n1, n2 = cross(b1, b2), cross(b2, b3)
    nb = _norm(b2)
    m1 = cross(n1, (b2[0] / nb, b2[1] / nb, b2[2] / nb))
    return math.atan2(dot(m1, n2), dot(n1, n2))


def _travel_to_length(
    k: Knuckle, beam: tuple[Vec, str], target: float, direction: int
) -> float | None:
    """Recorrido vertical ``dy`` (m, + = compresión) desde el reposo (pose inicial del
    modelo) en el que la viga ``beam`` mide ``target``, buscando en el sentido
    ``direction`` (+1 arriba, −1 abajo). None si no se alcanza en ``BUMP_SEARCH``."""
    p = [0.0] * 6
    prev_dy, prev_f = 0.0, length_along(k, p, beam) - target
    if prev_f == 0:
        return 0.0
    dy = 0.0
    while abs(dy) < BUMP_SEARCH:
        dy += direction * BUMP_STEP
        p, _ = solve(k, dy, p)
        f = length_along(k, p, beam) - target
        if (f > 0) != (prev_f > 0):
            lo, hi, plo = prev_dy, dy, p
            for _ in range(40):
                mid = (lo + hi) / 2
                pm, _ = solve(k, mid, plo)
                fm = length_along(k, pm, beam) - target
                if (fm > 0) == (prev_f > 0):
                    lo, plo = mid, pm
                else:
                    hi = mid
            return (lo + hi) / 2
        prev_dy, prev_f = dy, f
    return None


def analyse_corner(
    *,
    wheel: str,
    hub: tuple[str, str],
    side: int,
    nodes: dict[str, Any],
    rigid_beams: list[Any],
    spring: Any | None,
    damper: Any | None,
    variables: dict,
    bumpstop: Any | None = None,
    torsionbars: Iterable[Any] = (),
    rails: Iterable[tuple[str, str, str]] = (),
    torsion: dict[str, Any] | None = None,
) -> LinkageResult:
    """Análisis de una rueda. ``side`` = −1 derecha (x < 0 en BeamNG), +1 izquierda
    (se refleja). ``rigid_beams``: vigas NORMAL rígidas de todo el vehículo.

    G2.17 (opcionales): ``bumpstop`` (viga BOUNDED con ``*BoundRange``) y
    ``torsionbars`` (vigas de 4 nodos) para medir, con el DWB elegido, el recorrido
    hasta los topes y la relación ángulo/recorrido de las barras de torsión.

    G2.19 (opcional): ``rails`` = (nodo deslizante, extremo 1, extremo 2) de los
    ``slidenodes`` con ``fixToRail``; si las barras solas no guían la mangueta (rango
    < 5) y un raíl con un extremo en la mangueta y otro en la carrocería la completa,
    se analiza como puntal en raíl (:func:`_strut_corner`).

    ``torsion`` (eje torsional virtualizado por :mod:`src.extract.coupled_axle`): su
    ``dropped`` = (nodo de este semieje, nodo móvil del otro) de las vigas descartadas;
    si su distancia no se conserva en el barrido simétrico el eje sigue ``coupled``;
    si se conserva, el DWB resultante se marca ``dwb_coupled``."""
    if side not in (-1, 1):
        raise ValueError("side debe ser −1 (derecha) o +1 (izquierda)")

    def pos(n: str) -> Vec:
        p = beamng_to_ac(nodes[n].pos)
        return (-p[0], p[1], p[2]) if side > 0 else p

    hubset = set(hub)
    knuckle = {
        a if b in hubset else b
        for a, b in (bm.nodes for bm in rigid_beams)
        if (a in hubset) != (b in hubset)
    }
    body = knuckle | hubset
    if not knuckle:
        return LinkageResult(wheel, "unsupported", "sin mangueta rígida unida al buje")
    if spring is None:
        return LinkageResult(wheel, "unsupported", "sin muelle identificado")
    excl = {frozenset(spring.nodes)}
    if damper is not None:
        excl.add(frozenset(damper.nodes))
    links: list[LinkData] = []
    coupled: list[str] = []
    for bm in rigid_beams:
        a, b = bm.nodes
        if frozenset((a, b)) in excl:
            continue
        for k, o in ((a, b), (b, a)):
            if k in knuckle and o not in body:
                if pos(o)[0] >= 0:
                    coupled.append(o)
                pc = _num(bm, "beamPrecompression", variables, 1)
                st = _num(bm, "beamSpring", variables, None)
                links.append(
                    LinkData(
                        o,
                        k,
                        math.dist(nodes[o].pos, nodes[k].pos),
                        pc if isinstance(pc, float) else 1.0,
                        st if isinstance(st, float) else None,
                    )
                )
    coupled_result = LinkageResult(
        wheel,
        "coupled",
        "eje acoplado: enlaces de la mangueta al otro lado o al plano medio "
        f"({', '.join(sorted(set(coupled))[:4])}); no es una rueda independiente",
        links,
    )
    on = [n for n in spring.nodes if n in body]
    off = [n for n in spring.nodes if n not in body]
    if len(on) != 1 or len(off) != 1:
        return LinkageResult(
            wheel,
            "unsupported",
            f"el muelle {spring.nodes} no va de la mangueta a la carrocería",
            links,
        )
    outer, inner = sorted(hub, key=lambda n: abs(pos(n)[0]), reverse=True)
    pts = {n: pos(n) for n in body}
    pts["_C"] = tuple(  # type: ignore[assignment]
        (a + b) / 2 for a, b in zip(pos(hub[0]), pos(hub[1]))
    )
    stiff = [lk.stiffness for lk in links]
    top = max((s for s in stiff if s is not None), default=1.0)
    full = Knuckle(
        pts,
        "_C",
        (outer, inner),
        [(pos(lk.chassis), lk.upright) for lk in links],
        [lk.rest_length for lk in links],
        [math.sqrt((s if s is not None else top) / top) for s in stiff],
    )
    eig = constraint_eigen(full)
    rk = rank(eig)
    if coupled and rk < 5:
        return coupled_result
    if rk < 5:
        strut_rails = [
            (sl, a if a in body else b, b if a in body else a)
            for sl, a, b in rails
            if sl in knuckle and ((a in body) != (b in body))
        ]
        if strut_rails:
            return _strut_corner(
                wheel,
                full,
                links,
                strut_rails,
                pos,
                body,
                nodes,
                spring,
                damper,
                off,
                on,
                variables,
                bumpstop,
                torsionbars,
                rk,
            )
        return LinkageResult(
            wheel,
            "underconstrained",
            f"rango {rk} de los enlaces rígidos ({len(links)}): la mangueta no la "
            "guían solo barras (p. ej. puntal en raíl); sin equivalente DWB",
            links,
            rk,
        )
    p_rest, res = solve(full, 0.0)
    travels = _travels()
    ref = sweep(full, p_rest, travels)
    if coupled and _really_coupled(coupled, body, nodes, full, ref):
        return coupled_result
    dev_dropped = 0.0
    if torsion is not None:
        bad = _reflected_deviation(
            torsion.get("dropped", ()), body, nodes, pos, full, ref
        )
        if bad is None or bad > EQUIV_M:
            return coupled_result
        dev_dropped = bad
    rest_pts = {n: full.moved(p_rest, n) for n in body}
    rest_pts["_C"] = full.moved(p_rest, "_C")
    by_upright: dict[str, list[int]] = {}
    for i, lk in enumerate(links):
        by_upright.setdefault(lk.upright, []).append(i)
    # candidatos de rango 5, del mejor condicionado al peor (y por nombre): el primero
    # dentro de la tolerancia es el elegido y no hace falta barrer los demás (G2.29:
    # mismo resultado que evaluarlos todos; con 17 barras hay ~12 000 subconjuntos)
    cands: list[tuple] = []
    for u1, u2 in itertools.combinations(sorted(by_upright), 2):
        for a1 in itertools.combinations(by_upright[u1], 2):
            for a2 in itertools.combinations(by_upright[u2], 2):
                for rod in range(len(links)):
                    if rod in a1 or rod in a2:
                        continue
                    sub = list(a1) + list(a2) + [rod]
                    kn = Knuckle(
                        rest_pts,
                        "_C",
                        (outer, inner),
                        [(pos(links[i].chassis), links[i].upright) for i in sub],
                    )
                    e = constraint_eigen(kn)
                    if rank(e) != 5:
                        continue
                    names = tuple(f"{links[i].chassis}-{links[i].upright}" for i in sub)
                    cands.append(
                        (-(e[1] / e[-1]), names, sub, kn, (u1, a1), (u2, a2), rod)
                    )
    cands.sort(key=lambda c: c[:2])
    best: tuple | None = None
    for ncond, names, sub, kn, ua1, ua2, rod in cands:
        sw = sweep(kn, [0.0] * 6, travels)
        c0s, t0s, x0s = sw[0.0][:3]
        c0r, t0r, x0r = ref[0.0][:3]
        ddeg = max(
            max(
                abs((sw[d][0] - c0s) - (ref[d][0] - c0r)),
                abs((sw[d][1] - t0s) - (ref[d][1] - t0r)),
            )
            for d in travels
        )
        dm = max(abs((sw[d][2] - x0s) - (ref[d][2] - x0r)) for d in travels)
        # dentro de la tolerancia todos son equivalentes: el mejor condicionado
        # (2.º autovalor / máximo); fuera, el más cercano
        norm_dev = max(ddeg / EQUIV_DEG, dm / EQUIV_M)
        key = (0, ncond, names) if norm_dev <= 1 else (1, norm_dev, names)
        if best is None or key < best[0]:
            best = (key, sub, kn, ddeg, dm, ua1, ua2, rod)
        if norm_dev <= 1:
            break
    if best is None:
        return LinkageResult(
            wheel,
            "overconstrained" if len(links) > 5 else "unsupported",
            f"{len(links)} enlaces de rango {rk} sin ningún subconjunto de doble "
            "triángulo (dos vértices con dos barras y una barra) de rango 5",
            links,
            rk,
            res,
        )
    _, sub, kn, ddeg, dm, (u1, a1), (u2, a2), rod = best
    within = ddeg <= EQUIV_DEG and dm <= EQUIV_M
    fitted: tuple[Knuckle, float, float, list[float]] | None = None
    if not within and len(links) > 5:
        fitted = _fit_dwb(kn, full, ref, travels)
    if len(links) == 5 and within:
        kind, reason = (
            "dwb_5link",
            "doble triángulo de 5 barras (2 triángulos + 1 barra)",
        )
    elif within:
        kind = "dwb_equivalent"
        reason = (
            f"{len(links)} barras de rango {rk}; el doble triángulo elegido "
            "reproduce la "
            f"trayectoria del conjunto completo en ±{TRAVEL * 1000:.0f} mm a "
            f"{ddeg:.4f}° y {dm * 1000:.4f} mm (tolerancia {EQUIV_DEG}°, "
            f"{EQUIV_M * 1000:.1f} mm)"
        )
    elif fitted is not None:
        kind = "dwb_fitted"
        sub_dev = (ddeg, dm)
        kn, ddeg, dm, fit_move = fitted
        reason = (
            f"{len(links)} barras de rango {rk} (residuo en reposo {res * 1000:.3f} "
            "mm): BeamNG sigue el equilibrio elástico del conjunto completo y el "
            f"mejor subconjunto de 5 barras se desvía {sub_dev[0]:.3f}° / "
            f"{sub_dev[1] * 1000:.3f} mm; doble triángulo con los 5 puntos del chasis "
            f"ajustados (≤ {max(fit_move) * 1000:.0f} mm) que reproduce esa "
            f"trayectoria "
            f"en ±{TRAVEL * 1000:.0f} mm a {ddeg:.4f}° y {dm * 1000:.4f} mm. Los "
            "puntos son equivalentes, no los anclajes físicos"
        )
    else:
        kind = "overconstrained"
        reason = (
            f"{len(links)} barras de rango {rk} "
            f"(residuo en reposo {res * 1000:.3f} mm); "
            f"el mejor doble triángulo se desvía {ddeg:.3f}° / {dm * 1000:.3f} mm en "
            f"±{TRAVEL * 1000:.0f} mm "
            f"(tolerancia {EQUIV_DEG}°, {EQUIV_M * 1000:.1f} mm): "
            "elegir un equivalente es una decisión"
        )
    if torsion is not None and kind in ("dwb_5link", "dwb_equivalent", "dwb_fitted"):
        reason = (
            "eje torsional: cada semieje gira alrededor de la rótula "
            f"{torsion['pivot']} "
            f"(anclajes fijos {torsion['pivot']} y {torsion['other_pivot']}); "
            f"{len(torsion.get('dropped', ()))} vigas a nodos móviles del otro lado "
            f"conservan su distancia al reflejado a {dev_dropped * 1000:.4f} mm en "
            f"±{TRAVEL * 1000:.0f} mm (recorrido simétrico). " + reason
        )
        kind = "dwb_coupled"
    out = LinkageResult(wheel, kind, reason, links, rk, res, ddeg, dm)
    out.dwb_links = [(links[i].chassis, links[i].upright) for i in sub]
    cam, toe = wheel_angles(full, p_rest)
    out.rest_camber_deg, out.rest_toe_deg = cam, toe
    centre = rest_pts["_C"]
    (top_u, top_a), (bot_u, bot_a) = sorted(
        ((u1, a1), (u2, a2)), key=lambda ua: -rest_pts[ua[0]][1]
    )

    def front_rear(idx: tuple[int, ...]) -> tuple[int, int]:
        f, r = sorted(idx, key=lambda i: -pos(links[i].chassis)[2])  # mayor z = delante
        return f, r

    tf, tr = front_rear(top_a)
    bf, br = front_rear(bot_a)
    rod_lk = links[rod]
    # punto del chasis de cada barra del DWB elegido (el ajustado en ``dwb_fitted``)
    chassis_xyz = {i: kn.links[j][0] for j, i in enumerate(sub)}
    mapping = {
        "upper_chassis_front": links[tf].chassis,
        "upper_chassis_rear": links[tr].chassis,
        "lower_chassis_front": links[bf].chassis,
        "lower_chassis_rear": links[br].chassis,
        "upper_upright": top_u,
        "lower_upright": bot_u,
        "rod_chassis": rod_lk.chassis,
        "rod_upright": rod_lk.upright,
    }
    chassis_idx = {
        "upper_chassis_front": tf,
        "upper_chassis_rear": tr,
        "lower_chassis_front": bf,
        "lower_chassis_rear": br,
        "rod_chassis": rod,
    }
    out.anchor_nodes = dict(mapping)
    out.anchors = {
        k: _sub(
            chassis_xyz[chassis_idx[k]] if k in chassis_idx else rest_pts[n], centre
        )
        for k, n in mapping.items()
    }
    if fitted is not None:
        out.fit_displacement = [
            math.dist(chassis_xyz[i], pos(links[i].chassis)) for i in sub
        ]
        out.redundant_link_force, out.redundant_link_rate = _redundant_links(
            full, links, p_rest
        )
    # palanca del muelle y del amortiguador en el DWB elegido (reposo = pose inicial)
    sp_ref = (pos(off[0]), on[0])
    zero = [0.0] * 6
    out.spring_motion_ratio = motion_ratio(kn, sp_ref, zero)
    mrs = []
    for d in (-MR_TRAVEL, MR_TRAVEL):
        pd, _ = solve(kn, d, zero)
        mrs.append(motion_ratio(kn, sp_ref, pd, d))
    out.spring_motion_ratio_range = (min(mrs), max(mrs))
    if damper is not None:
        don = [n for n in damper.nodes if n in body]
        doff = [n for n in damper.nodes if n not in body]
        if len(don) == 1 and len(doff) == 1:
            dref = (pos(doff[0]), don[0])
            out.damper_motion_ratio = motion_ratio(kn, dref, zero)
            # G2.17: ¿se acorta el amortiguador cuando la rueda sube?
            pu, _ = solve(kn, 0.001, zero)
            pd, _ = solve(kn, -0.001, zero)
            out.damper_shortens_on_bump = length_along(kn, pu, dref) < length_along(
                kn, pd, dref
            )
    jl = math.dist(nodes[spring.nodes[0]].pos, nodes[spring.nodes[1]].pos)
    rng = _num(spring, "precompressionRange", variables, None)
    pc = _num(spring, "beamPrecompression", variables, 1)
    if isinstance(rng, float):
        rest_len = jl + rng  # stage2.lua: beamPrecompression = (L + r)/L
    elif isinstance(pc, float):
        rest_len = jl * pc
    else:
        rest_len = None
    if rest_len is not None:
        out.spring_rest_compression = rest_len - length_along(kn, zero, sp_ref)
    # barra: delante o detrás del eje y desplazamiento lateral del punto del chasis
    out.rod_ahead_of_axle = rest_pts[rod_lk.upright][2] > centre[2]
    base_links = list(kn.links)
    rod_pos = len(sub) - 1

    def toe_with_shift(dx: float) -> float:
        c, u = base_links[rod_pos]
        kn.links = base_links[:rod_pos] + [((c[0] + dx, c[1], c[2]), u)]
        p, _ = solve(kn, 0.0, zero)
        return wheel_angles(kn, p)[1]

    dtoe = toe_with_shift(0.001) - toe_with_shift(-0.001)
    out.rod_shift_per_toe_deg = 0.002 / abs(dtoe) if abs(dtoe) > 1e-9 else None
    # G2.17: con signo (dx > 0 = hacia dentro; convergencia + = toe-in) y, para
    # contrastar el primer orden, el desplazamiento EXACTO que gira la mangueta del
    # reposo la convergencia de reposo (Newton sobre el modelo)
    out.rod_inward_shift_per_toe_in = 0.002 / dtoe if abs(dtoe) > 1e-9 else None
    if out.rod_inward_shift_per_toe_in is not None and out.rest_toe_deg is not None:
        toe0 = toe_with_shift(0.0)
        x = out.rest_toe_deg * out.rod_inward_shift_per_toe_in
        for _ in range(8):
            x -= (toe_with_shift(x) - toe0 - out.rest_toe_deg) * (
                out.rod_inward_shift_per_toe_in
            )
        out.rod_inward_shift_exact = x
    kn.links = base_links
    _probe_bumpstop(out, kn, pos, body, bumpstop, variables, nodes)
    _probe_torsion(out, kn, pos, body, torsionbars)
    return out


def _really_coupled(
    far: list[str], body: set[str], nodes: dict[str, Any], k: Knuckle, sw: dict
) -> bool:
    """¿Los enlaces a nodos del otro lado acoplan las dos ruedas? (G2.29)

    Un enlace de esta mangueta a un nodo ``o`` del otro lado solo acopla si ``o`` se
    mueve con la OTRA rueda. Con el vehículo simétrico, ``o`` pertenece al cuerpo de
    la otra rueda si y solo si su reflejado pertenece a este; y se mueve si su
    reflejado se mueve en el barrido de esta rueda (hecho con ``o`` fijo: la hipótesis
    se comprueba a sí misma). Ejemplo: brazo semitirado cuyos dos pivotes van fijos al
    chasis con vigas a ambos lados (reflejado inmóvil ⇒ no acopla); eje rígido (los
    nodos del tubo se mueven ⇒ acopla). Un nodo en el plano medio acopla siempre
    (criterio conservador anterior).

    shortcut: un nodo lejano sin reflejado en esta mangueta se toma por chasis; un eje
    rígido cuyo tubo quede a más de una viga rígida del buje no se detectaría aquí
    (en el corpus, los 3 ejes rígidos siguen «coupled»); si aparece, usar la
    partición suspendido/no suspendido de ``unsprung.py``."""
    for o in set(far):
        x, y, z = nodes[o].pos
        if abs(x) < 1e-3:
            return True
        mirror = [
            n for n in body if n in nodes and math.dist(nodes[n].pos, (-x, y, z)) < 1e-3
        ]
        for m in mirror:
            rest = k.moved(sw[0.0][3], m)
            if any(math.dist(k.moved(s[3], m), rest) > 1e-4 for s in sw.values()):
                return True
    return False


def _reflected_deviation(
    dropped: Iterable[tuple[str, str]],
    body: set[str],
    nodes: dict[str, Any],
    pos: Any,
    k: Knuckle,
    sw: dict,
) -> float | None:
    """Máx. variación (m) de la distancia entre un nodo ``near`` del semieje y el
    REFLEJADO de su pareja ``far`` del otro lado, a lo largo del barrido simétrico.

    Con recorrido simétrico el nodo ``far`` del otro lado es el reflejado (respecto al
    plano medio) de un nodo ``m`` de ESTE semieje, que se mueve con él: la viga
    ``near``–``far`` es entonces una distancia entre dos puntos del mismo cuerpo rígido
    (``near`` y el reflejado de ``m``). Si esa distancia no cambia, la viga no
    restringe el movimiento y se puede descartar. ``None`` si algún ``far`` no tiene
    reflejado en el semieje (no se puede comprobar)."""
    worst = 0.0
    for near, far in dropped:
        fx, fy, fz = pos(far)
        mirror = [
            n for n in body if n in nodes and math.dist(pos(n), (-fx, fy, fz)) < 1e-3
        ]
        if not mirror or near not in body:
            return None
        m = mirror[0]
        rest = sw[0.0][3]
        mp0 = k.moved(rest, m)
        base = math.dist(k.moved(rest, near), (-mp0[0], mp0[1], mp0[2]))
        for s in sw.values():
            mp = k.moved(s[3], m)
            d = math.dist(k.moved(s[3], near), (-mp[0], mp[1], mp[2]))
            worst = max(worst, abs(d - base))
    return worst


def _drop_redundant_bars(
    kn: Knuckle, links: list[LinkData]
) -> tuple[list[int], str] | None:
    """Brazos en H (p. ej. trasera MacPherson de Automation: dos pivotes del chasis a
    dos puntos de la mangueta, 4 barras). Si sobra una barra, busca el subconjunto de 3
    (brazo de 2 barras al mismo nodo + bieleta) que con el raíl sigue dando rango 5 y
    cuya barra omitida mantiene su longitud en ±``TRAVEL`` dentro de ``EQUIV_M`` (es
    decir, es cinemáticamente redundante, no una restricción distinta). Devuelve los
    índices conservados y una nota con la barra omitida y su variación; ``None`` si
    ninguno cumple. Orden determinista; gana la menor variación."""
    best = None
    for keep in itertools.combinations(range(len(links)), 3):
        groups: dict[str, int] = {}
        for i in keep:
            groups[links[i].upright] = groups.get(links[i].upright, 0) + 1
        if sorted(groups.values()) != [1, 2]:
            continue
        sub = Knuckle(
            kn.points,
            kn.centre,
            kn.axis,
            [kn.links[i] for i in keep],
            [kn.lengths[i] for i in keep],
            [kn.weights[i] for i in keep],
            kn.rails,
        )
        if rank(constraint_eigen(sub)) != 5:
            continue
        p_rest, _ = solve(sub, 0.0)
        out = [i for i in range(len(links)) if i not in keep]

        def length(p: list[float], i: int, sub: Knuckle = sub) -> float:
            c, u = kn.links[i]
            return _norm(_sub(sub.moved(p, u), c))

        ref = {i: length(p_rest, i) for i in out}
        dev = 0.0
        for _, _, _, pose in sweep(sub, p_rest, _travels()).values():
            dev = max(dev, max(abs(length(pose, i) - ref[i]) for i in out))
        if dev <= EQUIV_M and (best is None or dev < best[0]):
            i = out[0]
            lk = links[i]
            best = (
                dev,
                list(keep),
                f"; barra redundante omitida {lk.chassis}→{lk.upright} "
                f"(variación {dev * 1000:.4f} mm en ±{TRAVEL * 1000:.0f} mm, "
                f"{(ref[i] - kn.lengths[i]) * 1000:+.3f} mm de la longitud del JBeam "
                f"en reposo)",
            )
    return None if best is None else (best[1], best[2])


def _strut_corner(
    wheel: str,
    full: Knuckle,
    links: list[LinkData],
    rails: list[tuple[str, str, str]],
    pos: Any,
    body: set[str],
    nodes: dict[str, Any],
    spring: Any,
    damper: Any | None,
    off: list[str],
    on: list[str],
    variables: dict,
    bumpstop: Any | None,
    torsionbars: Iterable[Any],
    rank_links: int,
) -> LinkageResult:
    """G2.19: puntal en raíl (MacPherson) y su equivalente ``STRUT`` de AC.

    BeamNG: el nodo deslizante (mangueta) debe quedar en la recta extremo-en-la-mangueta
    → extremo-en-la-carrocería (``rails`` + ``slidenodes`` con ``fixToRail``): el eje
    de la mangueta pasa siempre por el apoyo superior. AC [C] (``SuspensionStrut::
    attach``): cuerpo del puntal con rótula en ``STRUT_CAR`` y deslizadera SIN giro
    hacia el buje a lo largo de ``STRUT_CAR → STRUT_TYRE``; dos barras
    ``WBCAR_BOTTOM_FRONT/REAR`` → ``WBTYRE_BOTTOM`` y la barra ``WBCAR_STEER`` →
    ``WBTYRE_STEER``. Mismas restricciones (2 del raíl + 3 barras): misma cinemática,
    con ``STRUT_TYRE`` = nodo deslizante en reposo.

    Exige exactamente un raíl y 3 barras: dos al MISMO nodo de la mangueta (brazo
    inferior) y una a otro (bieleta)."""
    if len(rails) != 1:
        return LinkageResult(
            wheel,
            "unsupported",
            f"{len(rails)} raíles en la mangueta",
            links,
            rank_links,
        )
    slide, end_k, end_c = rails[0]
    kn = Knuckle(
        full.points,
        full.centre,
        full.axis,
        list(full.links),
        list(full.lengths),
        list(full.weights),
        [(slide, end_k, pos(end_c))],
    )
    rk = rank(constraint_eigen(kn))
    redundant_note = ""
    if rk == 5 and len(links) > 3:
        picked = _drop_redundant_bars(kn, links)
        if picked is not None:
            keep, redundant_note = picked
            links = [links[i] for i in keep]
            full = Knuckle(
                full.points,
                full.centre,
                full.axis,
                [full.links[i] for i in keep],
                [full.lengths[i] for i in keep],
                [full.weights[i] for i in keep],
            )
            kn = Knuckle(
                full.points,
                full.centre,
                full.axis,
                list(full.links),
                list(full.lengths),
                list(full.weights),
                [(slide, end_k, pos(end_c))],
            )
            rk = rank(constraint_eigen(kn))
    by_upright: dict[str, list[int]] = {}
    for i, lk in enumerate(links):
        by_upright.setdefault(lk.upright, []).append(i)
    arms = [u for u, idx in by_upright.items() if len(idx) == 2]
    rods = [u for u, idx in by_upright.items() if len(idx) == 1]
    if rk != 5 or len(links) != 3 or len(arms) != 1 or len(rods) != 1:
        return LinkageResult(
            wheel,
            "unsupported",
            f"raíl + {len(links)} barras de rango {rk}: no es un puntal "
            "(brazo inferior de 2 barras + bieleta)",
            links,
            rk,
        )
    a0 = pos(end_k)
    d0 = _sub(pos(end_c), a0)
    r0 = _sub(pos(slide), a0)
    cr = (
        r0[1] * d0[2] - r0[2] * d0[1],
        r0[2] * d0[0] - r0[0] * d0[2],
        r0[0] * d0[1] - r0[1] * d0[0],
    )
    offset = _norm(cr) / _norm(d0)
    p_rest, res = solve(kn, 0.0)
    rest_pts = {n: kn.moved(p_rest, n) for n in body}
    centre = kn.moved(p_rest, "_C")
    arm_u, rod_u = arms[0], rods[0]
    bf, br = sorted(
        (links[i].chassis for i in by_upright[arm_u]), key=lambda n: -pos(n)[2]
    )
    rod_lk = links[by_upright[rod_u][0]]
    mapping = {
        "strut_chassis": end_c,
        "strut_upright": slide,
        "lower_chassis_front": bf,
        "lower_chassis_rear": br,
        "lower_upright": arm_u,
        "rod_chassis": rod_lk.chassis,
        "rod_upright": rod_lk.upright,
    }
    out = LinkageResult(
        wheel,
        "strut_rail",
        f"puntal en raíl: {slide} desliza en {end_k}–{end_c} (eje de la mangueta "
        f"por el apoyo superior), brazo inferior {bf}/{br}→{arm_u} y bieleta "
        f"{rod_lk.chassis}→{rod_lk.upright}; rango 5 (2 del raíl + 3 barras); "
        f"{slide} está a {offset * 1000:.2f} mm del raíl en la pose del JBeam"
        + redundant_note,
        links,
        rk,
        res,
    )
    out.rail = (slide, end_k, end_c)
    out.rail_offset_jbeam = offset
    out.anchor_nodes = dict(mapping)
    out.anchors = {
        k: _sub(pos(n) if "chassis" in k else rest_pts[n], centre)
        for k, n in mapping.items()
    }
    out.rest_camber_deg, out.rest_toe_deg = wheel_angles(kn, p_rest)
    # la mangueta se reexpresa en su pose de reposo (con el raíl ya cumplido) para que
    # p = 0 sea el reposo, como en el resto del análisis
    pts = dict(full.points)
    pts.update(rest_pts)
    pts["_C"] = centre
    rk_kn = Knuckle(
        pts,
        "_C",
        full.axis,
        list(full.links),
        list(full.lengths),
        list(full.weights),
        [(slide, end_k, pos(end_c))],
    )
    zero = [0.0] * 6
    sp_ref = (pos(off[0]), on[0])
    st_ref = (pos(end_c), slide)
    out.spring_motion_ratio = motion_ratio(rk_kn, sp_ref, zero)
    out.strut_motion_ratio = motion_ratio(rk_kn, st_ref, zero)
    mrs, srs = [], []
    for d in (-MR_TRAVEL, MR_TRAVEL):
        pd, _ = solve(rk_kn, d, zero)
        mrs.append(motion_ratio(rk_kn, sp_ref, pd, d))
        srs.append(motion_ratio(rk_kn, st_ref, pd, d))
    out.spring_motion_ratio_range = (min(mrs), max(mrs))
    out.strut_motion_ratio_range = (min(srs), max(srs))
    if damper is not None:
        don = [n for n in damper.nodes if n in body]
        doff = [n for n in damper.nodes if n not in body]
        if len(don) == 1 and len(doff) == 1:
            dref = (pos(doff[0]), don[0])
            out.damper_motion_ratio = motion_ratio(rk_kn, dref, zero)
            pu, _ = solve(rk_kn, 0.001, zero)
            pdn, _ = solve(rk_kn, -0.001, zero)
            out.damper_shortens_on_bump = length_along(rk_kn, pu, dref) < length_along(
                rk_kn, pdn, dref
            )
    jl = math.dist(nodes[spring.nodes[0]].pos, nodes[spring.nodes[1]].pos)
    rng = _num(spring, "precompressionRange", variables, None)
    pc = _num(spring, "beamPrecompression", variables, 1)
    if isinstance(rng, float):
        rest_len = jl + rng  # stage2.lua: beamPrecompression = (L + r)/L
    elif isinstance(pc, float):
        rest_len = jl * pc
    else:
        rest_len = None
    if rest_len is not None:
        out.spring_rest_compression = rest_len - length_along(rk_kn, zero, sp_ref)
    out.rod_ahead_of_axle = rest_pts[rod_lk.upright][2] > centre[2]
    base_links = list(rk_kn.links)
    rod_pos = by_upright[rod_u][0]

    def toe_with_shift(dx: float) -> float:
        c, u = base_links[rod_pos]
        rk_kn.links = list(base_links)
        rk_kn.links[rod_pos] = ((c[0] + dx, c[1], c[2]), u)
        p, _ = solve(rk_kn, 0.0, zero)
        return wheel_angles(rk_kn, p)[1]

    dtoe = toe_with_shift(0.001) - toe_with_shift(-0.001)
    out.rod_shift_per_toe_deg = 0.002 / abs(dtoe) if abs(dtoe) > 1e-9 else None
    out.rod_inward_shift_per_toe_in = 0.002 / dtoe if abs(dtoe) > 1e-9 else None
    if out.rod_inward_shift_per_toe_in is not None and out.rest_toe_deg is not None:
        toe0 = toe_with_shift(0.0)
        x = out.rest_toe_deg * out.rod_inward_shift_per_toe_in
        for _ in range(8):
            x -= (toe_with_shift(x) - toe0 - out.rest_toe_deg) * (
                out.rod_inward_shift_per_toe_in
            )
        out.rod_inward_shift_exact = x
    rk_kn.links = base_links
    _probe_bumpstop(out, rk_kn, pos, body, bumpstop, variables, nodes)
    _probe_torsion(out, rk_kn, pos, body, torsionbars)
    return out


def _probe_bumpstop(
    out: LinkageResult,
    kn: Knuckle,
    pos: Any,
    body: set[str],
    beam: Any | None,
    variables: dict,
    nodes: dict[str, Any],
) -> None:
    """G2.17: recorrido vertical de la rueda hasta los topes con el DWB elegido.

    BeamNG (``stage2.lua``; comentario de la plantilla del exportador): el tope
    empieza a actuar cuando la viga mide la longitud de creación (pose del JBeam, sin
    precompresión) ∓ ``shortBoundRange``/``longBoundRange``. El banco de G2.17 lo
    contrasta (``output/g217_suspension_steering_2026-11-04/banco``)."""
    if beam is None:
        out.bumpstop_reason = "sin viga de tope identificada"
        return
    a, b = beam.nodes
    on = [n for n in (a, b) if n in body]
    off = [n for n in (a, b) if n not in body]
    if len(on) != 1 or len(off) != 1:
        out.bumpstop_reason = (
            f"el tope {beam.nodes} no va de la mangueta a la carrocería"
        )
        return
    ref = (pos(off[0]), on[0])
    spawn = math.dist(nodes[a].pos, nodes[b].pos)
    zero = [0.0] * 6
    up, _ = solve(kn, 0.001, zero)
    dn, _ = solve(kn, -0.001, zero)
    shortens_up = length_along(kn, up, ref) < length_along(kn, dn, ref)
    reasons = []
    for key, target in (
        ("shortBoundRange", lambda r: spawn - r),
        ("longBoundRange", lambda r: spawn + r),
    ):
        rng = _num(beam, key, variables, None)
        if not isinstance(rng, float) or rng <= 0:
            reasons.append(f"{key} ausente o no positivo")
            continue
        wheel_up = (key == "shortBoundRange") == shortens_up
        direction = 1 if wheel_up else -1
        dy = _travel_to_length(kn, ref, target(rng), direction)
        if dy is None:
            reasons.append(f"{key}: no se alcanza en ±{BUMP_SEARCH} m")
            continue
        pd, _ = solve(kn, dy, zero)
        mr = motion_ratio(kn, ref, pd, dy)
        if wheel_up:
            out.bumpstop_travel_up, out.bumpstop_motion_ratio_up = dy, mr
        else:
            out.bumpstop_travel_down, out.bumpstop_motion_ratio_down = -dy, mr
    out.bumpstop_reason = "; ".join(reasons)


def _probe_torsion(
    out: LinkageResult,
    kn: Knuckle,
    pos: Any,
    body: set[str],
    bars: Iterable[Any],
) -> None:
    """G2.17: |dθ/dh| de cada barra de torsión con UN extremo (id1 o id4) en esta
    mangueta y el eje (id2–id3) fuera de ella; el otro extremo y el eje, quietos en la
    pose del JBeam. θ = diedro (``dihedral``)."""
    zero = [0.0] * 6
    for bar in bars:
        ns = list(bar.nodes)
        if len(ns) != 4:
            continue
        ends = [i for i in (0, 3) if ns[i] in body]
        if len(ends) != 1 or any(n in body for n in ns[1:3]):
            continue
        i = ends[0]

        def angle(dy: float) -> float:
            p, _ = solve(kn, dy, zero)
            pts = [kn.moved(p, ns[j]) if j == i else pos(ns[j]) for j in range(4)]
            return dihedral(*pts)

        h = 0.001
        try:
            rate = abs(angle(h) - angle(-h)) / (2 * h)
            rest = angle(0.0)
        except KeyError:
            # barra con un nodo sin definir (p. ej. bisexual_aspiration: fsb2r): BeamNG
            # no la crea, así que no aporta relación de recorrido
            continue
        out.torsion_angle_rates.append(
            {"nodes": ns, "end": ns[i], "rad_per_m": rate, "rest_angle_rad": rest}
        )


def rigid_link_beams(beams: Iterable[Any], variables: dict) -> list[Any]:
    """Vigas ``NORMAL`` de dos nodos con ``beamSpring`` ≥ ``RIGID_LINK`` (o sin él)."""
    out = []
    for b in beams:
        if len(b.nodes) != 2:
            continue
        t = str(b.props.get("beamType", "|NORMAL")).replace(" ", "").lstrip("|")
        if (t.upper() or "NORMAL") != "NORMAL":
            continue
        s = b.value("beamSpring", variables, None)
        if not isinstance(s, (int, float)) or isinstance(s, bool) or s >= RIGID_LINK:
            out.append(b)
    return out
