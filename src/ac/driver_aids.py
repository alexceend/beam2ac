"""Opciones de ABS y control de tracción para ``electronics.ini`` (2026-11-21).

Son OPCIONES DE EXPORTACIÓN, no conversiones: Automation/BeamNG no dicen cuántos niveles
tiene el ABS o el TC ni su rango, así que los niveles y los valores del TC son una
**plantilla de diseño** copiada del patrón de Kunos (``ks_audi_r8_lms``: ``abs_control.lut``
0,08…0,16 y ``traction_control.lut`` 0,08…0,20, `índice|límite`), sin equivalencia medida.

Semántica de AC (AC; ``docs/research/diferencial_viscoso_y_electronica_ac_2026-11-21.md`` §6):

* ABS de AC (````) y control de tracción de AC (````) leen ``SLIP_RATIO_LIMIT``,
  ``PRESENT``, ``ACTIVE``, ``RATE_HZ``, ``CURVE`` (y ``MIN_SPEED_KMH`` en el TC). Sin archivo,
  ambos valen 0,2 / presente / activo / 20 Hz.
* Con ``CURVE``, ``cycleMode`` recorre los niveles ``0…N−1`` y luego apaga; cada nivel fija
  ``slipRatioLimit = curva(nivel)``. El nivel inicial se busca por IGUALDAD EXACTA de flotantes
  entre ``SLIP_RATIO_LIMIT`` y la curva: aquí ambos salen del mismo texto.
* Sin ``CURVE`` la tecla solo alterna encendido/apagado.
* Las ayudas globales del jugador (``cfg/assists.ini``) quedan fuera del coche.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MODES = ("auto", "on", "off")

# plantilla de diseño (patrón Kunos R8); sin evidencia de equivalencia con BeamNG
ABS_RANGE = (0.08, 0.16)
TC_RANGE = (0.08, 0.20)
ABS_TEMPLATE = {"SLIP_RATIO_LIMIT": "0.12", "RATE_HZ": "200"}
TC_TEMPLATE = {"SLIP_RATIO_LIMIT": "0.10", "RATE_HZ": "150", "MIN_SPEED_KMH": "40"}
ABS_LUT = "abs_control.lut"
TC_LUT = "traction_control.lut"


class DriverAidsError(ValueError):
    pass


@dataclass(frozen=True)
class AidSpec:
    mode: str = "auto"  # auto = lo que resuelve el origen; on; off
    levels: int = 0  # 0 = sin curva (solo on/off); si no, ≥ 2 niveles

    def validate(self, name: str) -> None:
        if self.mode not in MODES:
            raise DriverAidsError(f"{name}: modo {self.mode!r} no válido {MODES}")
        if isinstance(self.levels, bool) or not isinstance(self.levels, int):
            raise DriverAidsError(f"{name}: niveles no entero: {self.levels!r}")
        if self.levels != 0 and not 2 <= self.levels <= 20:
            raise DriverAidsError(f"{name}: niveles {self.levels}: 0 o de 2 a 20")


def _truthy(v: str | None) -> bool:
    """Como ``lectura de INI de AC(...) != 0`` (``wcstol``): ``0.0`` y ``00`` valen 0."""
    try:
        return int(float(v)) != 0 if v is not None else False
    except ValueError:
        return False


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def level_values(lo: float, hi: float, n: int) -> list[str]:
    """``n`` límites de deslizamiento crecientes de ``lo`` a ``hi`` (texto exacto)."""
    return [_fmt(lo + (hi - lo) * i / (n - 1)) for i in range(n)]


def _parse_ini(text: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Cabecera (comentarios iniciales) y secciones en orden, sin tocar otros valores."""
    head: list[str] = []
    sections: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            cur = sections.setdefault(line[1:-1], {})
        elif cur is None:
            if line:
                head.append(raw)
        elif line and not line.startswith(";") and "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.split(";", 1)[0].strip()
    return head, sections


def _render(head: list[str], sections: dict[str, dict[str, str]]) -> str:
    out = list(head)
    for name, kv in sections.items():
        out += ["", f"[{name}]"] + [f"{k}={v}" for k, v in kv.items()]
    return "\n".join(out) + "\n"


