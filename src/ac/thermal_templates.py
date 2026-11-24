"""Plantillas térmicas de frenos y neumáticos para AC (opt-in, ``--thermal-templates``).

NO derivan de Automation (no exporta térmica de neumáticos) ni están calibradas en AC:
son plantillas de DISEÑO etiquetadas. Informe: docs/research/g254_termica_frenos_neumaticos_2026-11-21.md.

* Neumáticos: copia de valores de Kunos (street = ``ks_mazda_mx5_nc`` «Street»; slick =
  ``ks_audi_r8_lms`` «Slick Soft») [obs].
* Frenos: ``[TEMPS_FRONT/REAR]`` con ``PERF_CURVE`` = η(T) de la pastilla de BeamNG
  (``wheels.lua`` l.80-88, evaluada) [obs en el Lua, hip en AC] y ``COOL_TRANSFER`` por
  la fórmula dimensional del informe [hip]; ``TORQUE_K`` y ``COOL_SPEED_FACTOR`` = medianas de
  mods de terceros (sin unidades resueltas) [hip].
"""

from __future__ import annotations

import math
from typing import Any

TYRE_KINDS = ("street", "slick")

_THERMAL = {
    "street": dict(
        SURFACE_TRANSFER=0.014, PATCH_TRANSFER=0.00027, CORE_TRANSFER=0.00049,
        INTERNAL_CORE_TRANSFER=0.0057, FRICTION_K=0.06001, ROLLING_K=0.23,
        COOL_FACTOR=2.51, SURFACE_ROLLING_K=1.15054,
    ),
    "slick": dict(
        SURFACE_TRANSFER=0.018, PATCH_TRANSFER=0.00027, CORE_TRANSFER=0.0013401,
        INTERNAL_CORE_TRANSFER=0.00307, FRICTION_K=0.07936, ROLLING_K=0.18,
        COOL_FACTOR=2.05, SURFACE_ROLLING_K=0.6364,
    ),
}  # fmt: skip
_COMMON = dict(GRAIN_GAMMA=1, GRAIN_GAIN=0.5, BLISTER_GAMMA=1, BLISTER_GAIN=0.5)
_TCURVE = {
    "street": "0|0.80 20|0.97 40|0.98 60|0.995 75|1.0 85|1.0 100|0.98 110|0.97 "
    "140|0.97 160|0.95 220|0.90 250|0.60",
    "slick": "0|0.70 20|0.80 45|0.90 60|0.98 70|1.0 85|1.0 100|0.97 140|0.95 "
    "180|0.95 240|0.80 300|0.60",
}
_WEAR = {  # x = km virtuales, y = % de agarre
    "street": "0|100 0.75|100 1.25|99.5 2.5|99 3.75|98.5 10|98 25|95 35|90 50|80",
    "slick": "0|99.5 0.48|100 0.96|100 4.8|98 12|94 19.2|90 28.8|88",
}
# Coeficientes de pastilla de BeamNG (wheels.lua l.79-87): (w1x1, w2x1, b1, b2, a) [obs]
_PAD_COEF = {
    "basic": (0.018462, -0.013846, 3, 7, -0.988),
    "premium": (0.016, -0.012, 2.6, 7.3, -0.985),
    "sport": (0.019231, -0.0136, 2, 9.75, -0.993),
    "semi-race": (0.008462, -0.0117, 1.5, 10.75, -0.982),
    "full-race": (0.009231, -0.0115, 0.8, 12.5, -0.988),
    "carbon-ceramic": (0.016, -0.011, -0.8, 16, -0.996),
}
PADS = tuple(_PAD_COEF)
_T_GRID = (0, 20, 50, 100, 150, 200, 300, 400, 500, 600, 700, 800, 900, 1000)


def pad_efficiency(pad: str, t: float) -> float:
    """η(T) de ``calculateThermalEfficiency`` (wheels.lua l.167-175)."""
    w1, w2, b1, b2, a = _PAD_COEF[pad]
    sig = lambda z: 1 / (1 + math.exp(-z))  # noqa: E731
    return min(max(sig(w1 * t + b1) + sig(w2 * t + b2) + a, 0.0), 1.0)


_SPECIFIC_HEAT = {
    "steel": 450.0,
    "aluminum": 910.0,
    "ceramic": 820.0,
    "carbon-ceramic": 820.0,
}  # J/(kg·K), wheels.lua
COOL_TRANSFER_RANGE = (
    0.0045,
    0.0142,
)  # observado en mods y Ferrari F2004 (Kunos, BACKUPS)


def _lut(spec: str) -> str:
    return "\n".join(spec.split()) + "\n"


def tyre_files(kind: str) -> dict[str, str]:
    """LUT de la plantilla de neumático (nombres ``thermal_<kind>_*``)."""
    return {
        f"thermal_{kind}_tcurve.lut": _lut(_TCURVE[kind]),
        f"thermal_{kind}_wear.lut": _lut(_WEAR[kind]),
    }


