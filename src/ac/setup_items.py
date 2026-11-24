"""Reglajes opcionales del ``setup.ini`` (menú de boxes de AC): reparto de frenada y presión de neumáticos.

Formato copiado de las secciones de los coches de Kunos (``FRONT_BIAS``, ``PRESSURE_LF/RF/LR/RR``): ``TAB``/``HELP``
deben ser los predeterminados o AC falla por la localización. El valor por defecto de cada reglaje NO se escribe
aquí: es el del coche (``brakes.ini`` ``FRONT_SHARE``; ``tyres.ini`` ``PRESSURE_STATIC``). Aquí solo se dan los
límites (``MIN``/``MAX``/``STEP``), que son un DISEÑO (el origen no dice qué rango se puede ajustar):

* presión: de 8 psi por debajo a 15 psi por encima de la estática del eje (mín. 10 psi), paso 1;
* reparto de frenada: de 15 puntos por debajo a 10 por encima del ``FRONT_SHARE`` (en %), paso 1.
"""

from __future__ import annotations

import math

from src.ac.tyre_compounds import parse_ini, render_ini

WHEELS = (
    ("LF", "FRONT", 0, 2),
    ("RF", "FRONT", 1, 2),
    ("LR", "REAR", 0, 4),
    ("RR", "REAR", 1, 4),
)


class SetupItemsError(ValueError):
    pass


def _statics(secs: dict, axle: str, what: str) -> list[float]:
    """``PRESSURE_STATIC`` de TODOS los compuestos del eje (``[FRONT]``, ``[FRONT_1]``…): el rango del setup
    debe contener la presión inicial de cualquiera que se elija en boxes."""
    names = [
        s
        for s in secs
        if s == axle or (s.startswith(axle + "_") and s[len(axle) + 1 :].isdigit())
    ]
    return [
        _num(secs, n, "PRESSURE_STATIC", what)
        for n in names
        if "PRESSURE_STATIC" in secs[n]
    ] or [_num(secs, axle, "PRESSURE_STATIC", what)]


def _num(secs: dict, sec: str, key: str, what: str) -> float:
    try:
        v = float(secs[sec][key])
    except (KeyError, ValueError):
        raise SetupItemsError(f"{what}: falta {sec}/{key} numérico") from None
    if not math.isfinite(v):
        raise SetupItemsError(f"{what}: {sec}/{key} no finito")
    return v


def apply(
    setup_text: str,
    tyres_text: str | None,
    brakes_text: str | None,
    *,
    brake_bias: bool = False,
    tyre_pressure: bool = False,
) -> tuple[str, list[str]]:
    """``(setup.ini nuevo, notas)``; ``SetupItemsError`` si falta el dato del que sale el valor por defecto."""
    secs = parse_ini(setup_text or "")
    secs.setdefault("DISPLAY_METHOD", {"SHOW_CLICKS": "0"})
    notes: list[str] = []
    if tyre_pressure:
        ty = parse_ini(tyres_text or "")
        for suffix, axle, x, y in WHEELS:
            st = _statics(ty, axle, f"presión {suffix}")
            lo, hi = max(10, math.floor(min(st)) - 8), math.ceil(max(st)) + 15
            secs[f"PRESSURE_{suffix}"] = {
                "SHOW_CLICKS": "0",
                "TAB": "TYRES",
                "NAME": f"Pressure {suffix}",
                "MIN": str(lo),
                "MAX": str(hi),
                "STEP": "1",
                "POS_X": str(x),
                "POS_Y": str(y),
                "HELP": f"HELP_{suffix}_PRESSURE",
            }
        notes.append(
            "presión de neumáticos ajustable en boxes: de −8 a +15 psi sobre la estática (diseño)"
        )
    if brake_bias:
        br = parse_ini(brakes_text or "")
        fs = _num(br, "DATA", "FRONT_SHARE", "reparto de frenada")
        pct = fs * 100 if fs <= 1 else fs
        lo, hi = max(0, math.floor(pct) - 15), min(100, math.ceil(pct) + 10)
        secs["FRONT_BIAS"] = {
            "SHOW_CLICKS": "0",
            "TAB": "GENERIC",
            "NAME": "Brake Bias",
            "MIN": str(lo),
            "MAX": str(hi),
            "STEP": "1",
            "POS_X": "0.5",
            "POS_Y": "2",
            "HELP": "HELP_BRAKE_BIAS",
        }
        notes.append(
            "reparto de frenada ajustable en boxes: de −15 a +10 puntos sobre FRONT_SHARE (diseño)"
        )
    return render_ini(secs), notes
