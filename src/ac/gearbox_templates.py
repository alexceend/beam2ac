"""Plantillas de caja (manual / doble embrague / automática) del colaborador para ``drivetrain.ini`` y ``ai.ini``.

PROCEDENCIA: valores HEURÍSTICOS aportados por el colaborador del proyecto (2026-11-24); no derivan de
Automation ni de BeamNG, ni están medidos en AC. Los nombres de clave existen en los ``drivetrain.ini`` de
Kunos (Celica ST185, R8 LMS). Reglas del colaborador:

* ``[CLUTCH] MAX_TORQUE`` = par máximo del motor + 100 N·m (cambia la fórmula 1,25·par + inercia de las reglas
  ``dt.clutch_max_torque*``: decisión del usuario, 2026-11-24).
* ``[AUTOCLUTCH] MIN_RPM/MAX_RPM`` relativos al ralentí (automática: ralentí+250; DCT y manual: ralentí;
  MAX_RPM = ralentí+850).
* ``[AUTO_SHIFTER] UP`` = corte del motor − 200; ``DOWN`` = 55 % del corte (decisión de diseño, no medida;
  hasta ahora se dejaban ausentes y AC los calculaba, ver g253 §1).
* DOWN se acota a 0,95·min(UP·g(n+1)/g(n)) si 0,55·limitador dejaría el motor por debajo de DOWN tras una subida
  (evita oscilar; decisión mía dentro de la delegación del usuario, 2026-11-24).
* Corrección de un defecto de la plantilla original: DCT ``[AUTOBLIP] POINT_2`` = 30 (menor que ``POINT_1`` = 50,
  los puntos deben crecer; el R8 de Kunos usa 10/50/105) → 100 [hip, confirmar con el colaborador].
"""

from __future__ import annotations

from dataclasses import dataclass

KIND_TO_TYPE = {
    "manualGearbox": "manual",
    "sequentialGearbox": "manual",
    "dctGearbox": "dct",
    "automaticGearbox": "auto",
}

_TYPES: dict[str, dict[str, dict[str, str]]] = {
    "manual": {
        "GEARBOX": dict(
            CHANGE_UP_TIME="280",
            CHANGE_DN_TIME="290",
            AUTO_CUTOFF_TIME="280",
            SUPPORTS_SHIFTER="1",
            CONTROLS_WINDOW_GAIN="0.4",
        ),
        "AUTOCLUTCH": dict(
            UPSHIFT_PROFILE="NONE",
            DOWNSHIFT_PROFILE="DOWNSHIFT_PROFILE",
            USE_ON_CHANGES="1",
            FORCED_ON="0",
        ),
        "UPSHIFT_PROFILE": dict(POINT_0="25", POINT_1="65", POINT_2="120"),
        "DOWNSHIFT_PROFILE": dict(POINT_0="15", POINT_1="150", POINT_2="200"),
        "AUTOBLIP": dict(
            ELECTRONIC="0", POINT_0="20", POINT_1="140", POINT_2="200", LEVEL="0.8"
        ),
        "AUTO_SHIFTER": dict(SLIP_THRESHOLD="0.95", GAS_CUTOFF_TIME="0.28"),
    },
    "dct": {
        "GEARBOX": dict(
            CHANGE_UP_TIME="40",
            CHANGE_DN_TIME="100",
            AUTO_CUTOFF_TIME="35",
            SUPPORTS_SHIFTER="0",
            CONTROLS_WINDOW_GAIN="0.4",
        ),
        "AUTOCLUTCH": dict(
            UPSHIFT_PROFILE="UPSHIFT_PROFILE",
            DOWNSHIFT_PROFILE="DOWNSHIFT_PROFILE",
            USE_ON_CHANGES="1",
            FORCED_ON="1",
        ),
        "UPSHIFT_PROFILE": dict(POINT_0="10", POINT_1="50", POINT_2="60"),
        "DOWNSHIFT_PROFILE": dict(POINT_0="10", POINT_1="110", POINT_2="130"),
        "AUTOBLIP": dict(
            ELECTRONIC="1", POINT_0="20", POINT_1="50", POINT_2="100", LEVEL="0.90"
        ),
        "AUTO_SHIFTER": dict(SLIP_THRESHOLD="0.95", GAS_CUTOFF_TIME="0.130"),
    },
    "auto": {
        "GEARBOX": dict(
            CHANGE_UP_TIME="100",
            CHANGE_DN_TIME="170",
            AUTO_CUTOFF_TIME="320",
            SUPPORTS_SHIFTER="0",
            CONTROLS_WINDOW_GAIN="0.4",
        ),
        "AUTOCLUTCH": dict(
            UPSHIFT_PROFILE="UPSHIFT_PROFILE",
            DOWNSHIFT_PROFILE="DOWNSHIFT_PROFILE",
            USE_ON_CHANGES="0",
            FORCED_ON="1",
        ),
        "UPSHIFT_PROFILE": dict(POINT_0="100", POINT_1="180", POINT_2="220"),
        "DOWNSHIFT_PROFILE": dict(POINT_0="100", POINT_1="180", POINT_2="220"),
        "AUTOBLIP": dict(
            ELECTRONIC="0", POINT_0="10", POINT_1="100", POINT_2="200", LEVEL="0.6"
        ),
        "DAMAGE": dict(RPM_WINDOW_K="100"),
        "AUTO_SHIFTER": dict(SLIP_THRESHOLD="1.00", GAS_CUTOFF_TIME="0.40"),
    },
}
_MIN_RPM_OFFSET = {"auto": 250.0, "dct": 0.0, "manual": 0.0}
_MAX_RPM_OFFSET = 850.0
CLUTCH_MARGIN_NM = 100.0
UP_MARGIN_RPM = 200.0
DOWN_FRACTION = 0.55


