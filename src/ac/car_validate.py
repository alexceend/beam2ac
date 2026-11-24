"""Validación ESTRUCTURAL de una carpeta de coche de Assetto Corsa.

No ejecuta el juego: comprueba archivos, referencias y coherencia interna. El
resultado distingue:

* ``errors``   — fallos del ensamblado (referencias rotas, valores incoherentes…);
* ``blockers`` — requisitos de AC conocidos que el coche aún no cumple
  (información que falta en la fuente o decisiones pendientes);
* ``checks``   — comprobaciones superadas, con sus valores.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILE_SUFFIXES = (".lut", ".kn5", ".rto", ".ksanim", ".knh")
_BRACKETED = re.compile(r"\[[^\]]*\]")


def _reference_base(car: Path, value: str) -> Path:
    """Carpeta donde AC busca un archivo referenciado desde un ``.ini`` de ``data/``.

    ``.kn5`` y ``.knh`` en la raíz del coche; ``.ksanim`` en ``animations/`` (los
    ``driver3d.ini`` de Kunos dicen
    ``NAME=steer.ksanim`` y el archivo está en ``animations/``); el resto (``.lut``,
    ``.rto``) en ``data/``.
    """
    low = value.lower()
    if low.endswith((".kn5", ".knh")):
        return car
    if low.endswith(".ksanim"):
        return car / "animations"
    return car / "data"


# Nulos (docs/research/ensamblado.md §7 y §8). El SDK de Kunos
# (AC_Pipeline_PUB_Rev2.0.pdf, pág. 13) pide WHEEL/SUSP/DISC/COCKPIT_LR/STEER_LR
# "in ALL LODs". Recuento del KN5 principal de 449 coches instalados (fase 7.1):
# * WHEEL_LF 449/449, SUSP_LF 444/449 → imprescindibles.
# * STEER_HR 419/449: falta solo en 30 coches de tráfico (IA). AC contiene
#   "ERROR: CANNOT FIND STEER_HR IN FILE:". Probable requisito de carga para un
#   coche conducible (sin probar si el error es fatal).
# * COCKPIT_LR 283/449 y STEER_LR 253/449; el oficial ks_ferrari_312_67 no tiene
#   STEER_LR → no bloquean (corrige la conclusión anterior, basada en 4 KN5).
# * DISC_LF 287/449; el oficial ks_audi_r8_lms no los tiene → no bloquean.
LOAD_NULLS = (
    "SUSP_LF",
    "SUSP_LR",
    "SUSP_RF",
    "SUSP_RR",
    "WHEEL_LF",
    "WHEEL_LR",
    "WHEEL_RF",
    "WHEEL_RR",
)
LIKELY_LOAD_NULLS = ("STEER_HR",)
LR_COCKPIT_NULLS = ("COCKPIT_LR", "STEER_LR")
DOCUMENTED_OPTIONAL_NULLS = ("DISC_LF", "DISC_LR", "DISC_RF", "DISC_RR")
KUNOS_REQUIRED_NULLS = LOAD_NULLS + LR_COCKPIT_NULLS + DOCUMENTED_OPTIONAL_NULLS

# Clasificación de elementos (grupo, elemento, tipo, nivel de evidencia, evidencia).
# Recuentos observados en la instalación local: 466 coches con KN5 y 113 con data/
# suelta (ver ensamblado.md §7–§8). "<id>" = identificador del coche.
# Niveles: documentado (SDK de Kunos), binario (mensaje presente en AC: prueba
# que el juego lo comprueba, no que el fallo sea fatal), observado (contenido
# instalado), hipótesis. La frecuencia de aparición NO prueba obligatoriedad.
_U113 = "presente en 113/113 coches con data/ suelta"
CLASSIFICATION = (
    ("cargar", "<id>.kn5", "file", "documentado", "SDK: modelo LOD A; 466/466"),
    (
        "cargar",
        "data/lods.ini",
        "file",
        "binario",
        "SDK pág. 12; AC: 'Error cannot find lods.ini for car'; " + _U113,
    ),
    (
        "cargar",
        "WHEEL_xx/SUSP_xx",
        "nulls",
        "documentado",
        "SDK pág. 13; WHEEL_LF 449/449, SUSP_LF 444/449 KN5 instalados",
    ),
    (
        "cargar",
        "STEER_HR",
        "nulls",
        "binario",
        "AC: 'ERROR: CANNOT FIND STEER_HR IN FILE:'; 419/449 KN5, faltan solo "
        "30 coches de tráfico; 130/130 ks_*",
    ),
    ("cargar", "collider.kn5", "file", "documentado", "SDK pág. 36; 466/466"),
    ("cargar", "data/car.ini", "file", "observado", _U113),
    ("cargar", "data/engine.ini", "file", "observado", _U113),
    ("cargar", "data/drivetrain.ini", "file", "observado", _U113),
    ("cargar", "data/suspensions.ini", "file", "observado", _U113),
    ("cargar", "data/tyres.ini", "file", "observado", _U113),
    ("cargar", "data/brakes.ini", "file", "observado", _U113),
    ("cargar", "ui/ui_car.json", "file", "observado", "466/466"),
    ("cargar", "skins/*", "skin", "observado", "466/466 con al menos una skin"),
    (
        "cargar",
        "sfx/<id>.bank",
        "file",
        "observado",
        "466/466; el registro de CSP muestra que AC carga "
        "content/cars/<id>/sfx/<id>.bank",
    ),
    (
        "conducir",
        "data/aero.ini",
        "file",
        "binario",
        _U113 + "; AC: 'ERROR: aero.ini does not contain WINGS nor DATA'",
    ),
    (
        "cargar",
        "data/setup.ini",
        "file",
        "binario",
        _U113 + "; sin él AC no crea menú de pit de AC y CSP falla al "
        "fijar la posición del control (ensamblado.md §9.12, verificado en el juego)",
    ),
    ("conducir", "data/escmode.ini", "file", "hipótesis", _U113),
    ("conducir", "data/ai.ini", "file", "hipótesis", _U113 + "; IA"),
    ("conducir", "data/cameras.ini", "file", "hipótesis", _U113 + "; vistas"),
    ("conducir", "data/dash_cam.ini", "file", "hipótesis", "112/113"),
    ("conducir", "data/sounds.ini", "file", "hipótesis", _U113),
    ("conducir", "data/electronics.ini", "file", "observado", "112/113; vacío en 27"),
    (
        "conducir",
        "data/colliders.ini",
        "file",
        "hipótesis",
        "112/113; cajas de colisión",
    ),
    ("conducir", "data/driver3d.ini", "file", "hipótesis", _U113 + "; piloto"),
    ("conducir", "driver_base_pos.knh", "file", "hipótesis", "466/466; piloto"),
    ("opcional", "data/mirrors.ini", "file", "hipótesis", _U113 + "; retrovisores"),
    ("opcional", "data/ambient_shadows.ini", "file", "hipótesis", _U113 + "; sombra"),
    ("opcional", "data/proview_nodes.ini", "file", "hipótesis", _U113 + "; cámaras"),
    ("opcional", "data/lights.ini", "file", "observado", "112/113"),
    ("opcional", "data/analog_instruments.ini", "file", "observado", "106/113"),
    (
        "opcional",
        "data/digital_instruments.ini",
        "file",
        "observado",
        "98/113; vacío en 33",
    ),
    ("opcional", "data/damage.ini", "file", "observado", "99/113"),
    (
        "opcional",
        "data/flames.ini",
        "file",
        "binario",
        "94/113; AC: 'WARNING: flames.ini not found' (aviso)",
    ),
    ("opcional", "data/flame_presets.ini", "file", "observado", "86/113"),
    (
        "opcional",
        "data/blurred_objects.ini",
        "file",
        "observado",
        "74/113; vacío en 33",
    ),
    ("opcional", "data/fuel_cons.ini", "file", "observado", "59/113"),
    (
        "opcional",
        "data/wing_animations.ini",
        "file",
        "observado",
        "106/113; vacío en 90",
    ),
    (
        "opcional",
        "data/suspension_graphics.ini",
        "file",
        "observado",
        "101/113; vacío en 98",
    ),
    ("opcional", "body_shadow.png", "file", "observado", "466/466; imagen de sombra"),
    ("opcional", "tyre_0_shadow.png", "file", "observado", "465/466"),
    (
        "opcional",
        "DISC_xx",
        "nulls",
        "observado",
        "SDK los exige, pero ks_audi_r8_lms no los tiene; DISC_LF 287/449",
    ),
    (
        "opcional",
        "COCKPIT_LR/STEER_LR",
        "nulls",
        "observado",
        "SDK los exige, pero COCKPIT_LR 283/449, STEER_LR 253/449; "
        "ks_ferrari_312_67 (oficial) sin STEER_LR",
    ),
    (
        "opcional",
        "COCKPIT_HR",
        "nulls",
        "observado",
        "SDK: secundario; 388/449 KN5",
    ),
    (
        "opcional",
        "RIM_xx/RIM_BLUR_xx",
        "nulls",
        "documentado",
        "SDK: secundarios, no esenciales",
    ),
)

_NULL_GROUPS = {
    "WHEEL_xx/SUSP_xx": [
        f"{k}_{s}" for k in ("WHEEL", "SUSP") for s in ("LF", "LR", "RF", "RR")
    ],
    "STEER_HR": ["STEER_HR"],
    "COCKPIT_LR/STEER_LR": list(LR_COCKPIT_NULLS),
    "DISC_xx": list(DOCUMENTED_OPTIONAL_NULLS),
    "COCKPIT_HR": ["COCKPIT_HR"],
    "RIM_xx/RIM_BLUR_xx": [
        f"{k}_{s}" for k in ("RIM", "RIM_BLUR") for s in ("LF", "LR", "RF", "RR")
    ],
}


def classify(
    car_dir: str | Path, car_id: str, kn5_nodes: set[str]
) -> list[dict[str, Any]]:
    """Estado de cada elemento de CLASSIFICATION en la carpeta del coche."""
    car = Path(car_dir)
    rows = []
    for group, item, kind, level, evidence in CLASSIFICATION:
        if kind == "nulls":
            present = all(n in kn5_nodes for n in _NULL_GROUPS[item])
        elif kind == "skin":
            present = (car / "skins").is_dir() and any(
                d.is_dir() for d in (car / "skins").iterdir()
            )
        else:
            present = (car / item.replace("<id>", car_id)).is_file()
        rows.append(
            {
                "group": group,
                "item": item,
                "present": present,
                "evidence_level": level,
                "evidence": evidence,
            }
        )
    return rows


def read_ini(path: str | Path) -> dict[str, dict[str, str]]:
    """INI de AC: secciones ``[X]``, ``CLAVE=valor``, comentarios con ``;``.

    Las secciones repetidas se fusionan (la última clave gana). Las líneas sin
    ``=`` fuera de secciones se ignoran.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1].strip().upper(), {})
            continue
        if "=" in line and current is not None:
            key, value = line.split("=", 1)
            current[key.strip().upper()] = value.strip()
    return sections


