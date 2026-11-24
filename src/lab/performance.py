"""Prestaciones del tren motriz exportado: 0-100 km/h limitado por potencia y comparación con la ficha de origen.

Modelo (el de AC leído en AC, G2.27 §5; misma ecuación que ``output/g229_2026-11-11/ibiza/ideal.py``)::

    G      = marcha · FINAL
    rpm    = v / r · G · 30/π                      (r = RADIUS de neumático; sin deslizamiento)
    F_rueda = K_AIR · T(rpm) · G / r               (AC no tiene pérdidas de caja: rendimiento 1)
    a      = (F_rueda − R) / (M + [(I_motor + I_caja)·G² + ΣI_rueda] / r²)
    R      = M·g·crr + ½·ρ·CdA·v²                  (crr y CdA opcionales; 0 si no se dan)

Qué es y qué NO es: con ``mu=None`` el resultado es el tiempo LIMITADO POR POTENCIA, una cota inferior del 0-100
real (sin patinaje, embrague ni gestión del gas). Solo con ``mu`` se estima un techo de agarre (reparto estático de
carga, sin transferencia de carga), que sigue siendo una aproximación. Las hipótesis viajan en ``Result.assumptions``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from src.ac.car_validate import read_ini

G = 9.81
RHO_AIR = 1.225
K_AIR = 0.9797  # densidad de AC a 26 °C (G2.27 §5)
KMH = 1 / 3.6
FUEL_DENSITY = 0.74  # kg/L


class PerformanceInputError(ValueError):
    pass


@dataclass
class Spec:
    mass_kg: float
    torque_curve: list[tuple[float, float]]  # (rpm, N·m) del power.lut, ordenada
    gears: list[float]
    final: float
    limiter_rpm: float
    idle_rpm: float
    wheel_radius_m: float
    engine_inertia: float
    gearbox_inertia: float
    wheel_inertia_sum: float
    driven_load_fraction: float = (
        1.0  # fracción de la carga estática sobre las ruedas motrices
    )
    clutch_max_torque: float | None = (
        None  # [CLUTCH] MAX_TORQUE; None = sin tope conocido
    )


@dataclass
class Result:
    marks: dict[int, float]  # km/h -> s (solo los alcanzados)
    assumptions: list[str] = field(default_factory=list)
    limited_by: str = "potencia"


def _num(sec: dict[str, str], key: str, where: str) -> float:
    try:
        return float(sec[key])
    except (KeyError, ValueError):
        raise PerformanceInputError(f"falta o no es numérico {where} {key}") from None


def _gearbox_inertia(sec: dict[str, str]) -> float:
    """Ausente o 0 ⇒ AC usa 1,0 kg·m² (`Drivetrain` [C], investigacion_capacidades_2026-11-10)."""
    v = _num({"I": sec.get("INERTIA", "0")}, "I", "[GEARBOX] INERTIA")
    return v if v > 0 else 1.0


def _opt_float(v: str | None) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None


def read_spec(data_dir: str | Path) -> Spec:
    """Lee la física de una carpeta ``data/`` de AC (solo los INI que el modelo usa)."""
    d = Path(data_dir)
    try:
        car, eng, dt, tyr, susp = (
            read_ini(d / n)
            for n in (
                "car.ini",
                "engine.ini",
                "drivetrain.ini",
                "tyres.ini",
                "suspensions.ini",
            )
        )
        lut_text = (d / "power.lut").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise PerformanceInputError(f"falta {e.filename}") from None
    try:
        curve = sorted(
            (float(a), float(b))
            for a, b in (
                ln.split("|")[:2]
                for ln in lut_text.replace(" ", "").split()
                if "|" in ln
            )
        )
    except ValueError:
        raise PerformanceInputError("power.lut no numérico") from None
    if len(curve) < 2:
        raise PerformanceInputError("power.lut sin puntos suficientes")
    count = int(_num(dt.get("GEARS", {}), "COUNT", "[GEARS]"))
    gears = [_num(dt["GEARS"], f"GEAR_{k}", "[GEARS]") for k in range(1, count + 1)]
    radii = [_num(tyr.get(s, {}), "RADIUS", f"[{s}]") for s in ("FRONT", "REAR")]
    winertia = [
        _num(tyr.get(s, {}), "ANGULAR_INERTIA", f"[{s}]") for s in ("FRONT", "REAR")
    ]
    ttype = dt.get("TRACTION", {}).get("TYPE", "").upper()
    # masa que AC mueve: TOTALMASS (piloto, sin combustible) + combustible inicial + 2·HUB_MASS por eje (medida en AC del
    # Ibiza, 1336,9 kg = 1119,57 + 55 L·0,74 + 2·(49,15 + 39,2); G2.26 §«Masa en AC»)
    # un eje tipo AXLE aporta su HUB_MASS una sola vez (G2.14)
    hubs = sum(
        (1 if susp.get(x, {}).get("TYPE", "").upper() == "AXLE" else 2)
        * _num(susp.get(x, {}), "HUB_MASS", f"[{x}]")
        for x in ("FRONT", "REAR")
    )
    fuel_kg = FUEL_DENSITY * _num(
        {"F": car.get("FUEL", {}).get("FUEL", "0") or "0"}, "F", "[FUEL] FUEL"
    )
    cg = float(
        susp.get("BASIC", {}).get("CG_LOCATION") or 0.5
    )  # fracción delantera; solo afecta a ``mu``
    if min(radii) <= 0:
        raise PerformanceInputError("RADIUS de neumático no positivo")
    return Spec(
        mass_kg=_num(car.get("BASIC", {}), "TOTALMASS", "[BASIC]") + fuel_kg + hubs,
        torque_curve=curve,
        gears=gears,
        final=_num(dt["GEARS"], "FINAL", "[GEARS]"),
        limiter_rpm=_num(eng.get("ENGINE_DATA", {}), "LIMITER", "[ENGINE_DATA]"),
        idle_rpm=_num(eng.get("ENGINE_DATA", {}), "MINIMUM", "[ENGINE_DATA]"),
        wheel_radius_m=sum(radii) / 2,
        engine_inertia=_num(eng["ENGINE_DATA"], "INERTIA", "[ENGINE_DATA]"),
        gearbox_inertia=_gearbox_inertia(dt.get("GEARBOX", {})),
        wheel_inertia_sum=2 * (winertia[0] + winertia[1]),
        driven_load_fraction={"FWD": cg, "RWD": 1 - cg}.get(ttype, 1.0),
        clutch_max_torque=_opt_float(dt.get("CLUTCH", {}).get("MAX_TORQUE")),
    )


def _torque(curve: list[tuple[float, float]], rpm: float) -> float:
    if rpm <= curve[0][0]:
        return curve[0][1]
    for (r0, t0), (r1, t1) in zip(curve, curve[1:]):
        if rpm <= r1:
            return t0 + (t1 - t0) * (rpm - r0) / (r1 - r0)
    return 0.0


def launch_rpm(s: Spec) -> float:
    """Régimen de salida óptimo con el embrague patinando: el de mayor par transmisible (tope del embrague si se conoce)."""
    cap = s.clutch_max_torque if s.clutch_max_torque else math.inf
    cands = [
        (min(t, cap), -r) for r, t in s.torque_curve if s.idle_rpm <= r <= s.limiter_rpm
    ]
    return -max(cands)[1]


def _accel(
    s: Spec, v: float, g: int, crr: float, cda: float, slip_rpm: float | None = None
) -> float | None:
    ratio = s.gears[g] * s.final
    rpm = v / s.wheel_radius_m * ratio * 30 / math.pi
    if rpm > s.limiter_rpm:
        return None
    t_eng = _torque(s.torque_curve, max(rpm, s.idle_rpm))
    if slip_rpm and g == 0 and rpm < slip_rpm:
        # embrague patinando en 1.ª: el motor se mantiene en slip_rpm y transmite el par (≤ tope del embrague)
        t_eng = _torque(s.torque_curve, slip_rpm)
        if s.clutch_max_torque:
            t_eng = min(t_eng, s.clutch_max_torque / K_AIR)
    force = K_AIR * t_eng * ratio / s.wheel_radius_m
    resist = s.mass_kg * G * crr + 0.5 * RHO_AIR * cda * v * v
    m_eff = (
        s.mass_kg
        + ((s.engine_inertia + s.gearbox_inertia) * ratio**2 + s.wheel_inertia_sum)
        / s.wheel_radius_m**2
    )
    return (force - resist) / m_eff


def simulate(
    s: Spec,
    *,
    targets_kmh: tuple[int, ...] = (60, 100),
    shift_s: float = 0.15,
    mu: float | None = None,
    launch_slip: bool = False,
    crr: float = 0.0,
    cda: float = 0.0,
    dt: float = 0.001,
    t_max: float = 120.0,
) -> Result:
    """Integra gas a fondo eligiendo en cada paso la marcha de mayor aceleración (cambio = ``shift_s`` sin tracción)."""
    if dt <= 0 or s.mass_kg <= 0:
        raise PerformanceInputError("dt y masa deben ser positivos")
    slip_rpm = launch_rpm(s) if launch_slip else None
    cap = math.inf if mu is None else mu * G * s.driven_load_fraction
    v, t, gear, marks = 0.5, 0.0, 0, {}
    pending = sorted(targets_kmh)
    while pending and t < t_max:
        opts = [
            (a, k)
            for k in range(len(s.gears))
            if (a := _accel(s, v, k, crr, cda, slip_rpm)) is not None
        ]
        if not opts:
            break  # límite de régimen en la última marcha: no se alcanza más velocidad
        a, best = max(opts)
        if best != gear:
            gear, t = best, t + shift_s
        if a <= 0:
            break
        v, t = v + min(a, cap) * dt, t + dt
        while pending and v >= pending[0] * KMH:
            marks[pending.pop(0)] = round(t, 3)
    notes = [
        "rendimiento de transmisión 1 (AC no tiene pérdidas de caja)",
        f"K_AIR={K_AIR} sobre el par de power.lut",
        f"crr={crr}, CdA={cda} m²"
        + (" (sin resistencias)" if not (crr or cda) else ""),
        f"cambio {shift_s:g} s sin tracción; marcha de mayor aceleración; arranque "
        + (
            f"con embrague patinando en 1.ª a {slip_rpm:.0f} rpm (salida óptima)"
            if slip_rpm
            else "sin patinaje de embrague (rpm de ralentí)"
        ),
    ]
    if mu is not None:
        notes.append(
            f"techo de agarre μ={mu:g}·g·{s.driven_load_fraction:g}: carga estática, sin transferencia"
        )
    return Result(marks, notes, "potencia" if mu is None else "potencia y agarre")


def compare_with_reference(
    power_limited_s: float,
    ref_s: float,
    tol: float = 0.10,
    grip_s: float | None = None,
    best_launch_s: float | None = None,
) -> dict:
    """Clasifica la ficha frente al modelo. ``tol`` (10 %) es un umbral de plausibilidad, no calibrado.

    * ``inconsistente``: la ficha es más rápida que el límite de potencia del paquete (cota inferior física) ⇒
      el motor, las relaciones, la masa o el radio exportados no pueden dar esa prestación.
    * ``coherente``: dentro de ``tol`` del límite de potencia.
    * ``mas_lento_que_potencia``: la ficha es más lenta; explicable por agarre, cambios o embrague, NO atribuible
      sin un ensayo (si ``grip_s`` coincide dentro de ``tol`` se indica).
    """
    # `power_limited_s` = salida a ralentí (cota superior del rango); `best_launch_s` = salida óptima (cota inferior real).
    # El 0-100 depende mucho del régimen de salida (Quail: ~1 s por 1000 rpm): cualquier valor del rango es plausible.
    lower = best_launch_s if best_launch_s is not None else power_limited_s
    if ref_s < lower * (1 - tol):
        verdict = "inconsistente"
    elif ref_s <= power_limited_s * (1 + tol):
        verdict = "coherente"
    else:
        verdict = "mas_lento_que_potencia"
    out = {
        "veredicto": verdict,
        "potencia_s": power_limited_s,
        "salida_optima_s": best_launch_s,
        "ficha_s": ref_s,
        "relativo": ref_s / power_limited_s - 1,
    }
    if grip_s is not None:
        out["agarre_s"] = grip_s
        out["coincide_con_agarre"] = abs(ref_s - grip_s) <= tol * grip_s
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("data_dir")
    p.add_argument("--ref-0-100", type=float)
    p.add_argument("--mu", type=float)
    a = p.parse_args(argv)
    spec = read_spec(a.data_dir)
    base = simulate(spec)
    out = {"potencia": base.marks, "hipotesis": base.assumptions}
    out["potencia_salida_optima"] = simulate(spec, launch_slip=True).marks
    grip = simulate(spec, mu=a.mu, launch_slip=True) if a.mu else None
    if grip:
        out["agarre"] = grip.marks
    if a.ref_0_100 and 100 in base.marks:
        out["comparacion"] = compare_with_reference(
            base.marks[100],
            a.ref_0_100,
            grip_s=grip.marks.get(100) if grip else None,
            best_launch_s=out["potencia_salida_optima"].get(100),
        )
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
