"""``data/sounds.ini [BACKFIRE]``: condiciones con las que AC dispara las llamas del escape (G2.55).

Fuente: ingeniería inversa de ``acs.exe`` (``BackfireParams::BackfireParams`` 0x1400cccb0 y ``checkBackfire`` 0x1400d26b0):

* ``BackfireParams`` lee ``MAXGAS`` (tope 0,3), ``MINRPM``, ``MAXRPM`` y ``TRIGGERGAS`` de ``data/sounds.ini [BACKFIRE]``.
  Sin ``sounds.ini`` quedan los valores por defecto del código: ``MINRPM`` = 9750 rpm, ``MAXRPM`` = 24000, ``MAXGAS`` = 0,25,
  ``TRIGGERGAS`` = 0,8. Con casi cualquier motor de calle 9750 rpm no se alcanza nunca: no hay llamas (Eagle Inca).
* ``checkBackfire``: el gas máximo pulsado ``g`` fija ``TRIGGERGAS·g`` y ``MAXGAS·g``. Se arma al bajar de ``TRIGGERGAS·g`` y
  dispara con ``0 < gas < MAXGAS·g``, ``MINRPM < rpm ≤ MAXRPM`` y combustible en el escape > 1 (se acumula con el gas
  mantenido). **Con el gas a 0 NO dispara**: hay que levantar el pie solo en parte (≈ 5-20 %), no soltarlo de golpe.

Valores que se escriben [hip, sin probar en AC]: ``MINRPM`` = 0,65·limitador (Kunos: Celica 5250/7500, Abarth 4700/6500),
``MAXRPM`` = max(12000, limitador + 2000) (Kunos 12000-15000), ``MAXGAS`` = 0,28 (Abarth 0,275; AC lo acota a 0,3),
``TRIGGERGAS`` = 0,8 (todos los Kunos) y los ``VOLUME_*`` del Celica (solo sonido).
"""

from __future__ import annotations

import math

SECTION = "BACKFIRE"


def _finite(name: str, v: float | None) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"BACKFIRE {name}: {v!r} no es un número finito")
    return float(v)


def backfire_values(
    limiter_rpm: float,
    min_rpm: float | None = None,
    max_rpm: float | None = None,
    max_gas: float | None = None,
) -> dict[str, str]:
    limiter_rpm = _finite("limitador", limiter_rpm) or 0.0
    min_rpm, max_rpm, max_gas = (
        _finite("MINRPM", min_rpm),
        _finite("MAXRPM", max_rpm),
        _finite("MAXGAS", max_gas),
    )
    lo = 0.65 * limiter_rpm if min_rpm is None else min_rpm
    hi = max(12000.0, limiter_rpm + 2000.0) if max_rpm is None else max_rpm
    if max_gas is not None and max_gas <= 0:
        raise ValueError(
            f"BACKFIRE MAXGAS {max_gas}: con 0 las llamas no salen nunca (use > 0)"
        )
    gas = 0.28 if max_gas is None else min(max_gas, 0.3)  # AC acota MAXGAS a 0,3
    if not (0 < lo < hi):
        raise ValueError(f"BACKFIRE: MINRPM {lo} y MAXRPM {hi} no son un rango válido")
    return {
        "MAXGAS": f"{gas:.3f}".rstrip("0").rstrip("."),
        "MINRPM": str(round(lo)),
        "MAXRPM": str(round(hi)),
        "TRIGGERGAS": "0.8",
        "VOLUME_IN": "1.0",
        "VOLUME_OUT": "0.5",
        "VOLUME_SCALE_OUT": "2",
    }