@dataclass
class CarValidation:
    car_dir: str
    errors: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def check(self, name: str, ok: bool, detail: Any, blocker: bool = False) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            (self.blockers if blocker else self.errors).append(f"{name}: {detail}")


def shift_window_problem(drivetrain: dict[str, dict[str, str]]) -> str | None:
    """Motivo por el que ``[GEARBOX] VALID_SHIFT_RPM_WINDOW`` no sirve, o ``None``.

    Requisito de compatibilidad con AC observado en AC y confirmado en el juego
    (ensamblado.md §9.15): sin un valor > 0, transmisión de AC deja a 0 el
    divisor de ``getRpmWindowStatus`` y el indicador de transmisión parpadea en rojo.
    """
    raw = drivetrain.get("GEARBOX", {}).get("VALID_SHIFT_RPM_WINDOW")
    if raw is None:
        return "falta [GEARBOX] VALID_SHIFT_RPM_WINDOW"
    try:
        value = float(raw)
    except ValueError:
        return f"VALID_SHIFT_RPM_WINDOW no numérico: {raw!r}"
    if not value > 0:
        return f"VALID_SHIFT_RPM_WINDOW debe ser > 0 (es {raw})"
    return None


# --- Lectura de INI tal como la hace AC (AC) -------------------
#
# ``read_ini`` (arriba) es tolerante: pasa claves y secciones a mayúsculas, recorta la
# clave y deja ganar la última repetición. AC no hace nada de eso. Para las claves cuya
# ausencia deja el coche sin frenos o sin tracción se lee como AC:
#
# * lectura de INI de AC: archivo UTF-8 completo, sin preprocesar.
# * lectura de INI de AC, por línea (``getline`` en ``\n``; el ``\r``
#   queda en la línea):
#   - si la línea contiene ``[`` y ``]``: cabecera; el nombre es lo que hay entre el
#     primer ``[`` y el primer ``]``. Una cabecera repetida VACÍA la sección anterior;
#   - si no, hace falta ``=``, una sección abierta y que no haya ``;`` antes del ``=``;
#     clave = texto antes del ``=`` SIN recortar; valor = texto tras el ``=`` hasta el
#     primer ``;``, recortado solo por la derecha (``trim2``: espacio, tabulador y
#     ``\r``);
#   - clave repetida con valor no vacío: «ERROR: MULTIPLE KEY FOUND … STILL USING OLD
#     VALUE» (gana la PRIMERA);
#   - comparación exacta: mayúsculas y minúsculas cuentan.
# * lectura de INI de AC: clave ausente ⇒ «KEY_NOT_FOUND» y "".
# * lectura de INI de AC: "" (o un solo espacio) ⇒ 0.0; si no,
#   ``wcstod``: acepta un PREFIJO numérico (y «inf»/«nan»); sin número lanza
#   ``invalid_argument``; con ERANGE lanza ``out_of_range``.


