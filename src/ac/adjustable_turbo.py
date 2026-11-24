"""Turbo/compresor con retardo y forma de boost de BeamNG: curva atmosférica + ``[TURBO_n]`` (2026-11-22).

Con esta transformación (sustituye a escribir el boost dentro de ``power.lut``):

* ``power.lut`` pasa a ser la curva SIN boost: par neto de BeamNG ÷ presión absoluta del mapa a gas a fondo
  (la misma construcción que ``camsoEngine.lua`` l. 62-81, ``(par − fricción) / peakPressureCurve``).
* ``engine.ini`` recibe 1 o 2 ``[TURBO_n]``. comportamiento de AC (turbo y motor):
  par = curva × (1 + Σ boost_i); ``boost_i = MAX_BOOST_i · rotación_i``; objetivo de la rotación =
  ``min(gas·rpm / REFERENCE_RPM, 1) ** GAMMA`` (0 con gas ≤ 0 o rpm ≤ 0); la rotación sigue al objetivo con un paso de
  primer orden por física (0,003 s): ``rot += (objetivo − rot)·min(dt·tasa, 1)``. motor de AC guarda
  ``tasa = (1 − LAG) · 1,3333334 · 333,33334``; por tanto ``LAG=0`` es instantáneo, no «sin movimiento».
* Una sola rampa potencial no sigue el mapa de BeamNG (el boost entra en S); :func:`fit_ramps` ajusta 1 rampa o, si el
  error de par supera ``FIT_TOL``, 2 (``[TURBO_0]`` + ``[TURBO_1]``).
* Lag: turbo (y ``both``) = el del coche exportado; compresor = 0 (no tiene lag propio). En ``camsoEngine.lua``
  (91392d70) l. 785 la presión es ``pressureSmoother:get(mapa)`` con ``newTemporalSmoothingNonLinear(5, 5)``
  (l. 1799; ``filters.lua``: ``st += dif·r·dt/(1+r·dt)``): primer orden, τ = 1/5 s igual al subir y al bajar. La
  simulación del turbo (``camsoTurbocharger.lua``) NO gobierna el par: l. 785 sobrescribe su ``inductionPressure``
  antes de leerlo (l. 824) y ``forcedInductionCoef`` solo va al flujo de datos.
* Ajustable desde la cabina (opcional): ``COCKPIT_ADJUSTABLE=1`` y ``WASTEGATE=MAX_BOOST``. el turbo de AC solo aplica
  el ajuste del jugador (``userSetting``) como tope ``WASTEGATE · userSetting`` sobre el boost: con ``WASTEGATE=0`` el
  ajuste no hacía nada. No ajustable: ``COCKPIT_ADJUSTABLE=0``, ``WASTEGATE=0``.

APROXIMACIONES: el objetivo depende de ``gas·rpm`` (a medio gas el boost de AC baja distinto que el mapa de BeamNG por
acelerador); ``DISPLAY_MAX_BOOST`` = boost total (no verificado cómo lo usa la interfaz con 2 secciones).
"""

from __future__ import annotations

import math
from typing import Any

from src.ac.tyre_compounds import parse_ini, render_ini

# shortcut: τ y el orden l. 785/827 comprobados en camsoEngine 91392d70 (40340) y 47437d5c (38022, Ibiza); otra versión, revisar.
BNG_PRESSURE_RATE = 5.0  # 1/s, camsoEngine.lua l. 1799
AC_DT = 0.003  # paso de física del turbo de AC (0,003 s)
AC_LAG_RATE = 1.3333334 * 333.33334  # motor de AC: tasa = (1 − LAG)·esto
FIT_TOL = 0.02  # error de par relativo máximo aceptado con 1 rampa
MAX_RAMPS = 3
DEFAULT_MAX_ERR = 0.08  # sin la casilla «ajustable»: peor ajuste aceptado; con ella se acepta cualquiera (se informa)


def lag_for(kind: str) -> float:
    """``LAG`` de AC cuyo paso por física iguala el primer orden de BeamNG (0 si es compresor)."""
    if kind == "supercharger":
        return 0.0
    k = 1.0 - math.exp(-AC_DT * BNG_PRESSURE_RATE)
    return 1.0 - k / (AC_DT * AC_LAG_RATE)


TURBO_LAG = lag_for("turbo")


class AdjustableTurboError(ValueError):
    pass


def _interp(table: list[list[float]], x: float) -> float:
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def parse_lut(text: str) -> list[list[float]]:
    rows = []
    for line in text.splitlines():
        line = line.split(";", 1)[0].strip()
        if line:
            a, b = line.split("|")
            rows.append([float(a), float(b)])
    if len(rows) < 2:
        raise AdjustableTurboError("power.lut con menos de 2 puntos")
    return rows


def na_power_lut(power_lut: str, pressure: list[list[float]]) -> str:
    """Mismo formato que ``power.lut`` (``rpm|N·m``) dividiendo cada punto por la presión absoluta (≥ 1)."""
    out = []
    for rpm, torque in parse_lut(power_lut):
        p = max(_interp(pressure, rpm), 1.0)
        out.append(f"{rpm:g}|{torque / p:.2f}\n")
    return "".join(out)