def _aid(
    section: dict[str, str],
    spec: AidSpec,
    template: dict[str, str],
    rng: tuple[float, float],
    lut: str,
    name: str,
    notes: list[str],
    luts: dict[str, str],
) -> dict[str, str] | None:
    """Sección resultante de una ayuda; ``None`` si no cambia."""
    if spec.mode == "auto" and spec.levels == 0:
        return None
    present = _truthy(section.get("PRESENT")) if section else False
    if spec.mode == "auto" and not present:
        notes.append(
            f"{name}: el origen no tiene la ayuda (PRESENT=0 o sin sección): los "
            "niveles no se aplican; use modo on para añadirla"
        )
        return None
    new = {**template, **section}
    if spec.mode == "off":
        new.pop("CURVE", None)
        new["PRESENT"], new["ACTIVE"] = "0", "0"
        return {k: new[k] for k in sorted(new, key=_pos)}
    new["PRESENT"], new["ACTIVE"] = "1", "1"
    if spec.levels:
        vals = level_values(*rng, spec.levels)
        try:
            base = float(new["SLIP_RATIO_LIMIT"])
        except (KeyError, ValueError):
            base = float("nan")
        if not math.isfinite(base):
            raise DriverAidsError(
                f"{name}: SLIP_RATIO_LIMIT no numérico ({new.get('SLIP_RATIO_LIMIT')!r})"
            )
        idx = min(range(len(vals)), key=lambda i: abs(float(vals[i]) - base))
        new["SLIP_RATIO_LIMIT"] = vals[idx]
        new["CURVE"] = lut
        luts[lut] = "".join(f"{i}|{v}\n" for i, v in enumerate(vals))
        notes.append(
            f"{name}: {spec.levels} niveles {vals[0]}…{vals[-1]} (plantilla del patrón de "
            f"Kunos, sin equivalencia medida); nivel inicial {idx} = {vals[idx]}"
        )
    elif "CURVE" in new:
        new.pop("CURVE")
    if spec.mode == "on" and not section:
        notes.append(f"{name}: añadida con la plantilla {template} (sin origen)")
    return {k: new[k] for k in sorted(new, key=_pos)}


def _pos(k: str) -> int:
    return _ORDER.index(k) if k in _ORDER else len(_ORDER)


_ORDER = [
    "SLIP_RATIO_LIMIT",
    "CURVE",
    "PRESENT",
    "ACTIVE",
    "RATE_HZ",
    "MIN_SPEED_KMH",
]


def apply_driver_aids(
    electronics: str | None, abs_spec: AidSpec, tc_spec: AidSpec
) -> tuple[str | None, dict[str, str], list[str]]:
    """``(electronics.ini nuevo | None si no cambia, {lut: texto}, notas)``."""
    abs_spec.validate("ABS")
    tc_spec.validate("TC")
    head, sections = _parse_ini(electronics or "")
    notes: list[str] = []
    luts: dict[str, str] = {}
    changed = False
    no_file = not (electronics or "").strip()
    for sec, spec, tpl, rng, lut in (
        ("ABS", abs_spec, ABS_TEMPLATE, ABS_RANGE, ABS_LUT),
        ("TRACTION_CONTROL", tc_spec, TC_TEMPLATE, TC_RANGE, TC_LUT),
    ):
        new = _aid(sections.get(sec, {}), spec, tpl, rng, lut, sec, notes, luts)
        if new is not None:
            sections[sec] = new
            changed = True
    if not changed:
        return None, {}, notes
    if no_file:
        # sin electronics.ini AC corre ambas ayudas presentes (ABS de AC/control de tracción de AC);
        # al crear el archivo, la que no se toca se escribe explícita para no perderla
        for sec, tpl in (("ABS", ABS_TEMPLATE), ("TRACTION_CONTROL", TC_TEMPLATE)):
            if sec not in sections:
                sections[sec] = {**tpl, "PRESENT": "1", "ACTIVE": "1"}
                notes.append(
                    f"{sec}: sin electronics.ini de origen, AC la corría presente; se escribe "
                    "explícita con la plantilla"
                )
    if not head:
        head = ["; generado por beam2ac: opciones de ayudas (src/ac/driver_aids.py)"]
    return _render(head, sections), luts, notes


def from_options(opts: dict[str, Any] | None) -> tuple[AidSpec, AidSpec]:
    o = opts or {}
    try:
        return (
            AidSpec(str(o.get("abs", "auto")), int(o.get("abs_levels") or 0)),
            AidSpec(str(o.get("tc", "auto")), int(o.get("tc_levels") or 0)),
        )
    except (TypeError, ValueError) as exc:
        raise DriverAidsError(f"opciones no válidas: {exc}") from None