@dataclass
class ACIni:
    """Resultado de leer un INI con la semántica de ``INIReader`` de AC."""

    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    duplicate_keys: list[str] = field(default_factory=list)  # "[S] K" ignoradas
    reset_sections: list[str] = field(default_factory=list)  # cabeceras repetidas


def read_ini_ac(path: str | Path) -> ACIni:
    """Port de lectura de INI de AC/``parse`` de AC (ver el comentario de arriba)."""
    return parse_ini_ac(Path(path).read_bytes().decode("utf-8", errors="replace"))


def parse_ini_ac(text: str) -> ACIni:
    """:func:`read_ini_ac` sobre un texto ya leído (misma semántica)."""
    ini = ACIni()
    current: str | None = None
    for line in text.split("\n"):
        lb, rb = line.find("["), line.find("]")
        if lb != -1 and rb != -1:
            current = line[lb + 1 : rb] if rb > lb else line[lb + 1 :]
            if current in ini.sections:
                ini.reset_sections.append(current)
            ini.sections[current] = {}
            continue
        eq, sc = line.find("="), line.find(";")
        if eq == -1 or not current or (sc != -1 and sc <= eq):
            continue
        key = line[:eq]
        value = (line[eq + 1 :] if sc == -1 else line[eq + 1 : sc]).rstrip(" \t\r")
        if ini.sections[current].get(key):
            ini.duplicate_keys.append(f"[{current}] {key}")
            continue
        ini.sections[current][key] = value
    return ini


_WCSTOD_PREFIX = re.compile(
    r"\s*[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|inf(?:inity)?|nan(?:\([^)]*\))?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ACFloat:
    """Lo que lectura de INI de AC obtendría de una clave."""

    status: str  # ausente | vacío | número | prefijo | no_finito | excepción
    value: float | None  # valor que usaría AC (None si AC lanzaría una excepción)
    raw: str | None  # texto tal cual (None si la clave no existe)


def ac_get_float(ini: ACIni, section: str, key: str) -> ACFloat:
    """Emula ``getString`` + ``getFloat`` sobre un ``ACIni``."""
    raw = ini.sections.get(section, {}).get(key)
    if raw is None:
        return ACFloat("ausente", 0.0, None)
    if raw == "" or (len(raw) == 1 and raw.isspace()):
        return ACFloat("vacío", 0.0, raw)
    m = _WCSTOD_PREFIX.match(raw)
    if not m:
        return ACFloat("excepción", None, raw)  # invalid stof argument
    num = m.group(0).strip()
    value = float(num)
    literal_special = num.lstrip("+-").lower().startswith(("inf", "nan"))
    digits = re.sub(r"[eE].*$", "", num)
    if not literal_special and (
        math.isinf(value) or (value == 0.0 and re.search(r"[1-9]", digits))
    ):
        return ACFloat("excepción", None, raw)  # ERANGE: stof argument out of range
    if not math.isfinite(value):
        return ACFloat("no_finito", value, raw)
    if m.end() < len(raw.rstrip()):
        return ACFloat("prefijo", value, raw)
    return ACFloat("número", value, raw)


def _near_keys(ini: ACIni, section: str, key: str) -> list[str]:
    """Claves que un lector tolerante tomaría por ``[section] key`` y AC no."""
    hits = []
    for sec, keys in ini.sections.items():
        for k in keys:
            if sec.strip().upper() == section and k.strip().upper() == key:
                if (sec, k) != (section, key):
                    hits.append(f"[{sec}] {k!r}")
    return hits


#: (archivo, sección, clave, consecuencia si falta o vale 0, dominio, ¿0 válido?)
CRITICAL_KEYS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (
        "brakes.ini",
        "DATA",
        "MAX_TORQUE",
        "sin freno de servicio: frenos de AC usa MAX_TORQUE·pedal y neumático de AC "
        "max(freno·ABS, freno de mano) deja 0 con un valor ≤ 0",
        "> 0",
        False,
    ),
    (
        "brakes.ini",
        "DATA",
        "FRONT_SHARE",
        "todo el freno en el eje trasero: AC lo leería como 0",
        "[0, 1] (frenos de AC lo recorta a [limitDown, limitUp], "
        "por defecto [0, 1])",
        True,
    ),
    (
        "drivetrain.ini",
        "GEARS",
        "FINAL",
        "sin tracción: la relación total (marcha·FINAL) sería 0 (transmisión 2WD de AC)",
        "≠ 0 (el signo negativo no se rechaza: su efecto no está demostrado)",
        False,
    ),
)