Ramp = tuple[float, float, float]  # (MAX_BOOST, REFERENCE_RPM, GAMMA)


def _ramp(rpm: float, ref: float, gamma: float) -> float:
    """Objetivo de rotación del turbo de AC a gas a fondo."""
    return min(rpm / ref, 1.0) ** gamma if rpm > 0 else 0.0


def _max_err(pressure: list[list[float]], ramps: list[Ramp]) -> float:
    """Error relativo máximo del par de AC, ``(1 + Σ boost) / presión − 1``, sobre el mapa."""
    return max(
        abs((1 + sum(m * _ramp(r, ref, g) for m, ref, g in ramps)) / p - 1)
        for r, p in pressure
    )


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Eliminación gaussiana con pivote parcial (sistemas de 1 a MAX_RAMPS incógnitas)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            return None
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _fit_amplitudes(
    pressure: list[list[float]], shapes: list[tuple[float, float]], irls: int = 0
) -> list[Ramp] | None:
    """Mínimos cuadrados del par relativo con (ref, gamma) fijos: lineal en MAX_BOOST (> 0).

    ``irls`` repesos iterativos (cada punto pesa más cuanto mayor es su error) que acercan el resultado al
    mínimo del error máximo. Devuelve tantas rampas como ``shapes`` o ``None`` si alguna amplitud no sale positiva.
    """
    n = len(shapes)
    basis = [[_ramp(r, *sh) / p for sh in shapes] for r, p in pressure]
    y = [(p - 1) / p for _, p in pressure]
    w = [1.0] * len(y)
    m: list[float] | None = None
    for _ in range(irls + 1):
        a = [[0.0] * n for _ in range(n)]
        b = [0.0] * n
        for f, yi, wi in zip(basis, y, w):
            for u in range(n):
                b[u] += wi * f[u] * yi
                for v in range(n):
                    a[u][v] += wi * f[u] * f[v]
        m = _solve(a, b)
        if m is None or min(m) <= 1e-4:
            return None
        errs = [
            abs(sum(mj * fj for mj, fj in zip(m, f)) - yi) for f, yi in zip(basis, y)
        ]
        top = max(errs) or 1.0
        w = [wi * (1 + 3 * e / top) for wi, e in zip(w, errs)]
        mean = sum(w) / len(w)
        w = [wi / mean for wi in w]
    assert m is not None
    return [(mj, *sh) for mj, sh in zip(m, shapes)]


def _refine(
    pressure: list[list[float]], ramps: list[Ramp], err: float
) -> tuple[list[Ramp], float]:
    """Búsqueda local de (ref, gamma) de cada rampa; los MAX_BOOST se recalculan en cada paso."""
    step = [125.0, 0.5]
    while step[0] >= 5:
        improved = False
        for j in range(len(ramps)):
            for d in (0, 1):
                for sign in (1, -1):
                    shapes = [(r, g) for _, r, g in ramps]
                    shapes[j] = (
                        shapes[j][0] + (sign * step[0] if d == 0 else 0),
                        shapes[j][1] + (sign * step[1] if d == 1 else 0),
                    )
                    if shapes[j][0] < 300 or shapes[j][1] < 0.3:
                        continue
                    cand = _fit_amplitudes(pressure, shapes, irls=5)
                    if cand:
                        e = _max_err(pressure, cand)
                        if e < err - 1e-9:
                            ramps, err, improved = cand, e, True
        if not improved:
            step = [step[0] / 2, step[1] / 2]
    return ramps, err


def _ref_grid(top: float, step: int) -> list[float]:
    """REFERENCE_RPM candidatos; el paso crece con el techo para acotar el coste (≤ ~48 valores)."""
    step = max(step, int(top / 48))
    return list(range(500, int(top) + 1, step)) or [top]


def _candidates(top: float) -> list[tuple[float, float]]:
    refs = _ref_grid(top, 250)
    return [(r, g) for r in refs for g in (0.5, 1, 1.5, 2, 3, 4, 6, 9, 14, 22, 35)]


def _fit_points(pressure: list[list[float]]) -> list[list[float]]:
    """Sin el 3 % de rpm más alto: ahí el limitador corta el par y el mapa cae (ninguna rampa lo sigue)."""
    pts = sorted(pressure)
    top = pts[-1][0]
    kept = [pt for pt in pts if pt[0] <= 0.97 * top]
    return kept if len(kept) > 3 else pts


def _best_of(
    pressure: list[list[float]],
    combos: list[list[tuple[float, float]]],
    ramps: list[Ramp],
) -> tuple[list[Ramp], float] | None:
    found = None
    for combo in combos:
        c = _fit_amplitudes(pressure, [(r, g) for _, r, g in ramps] + combo)
        if c:
            e = _max_err(pressure, c)
            if found is None or e < found[1]:
                found = (c, e)
    return found