@dataclass(frozen=True)
class EngineInputs:
    idle_rpm: float
    limiter_rpm: float
    peak_torque_nm: float


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.2f}"


def set_ini_keys(text: str, section: str, values: dict[str, str]) -> str:
    """Fija ``values`` en ``[section]`` (la crea si falta); conserva el resto y sustituye claves existentes."""
    lines = text.splitlines()
    head = f"[{section}]".upper()
    start = next((i for i, x in enumerate(lines) if x.strip().upper() == head), None)
    if start is None:
        body = [f"{k}={v}" for k, v in values.items()]
        return (
            "\n".join(
                lines
                + ([""] if lines and lines[-1].strip() else [])
                + [f"[{section}]"]
                + body
            )
            + "\n"
        )
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")),
        len(lines),
    )
    pending = dict(values)
    for i in range(start + 1, end):
        key = lines[i].split("=", 1)[0].strip().upper()
        match = next((k for k in pending if k.upper() == key), None)
        if match is not None and "=" in lines[i]:
            lines[i] = f"{match}={pending.pop(match)}"
    insert = [f"{k}={v}" for k, v in pending.items()]
    # insertar tras la última línea con contenido de la sección (antes de los huecos finales)
    pos = end
    while pos > start + 1 and not lines[pos - 1].strip():
        pos -= 1
    lines[pos:pos] = insert
    return "\n".join(lines) + "\n"


def engine_inputs(engine_ini: str, power_lut: str) -> EngineInputs | None:
    """Ralentí y corte de ``engine.ini [ENGINE_DATA]`` y par máximo de ``power.lut``; ``None`` si falta algo."""
    vals: dict[str, float] = {}
    sec = ""
    for raw in engine_ini.splitlines():
        s = raw.split(";", 1)[0].strip()
        if s.startswith("["):
            sec = s.strip("[]").upper()
        elif sec == "ENGINE_DATA" and "=" in s:
            k, v = s.split("=", 1)
            try:
                vals[k.strip().upper()] = float(v)
            except ValueError:
                pass
    torque = []
    for raw in power_lut.splitlines():
        parts = raw.split(";", 1)[0].strip().split("|")
        if len(parts) == 2:
            try:
                torque.append(float(parts[1]))
            except ValueError:
                pass
    idle, lim = vals.get("MINIMUM"), vals.get("LIMITER")
    if not idle or not lim or not torque or max(torque) <= 0:
        return None
    return EngineInputs(idle, lim, max(torque))


def read_ini(text: str) -> dict[tuple[str, str], str]:
    """``{(SECCIÓN, CLAVE): valor}`` en mayúsculas (sin comentarios ``;``)."""
    out: dict[tuple[str, str], str] = {}
    sec = ""
    for raw in text.splitlines():
        s = raw.split(";", 1)[0].strip()
        if s.startswith("["):
            sec = s.strip("[]").upper()
        elif "=" in s:
            k, v = s.split("=", 1)
            out[(sec, k.strip().upper())] = v.strip()
    return out