def critical_key_problems(data_dir: str | Path) -> list[tuple[str, bool, str]]:
    """Comprueba las claves de ``CRITICAL_KEYS`` en los INI que existan en ``data_dir``.

    Solo para archivos presentes (que existan lo exige ``required_data``). Devuelve
    ``(nombre, ok, detalle)``; el detalle indica archivo, sección, clave, valor, lo que
    haría AC y la solución. No sustituye ni inventa valores.
    """
    data = Path(data_dir)
    out: list[tuple[str, bool, str]] = []
    cache: dict[str, ACIni] = {}
    for fname, section, key, consequence, domain, zero_ok in CRITICAL_KEYS:
        path = data / fname
        if not path.is_file():
            continue
        ini = cache.setdefault(fname, read_ini_ac(path))
        f = ac_get_float(ini, section, key)
        name = f"{fname} [{section}] {key} ({domain})"
        where = f"data/{fname} [{section}] {key}"
        problem = None
        if f.status in ("ausente", "vacío"):
            hint = ""
            if section not in ini.sections:
                hint = f"; no hay sección [{section}] tal como la lee AC"
            near = _near_keys(ini, section, key)
            if near:
                hint += (
                    f"; existe {', '.join(near)}, pero AC distingue mayúsculas y no "
                    "recorta la clave"
                )
            if section in ini.reset_sections:
                hint += f"; [{section}] está repetida y AC vacía la anterior"
            problem = (
                f"{where}: {'falta' if f.status == 'ausente' else 'vacía'}{hint}. "
                f"AC leería 0 ⇒ {consequence}. "
                f"Solución: escribir {key}=<valor> en [{section}]"
            )
        elif f.status == "excepción":
            problem = (
                f"{where}={f.raw!r}: no es un número que AC pueda leer (wcstod lanza "
                "una excepción). Solución: valor decimal con punto"
            )
        elif f.status == "prefijo":
            problem = (
                f"{where}={f.raw!r}: AC solo leería el prefijo {f.value:g}. "
                "Solución: valor decimal con punto y sin texto añadido"
            )
        elif f.status == "no_finito":
            problem = f"{where}={f.raw!r}: valor no finito. Solución: valor finito"
        else:
            v = f.value
            assert v is not None
            if key == "FRONT_SHARE" and not 0.0 <= v <= 1.0:
                problem = (
                    f"{where}={f.raw!r}: fuera de [0, 1]; AC lo recortaría a "
                    f"{min(max(v, 0.0), 1.0):g}. Solución: fracción en [0, 1] "
                    "(no porcentaje)"
                )
            elif key != "FRONT_SHARE" and (v < 0 or (v == 0 and not zero_ok)):
                if key == "FINAL" and v < 0:
                    problem = None  # efecto no demostrado: no se rechaza
                else:
                    problem = (
                        f"{where}={f.raw!r}: {'0' if v == 0 else 'negativo'} ⇒ "
                        f"{consequence}. Solución: valor {domain}"
                    )
        dup = [d for d in ini.duplicate_keys if d == f"[{section}] {key}"]
        detail = problem or f"{f.raw}" + (
            f" (repetida: AC usa la primera, {f.raw})" if dup else ""
        )
        out.append((name, problem is None, detail))
    return out


def awd_problem(data_dir: str | Path) -> tuple[str, bool, str] | None:
    """Con ``[TRACTION] TYPE=AWD``, ``[AWD] FRONT_SHARE`` debe existir y estar en
    (0, 100]: transmisión de AC lo lee ×0,01 y una clave ausente vale 0 [C].
    el cálculo 4WD de AC lo usa como reparto fijo del par del motor (G2.12,
    ``output/g212_awd_2026-11-02/acs_awd_analisis.md`` §2); aquí solo se exige lo que
    evita un 0 leído en silencio. ``None`` si no hay drivetrain.ini o la tracción no
    es AWD.
    El valor de TYPE se compara sin el comentario ``;`` (lectura de texto de AC no
    verificada para esta clave)."""
    path = Path(data_dir) / "drivetrain.ini"
    if not path.is_file():
        return None
    ini = read_ini_ac(path)
    kind = ini.sections.get("TRACTION", {}).get("TYPE", "").split(";")[0].strip()
    if kind != "AWD":
        return None
    name = "drivetrain.ini [AWD] FRONT_SHARE (0, 100] con TYPE=AWD"
    f = ac_get_float(ini, "AWD", "FRONT_SHARE")
    if f.status in ("ausente", "vacío"):
        return (
            name,
            False,
            "data/drivetrain.ini [AWD] FRONT_SHARE: falta; AC leería 0 "
            "(transmisión de AC ×0,01): todo el par iría al eje trasero (el cálculo 4WD de AC). "
            "Solución: decidir FRONT_SHARE (porcentaje)",
        )
    if f.value is None or f.status != "número" or not 0 < f.value <= 100:
        return (name, False, f"data/drivetrain.ini [AWD] FRONT_SHARE={f.raw!r}")
    return (name, True, f.raw)


AWD_LOCK_KEYS = tuple(
    f"{s}_DIFF_{k}"
    for s in ("FRONT", "REAR", "CENTRE")
    for k in ("POWER", "COAST", "PRELOAD")
)


def awd_lock_problem(data_dir: str | Path) -> tuple[str, bool, str] | None:
    """Con ``[TRACTION] TYPE=AWD``, las 9 claves de bloqueo de ``[AWD]`` deben estar
    presentes y ser números ≥ 0 (G2.12).

    transmisión de AC las lee con ``getFloat`` (ausente ⇒ 0) y el cálculo 4WD de AC usa
    ``lim = PRELOAD + POWER|COAST·|T|·r``: una clave ausente convierte en silencio ese
    diferencial en abierto (``output/g212_awd_2026-11-02/acs_awd_analisis.md`` §2). Un 0
    escrito es válido (diferencial abierto decidido). ``None`` si no hay
    drivetrain.ini o la tracción no es AWD."""
    path = Path(data_dir) / "drivetrain.ini"
    if not path.is_file():
        return None
    ini = read_ini_ac(path)
    kind = ini.sections.get("TRACTION", {}).get("TYPE", "").split(";")[0].strip()
    if kind != "AWD":
        return None
    name = "drivetrain.ini [AWD] 9 claves de bloqueo (POWER/COAST/PRELOAD) con TYPE=AWD"
    bad = []
    for key in AWD_LOCK_KEYS:
        f = ac_get_float(ini, "AWD", key)
        if f.status in ("ausente", "vacío"):
            bad.append(f"{key} falta (AC leería 0: diferencial abierto)")
        elif f.value is None or f.status != "número" or f.value < 0:
            bad.append(f"{key}={f.raw!r}")
    if bad:
        return (name, False, "data/drivetrain.ini [AWD]: " + "; ".join(bad))
    return (name, True, "9 claves presentes")


