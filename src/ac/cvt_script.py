"""Script de CSP que emula la CVT de una exportación de Automation (2026-11-19).

AC no tiene CVT: el ``drivetrain.ini`` lleva una marcha de relación 1,0 y ``FINAL`` = grupo
final (reglas ``dt.*_cvt@1``) y este script fija cada paso ``FINAL·relación CVT`` con
``ac.setGearsFinalRatio`` (mismas llamadas que ``script-cvt.lua`` de la raíz del repositorio).

Ley de BeamNG que reproduce (``shiftLogic/cvtGearbox.lua`` del juego, sha256 en
``automatic_transmission.GAME_LUA``; ``docs/research/cvt_generalizacion_2026-11-19.md`` §2.1):

* ``targetAV = lowAV + (highAV − lowAV)·agresividad`` filtrada por un suavizador no lineal
  (entrada 0,5 / salida 2); parado: ``relación = maxGearRatio``.
* ``relación += (targetAV − engineAV)·cvtAggression·0,0166667`` por paso de gráficos (constante
  equivalente a ``dt`` a 60 fps, l. 234-236 del controlador) y filtro de relación (20 / 5).
  Aquí se escribe como ``e·a·dt`` (``e`` en rad/s), idéntico a 60 fps y estable con otro ``dt``.
* Agresividad (``vehicleController.lua``): ``1,333·gas·clamp(dGas/dt, 1, 20)`` entra SIN recortar
  a un suavizador (subida 1,5 / bajada = ``aggressionSmoothingDown`` del JBeam) y solo la salida
  se limita a 1; se pone a 0 por debajo de 1 m/s.
* Marcha atrás: ``GEAR_R`` = −maxGearRatio ya da ``|GEAR_R|·FINAL``; el script devuelve ``FINAL``
  a su valor base (no la varía).

NO verificado en AC: ``data/script.lua`` solo corre con la física extendida de CSP activa
(en G2.22 un script de física no se ejecutó, ``docs/research/investigacion_exportador_y_csp_2026-11-17.md``
§B). Sin el script el coche circula con relación total fija = grupo final. Las rpm objetivo
y la ganancia vienen del JBeam; los suavizadores y el modo S (suelo de agresividad 0,5) usan
valores del Lua del juego, no medidos en BeamNG. No modelados: el modo S (suelo de agresividad
0,5; agus lleva ``PRNDS``), la retención de agresividad al frenar y la rama de teclado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

RPM_TO_AV = 0.104719755  # constants.rpmToAV de BeamNG


@dataclass(frozen=True)
class CvtScriptParams:
    ratio_min: float
    ratio_max: float
    final_drive: float
    rpm_low: float
    rpm_high: float
    aggression: float  # cvtAggression
    aggression_down: float  # aggressionSmoothingDown (del JBeam; 0,15 por defecto)
    aggression_up: float = 1.5  # aggressionSmoothingUp (por defecto de BeamNG)


class CvtScriptError(ValueError):
    pass


_FACTS = {
    "ratio_min": "ir.drivetrain.gearbox.cvt_ratio_min",
    "ratio_max": "ir.drivetrain.gearbox.cvt_ratio_max",
    "final_drive": "ir.drivetrain.final_ratio",
    "rpm_low": "ir.drivetrain.gearbox.cvt_rpm_low",
    "rpm_high": "ir.drivetrain.gearbox.cvt_rpm_high",
    "aggression": "ir.drivetrain.gearbox.cvt_aggression",
    "aggression_down": "ir.drivetrain.gearbox.cvt_aggression_down",
}


def params_from_facts(facts: dict[str, Any]) -> CvtScriptParams | None:
    """Parámetros a partir de ``{id de hecho: valor}``; ``None`` si el coche no es CVT.
    Un dato de CVT ausente es un error (nunca se rellena)."""
    if facts.get("ir.drivetrain.gearbox.kind") != "cvtGearbox":
        return None
    values: dict[str, float] = {}
    for name, fact in _FACTS.items():
        v = facts.get(fact)
        if (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v <= 0
        ):
            raise CvtScriptError(f"CVT sin dato válido {fact}: {v!r}")
        values[name] = float(v)
    if (
        values["ratio_min"] > values["ratio_max"]
        or values["rpm_low"] >= values["rpm_high"]
    ):
        raise CvtScriptError(f"CVT con rangos incoherentes: {values}")
    return CvtScriptParams(**values)


def render_cvt_script(p: CvtScriptParams) -> str:
    return _TEMPLATE.format(
        ratio_min=repr(p.ratio_min),
        ratio_max=repr(p.ratio_max),
        final=repr(p.final_drive),
        low_av=repr(p.rpm_low * RPM_TO_AV),
        high_av=repr(p.rpm_high * RPM_TO_AV),
        aggression=repr(p.aggression),
        agg_down=repr(p.aggression_down),
        agg_up=repr(p.aggression_up),
        rpm_low=p.rpm_low,
        rpm_high=p.rpm_high,
    )


# Lua con las llamadas de script-cvt.lua (ac.getCar, ac.setGearsFinalRatio, script.update).
_TEMPLATE = """\
-- beam2ac: CVT emulada para Assetto Corsa (CSP). Generado; NO verificado en AC.
-- Ley de BeamNG shiftLogic/cvtGearbox.lua: ver src/ac/cvt_script.py y
-- docs/research/cvt_generalizacion_2026-11-19.md. Ventana de rpm {rpm_low:g}-{rpm_high:g}.
local RATIO_MIN, RATIO_MAX = {ratio_min}, {ratio_max}
local BASE_FINAL = {final}
local LOW_AV, HIGH_AV = {low_av}, {high_av}   -- cvtLowRPM / cvtHighRPM en rad/s
local AGGRESSION = {aggression}                 -- cvtAggression (ganancia del lazo)
local AGG_UP, AGG_DOWN = {agg_up}, {agg_down}   -- suavizado de la agresividad
local TARGET_IN, TARGET_OUT = 0.5, 2.0          -- suavizado de las rpm objetivo
local RATIO_IN, RATIO_OUT = 20.0, 5.0           -- suavizado de la relacion
local STANDSTILL_KMH = 2.0                      -- aprox. de |outputAV1| < 10 rad/s