def apply_tyres(text: str, kind: str) -> str:
    """Añade ``WEAR_CURVE`` a [FRONT]/[REAR] y ``[THERMAL_FRONT/REAR]``; no pisa lo existente."""
    if kind not in _THERMAL:
        raise ValueError(f"plantilla de neumático desconocida: {kind!r}")
    if "[THERMAL_FRONT]" in text.upper() or "WEAR_CURVE" in text.upper():
        return text  # ya tiene térmica (p. ej. Ibiza): no se pisa
    out, sec = [], ""
    for line in text.rstrip("\n").split("\n"):
        s = line.strip()
        if s.startswith("["):
            if sec in ("FRONT", "REAR"):
                out.append(f"WEAR_CURVE=thermal_{kind}_wear.lut")
            sec = s.strip("[]").upper()
        out.append(line)
    if sec in ("FRONT", "REAR"):
        out.append(f"WEAR_CURVE=thermal_{kind}_wear.lut")
    for axle in ("FRONT", "REAR"):
        out += ["", f"[THERMAL_{axle}]"]
        out += [f"{k}={v}" for k, v in {**_THERMAL[kind], **_COMMON}.items()]
        out.append(f"PERFORMANCE_CURVE=thermal_{kind}_tcurve.lut")
    return "\n".join(out) + "\n"


def cool_transfer(
    diameter_m: float, rotor_mass_kg: float, material: str = "steel"
) -> float:
    """[hip] ``k0 = A·40 / (0,15·m·c)`` con ``A = 0,7·π·D²/2`` (BeamNG ``initThermals``),
    acotado al rango observado en AC."""
    area = 0.7 * math.pi * diameter_m**2 / 2
    k0 = area * 40 / (0.15 * rotor_mass_kg * _SPECIFIC_HEAT[material])
    return min(max(k0, COOL_TRANSFER_RANGE[0]), COOL_TRANSFER_RANGE[1])


def brake_temps(
    pad: str,
    front: tuple[float, float],
    rear: tuple[float, float],
    vent: tuple[float, float] = (1.1, 1.1),
    material: tuple[str, str] = ("steel", "steel"),
) -> str:
    """Bloque ``[TEMPS_FRONT]``/``[TEMPS_REAR]``; ``front``/``rear`` = (``brakeDiameter`` m,
    ``brakeMass`` kg) del JBeam, ``vent`` = ``brakeVentingCoef`` por eje."""
    if pad not in _PAD_COEF:
        raise ValueError(f"pastilla desconocida: {pad!r}")
    eta = "(|" + "|".join(f"{t}={pad_efficiency(pad, t):.3f}" for t in _T_GRID) + "|)"
    out = []
    for i, (axle, (d, m)) in enumerate((("FRONT", front), ("REAR", rear))):
        out += [
            f"[TEMPS_{axle}]",
            f"COOL_TRANSFER={cool_transfer(d, m, material[i]):.5f}",
            "TORQUE_K=0.65",
            f"COOL_SPEED_FACTOR={0.0347 * vent[i]:.5f}",
            f"PERF_CURVE={eta}",
            "",
        ]
    return "\n".join(out)


def apply_brakes(text: str, **kw: Any) -> str:
    if "[TEMPS_FRONT]" in text.upper():
        return text
    return text.rstrip("\n") + "\n\n" + brake_temps(**kw)


_ROTOR = {"carbon-ceramic-vented-disc": "carbon-ceramic", "ceramic": "ceramic"}


def brake_params(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Parámetros por eje desde los hechos del modelo de atributos (``ir.brakes.axles.*``).
    ``None`` si falta cualquier dato (sin inventar). Ventilación: ``brakeVentingCoef`` no
    llega al modelo; se aproxima por tipo de disco (ventilado 1,1; resto 1,0) [hip]."""

    def v(axle: str, key: str) -> Any:
        return (facts.get(f"ir.brakes.axles.{axle}.{key}") or {}).get("value")

    pads, dims, vent, mat = [], {}, [], []
    for axle in ("front", "rear"):
        d, m, kind, pad = (
            v(axle, k)
            for k in ("disc_diameter", "disc_mass", "disc_type", "pad_material")
        )
        if not (isinstance(d, (int, float)) and isinstance(m, (int, float)) and m > 0):
            return None
        if pad not in _PAD_COEF or kind == "drum":
            return None  # tambor: sin modelo de disco
        dims[axle] = (float(d), float(m))
        pads.append(pad)
        vent.append(1.1 if "vented" in str(kind) else 1.0)
        mat.append(_ROTOR.get(str(kind), "steel") if kind in _ROTOR else "steel")
    # una sola curva por coche: la pastilla del eje delantero (el trasero comparte en Automation)
    return dict(
        pad=pads[0],
        front=dims["front"],
        rear=dims["rear"],
        vent=tuple(vent),
        material=tuple(mat),
    )