def axle_problem(data_dir: str | Path) -> tuple[str, bool, str] | None:
    """Con ``suspensions.ini [REAR] TYPE=AXLE`` (G2.44): ``[HEADER] VERSION`` ≥ 4
    (sin él ``ATTACH_REL_POS`` no se lee), ``[AXLE] LINK_COUNT`` entero 3…8, un par
    ``J<n>_CAR``/``J<n>_AXLE`` de tres números por enlace y ``ATTACH_REL_POS`` > 0
    (``SuspensionAxle`` [C]: ausente ⇒ 0 ⇒ muelle en el centro del eje). ``None``
    sin ese TYPE."""
    path = Path(data_dir) / "suspensions.ini"
    if not path.is_file():
        return None
    ini = read_ini_ac(path)
    if ini.sections.get("REAR", {}).get("TYPE", "").split(";")[0].strip() != "AXLE":
        return None
    name = "suspensions.ini TYPE=AXLE: VERSION ≥ 4, LINK_COUNT, J<n> y ATTACH_REL_POS"
    bad = []
    ver = ac_get_float(ini, "HEADER", "VERSION")
    if ver.status != "número" or ver.value is None or ver.value < 4:
        bad.append(f"[HEADER] VERSION={ver.raw!r} (< 4: ATTACH_REL_POS no se lee)")
    n = ac_get_float(ini, "AXLE", "LINK_COUNT")
    count = int(n.value) if n.status == "número" and n.value is not None else 0
    if not 3 <= count <= 8 or (n.value is not None and n.value != count):
        bad.append(f"[AXLE] LINK_COUNT={n.raw!r} (3…8)")
    else:
        for i in range(count):
            for side in ("CAR", "AXLE"):
                raw = ini.sections.get("AXLE", {}).get(f"J{i}_{side}", "")
                parts = raw.split(";")[0].replace(",", " ").split()
                try:
                    ok = len(parts) == 3 and all(math.isfinite(float(x)) for x in parts)
                except ValueError:
                    ok = False
                if not ok:
                    bad.append(f"[AXLE] J{i}_{side}={raw!r} (3 números)")
    a = ac_get_float(ini, "AXLE", "ATTACH_REL_POS")
    if a.status != "número" or a.value is None or not a.value > 0:
        bad.append(f"[AXLE] ATTACH_REL_POS={a.raw!r} (> 0)")
    if bad:
        return (name, False, "; ".join(bad))
    return (name, True, f"{count} enlaces")


def gears_problem(data_dir: str | Path) -> tuple[str, bool, str] | None:
    """``[GEARS] COUNT`` entero ≥ 1 y ``GEAR_R``, ``GEAR_1``…``GEAR_<COUNT>`` números
    finitos ≠ 0 (G2.18).

    transmisión de AC [C] (``docs/research/g211_transmissions_2026-11-02.md``
    tabla de AC): ``COUNT`` con ``getInt`` y ``GEAR_%d`` para 1…COUNT con
    ``addGear`` y ``getFloat`` (ausente ⇒ 0); la relación total es ``FINAL``·marcha
    (el cálculo 2WD de AC/el cálculo 4WD de AC), así que una marcha ausente sería una marcha sin
    tracción en silencio. Riesgo real: la propuesta de portland tiene COUNT 9 y el
    catálogo no tiene ``GEAR_9``. ``None`` sin drivetrain.ini o sin ``[GEARS]``.
    Solo lo usa la exportación de proyectos (``export.validate_package``), no
    ``validate_structure``: así no cambia el resultado de las herramientas anteriores.
    """
    path = Path(data_dir) / "drivetrain.ini"
    if not path.is_file():
        return None
    ini = read_ini_ac(path)
    if "GEARS" not in ini.sections:
        return None
    name = "drivetrain.ini [GEARS] COUNT y GEAR_R, GEAR_1…GEAR_COUNT ≠ 0"
    count = ac_get_float(ini, "GEARS", "COUNT")
    if count.status != "número" or count.value is None or count.value < 1:
        return (name, False, f"COUNT={count.raw!r} ({count.status})")
    n = int(count.value)  # std::stoi trunca
    bad = []
    for key in ["GEAR_R"] + [f"GEAR_{i}" for i in range(1, n + 1)]:
        f = ac_get_float(ini, "GEARS", key)
        if f.status in ("ausente", "vacío"):
            bad.append(f"{key} falta (AC leería 0)")
        elif f.status != "número" or f.value is None or f.value == 0:
            bad.append(f"{key}={f.raw!r}")
    if bad:
        return (name, False, f"COUNT={n}: " + "; ".join(bad))
    return (name, True, f"COUNT={n}, {n} marchas y R presentes")


# G2.20: claves del modelo SCTM (tyres.ini VERSION ≥ 10) cuya ausencia o valor 0 deja el
# neumático sin agarre o con divisiones por cero en AC [C] (AC, G2.20 acs_tyre/):
# FZ0 divide la carga en el deslizamiento del máximo; DY_REF/LS_EXPY dan μ_y; LS_EXPX da
# μ_x
# (DX_REF = 0 ⇒ 1,2·DY_REF, no se exige); CX_MULT multiplica la rigidez longitudinal
# (0 ⇒ Fx = 0); PRESSURE_IDEAL divide la presión dinámica; RATE y ANGULAR_INERTIA como
# en
# G2.19. DAMP no se exige: 0 = neumático sin amortiguación propia (la suspensión sigue
# amortiguando); es una pérdida de fidelidad conocida, no un comportamiento inválido.
SCTM_REQUIRED = (
    "FZ0",
    "DY_REF",
    "LS_EXPY",
    "LS_EXPX",
    "CX_MULT",
    "PRESSURE_IDEAL",
    "RATE",
    "ANGULAR_INERTIA",
)


