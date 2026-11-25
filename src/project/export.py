"""Fase B2: exportación aislada de un proyecto a un paquete de coche de Assetto Corsa.

Trabaja SOLO sobre las etapas del proyecto (``src/project``); nunca sobre la ruta
especial del Ibiza (``src.ac.assemble --physics-base``) ni sobre AC.

Origen de cada archivo del paquete (``manifest.json → files[*].provenance``):

* ``data/*.ini``, ``data/power.lut``: etapa ``resolved_ini`` (atributos resueltos,
  claves con regla o decisión). Nunca los borradores de los generadores.
* ``data/car.ini`` ``[GRAPHICS] DRIVEREYES``/``BONNET_CAMERA_POS``/
  ``BUMPER_CAMERA_POS`` y ``data/dash_cam.ini``: medidos en la geometría (etapa
  ``model``; reglas de ``src.ac.cockpit_config`` y ``src.ac.cameras``).
* ``data/lods.ini``, ``data/driver3d.ini``: requisitos de formato
  (``src.ac.assemble.render_lods_ini``, ``src.ac.cockpit_config.render_driver3d_ini``).
* ``<car_id>.kn5``, ``collider.kn5``, ``extension/ext_config.ini``,
  ``data/lights.ini``: etapa ``model``.
* ``ui/ui_car.json``, ``skins/<pintura>/ui_skin.json``: datos del origen
  (``info.json``, ``info_<pc>.json``) y curva de ``power.lut``.

Cada intento va a ``<proyecto>/exports/<NNN>/`` (carpeta nueva; nunca se sobrescribe
ni se borra otra). Dentro: ``<car_id>/`` (el paquete), ``export_report.json``/``.md``
(informe completo), ``attempt.json`` (lo único volátil: fecha y número) y, al final,
``manifest.json``, que sella la carpeta (``src.safe_paths``).

Opciones explícitas (nunca por defecto; fase C1):

* ``local_driver`` + ``ac_root``: pose sentada (``driver_base_pos.knh``) y manos
  (``animations/steer.ksanim``) como v18bu (``src/project/local_driver.py``). Leen un
  archivo de Kunos de la instalación local (solo lectura): el paquete queda marcado
  ``distributable: false``.
* ``audio_bank`` [+ ``audio_source_id``]: banco FMOD precompilado validado por GUID
  (``src/ac/audio.py``) y ``data/sounds.ini`` con la posición de motor del perfil.

Puertas de validación (fase C5): ``<proyecto>/validation_gates.json`` declara grupos de
decisiones provisionales (p. ej. un neumático NO CALIBRADO). Cada puerta que no esté en
``validada`` es un ERROR: el paquete se exporta para inspección, pero nunca con un
veredicto
completo hasta que alguien valide ese grupo con la evidencia que la puerta pide.

Severidad: ``error`` impide declarar el paquete completo; ``warning`` no. El veredicto
nunca es «instalable»: como mucho ``completo_sin_probar_en_el_juego``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.safe_paths import ensure_safe_output_path, new_output_dir
from src.steam_paths import find_game

SCHEMA = "beam2ac.export/1"
VERDICT_BLOCKED = "bloqueado"
VERDICT_COMPLETE = "completo_sin_probar_en_el_juego"
CAR_ID = re.compile(r"^[a-z0-9_]+$")
WHEEL_TOLERANCE_M = 0.01  # criterio de posición de la fase 6
# Archivos de data/ sin los que el paquete no está completo (los del ensamblador,
# más driver3d.ini: sin él CSP falla con driverModel_HR nulo, ensamblado.md §9.8).
REQUIRED_DATA = (
    "car.ini",
    "engine.ini",
    "power.lut",
    "drivetrain.ini",
    "suspensions.ini",
    "brakes.ini",
    "tyres.ini",
    "lods.ini",
    "setup.ini",
    "driver3d.ini",
)
EXPORT_CODE = (
    "src/project/export.py",
    "src/ac/car_validate.py",
    "src/ac/cockpit_config.py",
    "src/ac/cameras.py",
    "src/ac/driver_seat.py",
    "src/project/cockpit_edits.py",
    "src/ac/assemble.py",
    "src/ac/audio.py",
    "src/audio/pipeline.py",
    "src/audio/plan.py",
    "src/audio/fmod_build.py",
    "src/audio/wavinfo.py",
    "src/project/local_driver.py",
)


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    code: str
    detail: str
    target: str = ""


@dataclass
class PackageFile:
    rel: str
    data: bytes
    provenance: dict[str, Any]


@dataclass
class ExportResult:
    dir: Path
    car_dir: Path
    verdict: str
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    build: dict[str, Any] | None = None  # promoción a builds/ (src/project/builds.py)


def default_car_id(vehicle_id: str) -> str:
    """``beam2ac_<id>``: no pisa la carpeta de un coche con el mismo id."""
    clean = re.sub(r"[^a-z0-9_]+", "_", vehicle_id.lower()).strip("_")
    return f"{CAR_ID_PREFIX}{clean}"


# --------------------------------------------------- destino del paquete en AC
# Fase A (revisión 2026-11-10): el paquete se copia a mano a ``content/cars/<car_id>``.
# Un ``car_id`` que coincide con un coche ya instalado (de Kunos, de un mod o un
# paquete beam2ac anterior) lo sobrescribiría al instalarlo. Por eso:
# * el ``car_id`` debe empezar por ``beam2ac_`` (ningún coche de Kunos ni mod conocido
#   usa ese prefijo; los de Kunos son ``ks_*``);
# * con una instalación de AC conocida (``--ac-root``; el CLI usa por defecto la
#   instalación local si existe), un ``car_id`` que ya está en ``content/cars`` se
#   rechaza salvo ``allow_installed_car_id`` explícito (queda como advertencia).
CAR_ID_PREFIX = "beam2ac_"
# Respaldo si el usuario no fijó la ruta en los ajustes: las bibliotecas de Steam detectadas
# (src/steam_paths.py), no rutas fijas del equipo del autor.


def default_ac_root() -> Path | None:
    """Instalación de AC que existe (solo lectura), o ``None``: la de los ajustes del usuario
    (``BEAM2AC_AC_ROOT``) y, si no, las conocidas."""
    env = os.environ.get("BEAM2AC_AC_ROOT")
    detected = find_game("assettocorsa")
    for root in ((Path(env),) if env else ()) + ((detected,) if detected else ()):
        if (root / "content" / "cars").is_dir():
            return root
    return None


def installed_car_collision(car_id: str, ac_root: Path | str | None) -> Path | None:
    """Carpeta de ``content/cars`` que el paquete sobrescribiría, o ``None``.

    Compara sin distinguir mayúsculas (Windows): ``Beam2ac_X`` y ``beam2ac_x`` son la
    misma carpeta para el sistema de archivos de AC.
    """
    if not ac_root:
        return None
    cars = Path(ac_root) / "content" / "cars"
    if not cars.is_dir():
        return None
    for entry in cars.iterdir():
        if entry.name.lower() == car_id.lower():
            return entry
    return None


def check_car_id(
    car_id: str, ac_root: Path | str | None = None, allow_installed: bool = False
) -> list[Issue]:
    """Lanza ``ValueError`` si el ``car_id`` puede sobrescribir un coche de AC.

    Devuelve las advertencias que deben constar en el informe (colisión permitida).
    """
    if not CAR_ID.match(car_id):
        raise ValueError(f"car_id {car_id!r} no es [a-z0-9_]+")
    if not car_id.startswith(CAR_ID_PREFIX):
        raise ValueError(
            f"car_id {car_id!r} sin el prefijo {CAR_ID_PREFIX!r}: podría coincidir con "
            "un coche de Kunos o de un mod y sobrescribirlo al instalar el paquete"
        )
    hit = installed_car_collision(car_id, ac_root)
    if hit is None:
        return []
    if not allow_installed:
        raise ValueError(
            f"car_id {car_id!r} ya está instalado en AC ({hit}): instalar este paquete "
            "lo sobrescribiría. Usa otro --car-id o, si es intencionado, "
            "--allow-installed-car-id"
        )
    return [
        Issue(
            "warning",
            "paquete.car_id_instalado",
            f"el car_id coincide con un coche instalado ({hit}); instalar este "
            "paquete lo sustituiría (permitido de forma explícita)",
        )
    ]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _code_sha() -> str:
    from src.safe_paths import PROJECT_ROOT

    h = hashlib.sha256()
    for rel in sorted(EXPORT_CODE):
        h.update(rel.encode())
        h.update((PROJECT_ROOT / rel).read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------- recogida
def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def source_info(project: Any, variant: str) -> tuple[dict, dict]:
    """``info.json`` (modelo) e ``info_<variant>.json`` (configuración) del origen."""
    from src.jbeam import lax_json, vehicle_dirs_in_zip
    from src.jbeam.sources import ZipMember

    s = project.source

    def parse(text: str) -> dict:
        value = lax_json.loads(text).value
        return value if isinstance(value, dict) else {}

    out = []
    for name in ("info.json", f"info_{variant}.json"):
        if s.kind == "dir":
            p = Path(s.path) / name
            out.append(parse(p.read_text(encoding="utf-8")) if p.is_file() else {})
        else:
            prefix = vehicle_dirs_in_zip(Path(s.path))[0]
            try:
                out.append(parse(ZipMember(Path(s.path), prefix + name).read_text()))
            except KeyError:
                out.append({})
    return out[0], out[1]


def _ini_get(text: str, section: str, key: str) -> str | None:
    from src.project.stages import _ini_pairs

    return _ini_pairs(text).get((section, key))


# G2.18: claves cuya AUSENCIA AC lee como 0 (``getFloat``, validador_sensibilidad §2.3)
# con un efecto físico demostrado, aunque el coche cargue: la base de cada rueda es
# (±TRACK/2, BASEY, (1 − CG_LOCATION)·WHEELBASE | −CG_LOCATION·WHEELBASE)
# (suspensión de AC [C], ac_suspension_coordinates.md §2.7). CG_LOCATION = 0
# pone el CdG sobre el eje trasero y BASEY = 0 el centro de rueda a la altura del CdG.
ZERO_IS_WRONG_KEYS: dict[str, str] = {
    "ac:suspensions.ini/BASIC/CG_LOCATION": "AC leería 0: CdG sobre el eje trasero",
    "ac:suspensions.ini/FRONT/BASEY": "AC leería 0: rueda delantera a la altura "
    "del CdG",
    "ac:suspensions.ini/REAR/BASEY": "AC leería 0: rueda trasera a la altura del CdG",
    "ac:suspensions.ini/BASIC/WHEELBASE": "AC leería 0: los dos ejes en el mismo punto",
    "ac:suspensions.ini/FRONT/TRACK": "AC leería 0: ruedas delanteras en el centro",
    "ac:suspensions.ini/REAR/TRACK": "AC leería 0: ruedas traseras en el centro",
    # masa del cuerpo = TOTALMASS (ac_suspension_coordinates.md §2.8 [C];
    # ensamblado.md §9.13 medido)
    "ac:car.ini/BASIC/TOTALMASS": "AC leería 0: cuerpo sin masa",
    # dirección de AC [C] (G2.15 §2): barra −STEER_LOCK·volante/STEER_RATIO·LSRR
    "ac:car.ini/CONTROLS/STEER_RATIO": "AC leería 0: división por cero en la dirección",
    "ac:car.ini/CONTROLS/STEER_LOCK": "AC leería 0: dirección sin recorrido",
    # transmisión de AC [C] (g212_awd_2026-11-02.md §3): un TYPE que no es
    # FWD/RWD/AWD/AWD2 da un aviso y el tipo 0; la tracción del origen se pierde
    "ac:drivetrain.ini/TRACTION/TYPE": "AC usaría el tipo 0 (aviso), no la tracción "
    "del origen",
    # G2.24: con COAST_CURVE=FROM_COAST_REF, motor de AC lee RPM y TORQUE
    # con getFloat; ausentes ⇒ coast1 = coast2 = 0 ⇒ freno motor nulo (AC, G2.10
    # §2.3). Observado en el juego con los exports G2.23: las rpm no bajan al soltar el
    # gas. NON_LINEARITY = 0 es lineal (válido) y no se incluye.
    "ac:engine.ini/COAST_REF/RPM": "AC leería 0: sin freno motor, las rpm no bajan "
    "al soltar el gas",
    "ac:engine.ini/COAST_REF/TORQUE": "AC leería 0: sin freno motor, las rpm no bajan "
    "al soltar el gas",
    # G2.26: sin [EXPLICIT_INERTIA], coche de AC [C] usa el cálculo de inercia por caja de masa con
    # [BASIC] INERTIA (que el conversor nunca escribe) ⇒ caja (0,0,0): log.txt
    # «LOCAL INERTIA: 0 0 0» en los cuatro 200409 de G2.25. Observado: ciclo de
    # periodo 2 pasos parado con el freno a fondo (Prothean) y cabeceo que descarga
    # las traseras al frenar (Omega).
    "ac:car.ini/EXPLICIT_INERTIA/INERTIA": "AC usaría una carrocería con inercia "
    "(0, 0, 0)",
    # Investigación 2026-11-10: sin [GEARBOX] INERTIA, Drivetrain [C] deja
    # clutchInertia = 1,0 kg·m² reflejada por la marcha; medido en las sesiones de
    # G2.26 (aceleración por marcha a 1–3 % de ese modelo). Con 200409, 40340 y
    # 38022 la regla app.gearbox.inertia@3 la aplica; fuera de ellos es «no aplica».
    "ac:drivetrain.ini/GEARBOX/INERTIA": "AC usaría 1,0 kg·m² de inercia de embrague "
    "(50 veces la de cualquier coche de AC)",
}
# Además, por eje (G2.18; atlanta tiene un puntal delantero sin regla y antes se
# exportaba un [FRONT] sin TYPE, sin muelle y sin puntos con solo advertencias):
# * TYPE: coche de AC lee [FRONT]/[REAR] TYPE en el MISMO bucle de ruedas y solo
#   construye una suspensión si la cadena es STRUT/DWB/ML/AXLE
#   (ac_suspension_coordinates.md §1 [C]); sin [REAR] TYPE la carga aborta
#   (ensamblado.md §9.4, observado);
# * SPRING_RATE: muelle (k + prog·x)·x (G2.17 §8 [C]): ausente ⇒ eje sin muelle;
# * puntos WBCAR_*/WBTYRE_*/STRUT_*: relativos a la base de la rueda (§2.1 [C]):
#   ausentes ⇒ (0, 0, 0), puntos coincidentes. Los que no usa el TYPE resuelto los
#   marca «no aplica» una regla app.* y nunca llegan aquí como bloqueo.
for _axle in ("FRONT", "REAR"):
    ZERO_IS_WRONG_KEYS[f"ac:suspensions.ini/{_axle}/TYPE"] = (
        "sin clase de suspensión (coche de AC no la construye)"
    )
    ZERO_IS_WRONG_KEYS[f"ac:suspensions.ini/{_axle}/SPRING_RATE"] = (
        "AC leería 0: eje sin muelle"
    )
    for _pt in (
        "WBCAR_TOP_FRONT",
        "WBCAR_TOP_REAR",
        "WBCAR_BOTTOM_FRONT",
        "WBCAR_BOTTOM_REAR",
        "WBCAR_STEER",
        "WBTYRE_TOP",
        "WBTYRE_BOTTOM",
        "WBTYRE_STEER",
        "STRUT_CAR",
        "STRUT_TYRE",
    ):
        ZERO_IS_WRONG_KEYS[f"ac:suspensions.ini/{_axle}/{_pt}"] = (
            "AC leería (0, 0, 0): punto en la base de la rueda"
        )


# G2.19: neumático. Con las dimensiones del 200409 resueltas, ``tyres.ini`` existe
# aunque falte el modelo de agarre; sin estas claves AC lee 0 (``KEY_NOT_FOUND``
# observado en el juego con un tyres.ini sin modelo, ensamblado.md §9, pkg_v4/v5: carga
# oscilante y excesiva) y el coche no tendría agarre ni rigidez vertical del neumático;
# ``ANGULAR_INERTIA`` es además el divisor de neumático de AC [C]
# (ac_suspension_coordinates.md §2.8). Antes la ausencia del archivo ya era un error.
for _axle in ("FRONT", "REAR"):
    ZERO_IS_WRONG_KEYS[f"ac:tyres.ini/{_axle}/ANGULAR_INERTIA"] = (
        "AC leería 0: división por cero al integrar el giro de la rueda"
    )
    ZERO_IS_WRONG_KEYS[f"ac:tyres.ini/{_axle}/RATE"] = (
        "AC leería 0: neumático sin rigidez vertical"
    )
    for _k in ("DX0", "DY0"):
        ZERO_IS_WRONG_KEYS[f"ac:tyres.ini/{_axle}/{_k}"] = (
            "AC leería 0: neumático sin agarre de referencia"
        )
    # G2.25 (validación en AC): sin DAMP ni RELAXATION_LENGTH, parados con el freno
    # a fondo la fuerza longitudinal de cada neumático oscila ±8–9 kN y la carga
    # ±400 N; en Legion las ruedas giran y el coche se desliza (1,4 km/h). Con
    # 500 / 0,0725 (medianas de 222 ejes de AC) queda quieto y la carga oscila 37 N.
    ZERO_IS_WRONG_KEYS[f"ac:tyres.ini/{_axle}/DAMP"] = (
        "AC leería 0: neumático sin amortiguación vertical (la carga oscila)"
    )
    ZERO_IS_WRONG_KEYS[f"ac:tyres.ini/{_axle}/RELAXATION_LENGTH"] = (
        "AC leería 0: fuerza del neumático sin relajación (oscila casi parado)"
    )


# G2.30: sin curva de potencia el motor no tiene par (el validador del paquete ya lo
# daba por error)
ZERO_IS_WRONG_KEYS["ac:power.lut"] = "sin power.lut el motor no tiene curva de par"
SCTM_KEYS = ("DY_REF", "LS_EXPX", "LS_EXPY", "CX_MULT")


def sctm_targets(keys: Any) -> frozenset[str]:
    """G2.30: con ``tyres.ini`` VERSION ≥ 10 (modelo SCTM) las claves SCTM sin valor
    son error
    ya en la comprobación previa (antes solo lo detectaba el validador del paquete y la
    aplicación decía «exportable»). ``keys``: dicts con ``target`` y ``value`` (traza o
    resultados). Con VERSION < 10 (Ibiza: 7) no se exigen."""
    ver = next(
        (
            k.get("value")
            for k in keys
            if k.get("target") == "ac:tyres.ini/HEADER/VERSION"
        ),
        None,
    )
    if not isinstance(ver, (int, float)) or isinstance(ver, bool) or ver < 10:
        return frozenset()
    return frozenset(
        f"ac:tyres.ini/{a}/{k}" for a in ("FRONT", "REAR") for k in SCTM_KEYS
    )


def _blocker_severity(
    b: dict[str, Any], attrs: dict[str, Any], extra: frozenset[str] = frozenset()
) -> str:
    """Un bloqueo de ``resolved_ini`` impide exportar si afecta a una clave que AC
    necesita para cargar o conducir: ``required_for_load`` en el catálogo, una clave
    crítica del validador o de :data:`ZERO_IS_WRONG_KEYS` (AC leería 0). Los demás
    dejan la clave ausente (AC usa su valor interno; la base aprobada del Ibiza carga
    sin ellos): advertencia."""
    from src.ac.car_validate import CRITICAL_KEYS

    critical = {f"ac:{f}/{s}/{k}" for f, s, k, *_ in CRITICAL_KEYS}
    target = b.get("target", "")
    if (
        target in critical
        or target in ZERO_IS_WRONG_KEYS
        or target in extra
        or (attrs.get(target) or {}).get("required_for_load")
    ):
        return "error"
    return "warning"


def _graphics_offset(
    car_ini_text: str,
    susp_text: str | None,
    kn5: Path | None,
    issues: list[Issue],
    added: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> str:
    """G2.24: ``[BASIC] GRAPHICS_OFFSET`` coherente con el KN5 y ``suspensions.ini``.

    * Resuelto por los atributos (Ibiza: decisión de la base aprobada): se verifica que
      ``WHEEL_xx + OFFSET`` caiga en la base física de cada rueda.
    * Ausente: se deriva como la traslación rígida que pone los 4 nulos de rueda del KN5
      en sus bases físicas (:mod:`src.ac.graphics_offset`). Antes se dejaba ausente y AC
      leía 0: la carrocería se veía 0,42-0,51 m sobre las ruedas (prueba de G2.23).
    * Sin modelo o sin ``suspensions.ini`` no hay nada que colocar (otros errores).
    """
    from src.ac import cockpit_config
    from src.ac import graphics_offset as gfx
    from src.ac.car_validate import parse_ini_ac

    if kn5 is None or susp_text is None or not kn5.is_file():
        return car_ini_text
    susp = parse_ini_ac(susp_text)
    go_txt = _ini_get(car_ini_text, "BASIC", "GRAPHICS_OFFSET")
    pitch_txt = _ini_get(car_ini_text, "BASIC", "GRAPHICS_PITCH_ROTATION")
    try:
        if pitch_txt not in (None, "") and float(pitch_txt) != 0.0:
            issues.append(
                Issue(
                    "warning",
                    "coherencia.graphics_offset",
                    f"GRAPHICS_PITCH_ROTATION={pitch_txt}: la posición del modelo "
                    "respecto a las ruedas no se comprueba (solo con inclinación 0)",
                )
            )
            return car_ini_text
        if go_txt is not None:
            offset = tuple(float(v) for v in go_txt.split(","))
            if len(offset) != 3:
                raise gfx.GraphicsOffsetError(f"GRAPHICS_OFFSET={go_txt!r}")
            dev = gfx.check(kn5, susp, offset)  # type: ignore[arg-type]
            ctx["graphics_offset"] = {"source": "atributo", "max_wheel_deviation": dev}
            if dev > gfx.RIGID_TOLERANCE_M:
                issues.append(
                    Issue(
                        "error",
                        "coherencia.graphics_offset",
                        f"GRAPHICS_OFFSET={go_txt}: las ruedas del KN5 quedan a "
                        f"{dev:.4f} m de su posición física (> "
                        f"{gfx.RIGID_TOLERANCE_M} m)",
                        "ac:car.ini/BASIC/GRAPHICS_OFFSET",
                    )
                )
            return car_ini_text
        fit = gfx.derive(kn5, susp)
    except (gfx.GraphicsOffsetError, ValueError) as exc:
        issues.append(
            Issue(
                "error",
                "coherencia.graphics_offset",
                f"GRAPHICS_OFFSET no derivable: {exc} — AC leería 0: el modelo se "
                "dibujaría con su origen en el CdG, separado de las ruedas",
                "ac:car.ini/BASIC/GRAPHICS_OFFSET",
            )
        )
        return car_ini_text
    value = gfx.render(fit.offset)
    line = (
        f"GRAPHICS_OFFSET={value}\t\t; traslación KN5 -> cuerpo físico: nulos WHEEL_xx "
        f"del KN5 sobre la base física de cada rueda (TRACK, BASEY, CG_LOCATION, "
        f"WHEELBASE); residuo entre ruedas {fit.residual * 1000:.2f} mm (G2.24)"
    )
    try:
        car_ini_text = cockpit_config.add_basic_line(
            car_ini_text, "GRAPHICS_OFFSET", line
        )
    except cockpit_config.CockpitConfigError as exc:
        issues.append(Issue("error", "coherencia.graphics_offset", str(exc)))
        return car_ini_text
    added.append(
        {
            "key": "[BASIC] GRAPHICS_OFFSET",
            "stage": "model",
            "rule": "src.ac.graphics_offset.derive (nulos de rueda del KN5 + "
            "suspensions.ini)",
            "value": value,
            "residual_m": fit.residual,
        }
    )
    ctx["graphics_offset"] = {
        "source": "derivado",
        "value": list(fit.offset),
        "residual_m": fit.residual,
    }
    return car_ini_text


def _apply_camera_edits(cams: Any, eyes: Any, edits: dict, go: Any) -> Any:
    """Sustituye ojos/capó/parachoques por los ajustes del usuario (G2.32). KN5 → cuerpo:
    valor = KN5 + GRAPHICS_OFFSET (como ``cameras.compute_cameras``)."""
    import dataclasses

    kw: dict[str, Any] = {"dash_pos": tuple(eyes)}
    for item, kn5_f, body_f in (
        ("bonnet", "bonnet_kn5", "bonnet_body"),
        ("bumper", "bumper_kn5", "bumper_body"),
    ):
        if item in edits:
            v = tuple(float(f"{c:.3f}") for c in edits[item])
            kw[kn5_f] = v
            kw[body_f] = tuple(round(v[i] + go[i], 3) for i in range(3))
    return dataclasses.replace(cams, **kw)


def collect(
    project: Any,
    stage_dirs: dict[str, Path | None],
    failures: dict[str, str | None],
    car_id: str,
    options: dict[str, Any] | None = None,
) -> tuple[list[PackageFile], list[Issue], dict[str, Any]]:
    """Archivos del paquete con su procedencia, y problemas de la recogida."""
    options = options or {}
    from . import cockpit_edits

    ce = (
        cockpit_edits.for_project(project)
        if not options.get("ignore_cockpit_edits")
        else {"items": {}, "version": 0}
    )
    ctx_items = ce.get("items") or {}
    from src.ac import cameras as ac_cameras
    from src.ac import cockpit_config
    from src.ac.assemble import build_ui, render_lods_ini
    from src.attrs import schema as S
    from src.attrs.store import Catalog

    files: list[PackageFile] = []
    issues: list[Issue] = []
    ctx: dict[str, Any] = {}
    ctx["cockpit_items"] = ctx_items
    if ctx_items:
        ctx["cockpit_edits"] = {
            "key": ce.get("key"),
            "version": ce.get("version"),
            "items": ctx_items,
        }

    att_dir = stage_dirs.get("attributes")
    if att_dir is not None and (att_dir / "report.json").is_file():
        for f in _load_json(att_dir / "report.json").get("facts", {}).values():
            if (
                f.get("id") == "ir.formats.exporter_version"
                and f.get("kind") == "computed"
            ):
                issues.append(
                    Issue(
                        "warning",
                        "procedencia.version_equivalente",
                        str(f.get("note", "")),
                    )
                )
            elif f.get(
                "id"
            ) == "ir.formats.exporter_version" and "no equivalente" in str(
                f.get("note", "")
            ):
                issues.append(
                    Issue(
                        "warning",
                        "procedencia.version_desconocida",
                        str(f.get("note", "")),
                    )
                )

    def key(stage: str) -> str:
        d = stage_dirs.get(stage)
        return d.name if d is not None else ""

    # CVT (2026-11-19): AC no la tiene; el script de CSP varía FINAL en ejecución
    if att_dir is not None and (att_dir / "cvt_script.lua").is_file():
        files.append(
            PackageFile(
                "data/script.lua",
                (att_dir / "cvt_script.lua").read_bytes(),
                {"stage": "attributes", "stage_key": key("attributes"), "keys": []},
            )
        )
        issues.append(
            Issue(
                "warning",
                "cvt.script_sin_verificar",
                "caja CVT emulada con data/script.lua (CSP): no verificado en AC "
                "(solo corre con la física extendida activa); sin el script la relación "
                "total es fija e igual al grupo final",
            )
        )

    src_dir = stage_dirs.get("source")
    if src_dir is None:
        issues.append(Issue("error", "etapa.source", f"{failures.get('source')}"))
        return files, issues, ctx
    source = _load_json(src_dir / "source.json")
    ctx["vehicle"] = f"{source['vehicle_id']}/{source['variant']}"
    ctx["ref_accel_0_100"] = _declared_accel(stage_dirs.get("attributes"))

    # 1. data/ desde resolved_ini
    res_dir = stage_dirs.get("resolved_ini")
    car_ini_text: str | None = None
    data_texts: dict[str, str] = {}
    if res_dir is None:
        issues.append(
            Issue(
                "error",
                "etapa.resolved_ini",
                f"sin INI resueltos: {failures.get('resolved_ini')}",
            )
        )
    else:
        trace = _load_json(res_dir / "trace.json")
        catalog = Catalog.load(project.catalog_path, project.root)
        attrs = {k: S._to_plain(a) for k, a in catalog.attributes.items()}
        by_file: dict[str, list[dict[str, Any]]] = {}
        for k in trace["keys"]:
            by_file.setdefault(k["file"], []).append(k)
        for p in sorted((res_dir / "data").iterdir()):
            keys = by_file.get(p.name, [])
            prov = {
                "stage": "resolved_ini",
                "stage_key": key("resolved_ini"),
                "keys": [k["target"] for k in keys],
                "decisions": sorted({k["decision"] for k in keys if k["decision"]}),
                "rules": sorted({k["rule"] for k in keys if k["rule"]}),
                "epistemic": dict(
                    sorted(
                        {
                            e: sum(k["epistemic"] == e for k in keys)
                            for e in {k["epistemic"] for k in keys}
                        }.items()
                    )
                ),
            }
            if p.name == "car.ini":
                car_ini_text = p.read_text(encoding="utf-8")
                ctx["car_ini_provenance"] = prov
                continue
            data_texts[p.name] = p.read_text(encoding="utf-8")
            files.append(PackageFile(f"data/{p.name}", p.read_bytes(), prov))
        _ensure_auto_shifter(files, data_texts, issues)
        _apply_driver_aids(options, files, data_texts, issues)
        # primero la plantilla térmica (sobre el compuesto convertido) y después los compuestos de AC:
        # al revés, WEAR_CURVE de los añadidos hace que apply_tyres crea que ya hay térmica y no la aplique
        _apply_thermal(options, files, data_texts, issues, stage_dirs.get("attributes"))
        _apply_tyre_compounds(options, files, data_texts, issues, ctx)
        _apply_adjustable_turbo(
            options, files, data_texts, issues, stage_dirs.get("attributes"), ctx
        )
        _apply_gearbox_template(
            files, data_texts, issues, stage_dirs.get("attributes"), ctx, trace["keys"]
        )
        _add_usage_notes(issues, ctx, stage_dirs.get("attributes"))
        _apply_setup_items(options, files, data_texts, issues)
        _apply_supports_shifter(options, files, data_texts, issues)
        sctm = sctm_targets(trace["keys"])
        for b in trace["blockers"]:
            if b["kind"] in ("archivo", "validación"):
                continue  # validate_package los vuelve a comprobar en el paquete real
            sev = _blocker_severity(b, attrs, sctm)
            if b.get("target") in ctx.get("template_filled", ()):
                sev = "warning"  # rellenado por la plantilla de caja (heurística, ver gearbox.plantilla)
            why = ZERO_IS_WRONG_KEYS.get(b.get("target", ""))
            issues.append(
                Issue(
                    sev,
                    f"resolved_ini.{b['kind']}",
                    b["detail"] + (f" — {why}" if why else ""),
                    b["target"],
                )
            )
        for c in trace.get("conflicts_with_generators", []):
            issues.append(
                Issue(
                    "warning",
                    "resolved_ini.conflicto_con_generador",
                    "se exporta el atributo "
                    f"({c.get('attribute_value')}); el borrador del generador decía "
                    f"{c.get('generator_value')}",
                    c["target"],
                )
            )

        # neumático ESTIMADO sin banco (src/ac/tyre_estimate.py): visible en el informe
        estimated = [
            k
            for k in trace["keys"]
            if any(i.get("kind") == "estimated" for i in k.get("inputs", []))
        ]
        if estimated:
            notes = sorted(
                {
                    m.group(0)
                    for k in estimated
                    for i in k.get("inputs", [])
                    if i.get("kind") == "estimated"
                    and (
                        m := re.search(
                            r"ESTIMADO sin banco \([^)]*\)", i.get("note", "")
                        )
                    )
                }
            )
            issues.append(
                Issue(
                    "warning",
                    "resolved_ini.neumatico_estimado",
                    f"{len(estimated)} claves de tyres.ini salen de un neumático "
                    "ESTIMADO sin banco de BeamNG (no medido): "
                    + " | ".join(notes[:3]),
                    "ac:tyres.ini",
                )
            )

    # 1b. puertas de validación del proyecto
    gates_path = Path(project.root) / "validation_gates.json"
    if gates_path.is_file():
        gates = _load_json(gates_path).get("gates", {})
        ctx["validation_gates"] = gates
        for name, g in sorted(gates.items()):
            if g.get("status") != "validada":
                issues.append(
                    Issue(
                        "error",
                        f"validacion_pendiente.{name}",
                        f"{g.get('reason', '')} — cómo validarlo: {g.get('how', '')}",
                    )
                )

    # 2. modelo 3D
    model_dir = stage_dirs.get("model")
    model: dict[str, Any] | None = None
    kn5_name = f"{car_id}.kn5"
    if model_dir is None:
        issues.append(
            Issue(
                "error",
                "etapa.model",
                f"sin modelo 3D (KN5): {failures.get('model') or 'no ejecutada'}",
            )
        )
    else:
        model = _load_json(model_dir / "model.json")
        mprov = {"stage": "model", "stage_key": key("model")}
        if not model["verification"]["ok"]:
            issues.append(
                Issue(
                    "error",
                    "model.verificacion",
                    "el KN5 no coincide con el plan: "
                    f"{model['verification']['errors']}",
                )
            )
        for w in model.get("warnings", []):
            issues.append(Issue("warning", "model.aviso", w))
        if not model.get("textures"):
            # G2.24: sin ninguna textura los materiales ksPerPixel no tienen txDiffuse
            # y AC pinta el coche negro (informe_prueba_manual_2026-10-24.md; prueba de
            # G2.23). Antes solo era una advertencia y el veredicto salía «completo».
            issues.append(
                Issue(
                    "error",
                    "model.texturas",
                    "el KN5 no tiene texturas difusas: AC dibujaría el coche negro",
                )
            )
        files.append(
            PackageFile(kn5_name, (model_dir / "model.kn5").read_bytes(), mprov)
        )
        files.append(
            PackageFile(
                "collider.kn5", (model_dir / "collider.kn5").read_bytes(), mprov
            )
        )
        if (model_dir / "ext_config.ini").is_file():
            files.append(
                PackageFile(
                    "extension/ext_config.ini",
                    (model_dir / "ext_config.ini").read_bytes(),
                    mprov,
                )
            )
        for anim in sorted((model_dir / "animations").glob("*.ksanim")):
            files.append(
                PackageFile(
                    f"animations/{anim.name}",
                    anim.read_bytes(),
                    {
                        **mprov,
                        "heuristic": "animación de pieza configurada por el usuario, sin probar en AC",
                    },
                )
            )
        if (model_dir / "flames.ini").is_file():
            files.append(
                PackageFile(
                    "data/flames.ini",
                    (model_dir / "flames.ini").read_bytes(),
                    {
                        **mprov,
                        "heuristic": "salidas isExhaust, marco KN5 sin probar en AC",
                    },
                )
            )
            from src.ac.flames import flame_texture_files, render_flame_presets_ini

            for rel, dds in flame_texture_files().items():
                files.append(
                    PackageFile(
                        rel,
                        dds,
                        {
                            "generator": "src.ac.flames",
                            "heuristic": "sprites de llama generados; sin probar en AC",
                        },
                    )
                )

            files.append(
                PackageFile(
                    "data/flame_presets.ini",
                    render_flame_presets_ini().encode("utf-8"),
                    {
                        "generator": "src.ac.flames",
                        "heuristic": "aspecto de la llama, sin probar en AC",
                    },
                )
            )
        if (model_dir / "lights.ini").is_file():
            files.append(
                PackageFile(
                    "data/lights.ini", (model_dir / "lights.ini").read_bytes(), mprov
                )
            )

    # 3. car.ini: atributos + ojos y cámaras medidos en el modelo
    if car_ini_text is not None:
        added: list[dict[str, Any]] = []
        car_ini_text = _graphics_offset(
            car_ini_text,
            data_texts.get("suspensions.ini"),
            model_dir / "model.kn5" if model is not None and model_dir else None,
            issues,
            added,
            ctx,
        )
        eyes = (model or {}).get("drivereyes")
        if model is not None and model_dir is not None:
            from src.ac import driver_seat

            try:  # G2.32: ojos plausibles; si faltan o no lo son, posición por defecto
                rv, rinfo = driver_seat.resolve_eyes(
                    model_dir / "model.kn5",
                    tuple(float(c) for c in eyes["eyes_ac"]) if eyes else None,
                )
                ctx["eyes_resolved"] = list(rv)
                if rinfo["source"] == "default":
                    ctx["eyes_default"] = rinfo
                    eyes = {
                        "eyes_ac": list(rv),
                        "camera": "default",
                        "line": f"DRIVEREYES={rv[0]:.3f}, {rv[1]:.3f}, {rv[2]:.3f}		; posicion por "
                        f"defecto (G2.32): {rinfo['reason']}. {rinfo['rule']}",
                    }
                    issues.append(
                        Issue("warning", "camaras.ojos_por_defecto", rinfo["reason"])
                    )
            except (ValueError, KeyError, OSError) as exc:
                issues.append(Issue("warning", "camaras.ojos", f"sin ojos: {exc}"))
        if eyes:
            vec = tuple(float(f"{c:.3f}") for c in eyes["eyes_ac"])
            orig_vec = vec
            edits = ctx.get("cockpit_items") or {}
            eline = eyes["line"]
            if (
                "driver_eyes" in edits
            ):  # G2.32: ajuste del usuario (los demás se derivan del original)
                vec = tuple(float(f"{c:.3f}") for c in edits["driver_eyes"])
                eline = (
                    f"DRIVEREYES={vec[0]:.3f}, {vec[1]:.3f}, {vec[2]:.3f}		; editado en la "
                    f"interfaz (G2.32), marco del KN5; original {orig_vec[0]:.3f}, "
                    f"{orig_vec[1]:.3f}, {orig_vec[2]:.3f}"
                )
            car_ini_text = cockpit_config.add_graphics_line(
                car_ini_text, "DRIVEREYES", vec, eline
            )
            ctx.setdefault("cockpit_applied", set()).add("driver_eyes")
            added.append(
                {
                    "key": "[GRAPHICS] DRIVEREYES",
                    "stage": "model",
                    "rule": "cockpit_config.driver_eyes (cámara de cabina de BeamNG)",
                }
            )
            try:
                go_txt = _ini_get(car_ini_text, "BASIC", "GRAPHICS_OFFSET")
                pitch_txt = _ini_get(car_ini_text, "BASIC", "GRAPHICS_PITCH_ROTATION")
                go = tuple(float(v) for v in (go_txt or "0, 0, 0").split(","))
                pitch = float(pitch_txt or "0")
                cams = ac_cameras.cameras_from_kn5(
                    model_dir / "model.kn5", orig_vec, go, pitch  # type: ignore[operator]
                )
                cams = _apply_camera_edits(cams, vec, edits, go)
                for k, value, line in (
                    (
                        "BONNET_CAMERA_POS",
                        cams.bonnet_body,
                        ac_cameras.bonnet_line(cams),
                    ),
                    (
                        "BUMPER_CAMERA_POS",
                        cams.bumper_body,
                        ac_cameras.bumper_line(cams),
                    ),
                ):
                    car_ini_text = cockpit_config.add_graphics_line(
                        car_ini_text, k, value, line
                    )
                    added.append(
                        {"key": f"[GRAPHICS] {k}", "stage": "model", "rule": "cameras"}
                    )
                ctx.setdefault("cockpit_applied", set()).update(("bonnet", "bumper"))
                files.append(
                    PackageFile(
                        "data/dash_cam.ini",
                        ac_cameras.render_dash_cam_ini(cams),
                        {
                            "stage": "model",
                            "stage_key": key("model"),
                            "rule": "src.ac.cameras (medianas de coches Kunos)",
                        },
                    )
                )
                if go_txt is None:
                    issues.append(
                        Issue(
                            "warning",
                            "camaras.sin_graphics_offset",
                            "cámaras calculadas con GRAPHICS_OFFSET = 0 (no resuelto)",
                        )
                    )
            except (ac_cameras.CameraError, ValueError) as exc:
                issues.append(Issue("warning", "camaras", f"sin cámaras: {exc}"))
        tank = ctx_items.get("fuel_tank")
        if tank and "[FUELTANK]" in car_ini_text.upper():
            issues.append(
                Issue(
                    "warning",
                    "deposito.ya_definido",
                    "car.ini ya trae [FUELTANK]: la posición del editor no se aplica",
                )
            )
        elif tank:
            car_ini_text, applied = _place_fuel_tank(car_ini_text, tank, issues)
            if applied:
                ctx.setdefault("cockpit_applied", set()).add("fuel_tank")
                added.append(
                    {
                        "key": "[FUELTANK] POSITION",
                        "stage": "cockpit_edits",
                        "rule": "depósito colocado en el editor (marco KN5) + GRAPHICS_OFFSET",
                    }
                )
        files.append(
            PackageFile(
                "data/car.ini",
                car_ini_text.encode("utf-8"),
                {**ctx.get("car_ini_provenance", {}), "added": added},
            )
        )

    # G2.33: un ajuste de cámara guardado que no llega al paquete no se pierde en silencio (ni el manifiesto
    # lo da por aplicado): error nombrando los ítems; ``ignore_cockpit_edits`` es la salida explícita.
    lost = [
        k
        for k in ("driver_eyes", "bonnet", "bumper")
        if k in ctx_items and k not in ctx.get("cockpit_applied", set())
    ]
    if "fuel_tank" in ctx_items and "fuel_tank" not in ctx.get(
        "cockpit_applied", set()
    ):
        # G2.33: un ajuste guardado que no llega al paquete se informa (aviso: ya hay uno más arriba)
        ctx.setdefault("cockpit_unapplied", []).append("fuel_tank")
    if lost:
        issues.append(
            Issue(
                # sin etapa de modelo (--no-model) no hay cámaras que ajustar: aviso, no bloqueo
                "error" if model is not None else "warning",
                "camaras.ajustes_no_aplicados",
                f"ajustes guardados de {', '.join(lost)} sin aplicar: no se pudieron resolver los ojos o las "
                "cámaras (ver «camaras*» más arriba); restáuralos en el editor o exporta con ignore_cockpit_edits",
            )
        )
    # 4. requisitos de formato
    files.append(
        PackageFile(
            "data/lods.ini",
            render_lods_ini(kn5_name).encode("utf-8"),
            {"generator": "src.ac.assemble.render_lods_ini", "references": kn5_name},
        )
    )
    _add_shadows(
        files,
        issues,
        model_dir / "model.kn5" if model is not None and model_dir else None,
    )
    ctx["not_distributable"] = []
    if ctx.get("tyre_compounds_used"):
        ctx["not_distributable"].append(
            "compuestos de neumático copiados de la instalación de Assetto Corsa: material del juego, solo para tu equipo"
        )
    driver_files: dict[str, bytes] = {}
    if options.get("local_driver"):
        from .local_driver import LocalDriverError, hips_only_files, local_driver_files

        try:
            if model is None or model_dir is None:
                raise LocalDriverError("sin modelo 3D")
            if not options.get("ac_root"):
                raise LocalDriverError("falta --ac-root")
            hips_edit = ctx_items.get("hips")
            from .anim_preview import for_project as anim_for_project

            anim = anim_for_project(project).get("edits") or None
            if (model.get("profile") or {}).get("path"):
                driver_files, drep = local_driver_files(
                    model_dir,
                    model,
                    Path(options["ac_root"]),
                    tuple(hips_edit) if hips_edit else None,
                    anim_edits=anim,
                )
            else:  # G2.32: coche sin perfil propio → caderas derivadas de los ojos
                from src.ac import driver_seat

                hips = tuple(hips_edit) if hips_edit else None
                seat_rep: dict[str, Any] = {"rule": "editado en la interfaz (G2.32)"}
                if hips is None:
                    if not ctx.get("eyes_resolved"):
                        raise LocalDriverError("sin ojos para colocar las caderas")
                    eyes0 = tuple(ctx["eyes_resolved"])
                    hips, seat_rep = driver_seat.default_hips(
                        eyes0, driver_seat.roof_function(model_dir / "model.kn5")
                    )
                steer0 = model.get("steering_wheel") or {}
                if steer0.get("rim_material"):
                    # con volante montado (detectado o elegido): pose + manos con las constantes del Ibiza y las
                    # caderas en su misma posición respecto al volante (lo que exige el IK de los brazos)
                    from src.ac.driver_pose import DriverPoseError
                    from src.ac.hands import HandsError
                    from src.ac.steer_anim import SteerAnimError

                    from .local_driver import generic_profile, hips_from_wheel

                    h0 = tuple(hips_edit) if hips_edit else hips_from_wheel(steer0)
                    # el IK de los brazos y el ciclo de manos solo aceptan caderas en una región: si la del
                    # Ibiza no vale (otra forma de aro), se busca la más cercana que sí (±15 cm, y/z)
                    steps = [i * 0.025 for i in range(-6, 7)]
                    grid = sorted(
                        ((a, b) for a in steps for b in steps),
                        key=lambda ab: ab[0] ** 2 + ab[1] ** 2,
                    )
                    # con caderas editadas se prueban tal cual y, si los brazos no llegan, se busca alrededor
                    # de las del volante (antes se descartaban las manos sin buscar: Eagle Inca, exports 014/015)
                    bases = [(h0, ((0.0, 0.0),) if hips_edit else grid)]
                    if hips_edit:
                        bases.append((hips_from_wheel(steer0), grid))
                    last_err: Exception | None = None
                    h = None
                    used_fallback = False
                    for bi, (hb, cands) in enumerate(bases):
                        for dy, dz in cands:
                            h = (hb[0], round(hb[1] + dy, 4), round(hb[2] + dz, 4))
                            try:
                                driver_files, drep = local_driver_files(
                                    model_dir,
                                    model,
                                    Path(options["ac_root"]),
                                    h,
                                    generic=generic_profile(steer0),
                                    anim_edits=anim,
                                )
                                break
                            except (DriverPoseError, HandsError, SteerAnimError) as exc:
                                last_err = exc
                                h = None
                        if h is not None:
                            used_fallback = bi > 0
                            h0 = hb
                            break
                    if used_fallback:
                        issues.append(
                            Issue(
                                "warning",
                                "piloto_local.caderas_editadas_fuera_de_alcance",
                                f"las caderas editadas {list(hips_edit)} quedan fuera del alcance de los brazos "
                                f"para este volante: se usan {list(h)} (búsqueda alrededor de las del volante)",
                            )
                        )
                    if h is None:
                        issues.append(
                            Issue(
                                "warning",
                                "piloto_local.sin_manos",
                                f"manos no generadas ({last_err}): el piloto sale sentado, sin manos animadas"
                                + (
                                    "; probadas las caderas editadas y las del volante ±15 cm"
                                    if hips_edit
                                    else ""
                                ),
                            )
                        )
                        driver_files, drep = hips_only_files(
                            Path(options["ac_root"]), hips
                        )
                    else:
                        moved = round(
                            ((h[1] - h0[1]) ** 2 + (h[2] - h0[2]) ** 2) ** 0.5, 3
                        )
                        seat_rep = {
                            "rule": (
                                "editado en la interfaz (G2.32)"
                                if hips_edit and not used_fallback
                                else "caderas = volante + relación del Ibiza (marco del volante)"
                            ),
                            "hips": list(h),
                            "moved_for_reach_m": moved,
                        }
                        if moved:
                            issues.append(
                                Issue(
                                    "warning",
                                    "piloto_local.caderas_movidas",
                                    f"caderas desplazadas {moved} m respecto a la posición relativa al "
                                    "volante del Ibiza para que las manos alcancen este aro",
                                )
                            )
                else:  # sin volante montado: solo caderas, sin manos
                    driver_files, drep = hips_only_files(Path(options["ac_root"]), hips)
                    issues.append(
                        Issue(
                            "warning",
                            "piloto_local.sin_manos",
                            "sin volante montado en el modelo: el piloto sale sin manos animadas",
                        )
                    )
                drep["seat"] = seat_rep
            ctx["local_driver"] = drep
            ctx.setdefault("cockpit_applied", set()).add("hips")
            ctx["not_distributable"].append(drep["why"])
            dprov = {
                "generator": "src.project.local_driver (v8dp + v12 + v14ha)",
                "pose_source": drep["pose_source"],
                "pose_source_sha256": drep["pose_source_sha256"],
                "profile": drep["profile"],
                "distributable": False,
            }
            for rel, data in sorted(driver_files.items()):
                files.append(PackageFile(rel, data, dprov))
        except LocalDriverError as exc:
            issues.append(Issue("error", "piloto_local", str(exc)))
    if "hips" in ctx_items and "hips" not in ctx.get("cockpit_applied", set()):
        # las caderas solo se aplican con --local-driver: no se da por aplicado lo que no llegó al paquete
        lost.append("hips")
        issues.append(
            Issue(
                "warning",
                "camaras.ajustes_no_aplicados",
                "ajuste guardado de hips sin aplicar (solo se usa con --local-driver y --ac-root)",
            )
        )
    lost += ctx.get("cockpit_unapplied", [])
    if ctx.get("cockpit_edits"):
        ctx["cockpit_edits"]["items"] = {
            k: v for k, v in ctx_items.items() if k not in lost
        }
        if lost:
            ctx["cockpit_edits"]["not_applied"] = lost

    if "data/driver3d.ini" not in driver_files:
        files.append(
            PackageFile(
                "data/driver3d.ini",
                cockpit_config.render_driver3d_ini().encode("utf-8"),
                {"generator": "src.ac.cockpit_config.render_driver3d_ini"},
            )
        )

    # 4b. audio: UN solo camino. Un banco manual (--audio-bank) y uno generado a partir de los
    # WAV de origen (--audio-build, src.audio.pipeline) acaban igual en load_prebuilt.
    def _num(key: str) -> float | None:
        try:
            v = float(
                _ini_get(data_texts.get("engine.ini", ""), "ENGINE_DATA", key) or ""
            )
            return v if v == v and abs(v) != float("inf") else None  # nan/inf = ausente
        except ValueError:
            return None

    bank_dir, audio_origin, audio_run = options.get("audio_bank"), "manual", None
    if options.get("audio_build") and not bank_dir:
        audio_origin = "generado"
        from src.audio import pipeline as audio_pipeline

        audio_root = Path(project.root) / "audio"
        n = len([p for p in audio_root.glob("*") if not p.name.startswith("_")]) + 1
        audio_src = project.source.path
        # de un zip, el WAV se lee de una copia de art/sound
        if project.source.kind == "zip":
            st = Path(project.source.path).stat()
            audio_src = audio_pipeline.extract_zip_audio(
                project.source.path,
                audio_root / f"_src_zip_{st.st_size}_{st.st_mtime_ns}",
            )
        audio_run = audio_pipeline.run(
            audio_src,
            car_id,
            Path(project.root) / "audio" / f"{n:03d}_{car_id}",
            _num("MINIMUM"),
            _num("LIMITER"),
        )
        bank_dir = audio_run.get("prebuilt")
        if not bank_dir:
            issues.append(
                Issue(
                    "warning",
                    "audio.no_construido",
                    audio_run["error"]
                    or audio_run.get("note")
                    or f"sin banco generado (estado del audio: {audio_run['state']})",
                )
            )
    audio_installed = False
    if bank_dir:
        from src.ac import audio as ac_audio

        try:
            audio = ac_audio.load_prebuilt(
                Path(bank_dir),
                car_id,
                None if audio_origin == "generado" else options.get("audio_source_id"),
            )
            aprov = {
                "generator": "src.ac.audio.load_prebuilt",
                "origin": audio_origin,
                **audio.source,
                "events": len(audio.events),
            }
            files.append(PackageFile(f"sfx/{car_id}.bank", audio.bank, aprov))
            files.append(PackageFile("sfx/GUIDs.txt", audio.guids, aprov))
            audio_installed = True
            if audio.source.get("rebound_to"):
                issues.append(
                    Issue(
                        "warning",
                        "audio.reasignado",
                        f"banco construido para {audio.source['built_for']} y "
                        f"reasignado a {car_id} por GUIDs.txt (mismos GUID; "
                        "inferido de "
                        "AC, sin probar). No cargar los dos coches en la misma "
                        "sesión.",
                    )
                )
            sound = (model or {}).get("sound") or _profile_sound(model)
            if sound and sound.get("engine_position"):
                files.append(
                    PackageFile(
                        "data/sounds.ini",
                        ac_audio.render_sounds_ini(
                            sound["engine_position"], sound.get("provenance", "")
                        ).encode("utf-8"),
                        {"source": "perfil del vehículo (sound.engine_position)"},
                    )
                )
            else:
                issues.append(
                    Issue(
                        "warning",
                        "audio.sin_posicion",
                        "sin posición del motor en el perfil: sin data/sounds.ini (AC "
                        "usa el centro de gravedad)",
                    )
                )
        except ac_audio.AudioInputError as exc:
            issues.append(Issue("error", "audio", str(exc)))

    # 4c. audio de origen: inventario honesto (extraído ≠ convertido ≠ escuchado)
    if project.source.kind == "dir":
        from src.audio import pipeline as audio_pipeline
        from src.audio.plan import build_plan
        from src.extract import audio_sources

        scan = audio_sources.scan_audio(project.source.path)
        spec = audio_sources.audio_spec(scan, _num("MINIMUM"), _num("LIMITER"))
        plan_status = (
            audio_run["plan_status"]
            if audio_run
            else build_plan(scan, car_id, _num("MINIMUM"), _num("LIMITER"))["status"]
        )
        ctx["audio"] = {
            "banco_incluido": audio_installed,
            "origen_banco": audio_origin if bank_dir else None,
            **spec,
            **audio_pipeline.ladder(
                plan_status,
                project=bool(
                    audio_run and audio_run.get("stages", {}).get("proyecto_generado")
                ),
                bank=audio_installed,
                installed=audio_installed,
                detected=scan["present"],
            ),
        }
        if audio_run:
            ctx["audio"]["generacion"] = {
                k: audio_run.get(k)
                for k in ("out", "error", "note", "plan_status", "series_used")
            }
        if scan["present"] and not audio_installed:
            issues.append(
                Issue(
                    "warning",
                    "audio.fuente_sin_banco",
                    "el origen trae muestras de motor (extraídas, sin banco en el paquete): el coche "
                    "se exporta sin sfx/ y AC usará sonidos genéricos; pase --audio-build o --audio-bank",
                )
            )
        for w in spec.get("warnings", []):
            issues.append(Issue("warning", "audio.cobertura", w))

    # 5. interfaz y skin
    info, cfg_info = source_info(project, source["variant"])
    paint = (model or {}).get("paint") or cfg_info.get("defaultPaintName1") or "default"
    curve: list[tuple[float, float]] = []
    if "power.lut" in data_texts:
        for line in (ctx.get("ui_power_lut") or data_texts["power.lut"]).splitlines():
            if "|" in line:
                x, y = line.split(";")[0].split("|")
                curve.append((float(x), float(y)))
    traction = None
    if "drivetrain.ini" in data_texts:
        traction = _ini_get(data_texts["drivetrain.ini"], "TRACTION", "TYPE")
    mass = None
    if stage_dirs.get("physics") is not None:
        mass = _load_json(stage_dirs["physics"] / "preview.json").get("mass_kg")
    uprov = {
        "source": ["info.json", f"info_{source['variant']}.json"],
        "power_curve": "data/power.lut" if curve else None,
        "mass": "physics/preview.json mass_kg (medida, sin piloto)",
    }
    if curve and isinstance(mass, (int, float)) and mass > 0:
        ui = build_ui(info, cfg_info, float(mass), curve, traction)
    else:
        ui = {
            "name": info.get("Name", source["vehicle_id"]),
            "brand": info.get("Brand", ""),
            "tags": [],
            "specs": {},
        }
        issues.append(
            Issue(
                "warning",
                "ui.sin_curvas",
                "ui_car.json sin curvas ni especificaciones (falta power.lut o masa)",
            )
        )
    ui["description"] = (
        f"Convertido con beam2ac desde BeamNG ({ctx['vehicle']}). Validación solo "
        "estructural; sin probar en el juego. Revisa la licencia del original."
    )
    from . import details as car_details

    user_details = car_details.load(
        project
    )  # editados en la interfaz («Detalles del coche»)
    changed = car_details.apply_to_ui(ui, user_details)
    if changed:
        uprov["user_details"] = sorted(changed)
    files.append(
        PackageFile(
            "ui/ui_car.json",
            json.dumps(ui, indent=2, ensure_ascii=False).encode("utf-8"),
            uprov,
        )
    )
    skin = re.sub(r"[^a-z0-9_]+", "_", str(paint).lower()).strip("_") or "default"
    # portada de la lista de coches de AC: la imagen del usuario o un render con texturas
    cover = car_details.cover_bytes(project)
    cover_src = "imagen subida por el usuario"
    mdir = stage_dirs.get("model")
    if cover is None and mdir is not None and (mdir / "model.kn5").is_file():
        from .cover import CoverError, render_cover
        from .model import blender_path

        try:
            exe = blender_path()
            if not exe:
                raise CoverError("Blender no disponible")
            cover, cover_src = (
                render_cover(mdir / "model.kn5", exe),
                "render automático con texturas",
            )
        except Exception as exc:  # la portada es opcional: nunca bloquea la exportación
            issues.append(
                Issue("warning", "ui.sin_portada", f"sin imagen de portada: {exc}")
            )
    if cover is not None:
        files.append(
            PackageFile(f"skins/{skin}/preview.jpg", cover, {"source": cover_src})
        )
    # Content Manager avisa sin ``ui/badge.png`` (marca, 128 px) ni ``livery.png`` (icono de la pintura, 64 px)
    from src.ac.logo import default_logo_png

    logo = car_details.logo_bytes(project)
    logo_src = "logo subido por el usuario"
    if logo is None:
        logo, logo_src = default_logo_png(128), "logo b2ac por defecto"
    files.append(PackageFile("ui/badge.png", logo, {"source": logo_src}))
    files.append(
        PackageFile(
            f"skins/{skin}/livery.png",
            default_logo_png(64) if "defecto" in logo_src else _livery_from(logo),
            {"source": logo_src},
        )
    )
    files.append(
        PackageFile(
            f"skins/{skin}/ui_skin.json",
            json.dumps({"skinname": paint}, indent=2).encode("utf-8"),
            {"source": f"info_{source['variant']}.json defaultPaintName1"},
        )
    )
    ctx["kn5"] = kn5_name
    _apply_backfire(options, files, data_texts, issues)
    return files, issues, ctx


def _livery_from(logo: bytes) -> bytes:
    """Icono de la pintura (64 px) a partir del logo del usuario; sin Pillow se reutiliza el PNG tal cual."""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(logo)).convert("RGBA").resize((64, 64))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001 - es solo un icono
        return logo


# ------------------------------------------------------------------- validación
def _suspension_vs_kn5(car: Path, kn5_name: str) -> list[Issue]:
    """``WHEELBASE``/``TRACK`` de ``suspensions.ini`` frente a los nulos de rueda del
    KN5 (eje Z = avance, X = lateral de AC). Una diferencia > 1 cm: incoherencia."""
    from src.ac.car_validate import ac_get_float, read_ini_ac
    from src.ac.kn5_inspect import read_kn5

    susp, kn5 = car / "data" / "suspensions.ini", car / kn5_name
    if not susp.is_file() or not kn5.is_file():
        return []
    wheels = ("WHEEL_LF", "WHEEL_RF", "WHEEL_LR", "WHEEL_RR")
    found = [n for n in read_kn5(kn5, keep_vertices=False).nodes if n.name in wheels]
    pos = {n.name: n.translation for n in found if n.type == 1}
    if len(found) != 4 or len(pos) != 4:
        return [Issue("error", "coherencia.ruedas", f"nulos de rueda: {sorted(pos)}")]
    ini = read_ini_ac(susp)
    out = []
    checks = (
        ("BASIC", "WHEELBASE", abs(pos["WHEEL_LF"][2] - pos["WHEEL_LR"][2])),
        ("FRONT", "TRACK", abs(pos["WHEEL_LF"][0] - pos["WHEEL_RF"][0])),
        ("REAR", "TRACK", abs(pos["WHEEL_LR"][0] - pos["WHEEL_RR"][0])),
    )
    for sec, k, measured in checks:
        got = ac_get_float(ini, sec, k)
        if got.raw is None or got.value is None:
            continue  # ausencia o texto inválido: lo informa el validador
        if (
            not math.isfinite(got.value)
            or abs(got.value - measured) > WHEEL_TOLERANCE_M
        ):
            out.append(
                Issue(
                    "error",
                    "coherencia.ruedas",
                    f"suspensions.ini [{sec}] {k}={got.value:g} frente a "
                    f"{measured:.4f} m entre los nulos de rueda del KN5",
                )
            )
    return out


def _profile_sound(model: dict[str, Any] | None) -> dict[str, Any] | None:
    """``sound`` del perfil del vehículo registrado por la etapa model."""
    prof = (model or {}).get("profile") or {}
    if not prof.get("path"):
        return None
    from src.safe_paths import PROJECT_ROOT

    data = json.loads((PROJECT_ROOT / prof["path"]).read_text(encoding="utf-8"))
    return data.get("sound")


def _declared_accel(attr_dir: Path | None) -> float | None:
    """0-100 km/h de la ficha de origen; ``None`` si no hay. ≥ 900 s es el centinela de Automation («no llegó»)."""
    try:
        v = _load_json(attr_dir / "report.json")["facts"]["ir.declared.accel_0_100"][
            "value"
        ]
        return float(v) if 0 < float(v) < 900 else None
    except Exception:  # sin informe o sin el hecho: sin referencia
        return None


def performance_issues(car: Path, ref_s: float | None) -> list[Issue]:
    """Prestaciones del tren motriz exportado (``src.lab.performance``): solo avisos, nunca bloquea.

    Rango por potencia sin agarre (salida óptima … ralentí): una ficha MÁS RÁPIDA que la salida óptima es incoherente; una más lenta no es atribuible.
    """
    from src.lab import performance as perf

    try:
        spec = perf.read_spec(car / "data")
        res, best = perf.simulate(spec), perf.simulate(spec, launch_slip=True)
    except Exception as exc:  # diagnóstico opcional: nunca debe romper la exportación
        return [
            Issue(
                "warning", "prestaciones.no_evaluable", f"{type(exc).__name__}: {exc}"
            )
        ]
    t100 = res.marks.get(100)
    if t100 is None:
        return [
            Issue(
                "warning",
                "prestaciones.sin_100",
                "el paquete no alcanza 100 km/h (régimen/relaciones)",
            )
        ]
    b100 = best.marks.get(100)
    out = [
        Issue(
            "warning",
            "prestaciones.potencia",
            f"0-100 limitado por potencia: {t100:.2f} s saliendo a ralentí"
            + (
                f", {b100:.2f} s con salida óptima (cota inferior; sin agarre)"
                if b100
                else ""
            ),
        )
    ]
    if ref_s:
        c = perf.compare_with_reference(t100, ref_s, best_launch_s=b100)
        if c["veredicto"] != "coherente":
            out.append(
                Issue(
                    "warning",
                    "prestaciones.ficha",
                    f"ficha {ref_s:g} s frente a {t100:.2f} s: {c['veredicto']}",
                )
            )
    return out


# G2.42: claves de masa/geometría/dirección que AC leería como 0 (ver ZERO_IS_WRONG_KEYS); se comprueban en los
# archivos ESCRITOS, así un paquete editado o corrupto no pasa aunque la resolución fuera correcta.
_CORE_NONZERO = (
    ("car.ini", "BASIC", "TOTALMASS"),
    ("suspensions.ini", "BASIC", "WHEELBASE"),
    ("suspensions.ini", "BASIC", "CG_LOCATION"),
    ("suspensions.ini", "FRONT", "TRACK"),
    ("suspensions.ini", "REAR", "TRACK"),
    ("suspensions.ini", "FRONT", "BASEY"),
    ("suspensions.ini", "REAR", "BASEY"),
)


def _core_zero_issues(data: Path) -> list[Issue]:
    from src.ac.car_validate import ac_get_float, read_ini_ac

    out: list[Issue] = []
    for fname, sec, key in _CORE_NONZERO:
        try:
            f = ac_get_float(read_ini_ac(data / fname), sec, key)
        except OSError:
            continue  # archivo ausente: lo da validate_structure
        if f.value is None or f.value == 0:
            why = ZERO_IS_WRONG_KEYS.get(f"ac:{fname}/{sec}/{key}", "AC leería 0")
            out.append(
                Issue(
                    "error",
                    "paquete.clave_cero",
                    f"{fname} [{sec}] {key}={f.raw!r} ({f.status}): {why}",
                )
            )
    return out


def validate_package(
    car: Path, kn5_name: str, driver_dir: Path | None = None
) -> list[Issue]:
    """Validación del paquete escrito, sin AC: estructura, referencias, nulos,
    duplicados y coherencia. Reutiliza ``src.ac.car_validate``."""
    from collections import Counter

    from src.ac.car_validate import (
        LIKELY_LOAD_NULLS,
        LOAD_NULLS,
        axle_problem,
        gears_problem,
        tyre_model_problem,
        validate_references,
        validate_structure,
    )
    from src.ac.kn5_inspect import read_kn5

    issues: list[Issue] = []
    if not CAR_ID.match(car.name):
        issues.append(
            Issue("error", "paquete.nombre", f"{car.name!r} no es [a-z0-9_]+")
        )
    for v in (
        validate_structure(car, REQUIRED_DATA),
        validate_references(car, driver_dir),
    ):
        for c in v.checks:
            if not c["ok"]:
                issues.append(
                    Issue("error", "validador", f"{c['check']}: {c['detail']}")
                )
    # G2.18: COUNT frente a GEAR_n (AC lee 0 en una marcha ausente)
    gears = gears_problem(car / "data")
    if gears is not None and not gears[1]:
        issues.append(Issue("error", "validador", f"{gears[0]}: {gears[2]}"))
    # G2.44: TYPE=AXLE sin VERSION ≥ 4, enlaces o ATTACH_REL_POS (AC leería 0)
    axle = axle_problem(car / "data")
    if axle is not None and not axle[1]:
        issues.append(Issue("error", "validador", f"{axle[0]}: {axle[2]}"))
    # G2.20: modelo SCTM (VERSION ≥ 10) sin claves esenciales (AC leería 0)
    tyre = tyre_model_problem(car / "data")
    if tyre is not None and not tyre[1]:
        issues.append(Issue("error", "validador", f"{tyre[0]}: {tyre[2]}"))
    issues += _core_zero_issues(car / "data")
    rels = [p.relative_to(car).as_posix() for p in car.rglob("*") if p.is_file()]
    dup = [k for k, n in Counter(r.lower() for r in rels).items() if n > 1]
    if dup:
        issues.append(
            Issue("error", "paquete.duplicados", f"rutas iguales sin mayúsculas: {dup}")
        )
    kn5 = car / kn5_name
    if kn5.is_file():
        names = {n.name for n in read_kn5(kn5, keep_vertices=False).nodes}
        missing = [n for n in LOAD_NULLS + LIKELY_LOAD_NULLS if n not in names]
        if missing:
            issues.append(
                Issue("error", "kn5.nulos", f"faltan nulos de carga: {missing}")
            )
        if "COCKPIT_HR" not in names:
            issues.append(Issue("warning", "kn5.cockpit", "sin COCKPIT_HR (interior)"))
    if not (car / "collider.kn5").is_file():
        issues.append(Issue("error", "paquete.collider", "falta collider.kn5"))
    issues += _suspension_vs_kn5(car, kn5_name)
    # referencias .lut sin uso: no rompen la carga, pero sobran
    data = car / "data"
    if data.is_dir():
        text = "\n".join(p.read_text(encoding="utf-8") for p in data.glob("*.ini"))
        from .resolved_ini import parse_lut, table_problems

        for lut in sorted(data.glob("*.lut")):
            try:
                problems = table_problems(
                    [list(r) for r in parse_lut(lut.read_text(encoding="utf-8"))]
                )
            except ValueError as exc:
                problems = [str(exc)]
            if problems:
                issues.append(
                    Issue("error", "paquete.lut", f"data/{lut.name}: {problems}")
                )
            if lut.name not in text:
                issues.append(
                    Issue("warning", "paquete.lut_sin_uso", f"data/{lut.name}")
                )
    # recursos opcionales ausentes (el coche carga sin ellos)
    for rel, why in (
        ("sfx", "sin sonido (no hay banco FMOD)"),
        ("driver_base_pos.knh", "piloto en la pose por defecto (sin pose)"),
        ("animations", "sin animaciones (manos, volante)"),
        ("ui/badge.png", "sin logotipo de marca en la interfaz"),
    ):
        if not (car / rel).exists():
            issues.append(Issue("warning", "paquete.opcional_ausente", why, rel))
    return issues


# ------------------------------------------------------------------ escritura
def _next_attempt(exports: Path) -> Path:
    n = 1
    while (exports / f"{n:03d}").exists():
        n += 1
    return exports / f"{n:03d}"


def write_export(
    project: Any,
    files: list[PackageFile],
    issues: list[Issue],
    ctx: dict[str, Any],
    car_id: str,
    stage_keys: dict[str, str],
    options: dict[str, Any] | None = None,
    car_id_check_root: Path | str | None = None,
) -> ExportResult:
    """Escribe un intento nuevo. ``car_id_check_root``: instalación de AC (solo
    lectura) contra la que se comprueba que el ``car_id`` no esté ya instalado; no
    entra en el manifiesto ni cambia la validación."""
    options = options or {}
    issues = list(issues) + check_car_id(
        car_id,
        car_id_check_root or options.get("ac_root"),
        bool(options.get("allow_installed_car_id")),
    )
    seen: dict[str, str] = {}
    for f in files:
        if f.rel.lower() in seen:
            raise ValueError(f"dos orígenes para {f.rel}")
        seen[f.rel.lower()] = f.rel
    exports = ensure_safe_output_path(project.root / "exports")
    exports.mkdir(exist_ok=True)
    attempt = new_output_dir(_next_attempt(exports))
    car = attempt / car_id
    for f in sorted(files, key=lambda x: x.rel):
        target = ensure_safe_output_path(car / f.rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f.data)
    try:
        ac_root = options.get("ac_root")
        driver_dir = Path(ac_root) / "content" / "driver" if ac_root else None
        issues = (
            list(issues)
            + validate_package(
                car,
                ctx.get("kn5", f"{car_id}.kn5"),
                driver_dir if driver_dir and driver_dir.is_dir() else None,
            )
            + performance_issues(car, ctx.get("ref_accel_0_100"))
        )
    except Exception as exc:  # el intento queda marcado como fallido, no se borra
        _json(
            attempt / "attempt.json",
            {"attempt": attempt.name, "failed": f"{type(exc).__name__}: {exc}"},
        )
        raise
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    verdict = VERDICT_BLOCKED if errors else VERDICT_COMPLETE
    manifest = {
        "schema": SCHEMA,
        "kind": "beam2ac_export",
        "project": project.id,
        "vehicle": ctx.get("vehicle"),
        "car_id": car_id,
        "verdict": verdict,
        "installed": False,
        "tested_in_game": False,
        "distributable": not ctx.get("not_distributable"),
        "not_distributable_because": ctx.get("not_distributable", []),
        "options": {k: options[k] for k in sorted(options) if options[k]},
        "validation_gates": {
            k: v.get("status")
            for k, v in sorted(ctx.get("validation_gates", {}).items())
        },
        **({"cockpit_edits": ctx["cockpit_edits"]} if ctx.get("cockpit_edits") else {}),
        **(
            {
                "audio": {
                    k: ctx["audio"].get(k)
                    for k in ("state", "stages", "origen_banco", "status", "generacion")
                }
            }
            if (ctx.get("audio") or {}).get("stages")
            and (ctx["audio"].get("rpm_range") or ctx["audio"].get("banco_incluido"))
            else {}
        ),
        **({"eyes_default": ctx["eyes_default"]} if ctx.get("eyes_default") else {}),
        **(
            {"driver_seat": ctx["local_driver"].get("seat")}
            if (ctx.get("local_driver") or {}).get("seat")
            else {}
        ),
        "stage_keys": stage_keys,
        "export_code_sha256": _code_sha(),
        "files": {
            f.rel: {
                "sha256": _sha(f.data),
                "size": len(f.data),
                "provenance": f.provenance,
            }
            for f in sorted(files, key=lambda x: x.rel)
        },
        "errors": len(errors),
        "warnings": len(warnings),
    }
    report = {
        **{k: manifest[k] for k in ("schema", "project", "vehicle", "car_id")},
        "verdict": verdict,
        "why": (
            "hay errores bloqueantes"
            if errors
            else "estructura y referencias completas; NO probado en el juego"
        ),
        "errors": [asdict(i) for i in errors],
        "warnings": [asdict(i) for i in warnings],
    }
    _json(attempt / "export_report.json", report)
    md = [
        f"# Exportación {attempt.name}: {car_id}",
        "",
        f"- Proyecto: `{project.id}` ({ctx.get('vehicle')})",
        f"- Veredicto: **{verdict}**",
        f"- Archivos: {len(files)}; errores: {len(errors)}; advertencias: "
        f"{len(warnings)}",
        "- No se ha instalado nada ni se ha probado en el juego.",
        "",
        "## Errores (bloquean)",
        "",
    ]
    md += [f"- `{i.code}` {i.target} {i.detail}".rstrip() for i in errors] or [
        "- ninguno"
    ]
    md += ["", "## Advertencias", ""]
    md += [f"- `{i.code}` {i.target} {i.detail}".rstrip() for i in warnings] or [
        "- ninguna"
    ]
    md += ctx.get("usage_notes_md", [])
    ensure_safe_output_path(attempt / "export_report.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8"
    )
    import datetime as _dt

    _json(
        attempt / "attempt.json",
        {
            "attempt": attempt.name,
            "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "note": "único archivo volátil del intento",
        },
    )
    _json(attempt / "manifest.json", manifest)  # el último: sella la carpeta
    return ExportResult(attempt, car, verdict, errors, warnings)


def _json(path: Path, data: Any) -> None:
    ensure_safe_output_path(path).write_text(
        json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


_AUTO_SHIFTER_KEYS = "SLIP_THRESHOLD=0.95\nGAS_CUTOFF_TIME=0.3\n"
AUTO_SHIFTER_BLOCK = "[AUTO_SHIFTER]\n" + _AUTO_SHIFTER_KEYS
# Mínimo de ai.ini: solo [GEARS] (lo lee cambio automático de AC); IA sin calibrar, ver g253 §2.
AI_INI_TEXT = "[GEARS]\n" + _AUTO_SHIFTER_KEYS


def _ensure_auto_shifter(
    files: list[PackageFile], data_texts: dict[str, str], issues: list[Issue]
) -> None:
    """``[AUTO_SHIFTER]`` + ``ai.ini [GEARS]`` con ``SLIP_THRESHOLD`` 0,95 y ``GAS_CUTOFF_TIME`` 0,3.

    cambio automático de AC (AC ````) pisa los valores de ``init`` con ``getFloat``
    (clave ausente = 0), exista o no ``ai.ini``: con ``SLIP_THRESHOLD`` = 0 la guarda de
    deslizamiento (``getDrivingTyresSlip() > slipThreshold`` y v > 5 m/s) bloquea todo cambio
    automático por encima de 18 km/h. 0,95 y 0,3 son la referencia de Kunos (g253 §1.3), no los
    0,8/0,5 de ``init``. ``UP``/``DOWN`` se dejan ausentes: con ``UP`` = 0 ``step`` los calcula.
    """
    text = data_texts.get("drivetrain.ini")
    if text is None:
        return
    prov = {
        "stage": "export",
        "stage_key": "",
        "keys": [],
        "auto_shifter": "g253: referencia Kunos; cambio automático de AC (AC)",
    }
    for i, f in enumerate(files):
        if f.rel == "data/drivetrain.ini" and "[AUTO_SHIFTER]" not in text.upper():
            new = text.rstrip("\n") + "\n\n" + AUTO_SHIFTER_BLOCK
            files[i] = PackageFile(
                f.rel,
                new.encode("utf-8"),
                {**f.provenance, "auto_shifter": prov["auto_shifter"]},
            )
            data_texts["drivetrain.ini"] = new
            break
    if "ai.ini" not in data_texts and not any(f.rel == "data/ai.ini" for f in files):
        files.append(PackageFile("data/ai.ini", AI_INI_TEXT.encode("utf-8"), prov))
        data_texts["ai.ini"] = AI_INI_TEXT
    issues.append(
        Issue(
            "warning",
            "ayudas.auto_shifter",
            "[AUTO_SHIFTER] y ai.ini [GEARS]: SLIP_THRESHOLD=0.95, GAS_CUTOFF_TIME=0.3 (Kunos); sin ellos "
            "AC los lee como 0 y el cambio automático no sube de marcha. IA sin calibrar",
        )
    )


def _apply_driver_aids(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
) -> None:
    """Opciones de ABS/TC (``--abs/--tc``): reescribe ``electronics.ini`` y añade las LUT
    de niveles. Sin opciones no cambia nada. Plantilla de diseño: aviso en el informe.
    """
    opts = options.get("driver_aids")
    if not opts:
        return
    from src.ac.driver_aids import apply_driver_aids, from_options, DriverAidsError

    try:
        text, luts, notes = apply_driver_aids(
            data_texts.get("electronics.ini"), *from_options(opts)
        )
    except DriverAidsError as exc:
        issues.append(Issue("error", "ayudas.opciones", str(exc)))
        return
    prov = {"stage": "options", "stage_key": "", "keys": [], "driver_aids": opts}
    if text is not None:
        files[:] = [f for f in files if f.rel != "data/electronics.ini"]
        files.append(PackageFile("data/electronics.ini", text.encode("utf-8"), prov))
        data_texts["electronics.ini"] = text
    for name, body in luts.items():
        files.append(PackageFile(f"data/{name}", body.encode("utf-8"), prov))
    for n in notes:
        issues.append(Issue("warning", "ayudas.plantilla", n))


def _apply_adjustable_turbo(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
    att_dir: Path | None,
    ctx: dict[str, Any],
) -> None:
    """Turbo con lag y forma de boost de BeamNG: ``power.lut`` sin boost + ``[TURBO_n]``.

    Siempre en turbo y turbo+compresor (el lag es el mismo con o sin la casilla); la casilla de «Extras del
    coche» añade el ajuste desde la cabina y también cubre al compresor solo (que sin ella sigue con el boost en
    ``power.lut``: no tiene lag que reproducir)."""
    from src.ac import adjustable_turbo as AT

    adjustable = bool(options.get("adjustable_turbo"))
    facts = _load_json(att_dir / "report.json").get("facts", {}) if att_dir else {}
    kind = (facts.get("ir.engine.boost_kind") or {}).get("value")
    if not adjustable and kind not in ("turbo", "both"):
        return
    if "power.lut" not in data_texts or "engine.ini" not in data_texts:
        issues.append(
            Issue(
                "error" if adjustable else "warning",
                "turbo.ajustable",
                "sin power.lut o engine.ini: no se puede aplicar el turbo con lag",
            )
        )
        return
    try:
        lut, ini, notes = AT.apply(
            data_texts["power.lut"],
            data_texts["engine.ini"],
            facts,
            adjustable,
            None if adjustable else AT.DEFAULT_MAX_ERR,
        )
    except AT.AdjustableTurboError as exc:
        issues.append(
            Issue("error" if adjustable else "warning", "turbo.ajustable", str(exc))
        )
        return
    ctx["ui_power_lut"] = data_texts[
        "power.lut"
    ]  # la interfaz de AC muestra la curva CON boost (al 100 %)
    meta = {"adjustable_turbo": adjustable, "turbo_lag": kind != "supercharger"}
    _replace_data(files, data_texts, "power.lut", lut, meta)
    _replace_data(files, data_texts, "engine.ini", ini, meta)
    for n in notes:
        issues.append(Issue("warning", "turbo.ajustable", n))
    issues.append(
        Issue(
            "warning",
            "turbo.ajustable",
            "las validaciones en juego de ac:power.lut (curva con boost) no cubren este paquete: "
            "el par con boost ya sale de [TURBO_n] y está sin medir en AC",
        )
    )


def _apply_setup_items(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
) -> None:
    """Reglajes opcionales del setup de boxes: reparto de frenada y presión de neumáticos."""
    # la presión de neumáticos va SIEMPRE en el setup (no es opcional)
    bias, pressure = options.get("setup_brake_bias"), True
    from src.ac import setup_items as SI

    try:
        text, notes = SI.apply(
            data_texts.get("setup.ini", ""),
            data_texts.get("tyres.ini"),
            data_texts.get("brakes.ini"),
            brake_bias=bool(bias),
            tyre_pressure=bool(pressure),
        )
    except SI.SetupItemsError as exc:
        issues.append(Issue("error", "setup.reglajes", str(exc)))
        return
    _replace_data(files, data_texts, "setup.ini", text, {"setup_items": True})
    for n in notes:
        issues.append(Issue("warning", "setup.reglajes", n))


def _add_usage_notes(
    issues: list[Issue], ctx: dict[str, Any], att_dir: Path | None
) -> None:
    """Notas de uso en el informe y avisos automáticos (``src/project/usage_notes.py``)."""
    from . import usage_notes as un

    facts = _load_json(att_dir / "report.json").get("facts", {}) if att_dir else {}
    ctx["usage_notes_md"] = un.report_lines(facts)
    for w in un.auto_warnings(facts):
        issues.append(Issue("warning", "uso.nota", w))
    sc = (facts.get("ir.engine.supercharger_type") or {}).get("value")
    if sc:
        issues.append(
            Issue(
                "warning",
                "compresor.tipo",
                f"compresor '{sc}' (JBeam del exportador): BeamNG solo lo usa para el sonido; el boost sale del "
                "mapa de presión (en power.lut, o como [TURBO_n] sin lag con la casilla de turbo ajustable). "
                "AC no tiene sección de compresor: tipo sin reflejar y sin probar en AC",
            )
        )


def _apply_backfire(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
) -> None:
    """``data/sounds.ini [BACKFIRE]`` (src/ac/backfire.py): sin él AC usa MINRPM = 9750 y no hay llamas.

    Solo con ``data/flames.ini`` en el paquete y ``options['backfire']`` no desactivado; ``options['backfire_params']``
    (``min_rpm``, ``max_rpm``, ``max_gas``) los sobrescribe."""
    from src.ac import backfire as bf
    from src.ac import gearbox_templates as gt

    if options.get("backfire") is False or not any(
        f.rel == "data/flames.ini" for f in files
    ):
        return
    limiter = gt.read_ini(data_texts.get("engine.ini", "")).get(
        ("ENGINE_DATA", "LIMITER")
    )
    try:
        vals = bf.backfire_values(
            float(limiter), **(options.get("backfire_params") or {})
        )
    except (TypeError, ValueError, OverflowError) as exc:
        issues.append(Issue("warning", "llamas.backfire", f"sin [BACKFIRE]: {exc}"))
        return
    old = next((f for f in files if f.rel == "data/sounds.ini"), None)
    base = old.data.decode("utf-8") if old is not None else ""
    if "[BACKFIRE]" in base.upper():
        return  # ya definido (banco de audio propio)
    text = gt.set_ini_keys(base, bf.SECTION, vals)
    prov = {
        **(old.provenance if old is not None else {}),
        "heuristic": "[BACKFIRE] de las llamas, sin probar en AC",
    }
    files[:] = [f for f in files if f.rel != "data/sounds.ini"]
    files.append(PackageFile("data/sounds.ini", text.encode("utf-8"), prov))
    issues.append(
        Issue(
            "warning",
            "llamas.backfire",
            f"[BACKFIRE] en data/sounds.ini: MINRPM={vals['MINRPM']} MAXRPM={vals['MAXRPM']} "
            f"MAXGAS={vals['MAXGAS']}; las llamas salen levantando el gas solo en parte "
            "(0 < gas < MAXGAS·gas máx.), no soltándolo del todo"
            + (
                ""
                if old is not None
                else "; sin banco de audio: sounds.ini solo con [BACKFIRE] (probar que el coche carga)"
            ),
        )
    )


def _add_shadows(
    files: list[PackageFile], issues: list[Issue], kn5: Path | None
) -> None:
    """Sombras falsas de AC (``src/ac/shadows.py``): PNG generados y ``ambient_shadows.ini``; batalla del KN5."""
    from src.ac import shadows
    from src.ac.kn5_inspect import read_kn5

    if kn5 is None or not kn5.is_file():
        return
    zs: dict[str, float] = {}
    for n in read_kn5(kn5, keep_vertices=False).nodes:
        if n.name.startswith("WHEEL_") and n.type == 1:
            zs[n.name[-2:]] = n.translation[2]
    front = [zs[k] for k in ("LF", "RF") if k in zs]
    rear = [zs[k] for k in ("LR", "RR") if k in zs]
    if not front or not rear:
        issues.append(
            Issue("warning", "sombras", "sin nulos WHEEL_xx: no se generan las sombras")
        )
        return
    wheelbase = sum(front) / len(front) - sum(rear) / len(rear)
    have = {f.rel for f in files}
    for rel, data in shadows.shadow_files(abs(wheelbase)).items():
        if rel not in have:
            files.append(
                PackageFile(
                    rel,
                    data,
                    {
                        "generator": "src.ac.shadows",
                        "heuristic": "tamaños de Kunos observados; sin probar en AC",
                    },
                )
            )
    issues.append(
        Issue(
            "warning",
            "sombras.generadas",
            f"sombras de contacto generadas (WIDTH=1.0, LENGTH={abs(wheelbase) + 0.4:.2f}; heurística, sin probar en AC)",
        )
    )


def _place_fuel_tank(
    car_ini_text: str, tank: list[float], issues: list[Issue]
) -> tuple[str, bool]:
    """``[FUELTANK] POSITION`` va en el marco FÍSICO (origen en el CdG; Celica ST185: 0,-0.15,-1.45);
    el editor trabaja en el marco del KN5: físico = KN5 + GRAPHICS_OFFSET (src/ac/graphics_offset.py).
    """
    from src.ac.gearbox_templates import set_ini_keys

    go_txt = _ini_get(car_ini_text, "BASIC", "GRAPHICS_OFFSET")
    pitch_txt = _ini_get(car_ini_text, "BASIC", "GRAPHICS_PITCH_ROTATION")
    try:
        go = tuple(float(v) for v in (go_txt or "").split(","))
        ok = len(go) == 3 and float(pitch_txt or 0) == 0.0
    except ValueError:
        ok = False
    if not ok:
        issues.append(
            Issue(
                "warning",
                "deposito.sin_graphics_offset",
                "depósito colocado en el editor sin aplicar: falta GRAPHICS_OFFSET (o hay inclinación) "
                "para pasar de marco KN5 a marco físico",
            )
        )
        return car_ini_text, False
    phys = ",".join(f"{c + o:.3f}" for c, o in zip(tank, go))
    return set_ini_keys(car_ini_text, "FUELTANK", {"POSITION": phys}), True


def _apply_gearbox_template(
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
    att_dir: Path | None,
    ctx: dict[str, Any],
    trace_keys: list[dict[str, Any]],
) -> None:
    """Plantilla de caja del colaborador (heurística, ``src/ac/gearbox_templates.py``); tras el ajuste de turbo.

    Una clave de ``drivetrain.ini`` con decisión del usuario no se toca; el resto se sustituye y los valores
    anteriores quedan en la procedencia (``gearbox_template_overrides``)."""
    from src.ac import gearbox_templates as gt

    if "drivetrain.ini" not in data_texts or att_dir is None:
        return
    facts = _load_json(att_dir / "report.json").get("facts", {})
    kind = (facts.get("ir.drivetrain.gearbox.kind") or {}).get("value")
    if kind not in gt.KIND_TO_TYPE:
        if kind:  # p. ej. cvtGearbox: sin plantilla
            issues.append(
                Issue("warning", "gearbox.plantilla", f"caja '{kind}': sin plantilla")
            )
        return
    # par máximo CON boost (la interfaz de AC lo muestra así); sin turbo ajustable es power.lut
    lut = ctx.get("ui_power_lut") or data_texts.get("power.lut", "")
    eng = gt.engine_inputs(data_texts.get("engine.ini", ""), lut)
    if eng is None:
        issues.append(
            Issue(
                "warning",
                "gearbox.plantilla",
                "sin ralentí, corte o par máximo: no se aplica la plantilla de caja",
            )
        )
        return
    ai_prev = data_texts.get("ai.ini")
    protected = frozenset(
        (m.group(1).upper(), m.group(2).upper())
        for k in trace_keys
        if k.get("decision")
        and (m := re.match(r"ac:drivetrain\.ini/([^/]+)/([^/]+)$", k.get("target", "")))
    )
    old = gt.read_ini(data_texts["drivetrain.ini"])
    old_ai = gt.read_ini(ai_prev or "")
    dt, ai = gt.apply_gearbox(
        data_texts["drivetrain.ini"], ai_prev, kind, eng, protected
    )
    new = gt.read_ini(dt)
    overrides = {
        f"{s}/{k}": old[(s, k)]
        for (s, k), v in new.items()
        if (s, k) in old and old[(s, k)] != v
    }
    added_keys = sorted(f"{s}/{k}" for (s, k) in new if (s, k) not in old)
    ai_over = {
        f"{s}/{k}": old_ai[(s, k)]
        for (s, k), v in gt.read_ini(ai).items()
        if (s, k) in old_ai and old_ai[(s, k)] != v
    }
    kept = sorted("/".join(p) for p in protected)
    meta = {
        "gearbox_template": gt.KIND_TO_TYPE[kind],
        "gearbox_template_overrides": overrides,
        "gearbox_template_added": added_keys,
        "gearbox_template_kept_decisions": kept,
    }
    _replace_data(files, data_texts, "drivetrain.ini", dt, meta, keep_prov=True)
    _replace_data(
        files,
        data_texts,
        "ai.ini",
        ai,
        {
            "gearbox_template": meta["gearbox_template"],
            "gearbox_template_overrides": ai_over,
        },
        keep_prov=True,
    )
    # claves que la plantilla rellena y que resolved_ini daba por no resueltas: se informan como aviso
    ctx["template_filled"] = {
        f"ac:drivetrain.ini/{k}"
        for k in added_keys
        if k.startswith(("CLUTCH/", "GEARBOX/"))
    }
    final = gt.read_ini(
        dt
    )  # valores que quedan en el archivo (con decisiones respetadas)
    mt = final.get(("CLUTCH", "MAX_TORQUE"), "?")
    up = float(final.get(("AUTO_SHIFTER", "UP"), eng.limiter_rpm - gt.UP_MARGIN_RPM))
    down = float(
        final.get(("AUTO_SHIFTER", "DOWN"), gt.DOWN_FRACTION * eng.limiter_rpm)
    )
    issues.append(
        Issue(
            "warning",
            "gearbox.plantilla",
            f"plantilla de caja '{meta['gearbox_template']}' (heurística del colaborador, sin medir en AC): "
            f"MAX_TORQUE={mt} N·m, AUTO_SHIFTER UP={up:.0f} DOWN={down:.0f} rpm"
            + (f"; sustituye {sorted(overrides)}" if overrides else "")
            + (f"; claves nuevas {added_keys}" if added_keys else "")
            + (f"; respeta decisiones {kept}" if kept else "")
            + (
                f"; DOWN acotado (0,55·limitador = {gt.DOWN_FRACTION * eng.limiter_rpm:.0f}) para no oscilar tras subir"
                if "AUTO_SHIFTER/DOWN" not in kept
                and abs(down - gt.DOWN_FRACTION * eng.limiter_rpm) > 1
                else ""
            ),
        )
    )
    hunt = gt.shift_hunting(dt, up, down)
    if hunt:
        issues.append(
            Issue(
                "warning",
                "gearbox.oscilacion",
                f"subir de la marcha {hunt} a la siguiente en UP={up:.0f} rpm deja el motor por debajo de "
                f"DOWN={down:.0f}: AC podría bajar de inmediato y oscilar (revisar UP/DOWN)",
            )
        )


def _apply_supports_shifter(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
) -> None:
    """``drivetrain.ini [GEARBOX] SUPPORTS_SHIFTER=0/1`` (palanca de cambios en AC) si la interfaz lo pidió.

    Solo para cajas manuales o secuenciales; con otra caja (``options`` sin la clave) no se toca.
    """
    if "supports_shifter" not in options:
        return
    text = data_texts.get("drivetrain.ini")
    if text is None:
        issues.append(
            Issue(
                "error",
                "gearbox.shifter",
                "sin drivetrain.ini: no se puede fijar SUPPORTS_SHIFTER",
            )
        )
        return
    line = f"SUPPORTS_SHIFTER={1 if options['supports_shifter'] else 0}"
    lines = text.splitlines()
    lines = [x for x in lines if not x.strip().upper().startswith("SUPPORTS_SHIFTER")]
    idx = next(
        (i for i, x in enumerate(lines) if x.strip().upper() == "[GEARBOX]"), None
    )
    if idx is None:
        lines += ["", "[GEARBOX]", line]
    else:
        lines.insert(idx + 1, line)
    _replace_data(
        files,
        data_texts,
        "drivetrain.ini",
        "\n".join(lines) + "\n",
        {"supports_shifter": True},
    )


def _apply_tyre_compounds(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
    ctx: dict[str, Any],
) -> None:
    """Compuestos de AC marcados en «Detalles del coche»: el neumático convertido pasa a llamarse
    «Exported» y se añaden los elegidos como plantillas de diseño leídas de la instalación de AC.
    """
    ids = options.get("tyre_compounds")
    if not ids:
        return
    from src.ac import tyre_compounds as TC

    if "tyres.ini" not in data_texts:
        issues.append(
            Issue(
                "error",
                "neumaticos.compuestos",
                "sin tyres.ini: no se pueden añadir compuestos",
            )
        )
        return
    if not options.get("ac_root"):
        issues.append(
            Issue(
                "error",
                "neumaticos.compuestos",
                "falta la ruta de AC: de ahí salen los compuestos",
            )
        )
        return
    try:
        text, extra, notes = TC.apply(
            data_texts["tyres.ini"], options["ac_root"], list(ids)
        )
    except TC.CompoundError as exc:
        issues.append(Issue("error", "neumaticos.compuestos", str(exc)))
        return
    _replace_data(files, data_texts, "tyres.ini", text, {"tyre_compounds": list(ids)})
    for name, body in extra.items():
        files[:] = [f for f in files if f.rel != f"data/{name}"]
        files.append(
            PackageFile(
                f"data/{name}",
                body,
                {
                    "stage": "options",
                    "stage_key": "",
                    "keys": [],
                    "tyre_compounds": list(ids),
                },
            )
        )
    ctx["tyre_compounds_used"] = list(ids)
    for n in notes:
        issues.append(Issue("warning", "neumaticos.compuestos", n))


def _replace_data(
    files: list[PackageFile],
    data_texts: dict[str, str],
    name: str,
    text: str,
    extra_prov: dict[str, Any] | None = None,
    keep_prov: bool = False,
):
    """``keep_prov``: conserva la procedencia del archivo sustituido (decisiones, reglas) y añade la marca."""
    prov = {"stage": "options", "stage_key": "", "keys": [], "thermal_template": True}
    if extra_prov:
        prov = {k: v for k, v in prov.items() if k != "thermal_template"} | extra_prov
    old = next((f for f in files if f.rel == f"data/{name}"), None)
    if keep_prov and old is not None:
        prov = {
            **old.provenance,
            **{
                k: v for k, v in prov.items() if k not in ("stage", "stage_key", "keys")
            },
        }
    files[:] = [f for f in files if f.rel != f"data/{name}"]
    files.append(PackageFile(f"data/{name}", text.encode("utf-8"), prov))
    data_texts[name] = text


def _apply_thermal(
    options: dict[str, Any],
    files: list[PackageFile],
    data_texts: dict[str, str],
    issues: list[Issue],
    att_dir: Path | None = None,
) -> None:
    """Plantillas térmicas opt-in (g254): diseño etiquetado, sin calibrar en AC."""
    from src.ac import thermal_templates as tt

    kind, pad = options.get("thermal_tyres"), options.get("thermal_brakes")
    if kind and "tyres.ini" in data_texts:
        new = tt.apply_tyres(data_texts["tyres.ini"], kind)
        if new != data_texts["tyres.ini"]:
            _replace_data(files, data_texts, "tyres.ini", new, keep_prov=True)
            for name, body in tt.tyre_files(kind).items():
                _replace_data(files, data_texts, name, body)
            issues.append(
                Issue(
                    "warning",
                    "termica.neumaticos",
                    f"plantilla de diseño '{kind}' ({'valores del colaborador' if kind == 'colab' else 'Kunos'}), sin calibrar",
                )
            )
    if pad and "brakes.ini" in data_texts:
        facts = _load_json(att_dir / "report.json").get("facts", {}) if att_dir else {}
        kw = tt.brake_params(facts)
        if kw is None:
            issues.append(
                Issue(
                    "warning",
                    "termica.frenos",
                    "sin datos de disco/pastilla completos (o tambor): no se añade [TEMPS_*]",
                )
            )
        else:
            if pad != "auto":
                kw["pad"] = pad
            new = tt.apply_brakes(data_texts["brakes.ini"], **kw)
            if new != data_texts["brakes.ini"]:
                _replace_data(files, data_texts, "brakes.ini", new, keep_prov=True)
                issues.append(
                    Issue(
                        "warning",
                        "termica.frenos",
                        f"[TEMPS_*] pastilla '{kw['pad']}', discos del JBeam (hip, sin calibrar en AC)",
                    )
                )


def export_project(
    project: Any,
    car_id: str | None = None,
    with_model: bool = True,
    log: Callable[[str], None] = print,
    local_driver: bool = False,
    ac_root: Path | str | None = None,
    audio_bank: Path | str | None = None,
    audio_source_id: str | None = None,
    allow_installed_car_id: bool = False,
    audio_build: bool = False,
    car_id_check_root: Path | str | None = None,
    driver_aids: dict[str, Any] | None = None,
    thermal_tyres: str | None = None,
    thermal_brakes: str | None = None,
    tyre_compounds: list[str] | None = None,
    adjustable_turbo: bool = False,
    setup_brake_bias: bool = False,
    setup_tyre_pressure: bool = True,  # obligatorio: se ignora False
    supports_shifter: bool | None = None,
    backfire: bool = True,
    backfire_params: dict[str, float] | None = None,
) -> ExportResult:
    """Ejecuta (o reutiliza de la caché) las etapas y escribe un intento nuevo.

    ``car_id_check_root``: instalación de AC para detectar un ``car_id`` ya instalado
    (por defecto, ``ac_root``). El CLI pasa la instalación local conocida.
    """
    from .orchestrator import run_project

    check_root = car_id_check_root or ac_root
    if car_id:  # antes de ejecutar etapas: un car_id inválido no gasta trabajo
        check_car_id(car_id, check_root, allow_installed_car_id)
    from .stages import STAGES, STAGES_WITH_MODEL

    stages = STAGES_WITH_MODEL if with_model else STAGES
    results = {r.name: r for r in run_project(project, stages, log=log)}
    dirs = {
        n: (r.dir if r.status in ("ok", "cached") else None) for n, r in results.items()
    }
    failures = {
        n: (None if r.status in ("ok", "cached") else f"{r.status}: {r.error}")
        for n, r in results.items()
    }
    if not with_model:
        failures["model"] = "omitida (--no-model)"
    source = dirs.get("source")
    vehicle_id = (
        _load_json(source / "source.json")["vehicle_id"] if source else project.id
    )
    car_id = car_id or default_car_id(vehicle_id)
    options = {
        "local_driver": bool(local_driver),
        "ac_root": str(ac_root) if ac_root else None,
        "audio_bank": str(audio_bank) if audio_bank else None,
        "audio_source_id": audio_source_id,
        "audio_build": bool(audio_build),
        "allow_installed_car_id": bool(allow_installed_car_id),
    }
    if driver_aids:
        options["driver_aids"] = dict(driver_aids)
    if tyre_compounds:
        options["tyre_compounds"] = list(tyre_compounds)
    for key, on in (
        ("adjustable_turbo", adjustable_turbo),
        ("setup_brake_bias", setup_brake_bias),
    ):
        if on:
            options[key] = True
    options["setup_tyre_pressure"] = True
    if (
        supports_shifter is not None
    ):  # None = no se escribe (cajas que no son manuales/secuenciales)
        options["supports_shifter"] = bool(supports_shifter)
    if not backfire:
        options["backfire"] = False
    if backfire_params:
        options["backfire_params"] = {
            k: float(v) for k, v in backfire_params.items() if v is not None
        }
    from src.ac.thermal_templates import DEFAULT_BRAKES, DEFAULT_TYRES

    # térmica siempre activa (decisión del usuario, 2026-11-24); el CLI solo elige la variante
    options["thermal_tyres"] = thermal_tyres or DEFAULT_TYRES
    options["thermal_brakes"] = thermal_brakes or DEFAULT_BRAKES
    files, issues, ctx = collect(project, dirs, failures, car_id, options)
    keys = {n: r.key[:16] for n, r in sorted(results.items()) if r.key}
    res = write_export(
        project, files, issues, ctx, car_id, keys, options, car_id_check_root=check_root
    )
    # punto único de promoción (CLI, escritorio, con/sin audio): nunca rompe la exportación
    from .builds import promote

    res.build = promote(res.dir, options)
    log(
        f"builds/: promovida a {res.build['path']}"
        if res.build["promoted"]
        else f"builds/: no promovida ({res.build['reason']})"
    )
    return res


def inspect_export(attempt: Path) -> dict[str, Any]:
    """Comprueba un intento sin modificarlo: hashes frente al manifiesto."""
    manifest = _load_json(attempt / "manifest.json")
    car = attempt / manifest["car_id"]
    on_disk = {
        p.relative_to(car).as_posix(): _sha(p.read_bytes())
        for p in car.rglob("*")
        if p.is_file()
    }
    listed = {k: v["sha256"] for k, v in manifest["files"].items()}
    return {
        "attempt": str(attempt),
        "car_id": manifest["car_id"],
        "verdict": manifest["verdict"],
        "errors": manifest["errors"],
        "warnings": manifest["warnings"],
        "changed": sorted(k for k in listed if on_disk.get(k) != listed[k]),
        "unlisted": sorted(k for k in on_disk if k not in listed),
        "intact": on_disk == listed,
    }