local function clamp(x, a, b) return math.max(a, math.min(b, x)) end
-- temporalSmoothingNonLinear (filters.lua): tasa de salida si el objetivo se aleja de 0
local function smooth(state, sample, dt, rate_in, rate_out)
  local dif = sample - state
  local rate = ((dif * state >= 0) and rate_out or rate_in) * dt
  return state + dif * (rate / (1 + rate))
end

local ratio, targetAV, aggr, lastGas, ready = RATIO_MAX, LOW_AV, 0.0, 0.0, false

function script.update(dt)
  local car = ac.getCar(0)
  if not car then return end
  dt = math.max(dt, 1e-4)
  local gear, gas, vKmh = car.gear or 0, car.gas or 0, car.speedKmh or 0
  if not ready then
    ready = true
    ac.setGearsFinalRatio(BASE_FINAL * ratio)
  end
  if gear < 0 then
    -- marcha atras: |GEAR_R|*FINAL = maxGearRatio*FINAL (relacion de salida desde parado)
    ratio = RATIO_MAX
    ac.setGearsFinalRatio(BASE_FINAL)
    lastGas = gas
    return
  end
  local dGas = clamp((gas - lastGas) / dt, 1, 20)
  lastGas = gas
  -- vehicleController.lua: gas*1.333*dGas SIN recortar entra al suavizador; solo la salida se
  -- limita a 1 (un pisotón lleva la agresividad a ~1 en pocos fotogramas)
  local aggTarget = (vKmh / 3.6 < 1.0) and 0.0 or (gas * 1.333 * dGas)
  aggr = smooth(aggr, aggTarget, dt, AGG_DOWN, AGG_UP)
  -- BeamNG actualiza las rpm objetivo en todos los fotogramas, también parado
  targetAV = smooth(targetAV, LOW_AV + (HIGH_AV - LOW_AV) * math.min(aggr, 1.0), dt, TARGET_IN, TARGET_OUT)
  if vKmh < STANDSTILL_KMH then
    ratio = RATIO_MAX
  else
    local engineAV = (car.rpm or 0) * {rpm_to_av}
    -- BeamNG: avError*cvtAggression/60 por paso a 60 fps  ==  avError*cvtAggression*dt
    local desired = clamp(ratio + (targetAV - engineAV) * AGGRESSION * dt, RATIO_MIN, RATIO_MAX)
    ratio = smooth(ratio, desired, dt, RATIO_IN, RATIO_OUT)
  end
  ac.setGearsFinalRatio(BASE_FINAL * ratio)
end

function script.reset()
  ratio, targetAV, aggr, lastGas, ready = RATIO_MAX, LOW_AV, 0.0, 0.0, false
end
""".replace("{rpm_to_av}", repr(RPM_TO_AV))