def tyre_model_problem(data_dir: str | Path) -> tuple[str, bool, str] | None:
    """Con ``tyres.ini`` [HEADER] VERSION ≥ 10 (modelo SCTM, neumático de AC →
    el cálculo de fuerzas del neumático de AC [C]), las claves de :data:`SCTM_REQUIRED` de [FRONT] y [REAR]
    deben
    ser números finitos > 0. ``None`` sin tyres.ini o con VERSION < 10 (el Ibiza usa 7).
    """
    path = Path(data_dir) / "tyres.ini"
    if not path.is_file():
        return None
    ini = read_ini_ac(path)
    ver = ac_get_float(ini, "HEADER", "VERSION")
    if ver.status != "número" or ver.value is None or int(ver.value) < 10:
        return None
    name = "tyres.ini VERSION ≥ 10: claves SCTM > 0 en [FRONT] y [REAR]"
    bad = []
    for sec in ("FRONT", "REAR"):
        for key in SCTM_REQUIRED:
            f = ac_get_float(ini, sec, key)
            if f.status in ("ausente", "vacío"):
                bad.append(f"[{sec}] {key} falta (AC leería 0)")
            elif f.status != "número" or f.value is None or not f.value > 0:
                bad.append(f"[{sec}] {key}={f.raw!r}")
    if bad:
        return (name, False, "; ".join(bad))
    return (name, True, f"VERSION={int(ver.value)}: claves SCTM presentes")


LOAD_REAR_TYPE = "BLOQUEO DE CARGA: suspensions.ini [REAR] TYPE (ensamblado.md §9.4)"
LOAD_AERO = "BLOQUEO DE CARGA: data/aero.ini con WING_n o DATA (ensamblado.md §9.6-9.7)"


def load_requirements(
    inis: dict[str, dict[str, dict[str, str]]],
    ac_inis: dict[str, ACIni] | None = None,
) -> list[tuple[str, bool, str]]:
    """Requisitos cuya ausencia hace que AC aborte la carga del coche (observado).

    Solo los dos con evidencia de un fallo fatal en el juego:

    * ``suspensions.ini [REAR] TYPE``: sin ella, coche de AC termina con
      ``ERROR: Cannot create suspension type:   for wheel:2`` y CSP pide la salida de
      emergencia (§9.4, prueba de la fase 7.1).
    * ``data/aero.ini`` con al menos una sección ``WING_n`` o ``DATA``: sin el archivo,
      AC da ``CRITICAL ERROR: aero.ini does not contain WINGS nor
      DATA`` (§9.6); con solo ``[WING_0] NAME=BODY`` la carga termina (§9.7).

    Devuelve ``(nombre, ok, detalle)``; ``validate_structure`` los cuenta como errores.

    Con ``ac_inis`` (Fase A, revisión 2026-11-10) se leen como AC (:func:`read_ini_ac`):
    clave y sección exactas (mayúsculas), la primera clave repetida gana y una cabecera
    repetida vacía la sección. Así ``type=DWB``, `` TYPE=DWB`` o un ``[REAR]`` repetido
    sin TYPE ya no pasan por buenos. Sin ``ac_inis``, el comportamiento anterior (lector
    tolerante) para quien llame solo con ``inis``.
    """
    if ac_inis is not None:
        return _load_requirements_ac(ac_inis)
    rear = inis.get("suspensions.ini", {}).get("REAR", {}).get("TYPE", "").strip()
    if "suspensions.ini" not in inis:
        rear_detail = "falta data/suspensions.ini"
    else:
        rear_detail = rear or "falta [REAR] TYPE"
    aero = inis.get("aero.ini")
    aero_sections = sorted(
        s for s in aero or {} if s.startswith("WING_") or s == "DATA"
    )
    if aero is None:
        aero_detail = "falta data/aero.ini"
    else:
        aero_detail = ", ".join(aero_sections) or "sin secciones WING_n ni DATA"
    return [
        (LOAD_REAR_TYPE, bool(rear), rear_detail),
        (LOAD_AERO, bool(aero_sections), aero_detail),
    ]


def _load_requirements_ac(ac_inis: dict[str, ACIni]) -> list[tuple[str, bool, str]]:
    """:func:`load_requirements` leyendo los INI como AC."""
    susp = ac_inis.get("suspensions.ini")
    if susp is None:
        rear, rear_detail = "", "falta data/suspensions.ini"
    else:
        rear = susp.sections.get("REAR", {}).get("TYPE", "").strip()
        rear_detail = rear or "falta [REAR] TYPE tal como la lee AC"
        near = _near_keys(susp, "REAR", "TYPE")
        if not rear and near:
            rear_detail += (
                f"; hay {', '.join(near)}, que AC no reconoce (distingue mayúsculas "
                "y no recorta la clave)"
            )
        if not rear and "REAR" in susp.reset_sections:
            rear_detail += "; [REAR] repetida: AC vacía la primera"
    aero = ac_inis.get("aero.ini")
    if aero is None:
        aero_sections: list[str] = []
        aero_detail = "falta data/aero.ini"
    else:
        aero_sections = sorted(
            s for s in aero.sections if s.startswith("WING_") or s == "DATA"
        )
        aero_detail = ", ".join(aero_sections) or (
            "sin secciones WING_n ni DATA tal como las lee AC (mayúsculas exactas)"
        )
    return [
        (LOAD_REAR_TYPE, bool(rear), rear_detail),
        (LOAD_AERO, bool(aero_sections), aero_detail),
    ]


