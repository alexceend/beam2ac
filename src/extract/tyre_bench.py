"""Banco de neumáticos de BeamNG → fuerzas de contacto por rueda (G2.20).

El rozamiento del neumático de BeamNG lo calcula el C++ del juego en cada nodo de la
banda
(``stage2.lua`` → ``obj:setNode``); la curva fuerza–deslizamiento EMERGE de la
estructura de
vigas de ``pressureWheels`` y no hay fórmula legible (G2.19 §3). Este módulo solo REDUCE
las
medidas del banco ``beam2acTyre.lua`` (``output/g220_2026-11-07/banco/``): no ajusta ni
inventa.

Método de medida (banco virtual, coche completo y SIN modificar, sobre smallgrid):

* la carrocería se sujeta con fuerzas externas proporcionales a la masa (velocidad V,
deriva
  de la carrocería β, rumbo fijo) aplicadas «a la altura del suelo» (sin transferencia
  de
  carga);
* el par de cada rueda se sustituye en cada paso de física (velocidad angular impuesta);
* ``S`` = Σ getBeamStress·unidad(getBeamVectorFromNode(viga, nodo de W)) sobre las vigas
que
  cruzan la frontera del conjunto rotatorio W (banda + buje): fuerza de las vigas SOBRE
  W
  (tracción positiva), el método de ``lua/vehicle/extensions/tech/wheelForces.lua``.

Equilibrio medio de W (aceleración media nula en régimen; pares de propulsión y freno =
pares
puros): S + m_W·g⃗ + F_c = 0  ⇒  **F_c = −S + m_W·g·ẑ** (comprobado: Σ F_c,z = M·g a
2·10⁻⁴ en reposo rodando; Σ F_c horizontal = −fuerza del control).

Marco de la rueda (suelo plano, normal ẑ): eje a = unidad(node2 − node1); rumbo h =
horizontal
⊥ a, con el sentido de la velocidad del buje; lateral l = ẑ × h (a la izquierda de h).

    Fx = F_c·h,  Fy = F_c·l,  Fz = F_c·ẑ
    α  = atan2(v·l, v·h)            (deriva; v = velocidad del centro del buje)
    κ  = (ω·r − v·h)/|v·h|          (r = radio de rodadura libre medido, o el que se
    pida)
    γ  = asin(a·ẑ)                  (inclinación del eje; signo según el lado en el
    análisis)

Con esta convención, una deriva positiva da Fy negativa (fuerza contra el
deslizamiento).
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from typing import Any, Iterable

G = 9.81


class TyreBenchError(ValueError):
    """Registro de banco incompleto o incoherente."""


@dataclass(frozen=True)
class WheelSample:
    seg: int
    seg_name: str
    t: float
    wheel: str
    axle: str
    fx: float
    fy: float
    fz: float
    slip_angle: float  # rad
    vx: float
    vy: float
    omega: float  # rad/s (positivo hacia delante)
    camber_axis: float  # rad, asin(a·ẑ)
    torque: float
    kappa_cmd: float
    hub_height: float  # centro del buje − z mínima de la banda (m)
    pressure: float  # Pa absolutos (grupo de presión)
    kload: float
    v_cmd: float
    beta_cmd: float  # rad

    def slip_ratio(self, radius: float) -> float:
        return (self.omega * radius - self.vx) / abs(self.vx) if self.vx else 0.0


def _vec(r: dict[str, str], key: str) -> tuple[float, float, float]:
    return (float(r[key + "x"]), float(r[key + "y"]), float(r[key + "z"]))


def reduce_rows(
    meta: dict[str, Any], rows: Iterable[dict[str, str]]
) -> list[WheelSample]:
    wheels = meta.get("wheels") or []
    if not wheels:
        raise TyreBenchError("meta sin ruedas")
    segs = meta.get("segments") or []
    out: list[WheelSample] = []
    for r in rows:
        seg = int(r["seg"])
        name = segs[seg - 1]["name"] if 0 < seg <= len(segs) else str(seg)
        for i, w in enumerate(wheels, start=1):
            s = _vec(r, f"S{i}")
            m = float(w["mass"])
            f = (-s[0], -s[1], -s[2] + m * G)
            a = _vec(r, f"a{i}")
            na = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
            a = (a[0] / na, a[1] / na, a[2] / na)
            h = (-a[1], a[0])
            nh = math.hypot(*h)
            h = (h[0] / nh, h[1] / nh)
            v = _vec(r, f"v{i}")
            if v[0] * h[0] + v[1] * h[1] < 0:
                h = (-h[0], -h[1])
            lat = (-h[1], h[0])
            vx = v[0] * h[0] + v[1] * h[1]
            vy = v[0] * lat[0] + v[1] * lat[1]
            p = _vec(r, f"p{i}")
            out.append(
                WheelSample(
                    seg=seg,
                    seg_name=name,
                    t=float(r["t"]),
                    wheel=w["name"],
                    axle=w["axle"],
                    fx=f[0] * h[0] + f[1] * h[1],
                    fy=f[0] * lat[0] + f[1] * lat[1],
                    fz=f[2],
                    slip_angle=math.atan2(vy, vx),
                    vx=vx,
                    vy=vy,
                    omega=float(r[f"om{i}"]),
                    camber_axis=math.asin(max(-1.0, min(1.0, a[2]))),
                    torque=float(r[f"T{i}"]),
                    kappa_cmd=float(r[f"k{i}"]),
                    hub_height=p[2] - float(r[f"zt{i}"]),
                    pressure=float(r[f"pr{i}"]),
                    kload=float(r["kload"]),
                    v_cmd=float(r["Vcmd"]),
                    beta_cmd=float(r["betacmd"]),
                )
            )
    return out


def read_csv_text(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def static_check(
    meta: dict[str, Any], samples: list[WheelSample], seg_name: str
) -> dict:
    """Cierre vertical en un segmento de rodadura libre: Σ F_z frente al peso."""
    rows: dict[float, float] = {}
    for s in samples:
        if s.seg_name == seg_name:
            rows[s.t] = rows.get(s.t, 0.0) + s.fz
    if not rows:
        raise TyreBenchError(f"sin muestras en {seg_name}")
    mean = sum(rows.values()) / len(rows)
    weight = float(meta["totalMass"]) * G
    return {"sum_fz": mean, "weight": weight, "rel_error": mean / weight - 1.0}


# ---------------------------------------------------------------------------- resumen
def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """y = a + b·x → (a, b, rms del residuo)."""
    n = len(xs)
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        raise TyreBenchError("regresión sin variación en x")
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    rms = math.sqrt(sum((y - a - b * x) ** 2 for x, y in zip(xs, ys)) / n)
    return a, b, rms


def segment_tag(name: str) -> tuple[str, str]:
    """``lat_base`` → (lat, base); ``comb_b5`` → (comb, b5); ``rf2_kp`` → (rf2, kp)."""
    kind, _, tag = name.partition("_")
    return kind, tag


SWEEP_KINDS = ("lat", "lon", "comb")
SKIP_S = 0.3  # transitorio al empezar cada segmento
TRANSITIONS = (
    "in",
    "out",
)  # comb_in/comb_out: rampas de β entre barridos, no son medidas


def free_radius(samples: list[WheelSample]) -> dict[tuple[str, str], float]:
    """r_libre por (rueda, condición): media de v_x/ω en la 2.ª mitad de rf_/rf2_<tag>
    (par 0); ``settle`` cuenta como la condición ``base``."""
    acc: dict[tuple[str, str], list[float]] = {}
    durs: dict[str, float] = {}
    for s in samples:
        durs[s.seg_name] = max(durs.get(s.seg_name, 0.0), s.t)
    for s in samples:
        kind, tag = segment_tag(s.seg_name)
        if s.seg_name == "settle":
            kind, tag = "rf", "base"
        if kind not in ("rf", "rf2") or s.omega < 1 or s.t < durs[s.seg_name] / 2:
            continue
        acc.setdefault((s.wheel, tag), []).append(s.vx / s.omega)
    return {k: _mean(v) for k, v in acc.items()}


def binned_points(
    samples: list[WheelSample],
    rfree: dict[tuple[str, str], float],
    alpha_bin_deg: float = 0.5,
    kappa_bin: float = 0.01,
) -> list[dict[str, Any]]:
    """Medias por (rueda, barrido, intervalo de α o κ) de los barridos lat_/lon_/comb_.

    Ida y vuelta de cada barrido caen en los mismos intervalos (se promedia el retraso).
    κ con el radio libre de la condición; ``fy`` en la convención de AC (= −Fy del
    banco).
    """
    groups: dict[tuple, list[WheelSample]] = {}
    for s in samples:
        kind, tag = segment_tag(s.seg_name)
        if kind not in SWEEP_KINDS or tag in TRANSITIONS or s.t < SKIP_S:
            continue
        r = rfree.get((s.wheel, tag)) or rfree.get((s.wheel, "base"))
        if r is None:
            raise TyreBenchError(f"sin radio libre para {s.wheel}/{tag}")
        if kind == "lat":
            b = round(math.degrees(s.slip_angle) / alpha_bin_deg)
        else:
            b = round(s.slip_ratio(r) / kappa_bin)
        groups.setdefault((s.axle, s.wheel, kind, tag, b), []).append(s)
    out = []
    for (axle, wheel, kind, tag, b), ss in sorted(groups.items()):
        r = rfree.get((wheel, tag)) or rfree[(wheel, "base")]
        out.append(
            {
                "axle": axle,
                "wheel": wheel,
                "kind": kind,
                "tag": tag,
                "n": len(ss),
                "fz": _mean([s.fz for s in ss]),
                "slip_angle_deg": math.degrees(_mean([s.slip_angle for s in ss])),
                "slip_ratio": _mean([s.slip_ratio(r) for s in ss]),
                "camber_axis_deg": math.degrees(_mean([s.camber_axis for s in ss])),
                "speed": _mean([s.vx for s in ss]),
                "fx": _mean([s.fx for s in ss]),
                "fy": -_mean([s.fy for s in ss]),
                "pressure_pa": _mean([s.pressure for s in ss]),
            }
        )
    return out


def vertical_static(samples: list[WheelSample]) -> dict[str, dict[str, float]]:
    """Rigidez vertical rodando: escalones de ``load_steps`` (2.ª mitad de cada
    escalón);
    k = −dFz/dh por rueda (h = centro del buje − z mínima de la banda)."""
    by: dict[str, dict[float, list[WheelSample]]] = {}
    seg = [s for s in samples if s.seg_name == "load_steps"]
    if not seg:
        raise TyreBenchError("sin segmento load_steps")
    dur = max(s.t for s in seg)
    step = dur / 4
    for s in seg:
        i = min(3, int(s.t / step))
        if s.t - i * step < step / 2:
            continue
        by.setdefault(s.wheel, {}).setdefault(i, []).append(s)
    out = {}
    for wheel, levels in by.items():
        hs = [_mean([s.hub_height for s in v]) for _, v in sorted(levels.items())]
        fz = [_mean([s.fz for s in v]) for _, v in sorted(levels.items())]
        a, b, rms = _linreg(hs, fz)
        out[wheel] = {
            "stiffness": -b,
            "h_at_zero_load": -a / b,
            "rms_n": rms,
            "fz_levels": fz,
            "h_levels": hs,
        }
    return out


def _lockin(ts: list[float], xs: list[float], w: float) -> complex:
    """Amplitud compleja de x(t) ≈ a0 + A·sin(ωt) + B·cos(ωt): A + iB (mínimos
    cuadrados)."""
    from src.ac.tyre_fit import _solve_linear  # mismo eliminador (sin numpy)

    X = [(1.0, math.sin(w * t), math.cos(w * t)) for t in ts]
    A = [[sum(x[i] * x[j] for x in X) for j in range(3)] for i in range(3)]
    B = [sum(x[i] * y for x, y in zip(X, xs)) for i in range(3)]
    _, a, b = _solve_linear(A, B)
    return complex(a, b)


def vertical_dynamic(
    meta: dict[str, Any], samples: list[WheelSample], seg_name: str
) -> dict:
    """Rigidez y amortiguación verticales por detección síncrona en un segmento
    ``vosc``.

    Con la excitación senoidal de frecuencia f (ω = 2πf) se ajustan las componentes en
    fase
    y en cuadratura de Fz y de la altura del buje h (el rizado del paso de los nodos de
    la
    banda, ≫ f, queda fuera). Equilibrio de W con aceleración: F_c = F_medida − m_W·z̈ y
    z̈ = −ω²·h ⇒ F̂_c = F̂ + m_W·ω²·ĥ. Neumático lineal F̂_c = −(k + iωc)·ĥ:
    k = −Re(F̂_c/ĥ), c = −Im(F̂_c/ĥ)/ω."""
    masses = {w["name"]: float(w["mass"]) for w in meta["wheels"]}
    freq = None
    for seg in meta.get("segments") or []:
        if seg.get("name") == seg_name and seg.get("vosc"):
            freq = float(seg["vosc"]["f"])
    if freq is None:
        raise TyreBenchError(f"{seg_name}: sin excitación vosc en el protocolo")
    w = 2 * math.pi * freq
    by: dict[str, list[WheelSample]] = {}
    for s in samples:
        if s.seg_name == seg_name and s.t >= 0.5:
            by.setdefault(s.wheel, []).append(s)
    out = {}
    for wheel, ss in by.items():
        if len(ss) < 20:
            raise TyreBenchError(f"{seg_name}: pocas muestras")
        ts = [s.t for s in ss]
        f_hat = _lockin(ts, [s.fz for s in ss], w)
        h_hat = _lockin(ts, [s.hub_height for s in ss], w)
        fc = f_hat + masses[wheel] * w * w * h_hat
        z = fc / h_hat
        out[wheel] = {
            "stiffness": -z.real,
            "damping": -z.imag / w,
            "freq": freq,
            "force_amplitude": abs(f_hat),
            "height_amplitude": abs(h_hat),
            "n": len(ss),
        }
    return out


def rolling_resistance(samples: list[WheelSample]) -> dict[str, dict[str, Any]]:
    """−Fx/Fz en rodadura libre (2.ª mitad de settle, rf_v10, rf_v30) por eje."""
    acc: dict[tuple[str, float], list[float]] = {}
    durs: dict[str, float] = {}
    for s in samples:
        durs[s.seg_name] = max(durs.get(s.seg_name, 0.0), s.t)
    for s in samples:
        if (
            s.seg_name not in ("settle", "rf_v10", "rf_v30")
            or s.t < durs[s.seg_name] / 2
        ):
            continue
        acc.setdefault((s.axle, round(s.v_cmd)), []).append(-s.fx / s.fz)
    out: dict[str, dict[str, Any]] = {}
    for (axle, v), xs in sorted(acc.items()):
        out.setdefault(axle, {"points": []})["points"].append([float(v), _mean(xs)])
    return out