def shift_hunting(drivetrain: str, up_rpm: float, down_rpm: float) -> list[int]:
    """Marchas ``n`` cuya subida a ``n+1`` en ``UP`` deja el motor por debajo de ``DOWN``
    (``UP·g(n+1)/g(n) < DOWN``): riesgo de que AC baje de inmediato y oscile."""
    ini = read_ini(drivetrain)
    gears = []
    for i in range(1, 30):
        v = ini.get(("GEARS", f"GEAR_{i}"))
        if v is None:
            break
        try:
            gears.append(float(v))
        except ValueError:
            break
    return [
        n + 1
        for n in range(len(gears) - 1)
        if gears[n] > 0 and up_rpm * gears[n + 1] / gears[n] < down_rpm
    ]


def _hunting_floor(drivetrain: str, up_rpm: float) -> float | None:
    """0,95 · min sobre pares de marchas de ``UP·g(n+1)/g(n)`` (``None`` sin ≥ 2 marchas)."""
    ini = read_ini(drivetrain)
    gears = []
    for i in range(1, 30):
        v = ini.get(("GEARS", f"GEAR_{i}"))
        try:
            gears.append(float(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            break
    ratios = [up_rpm * b / a for a, b in zip(gears, gears[1:]) if a > 0 and b > 0]
    return 0.95 * min(ratios) if ratios else None


def apply_gearbox(
    drivetrain: str,
    ai: str | None,
    kind: str,
    eng: EngineInputs,
    protected: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[str, str]:
    """Devuelve ``(drivetrain.ini, ai.ini)`` con la plantilla del tipo de caja ``kind`` (``KIND_TO_TYPE``).

    ``protected``: ``(SECCIÓN, CLAVE)`` de ``drivetrain.ini`` con una decisión del usuario; no se tocan.
    """
    t = KIND_TO_TYPE[kind]
    tpl = {s: dict(v) for s, v in _TYPES[t].items()}
    tpl["CLUTCH"] = {"MAX_TORQUE": _fmt(eng.peak_torque_nm + CLUTCH_MARGIN_NM)}
    tpl["AUTOCLUTCH"]["MIN_RPM"] = _fmt(eng.idle_rpm + _MIN_RPM_OFFSET[t])
    tpl["AUTOCLUTCH"]["MAX_RPM"] = _fmt(eng.idle_rpm + _MAX_RPM_OFFSET)
    # AC lee UP/DOWN como enteros (g253 §1.1): se redondean antes de acotar y de escribir
    up = float(round(eng.limiter_rpm - UP_MARGIN_RPM))
    down = float(round(DOWN_FRACTION * eng.limiter_rpm))
    ini0 = read_ini(drivetrain)
    # una decisión del usuario sobre UP/DOWN manda también para el tope y para ai.ini
    if ("AUTO_SHIFTER", "UP") in protected and ("AUTO_SHIFTER", "UP") in ini0:
        up = float(ini0[("AUTO_SHIFTER", "UP")])
    # evita oscilar: tras subir en UP el motor no debe quedar por debajo de DOWN (0,95 de margen)
    floor = _hunting_floor(drivetrain, up)
    if floor is not None and floor < down:
        down = float(int(floor))  # entero por debajo del tope
    down = max(down, float(round(eng.idle_rpm)))  # nunca por debajo del ralentí
    if ("AUTO_SHIFTER", "DOWN") in protected and ("AUTO_SHIFTER", "DOWN") in ini0:
        down = float(ini0[("AUTO_SHIFTER", "DOWN")])
    tpl["AUTO_SHIFTER"].update(UP=_fmt(up), DOWN=_fmt(down))
    out = drivetrain
    for sec, vals in tpl.items():
        vals = {k: v for k, v in vals.items() if (sec.upper(), k) not in protected}
        if vals:
            out = set_ini_keys(out, sec, vals)
    ai_out = set_ini_keys(
        ai or "",
        "GEARS",
        {
            "UP": _fmt(up),
            "DOWN": _fmt(down),
            "SLIP_THRESHOLD": tpl["AUTO_SHIFTER"]["SLIP_THRESHOLD"],
            "GAS_CUTOFF_TIME": tpl["AUTO_SHIFTER"]["GAS_CUTOFF_TIME"],
        },
    )
    return out, ai_out