def validate_structure(
    car_dir: str | Path,
    required_data: tuple[str, ...],
    kunos_reference_data: list[str] | None = None,
) -> CarValidation:
    car = Path(car_dir)
    v = CarValidation(str(car))
    data = car / "data"
    for name in required_data:
        v.check(f"existe data/{name}", (data / name).is_file(), str(data / name))
    v.check(
        "existe ui/ui_car.json",
        (car / "ui" / "ui_car.json").is_file(),
        "ui/ui_car.json",
    )
    skins = (
        [d for d in (car / "skins").glob("*") if d.is_dir()]
        if (car / "skins").is_dir()
        else []
    )
    v.check("al menos una skin", bool(skins), [d.name for d in skins])

    inis = (
        {p.name: read_ini(p) for p in sorted(data.glob("*.ini"))}
        if data.is_dir()
        else {}
    )
    # Observado en el juego (fase 7.1, ensamblado.md §9.3): AC toma "[SECCIÓN]" dentro
    # de una línea de comentario como cabecera, y las claves anteriores de esa
    # sección se pierden (TOTALMASS, FUEL, [REAR] TRACK dieron KEY_NOT_FOUND).
    for ini_path in sorted(data.glob("*.ini")) if data.is_dir() else []:
        bad = [
            line.strip()
            for line in ini_path.read_text(encoding="utf-8", errors="replace").split(
                "\n"
            )
            if line.strip().startswith(";") and _BRACKETED.search(line)
        ]
        v.check(f"{ini_path.name} sin [SECCIÓN] en comentarios", not bad, bad)
    for ini_name, sections in inis.items():
        for section, keys in sections.items():
            for key, value in keys.items():
                if value.lower().endswith(FILE_SUFFIXES):
                    base = _reference_base(car, value)
                    v.check(
                        f"referencia {ini_name} [{section}] {key}",
                        (base / value).is_file(),
                        value,
                    )
    engine = inis.get("engine.ini", {})
    turbo = [s for s in engine if s.startswith("TURBO")]
    # [TURBO_n] solo lo escribe src/ac/adjustable_turbo.py (power.lut sin boost + lag y forma de boost de BeamNG):
    # ajustable (COCKPIT_ADJUSTABLE=1 + DEFAULT_TURBO_ADJUSTMENT) o fijo (todos a 0), sin mezclar
    flags = {str(engine[s].get("COCKPIT_ADJUSTABLE", "?")).strip() for s in turbo}
    has_default = "DEFAULT_TURBO_ADJUSTMENT" in engine.get("ENGINE_DATA", {})
    v.check(
        "engine.ini: [TURBO_n] coherentes (todos ajustables con DEFAULT_TURBO_ADJUSTMENT, o todos fijos)",
        not turbo or flags == {"1"} and has_default or flags == {"0"},
        turbo or "ninguna",
    )
    ac_inis = (
        {p.name: read_ini_ac(p) for p in sorted(data.glob("*.ini"))}
        if data.is_dir()
        else {}
    )
    for name, ok, detail in load_requirements(inis, ac_inis):
        v.check(name, ok, detail)
    if "drivetrain.ini" in inis:
        problem = shift_window_problem(inis["drivetrain.ini"])
        v.check(
            "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW > 0 (ensamblado.md §9.15)",
            problem is None,
            problem or inis["drivetrain.ini"]["GEARBOX"]["VALID_SHIFT_RPM_WINDOW"],
        )
    # Claves sin las que AC deja el coche sin frenos o sin tracción, leídas como AC
    # (read_ini_ac/ac_get_float). Solo para archivos presentes.
    if data.is_dir():
        for name, ok, detail in critical_key_problems(data):
            v.check(name, ok, detail)
        awd = awd_problem(data)
        if awd is not None:
            v.check(*awd)
        awd_lock = awd_lock_problem(data)
        if awd_lock is not None:
            v.check(*awd_lock)
    lods = inis.get("lods.ini", {})
    v.check(
        "lods.ini con al menos un LOD",
        any(s.startswith("LOD_") for s in lods),
        sorted(lods),
    )
    if kunos_reference_data is not None:
        ours = {p.name.lower() for p in data.glob("*")} if data.is_dir() else set()
        # Solo .ini: los .lut/.rto son propios de cada coche y los referencia un ini.
        # Se excluyen copias locales ("x - Copy.ini") y archivos de CM ("__*").
        missing = sorted(
            n
            for n in kunos_reference_data
            if n.lower().endswith(".ini")
            and " - " not in n
            and not n.startswith("__")
            and n.lower() not in ours
        )
        v.check(
            "archivos de data/ presentes en el coche de referencia de Kunos",
            not missing,
            missing,
            blocker=True,
        )
    return v


# --- Referencias entre archivos del paquete (errores reales de empaquetado del ciclo de
# 2026-10-24) ---

_PLAIN_NAME = re.compile(
    r"^[A-Za-z0-9_.\- #]+$"
)  # sin comodines ni consultas de CSP ('?', '*', '{…}')


def duplicate_keys(path: str | Path) -> list[str]:
    """``[SECCIÓN] CLAVE`` que aparecen más de una vez (``read_ini`` las fusiona sin
    avisar).
    """
    seen: Counter = Counter()
    section = None
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().upper()
        elif "=" in line and section is not None:
            seen[(section, line.split("=", 1)[0].strip().upper())] += 1
    return [f"[{s}] {k}" for (s, k), n in seen.items() if n > 1]


def knh_bones(path: str | Path) -> list[str]:
    """Nombres de nodo de un ``driver_base_pos.knh``.

    Formato (466/466 instalados): cadena nombre, 16 float, int32 nº de hijos, recursivo.
    Lanza ``ValueError`` si el archivo no se lee completo.
    """
    data = Path(path).read_bytes()
    names: list[str] = []

    def node(p: int) -> int:
        if p + 4 > len(data):
            raise ValueError("knh truncado")
        n = struct.unpack_from("<i", data, p)[0]
        if n < 0 or p + 4 + n + 68 > len(data):
            raise ValueError("knh truncado")
        names.append(data[p + 4 : p + 4 + n].decode("latin1"))
        kids = struct.unpack_from("<i", data, p + 4 + n + 64)[0]
        p += 4 + n + 68
        for _ in range(kids):
            p = node(p)
        return p

    if node(0) != len(data):
        raise ValueError("bytes sobrantes tras el árbol")
    return names


