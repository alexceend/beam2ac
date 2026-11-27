"""Ajuste del modelo SCTM de AC a curvas de neumático medidas en BeamNG (G2.20).

Entrada: muestras por rueda de un eje (las dos ruedas juntas) con carga, deriva,
deslizamiento, caída, velocidad y fuerzas (``src/extract/tyre_bench.py``; convención:
deriva positiva ⇒ Fy negativa, κ > 0 ⇒ Fx > 0). Salida: ``SctmParams`` y el error del
ajuste. El modelo es ``src/ac/tyre_sctm.solve`` (transcripción de AC).

Qué se ajusta y cómo (procedimiento determinista; sin aleatoriedad):

* ``FZ0`` NO se ajusta: es la carga de referencia de la parametrización (la carga
estática
  medida de la rueda, ``reference_load``); fija dónde valen D_REF y
  FRICTION_LIMIT_ANGLE.
* Mínimos cuadrados de (F_modelo − F_medida)/Fz (en unidades de μ) con
Levenberg–Marquardt
  y jacobiano por diferencias centrales; límites por proyección. Se ajustan JUNTOS los
  barridos laterales y longitudinales porque AC comparte FRICTION_LIMIT_ANGLE, FLEX_GAIN
  y
  la caída (FALLOFF_*) entre Fx y Fy.
* ``COMBINED_FACTOR`` se ajusta solo si hay barridos combinados; si no, 2 (el valor por
  defecto de neumático de AC) y queda como supuesto.

Caída: el modelo recibe la caída de AC de cada rueda (``Point.camber``), con el convenio
de
``ksCalcCamberRAD`` [C]: γ_AC = −asin(eje·n), deriva = atan(−v_y/|v_x|); con CAMBER_GAIN
> 0
el empuje va hacia donde se inclina la parte alta de la rueda. Para una caída física c
(negativa = parte alta hacia dentro), γ_AC = −c en la rueda izquierda y +c en la derecha
(:func:`ac_camber`). ``CAMBER_GAIN`` se ajusta con los barridos laterales;
``DCAMBER_0/1``
no (la caída del banco varía con la carga y no se separa de la sensibilidad a la carga):
quedan los valores internos de AC (0,1 y −0,8 si faltan) y se usan tal cual en el
ajuste.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from operator import mul
from typing import Iterable, Sequence

from src.ac.tyre_sctm import SctmParams, solve

# (nombre, mínimo, máximo)
LATERAL = (
    ("dy_ref", 0.2, 4.0),
    ("ls_expy", 0.3, 1.2),
    ("friction_limit_angle", 0.5, 40.0),
    ("flex_gain", -0.95, 3.0),
    ("falloff_level", 0.2, 1.0),
    ("falloff_speed", 0.0, 100.0),
    ("speed_sensitivity", 0.0, 0.1),
    ("camber_gain", -1.0, 3.0),
)
LONGITUDINAL = (
    ("dx_ref", 0.2, 4.0),
    ("ls_expx", 0.3, 1.2),
    ("cx_mult", 0.2, 8.0),
    ("brake_dx_mod", -0.6, 1.0),
)
COMBINED = (("combined_factor", 1.0, 6.0),)


@dataclass(frozen=True)
class Point:
    """Una muestra de una rueda. ``kind``: lat | lon | comb (solo informativo)."""

    kind: str
    fz: float
    slip_angle: float  # rad
    slip_ratio: float
    camber: float  # rad, caída de AC (ver ac_camber)
    speed: float
    fx: float
    fy: float  # convención de AC: positiva con deriva positiva (= −Fy del banco)


@dataclass
class FitResult:
    params: SctmParams
    rms: dict[str, float]  # error cuadrático medio en μ por tipo de muestra
    max_abs: dict[str, float]
    n: dict[str, int]
    iterations: int
    fitted: list[str]
    fixed: dict[str, float] = field(default_factory=dict)


def ac_camber(physical_camber: float, side: str) -> float:
    """Caída de AC (rad) de una rueda con caída física ``physical_camber`` (negativa =
    parte
    alta hacia dentro) en el lado ``side`` (``left`` | ``right``)."""
    if side == "left":
        return -physical_camber
    if side == "right":
        return physical_camber
    raise ValueError(f"lado desconocido: {side!r}")


def model(p: SctmParams, pt: Point) -> tuple[float, float]:
    fy, fx = solve(
        p, pt.fz, pt.slip_angle, pt.slip_ratio, camber=pt.camber, speed=pt.speed
    )
    return fy, fx


def _residuals(p: SctmParams, pts: Sequence[Point]) -> list[float]:
    """``(F_modelo − F_medida)/Fz`` de cada muestra (``lat`` → Fy, ``lon`` → Fx,
    ``comb`` → ambas).

    Es :func:`src.ac.tyre_sctm.solve` (con ``u`` = 1 y ``pressure_ratio`` = 0)
    desenrollado: el ajuste lo evalúa decenas de millones de veces, así que lo que solo
    depende de ``p`` se calcula una vez y cada muestra calcula solo la fuerza que usa.
    Mismo orden de operaciones (resultado idéntico bit a bit;
    ``tests/test_tyre_fit_residuals.py``). Cambiar ``solve`` obliga a cambiar esto."""
    sin, cos, tan, sqrt = math.sin, math.cos, math.tan, math.sqrt
    fz0, cg, ssens = p.fz0, p.camber_gain, p.speed_sensitivity
    asy, fs, cx_mult = p.falloff_level, p.falloff_speed, p.cx_mult
    cfac = p.combined_factor
    circle = cfac <= 0.0 or cfac == 2.0
    ms0, ms1 = p.max_slip0, p.max_slip1
    brake = p.brake_dx_mult
    c0, c1 = p.dcamber
    ls_expy, ls_expx = p.ls_expy, p.ls_expx
    ls_y = p.ls_mult(p.dy_ref, ls_expy)
    dx_ref = p.dx_ref if p.dx_ref != 0.0 else p.dy_ref * 1.2
    ls_x = p.ls_mult(dx_ref, ls_expx)

    out: list[float] = []
    add = out.append
    for pt in pts:
        kind, load = pt.kind, pt.fz
        want_y = kind == "lat" or kind == "comb"
        want_x = kind == "lon" or kind == "comb"
        sa, sr_in, camber = pt.slip_angle, pt.slip_ratio, pt.camber
        slip = 0.0
        if load > 0.0 and not (sa == 0.0 and sr_in == 0.0 and camber == 0.0):
            v8 = sin(camber) * cg + sa
            sr = sr_in if sr_in > -0.99998999 else -0.9999949
            v42 = sr + 1.0
            v43 = sr_in / v42
            v44 = tan(v8) / v42
            if circle:
                slip = sqrt(v44 * v44 + v43 * v43)
            else:
                slip = (abs(v44) ** cfac + abs(v43) ** cfac) ** (1.0 / cfac)
        fy = fx = 0.0
        if slip != 0.0:
            speed = pt.speed
            v37 = (
                sqrt((speed * sin(sa)) ** 2 + (cos(sa) * (speed * sr_in)) ** 2) * ssens
                + 1.0
            )
            ms = (load - fz0) / fz0 * (ms1 - ms0) + ms0
            peak = 3.0 / ((1.0 / ms) * 3.0 * 78.125 * 2.0 * 0.0064)
            if want_y:
                v20 = (load**ls_expy) * ls_y / load
                v23 = abs(camber)
                if (camber < 0.0 or v8 < 0.0) and (camber > 0.0 or v8 > 0.0):
                    v23 = -v23
                v24 = -v23
                v28 = v24 * c0 - v24 * v24 * c1
                if v28 <= -1.0:
                    v28 = -0.9
                v38 = (v20 + (v20 / (v28 + 1.0) - v20) * 1.0) / v37
                if peak < slip:
                    shape = (1.0 / ((slip - peak) * fs + 1.0)) * (1.0 - asy) + asy
                else:
                    x = slip / peak
                    shape = (1.0 - x) * (1.0 - x) * (3.0 / peak * slip) + (
                        3.0 - x * 2.0
                    ) * (x * x)
                fy = shape * v38 * (v44 / slip) * load
            if want_x:
                v21 = (load**ls_expx) * ls_x / load
                if sr_in < 0.0:
                    v21 *= brake
                v39 = v21 / v37
                px = peak / cx_mult
                if px < slip:
                    shape = (1.0 / ((slip - px) * fs + 1.0)) * (1.0 - asy) + asy
                else:
                    x = slip / px
                    shape = (1.0 - x) * (1.0 - x) * (3.0 / px * slip) + (
                        3.0 - x * 2.0
                    ) * (x * x)
                fx = (v43 / slip) * (shape * v39) * load
        if want_y:
            add((fy - pt.fy) / load)
        if want_x:
            add((fx - pt.fx) / load)
    return out


# parámetros que no cambian ninguna fuerza de un tipo de muestra puro; solo vale si
# ``dx_ref`` se ajusta (nunca es 0): con ``dx_ref`` = 0, ``dy_ref`` también mueve Fx
_UNAFFECTED = {
    "dy_ref": "lon",
    "ls_expy": "lon",
    "dx_ref": "lat",
    "ls_expx": "lat",
    "cx_mult": "lat",
    "brake_dx_mod": "lat",
}


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-300:
            raise ZeroDivisionError("matriz singular")
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                if f:
                    for k in range(c, n + 1):
                        m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def levenberg_marquardt(
    base: SctmParams,
    names: Sequence[tuple[str, float, float]],
    pts: Sequence[Point],
    max_iter: int = 120,
) -> tuple[SctmParams, int]:
    lo = [x[1] for x in names]
    hi = [x[2] for x in names]
    keys = [x[0] for x in names]

    def build(theta: list[float]) -> SctmParams:
        return base.with_(**dict(zip(keys, theta)))

    def clamp(theta: list[float]) -> list[float]:
        return [min(max(t, a), b) for t, a, b in zip(theta, lo, hi)]

    theta = clamp([getattr(base, k) for k in keys])
    # por tipo puro: (muestras restantes, posición de sus residuos en el vector)
    rows: dict[str, tuple[list[Point], list[int]]] = {}
    for skip in ("lat", "lon"):
        kept: list[Point] = []
        where: list[int] = []
        at = 0
        for pt in pts:
            size = 2 if pt.kind == "comb" else int(pt.kind in ("lat", "lon"))
            if pt.kind != skip:
                kept.append(pt)
                where.extend(range(at, at + size))
            at += size
        rows[skip] = (kept, where)
    r = _residuals(build(theta), pts)
    cost = sum(map(mul, r, r))
    lam = 1e-3
    it = 0
    for it in range(1, max_iter + 1):
        cols = []
        for j in range(len(theta)):
            h = 1e-5 * max(1.0, abs(theta[j]))
            tp, tm = theta[:], theta[:]
            tp[j] = min(theta[j] + h, hi[j])
            tm[j] = max(theta[j] - h, lo[j])
            d = tp[j] - tm[j]
            skip = _UNAFFECTED.get(keys[j]) if "dx_ref" in keys else None
            sub_pts, where = rows[skip] if skip else (pts, None)
            rp = _residuals(build(tp), sub_pts)
            rm = _residuals(build(tm), sub_pts)
            col = [(a - b) / d for a, b in zip(rp, rm)]
            if where is not None:  # el resto de la columna es exactamente 0
                full = [0.0] * len(r)
                for i, v in zip(where, col):
                    full[i] = v
                col = full
            cols.append(col)
        n = len(theta)
        jtj = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i, n):  # simétrica: ci·cj == cj·ci bit a bit
                jtj[i][j] = jtj[j][i] = sum(map(mul, cols[i], cols[j]))
        jtr = [sum(map(mul, cols[i], r)) for i in range(n)]
        improved = False
        for _ in range(12):
            a = [row[:] for row in jtj]
            for i in range(n):
                a[i][i] += lam * (jtj[i][i] if jtj[i][i] > 0 else 1.0)
            try:
                step = _solve_linear(a, [-x for x in jtr])
            except ZeroDivisionError:
                lam *= 10
                continue
            cand = clamp([t + s for t, s in zip(theta, step)])
            rc = _residuals(build(cand), pts)
            cc = sum(map(mul, rc, rc))
            if cc < cost:
                rel = (cost - cc) / max(cost, 1e-30)
                theta, r, cost = cand, rc, cc
                lam = max(lam / 3, 1e-9)
                improved = True
                break
            lam *= 4
        if not improved or rel < 1e-10:
            break
    return build(theta), it


def _stats(p: SctmParams, pts: Sequence[Point]) -> tuple[dict, dict, dict]:
    acc: dict[str, list[float]] = {}
    for pt in pts:
        fy, fx = model(p, pt)
        if pt.kind in ("lat", "comb"):
            acc.setdefault(pt.kind + "_y", []).append((fy - pt.fy) / pt.fz)
        if pt.kind in ("lon", "comb"):
            acc.setdefault(pt.kind + "_x", []).append((fx - pt.fx) / pt.fz)
    rms = {k: math.sqrt(sum(x * x for x in v) / len(v)) for k, v in acc.items()}
    mx = {k: max(abs(x) for x in v) for k, v in acc.items()}
    n = {k: len(v) for k, v in acc.items()}
    return rms, mx, n


def initial_guess(pts: Iterable[Point], fz0: float) -> SctmParams:
    lat = [p for p in pts if p.kind == "lat"]
    lon = [p for p in pts if p.kind == "lon"]

    def peak(seq, attr_f, attr_s):
        best = max(seq, key=lambda q: abs(getattr(q, attr_f)) / q.fz)
        return abs(getattr(best, attr_f)) / best.fz, abs(getattr(best, attr_s))

    muy, a = peak(lat, "fy", "slip_angle") if lat else (1.0, math.radians(6))
    mux, k = peak(lon, "fx", "slip_ratio") if lon else (1.1 * muy, 0.1)
    fla = max(1.0, math.degrees(a))
    cx = max(0.3, math.tan(math.radians(fla)) / max(k / (1 + k), 1e-3))
    return SctmParams(
        fz0=fz0,
        dy_ref=muy,
        dx_ref=mux,
        ls_expy=0.85,
        ls_expx=0.85,
        friction_limit_angle=fla,
        flex_gain=0.0,
        falloff_level=0.8,
        falloff_speed=2.0,
        cx_mult=cx,
        brake_dx_mod=0.0,
        speed_sensitivity=0.0,
        combined_factor=2.0,
    )


def fit(
    pts: Sequence[Point], fz0: float, fit_combined: bool | None = None
) -> FitResult:
    """Ajusta los parámetros SCTM; ``fz0`` = carga de referencia medida (N)."""
    if not pts:
        raise ValueError("sin muestras")
    base = initial_guess(pts, fz0)
    names = list(LATERAL) + list(LONGITUDINAL)
    has_lat = any(p.kind == "lat" for p in pts)
    has_lon = any(p.kind == "lon" for p in pts)
    if not (has_lat and has_lon):
        raise ValueError("hacen falta barridos laterales y longitudinales")
    pure = [p for p in pts if p.kind in ("lat", "lon")]
    # 1) lateral y longitudinal puros
    p1, it1 = levenberg_marquardt(base, names, pure)
    it = it1
    fitted = [n for n, _, _ in names]
    comb = [p for p in pts if p.kind == "comb"]
    if fit_combined is None:
        fit_combined = bool(comb)
    if fit_combined and comb:
        p1, it2 = levenberg_marquardt(p1, COMBINED, comb)
        it += it2
        fitted.append("combined_factor")
    rms, mx, n = _stats(p1, pts)
    return FitResult(p1, rms, mx, n, it, fitted, {"fz0": fz0})


# ---------------------------------------------------------------------------- dataset →
# ajuste
DATASET_COLUMNS = (
    "wheel",
    "kind",
    "condition",
    "n",
    "fz",
    "slip_angle",
    "slip_ratio",
    "camber_axis",
    "speed",
    "fx",
    "fy",
    "pressure",
)
_CACHE: dict[str, FitResult] = {}


def wheel_side(name: str) -> str:
    """Lado de una rueda por su nombre de BeamNG (FL/FR/RL/RR del exportador)."""
    if name in ("FL", "RL"):
        return "left"
    if name in ("FR", "RR"):
        return "right"
    raise ValueError(f"nombre de rueda sin lado conocido: {name!r}")


def free_rolling_slip(
    rows: Sequence[Sequence],
) -> dict[tuple[str, str], list[tuple[int, float]]]:
    """κ de giro libre MEDIDO por (rueda, condición) y deriva (grados enteros), solo con
    las filas ``lat`` (barrido de deriva sin orden de κ: la rueda rueda libre).

    Con la rueda inclinada, el giro libre a deriva α no está en κ = 0 sino en
    κ₀(α, rueda)
    (el banco de G2.31 mide −0,0008…0,018 a 5°; atribuirlo al efecto cónico de la caída
    es una inferencia, lo medido es el desfase). Es una propiedad de la REFERENCIA de κ
    (radio de rodadura libre a α = 0), no del agarre."""
    acc: dict[tuple[str, str], dict[int, list[float]]] = {}
    for r in rows:
        if r[1] == "lat":
            acc.setdefault((r[0], r[2]), {}).setdefault(round(r[5]), []).append(r[6])
    return {
        k: sorted((a, sum(v) / len(v)) for a, v in b.items()) for k, b in acc.items()
    }


def _interp(tab: Sequence[tuple[int, float]], a: float) -> float:
    if a <= tab[0][0]:
        return tab[0][1]
    if a >= tab[-1][0]:
        return tab[-1][1]
    for (x0, y0), (x1, y1) in zip(tab, tab[1:]):
        if x0 <= a <= x1:
            return y0 + (y1 - y0) * (a - x0) / (x1 - x0)
    raise AssertionError("tabla de giro libre desordenada")


def points_from_dataset(data: dict, free_reference: bool = False) -> list[Point]:
    """Muestras del IR (``tyres.axles.<eje>.force_slip_samples``) → puntos del ajuste.

    ``free_reference``: κ' = κ − κ₀(α) con κ₀ el giro libre medido de la MISMA rueda
    a la misma deriva (:func:`free_rolling_slip`; el barrido combinado ``b5`` usa la
    condición
    ``base``). Solo usa filas ``lat``: nunca el error del modelo."""
    cols = [c[0] for c in data.get("columns", [])]
    if tuple(cols) != DATASET_COLUMNS:
        raise ValueError(f"columnas inesperadas: {cols}")
    tabs = free_rolling_slip(data.get("rows", [])) if free_reference else {}
    out = []
    for r in data.get("rows", []):
        wheel, kind, cond, n, fz, a, k, cam, v, fx, fy, pr = r
        side = wheel_side(wheel)
        if free_reference:
            tab = tabs.get((wheel, "base" if cond == "b5" else cond)) or tabs.get(
                (wheel, "base")
            )
            if tab is None:
                raise ValueError(f"sin giro libre medido para {wheel}/{cond}")
            k = k - _interp(tab, a)
        out.append(
            Point(
                kind,
                float(fz),
                math.radians(a),
                float(k),
                ac_camber(math.radians(cam), side),
                float(v),
                float(fx),
                float(fy),
            )
        )
    return out


def fit_dataset(data: dict, fz0: float, free_reference: bool = False) -> FitResult:
    """:func:`fit` memorizado por la huella de las muestras, de ``fz0`` y de la
    referencia de κ (determinista)."""
    import hashlib
    import json

    key = hashlib.sha256(
        json.dumps(
            [data, fz0, free_reference], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if key not in _CACHE:
        _CACHE[key] = fit(points_from_dataset(data, free_reference), fz0)
    return _CACHE[key]


# clave de tyres.ini → campo de SctmParams
INI_FIELDS = {
    "DY_REF": "dy_ref",
    "DX_REF": "dx_ref",
    "LS_EXPY": "ls_expy",
    "LS_EXPX": "ls_expx",
    "FRICTION_LIMIT_ANGLE": "friction_limit_angle",
    "FLEX_GAIN": "flex_gain",
    "FALLOFF_LEVEL": "falloff_level",
    "FALLOFF_SPEED": "falloff_speed",
    "CX_MULT": "cx_mult",
    "BRAKE_DX_MOD": "brake_dx_mod",
    "SPEED_SENSITIVITY": "speed_sensitivity",
    "COMBINED_FACTOR": "combined_factor",
    "CAMBER_GAIN": "camber_gain",
}