def fit_ramps(
    pressure: list[list[float]], max_ramps: int = MAX_RAMPS, tol: float = FIT_TOL
) -> tuple[list[Ramp], float]:
    """Rampas ``MAX_BOOST·min(rpm/REF,1)^GAMMA`` (1, 2 o 3 según ``tol``) que imitan el mapa de BeamNG.

    1 rampa: rejilla completa; 2: pares de una rejilla gruesa; 3: se añade una a las 2 mejores. Tras cada paso se
    refinan todas; los MAX_BOOST salen por mínimos cuadrados. Devuelve ``(rampas, error de par relativo máximo)``
    medido sin el 3 % de rpm más alto (limitador). shortcut: solo biblioteca estándar, mínimos cuadrados (no
    minimax) y sin mirar el reparto por acelerador.
    """
    pts = _fit_points(pressure)
    top = max(r for r, _ in pts)
    fine = [[c] for c in _candidates(top)]
    coarse = [(r, g) for r in _ref_grid(top, 250) for g in (1.5, 3, 6, 14)]
    pairs = [[a, b] for i, a in enumerate(coarse) for b in coarse[i + 1 :]]
    out: tuple[list[Ramp], float] | None = None
    for n, combos, base in ((1, fine, []), (2, pairs, []), (3, fine, None)):
        if n > max_ramps or (out is not None and out[1] <= tol):
            break
        found = _best_of(pts, combos, out[0] if base is None and out else [])
        if found is None:
            continue
        found = _refine(pts, *found)
        if out is None or found[1] < out[1] - 1e-9:
            out = found
    if out is None:
        raise AdjustableTurboError("no se pudo ajustar el boost del mapa")
    return out


def apply(
    power_lut: str,
    engine_ini: str,
    facts: dict[str, Any],
    adjustable: bool = True,
    max_err: float | None = None,
) -> tuple[str, str, list[str]]:
    """``(power.lut nuevo, engine.ini nuevo, notas)``; ``AdjustableTurboError`` si el coche no tiene boost.

    ``adjustable``: el jugador puede bajar el boost desde la cabina (0-100 %); si no, el [TURBO_n] es fijo.
    """

    def val(k: str) -> Any:
        f = facts.get(k)
        return f.get("value") if isinstance(f, dict) else f

    pressure = val("ir.engine.boost_pressure_curve")
    max_boost = val("ir.engine.boost_max")
    if not pressure or not isinstance(max_boost, (int, float)) or max_boost <= 0:
        raise AdjustableTurboError(
            "no hay datos de boost: solo se ajusta en coches de camsoEngine con turbo o compresor "
            "(presión > 1 en el mapa de par)"
        )
    kind = val("ir.engine.boost_kind") or "turbo"
    secs = parse_ini(engine_ini)
    if "ENGINE_DATA" not in secs:
        raise AdjustableTurboError("engine.ini sin [ENGINE_DATA]")
    if any(s.startswith("TURBO_") for s in secs):
        raise AdjustableTurboError("engine.ini ya tiene un [TURBO_n]")
    try:
        pressure = sorted([float(r), max(float(p), 1.0)] for r, p in pressure)
    except (TypeError, ValueError) as exc:
        raise AdjustableTurboError(f"mapa de presión no numérico: {exc}") from exc
    if not all(math.isfinite(x) for pt in pressure for x in pt):
        raise AdjustableTurboError("mapa de presión con valores no finitos")
    ramps, err = fit_ramps(pressure)
    if max_err is not None and err > max_err:
        raise AdjustableTurboError(
            f"el boost del mapa no se ajusta bien a [TURBO_n] (error de par {err:.1%} > {max_err:.0%}): se deja el boost en power.lut, sin lag"
        )
    if adjustable:
        secs["ENGINE_DATA"]["DEFAULT_TURBO_ADJUSTMENT"] = "1"
    total = sum(m for m, _, _ in ramps)

    def fmt(x: float) -> str:
        return f"{x:.3f}".rstrip("0").rstrip(".")

    lag = f"{lag_for(kind):.4f}".rstrip("0").rstrip(".")
    for i, (m, ref, g) in enumerate(ramps):
        secs[f"TURBO_{i}"] = {
            "LAG_DN": lag,
            "LAG_UP": lag,
            "MAX_BOOST": fmt(m),
            "WASTEGATE": fmt(m) if adjustable else "0",
            "DISPLAY_MAX_BOOST": fmt(total),
            "REFERENCE_RPM": f"{ref:.0f}",
            "GAMMA": fmt(g),
            "COCKPIT_ADJUSTABLE": "1" if adjustable else "0",
        }
    shape = " + ".join(
        f"{fmt(m)} bar (ref {ref:.0f} rpm, γ {fmt(g)})" for m, ref, g in ramps
    )
    notes = [
        f"{kind}{' ajustable del 0 al 100 % desde la cabina' if adjustable else ''}: power.lut sin boost y "
        f"{len(ramps)} [TURBO_n] = {shape}; error de par frente al mapa de BeamNG ≤ {err:.1%} a gas a fondo; "
        f"lag {lag} ({'compresor: sin lag' if lag == '0' else 'τ = 0,2 s de BeamNG'})"
    ]
    return na_power_lut(power_lut, pressure), render_ini(secs), notes