def validate_references(
    car_dir: str | Path, driver_dir: str | Path | None = None
) -> CarValidation:
    """Comprueba que lo que el paquete referencia existe de verdad.

    - Cada ranura de textura de los KN5 de ``lods.ini`` está embebida o existe en
      todas las skins (el coche negro).
    - ``extension/ext_config.ini``: los nombres de ``MATERIALS`` sin comodines
      existen en los KN5.
    - ``data/mirrors.ini``: cada ``NAME`` es una malla única del KN5 del LOD 0.
    - ``data/lights.ini`` y ``EMISSIVE_*`` de CSP: cada ``NAME`` existe en el LOD 0 y
      no abarca varias mallas.
    - ``driver_base_pos.knh`` se lee completo. Con ``driver_dir``
      (``content/driver``), sus huesos ``DRIVER:*`` existen en el modelo de
      ``driver3d.ini``.
    - ``sfx/``: banco ``<carpeta>.bank`` y ``GUIDs.txt`` con ``bank:/<carpeta>``
      (466/466 coches instalados).
    - Sin claves duplicadas en ``data/*.ini`` ni en ``extension/ext_config.ini``.
    """
    from src.ac.kn5_inspect import read_kn5
    from src.ac.kn5_materials import split_kn5

    car = Path(car_dir)
    v = CarValidation(str(car))
    data = car / "data"
    lods = read_ini(data / "lods.ini") if (data / "lods.ini").is_file() else {}
    kn5_names = [
        lods[s]["FILE"]
        for s in sorted(lods)
        if s.startswith("LOD_") and "FILE" in lods[s]
    ]
    skins = (
        [d for d in (car / "skins").glob("*") if d.is_dir()]
        if (car / "skins").is_dir()
        else []
    )
    materials: set[str] = set()
    lod0_meshes: Counter = Counter()
    lod0_nodes: set[str] = set()
    for i, name in enumerate(kn5_names):
        path = car / name
        if not path.is_file():
            v.check(f"existe {name} (lods.ini)", False, name)
            continue
        parts = split_kn5(path.read_bytes())
        embedded = {t.name.lower() for t in parts.textures}
        missing = sorted(
            {
                tex
                for m in parts.materials
                for _, _, tex in m.slots
                if tex.lower() not in embedded
                and not (skins and all((s / tex).is_file() for s in skins))
            }
        )
        v.check(
            f"{name}: texturas referenciadas presentes "
            "(embebidas o en todas las skins)",
            not missing,
            missing,
        )
        materials |= {m.name for m in parts.materials}
        if i == 0:
            lod0 = read_kn5(path, keep_vertices=False).nodes
            lod0_meshes = Counter(n.name for n in lod0 if n.type == 2)
            lod0_nodes = {n.name for n in lod0}
    ext = car / "extension" / "ext_config.ini"
    if ext.is_file():
        dups = duplicate_keys(ext)
        v.check("ext_config.ini sin claves duplicadas", not dups, dups)
        unknown, unverifiable = [], []
        for section, keys in read_ini(ext).items():
            if section.startswith("SHADER_REPLACEMENT") and "MATERIALS" in keys:
                for item in (x.strip() for x in keys["MATERIALS"].split(",")):
                    if not item:
                        continue
                    if not _PLAIN_NAME.match(item):
                        unverifiable.append(item)
                    elif item not in materials:
                        unknown.append(item)
        v.check(
            "ext_config.ini: los materiales nombrados existen en los KN5",
            not unknown,
            unknown or {"no verificables (comodines)": unverifiable},
        )
    mirrors = data / "mirrors.ini"
    if mirrors.is_file():
        for section, keys in read_ini(mirrors).items():
            if section.startswith("MIRROR_"):
                name = keys.get("NAME", "")
                v.check(
                    f"mirrors.ini [{section}] NAME es una malla única del LOD 0",
                    lod0_meshes.get(name, 0) == 1,
                    f"{name}: {lod0_meshes.get(name, 0)} mallas",
                )
    _check_light_names(v, data, ext, lod0_meshes, lod0_nodes)
    knh = car / "driver_base_pos.knh"
    if knh.is_file():
        try:
            bones = knh_bones(knh)
            v.check("driver_base_pos.knh se lee completo", True, len(bones))
        except ValueError as exc:
            bones = []
            v.check("driver_base_pos.knh se lee completo", False, str(exc))
        drv = (
            read_ini(data / "driver3d.ini") if (data / "driver3d.ini").is_file() else {}
        )
        model = drv.get("MODEL", {}).get("NAME")
        if driver_dir is not None and model and bones:
            model_path = Path(driver_dir) / f"{model}.kn5"
            if not model_path.is_file():
                v.check(f"modelo de piloto {model}.kn5", False, str(model_path))
            else:
                have = {n.name for n in read_kn5(model_path, keep_vertices=False).nodes}
                absent = sorted(
                    b for b in bones if b.startswith("DRIVER:") and b not in have
                )
                v.check(f"huesos del knh presentes en {model}.kn5", not absent, absent)
    sfx = car / "sfx"
    if sfx.is_dir():
        bank = sfx / f"{car.name}.bank"
        v.check("sfx/<carpeta>.bank", bank.is_file(), bank.name)
        guids = sfx / "GUIDs.txt"
        text = (
            guids.read_text(encoding="utf-8", errors="replace")
            if guids.is_file()
            else ""
        )
        v.check(
            "sfx/GUIDs.txt con bank:/<carpeta>", f"bank:/{car.name}" in text, guids.name
        )
    for ini in sorted(data.glob("*.ini")) if data.is_dir() else []:
        dups = duplicate_keys(ini)
        v.check(f"{ini.name} sin claves duplicadas", not dups, dups)
    return v


def _check_light_names(v, data: Path, ext: Path, meshes: Counter, nodes: set) -> None:
    """``lights.ini`` (``LIGHT_n``/``BRAKE_n``) y las secciones ``EMISSIVE_*`` de CSP
    nombran mallas o nulos del LOD 0; un nombre no puede abarcar varias mallas: AC/CSP
    encenderían todas (el KN5 exportado repite ``35b84_body0`` en 71 mallas).
    """
    sources = []
    if (data / "lights.ini").is_file():
        sources.append(
            ("lights.ini", read_ini(data / "lights.ini"), ("LIGHT_", "BRAKE_"))
        )
    if ext.is_file():
        sources.append(("ext_config.ini", read_ini(ext), ("EMISSIVE_",)))
    for label, ini, prefixes in sources:
        for section, keys in ini.items():
            if not section.startswith(prefixes) or "NAME" not in keys:
                continue
            for name in (x.strip() for x in keys["NAME"].split(",")):
                if not name:
                    continue
                if not _PLAIN_NAME.match(name):
                    v.check(
                        f"{label} [{section}] NAME verificable",
                        True,
                        f"comodín: {name}",
                    )
                    continue
                ok = name in nodes and meshes.get(name, 0) <= 1
                v.check(
                    f"{label} [{section}] NAME existe y no abarca varias mallas",
                    ok,
                    f"{name}: {meshes.get(name, 0)} mallas"
                    + ("" if name in nodes else " (no existe)"),
                )


def changed_protected(
    base_dir: str | Path, new_dir: str | Path, protected: list[str]
) -> list[str]:
    """Rutas protegidas (física, collider…) que difieren entre dos paquetes o faltan en
    alguno.
    """

    def digest(p: Path) -> str | None:
        return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

    base, new = Path(base_dir), Path(new_dir)
    return [
        rel
        for rel in protected
        if digest(base / rel) is None or digest(base / rel) != digest(new / rel)
    ]
