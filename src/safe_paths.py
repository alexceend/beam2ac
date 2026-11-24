"""Protección de escritura del proyecto.

Toda ruta en la que un script vaya a escribir debe pasar antes por
:func:`ensure_safe_output_path`. La regla es «solo se escribe donde está permitido y
nunca en algo protegido»:

1. **Raíces permitidas** (lista blanca): ``<proyecto>/output``, la carpeta temporal del
   sistema (pruebas) y las carpetas de ``BEAM2AC_OUTPUT_ROOTS`` (separadas por
   ``os.pathsep``). Fuera de ellas se rechaza todo; así una ruta equivalente que no se
   reconozca (UNC, unidad sustituida…) falla del lado seguro.
2. **Muestras** de solo lectura: ``sample_beamng/``, ``samples_beamng/`` (vehículos
   añadidos por el usuario), ``sample_assetto/`` y ``sample_assetto_unpacked/`` (datos
   oficiales de Kunos desempaquetados).
3. **Juegos y datos del usuario**: instalaciones de Assetto Corsa y BeamNG (por nombre
   ``steamapps/common/<juego>`` y por ejecutables marcadores en cualquier carpeta
   antecesora), sus carpetas de usuario (``Documentos/Assetto Corsa`` con
   ``controls.ini``, ``race.ini``, ``video.ini``; ``%LOCALAPPDATA%/BeamNG*``), la de
   Content Manager y las de ``BEAM2AC_PROTECTED_ROOTS``.
4. **Paquetes sellados**: una carpeta que contiene ``manifest.json`` (todos los
   ``ingame_cycle/pkg_*`` aprobados, los ``ingame_test*``) queda protegida con todo su
   contenido, incluido el propio manifiesto.
5. **Producción y versiones históricas sin sello** (:data:`HISTORICAL_DIRS`): el
   ensamblado de producción, las ejecuciones de referencia del Ibiza, el origen de los
   scripts preservados y las evidencias de G2.25 y G2.26, protegidos por ruta.

**Vía explícita para resultados nuevos:** :func:`new_output_dir` crea una carpeta
que no existía y :func:`claim_output_dir` reclama una existente sin sellar. En ambos
casos queda registrada como propia de este proceso, que puede seguir escribiendo
dentro aunque ya haya escrito su ``manifest.json``. Otro proceso la verá sellada.

Las rutas se resuelven con :func:`os.path.realpath` (``..``, enlaces simbólicos y
uniones de Windows) y se comparan sin distinguir mayúsculas en Windows. En Windows se
quitan además los puntos y espacios finales de cada componente, como hace el sistema al
abrir la ruta (``pkg_v18bu.`` es ``pkg_v18bu``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
PROTECTED_DIRS: tuple[Path, ...] = (
    PROJECT_ROOT / "sample_beamng",
    PROJECT_ROOT / "samples_beamng",
    PROJECT_ROOT / "sample_assetto",
    PROJECT_ROOT / "sample_assetto_unpacked",
)
# Instalado con PyInstaller, la carpeta del programa es de solo lectura: lo escrito va a datos de usuario.
FROZEN: bool = bool(getattr(sys, "frozen", False))
DATA_ROOT: Path = (
    Path(
        os.environ.get("BEAM2AC_DATA_DIR")
        or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "beam2ac"
    )
    if FROZEN or os.environ.get("BEAM2AC_DATA_DIR")
    else PROJECT_ROOT
)
DEFAULT_OUTPUT_DIR: Path = DATA_ROOT / "output"
# Copias de acceso rápido a las últimas builds funcionales (src/project/builds.py)
BUILDS_DIR: Path = DATA_ROOT / "builds"
# Paquetes de producción y versiones históricas SIN manifest.json. No se sellan (eso
# sería añadirles un archivo): se protegen por ruta, con todo su contenido. Fase A,
# revisión del 2026-11-10
# (docs/research/fase_a_seguridad_reproducibilidad_2026-11-10.md):
# sin esta lista, ``assemble --overwrite`` (cuya salida por defecto es la de producción)
# los habría sobrescrito.
HISTORICAL_DIRS: tuple[Path, ...] = tuple(
    DEFAULT_OUTPUT_DIR / rel
    for rel in (
        # salida de producción del ensamblador (CLI sin --out)
        "seat_ibiza_acfr/assembly",
        # ejecuciones de referencia de las comparaciones byte a byte
        "seat_ibiza_acfr/phase1_gearbox_2026-10-25",
        "seat_ibiza_acfr/integration_2026-10-25",
        "seat_ibiza_acfr/phaseB_2026-10-25",
        "seat_ibiza_acfr/phaseC_2026-10-25",
        "seat_ibiza_acfr/phaseD_2026-10-25",
        "seat_ibiza_acfr/repro_2026-10-25",
        # origen de los scripts preservados en tools/variants/original (SHA256SUMS.txt)
        "seat_ibiza_acfr/ingame_cycle/tools_2026-10-25_v14",
        "seat_ibiza_acfr/ingame_cycle/tools_2026-10-25_v15",
        "seat_ibiza_acfr/sound_work_2026-10-25",
        "seat_ibiza_acfr/camera_work_2026-10-25",
        # intento de exportación sin sellar (fase B2)
        "projects/ibiza_35b84_b1/exports/001",
        # G2.25 cerrada: evidencias y preparaciones de la validación funcional en AC
        "g225_2026-11-09",
        # G2.26 cerrada: diagnóstico y corrección de Prothean y Omega (sesiones en AC)
        "g226_2026-11-10",
        # investigación de capacidades pendientes (exports/006 con [GEARBOX] INERTIA)
        "investigacion_2026-11-10",
        # G2.27 cerrada: sesiones de AC, variantes y trabajo paralelo
        "g227_2026-11-10",
        "g227_paralelo_2026-11-11",
    )
)
SEAL_FILE = "manifest.json"
OUTPUT_ROOTS_ENV = "BEAM2AC_OUTPUT_ROOTS"
PROTECTED_ROOTS_ENV = "BEAM2AC_PROTECTED_ROOTS"

# Carpetas de juego bajo steamapps/common (en minúsculas).
GAME_DIR_NAMES = ("assettocorsa", "beamng.drive", "beamng.tech")
# Un antecesor que contiene alguno de estos archivos es la instalación de un juego o de
# Content Manager (AC: acs.exe y AssettoCorsa.exe en la raíz; BeamNG: BeamNG.drive.exe
# en la raíz y el ejecutable de 64 bits en Bin64).
GAME_MARKERS = (
    "acs.exe",
    "AssettoCorsa.exe",
    "BeamNG.drive.exe",
    "Bin64/BeamNG.drive.x64.exe",
    "Content Manager.exe",
)

_OWNED: set[str] = set()


class ProtectedPathError(ValueError):
    """Se intentó escribir en una ruta no permitida o protegida."""


# ----------------------------------------------------------------- normalización
def _strip_windows_tail(path: str) -> str:
    """Quita puntos y espacios finales de cada componente (semántica de Win32)."""
    if os.name != "nt":
        return path
    drive, rest = os.path.splitdrive(path)
    parts = rest.replace("/", "\\").split("\\")
    parts = [p if p in ("", ".", "..") else (p.rstrip(" .") or p) for p in parts]
    return drive + "\\".join(parts)


def _resolve(path: str | Path) -> Path:
    raw = os.fspath(path)
    if raw.startswith("\\\\?\\") and not raw.startswith("\\\\?\\UNC\\"):
        raw = raw[4:]
    return Path(os.path.realpath(_strip_windows_tail(raw)))


def _key(path: Path) -> str:
    s = os.path.normcase(os.fspath(path))
    if s.startswith("\\\\?\\"):
        s = s[4:]
    return s.rstrip("\\/") or s


def _inside(child: Path, parent: Path) -> bool:
    c, p = _key(child), _key(parent)
    return c == p or c.startswith(p + os.sep)


def _env_paths(name: str) -> list[Path]:
    return [
        _resolve(p) for p in os.environ.get(name, "").split(os.pathsep) if p.strip()
    ]


# ----------------------------------------------------------------- raíces
def _config_dir() -> Path:
    """Carpeta de ajustes del usuario (``%APPDATA%/beam2ac``)."""
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "beam2ac"


def allowed_roots() -> list[Path]:
    """Raíces donde se permite escribir (ver el docstring del módulo)."""
    return [
        _resolve(DEFAULT_OUTPUT_DIR),
        _resolve(BUILDS_DIR),
        _resolve(tempfile.gettempdir()),
        *_env_paths(OUTPUT_ROOTS_ENV),
        _resolve(_config_dir()),
    ]


def _documents_dirs() -> list[Path]:
    dirs = []
    if os.name == "nt":
        try:  # carpeta Documentos real (puede estar redirigida, p. ej. a OneDrive)
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                dirs.append(Path(buf.value))
        except (AttributeError, OSError):
            pass
    home = Path.home()
    dirs += [home / "Documents", home / "Documentos"]
    if os.environ.get("OneDrive"):
        dirs.append(Path(os.environ["OneDrive"]) / "Documents")
    return dirs


def protected_user_roots() -> list[Path]:
    """Carpetas de usuario de los juegos y de Content Manager, más las del entorno."""
    roots = []
    for docs in _documents_dirs():
        roots += [docs / "Assetto Corsa", docs / "BeamNG.drive"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots += [
            Path(local) / "BeamNG.drive",
            Path(local) / "BeamNG",
            Path(local) / "AcTools Content Manager",
        ]
    return [_resolve(r) for r in roots] + _env_paths(PROTECTED_ROOTS_ENV)


# ----------------------------------------------------------------- reglas
def is_protected(path: str | Path) -> bool:
    """True si ``path`` está dentro (o es) una carpeta de muestras."""
    resolved = _resolve(path)
    return any(_inside(resolved, _resolve(p)) for p in PROTECTED_DIRS)


def is_historical(path: str | Path) -> bool:
    """True si ``path`` está dentro (o es) una carpeta de :data:`HISTORICAL_DIRS`."""
    resolved = _resolve(path)
    return any(_inside(resolved, _resolve(p)) for p in HISTORICAL_DIRS)


def _game_reason(resolved: Path) -> str | None:
    chain = [resolved, *resolved.parents]
    for anc in chain:
        parts = [p.lower() for p in anc.parts]
        if (
            len(parts) >= 3
            and parts[-3:-1] == ["steamapps", "common"]
            and parts[-1] in GAME_DIR_NAMES
        ):
            return f"instalación de juego ({anc})"
        for marker in GAME_MARKERS:
            try:
                if (anc / marker).is_file():
                    return (
                        f"instalación de juego o de Content Manager ({anc}, {marker})"
                    )
            except OSError:
                continue
    for root in protected_user_roots():
        if _inside(resolved, root):
            return f"carpeta de usuario de un juego ({root})"
    return None


def _sealed_dir(resolved: Path, stop: Path) -> Path | None:
    """Primer antecesor (o la propia ruta) con ``manifest.json``, hasta ``stop``.

    Las carpetas propias de este proceso cortan la búsqueda: dentro de ellas se puede
    escribir aunque ya tengan su manifiesto.
    """
    for anc in [resolved, *resolved.parents]:
        if _key(anc) in _OWNED:
            return None
        if anc.is_dir() and (anc / SEAL_FILE).is_file():
            return anc
        if _key(anc) == _key(stop):
            return None
    return None


def protection_reason(path: str | Path) -> str | None:
    """Motivo por el que no se puede escribir en ``path``, o ``None`` si se puede."""
    resolved = _resolve(path)
    if is_protected(resolved):
        return "muestra de referencia de solo lectura"
    if is_historical(resolved):
        return (
            "paquete de producción o versión histórica sin sello "
            "(safe_paths.HISTORICAL_DIRS); no se sobrescribe"
        )
    game = _game_reason(resolved)
    if game:
        return game
    roots = [r for r in allowed_roots() if _inside(resolved, r)]
    if not roots:
        return "fuera de las raíces de salida permitidas: " + ", ".join(
            str(r) for r in allowed_roots()
        )
    sealed = _sealed_dir(resolved, max(roots, key=lambda r: len(_key(r))))
    if sealed is not None:
        return f"paquete sellado con {SEAL_FILE} ({sealed}); no se sobrescribe"
    return None


def ensure_safe_output_path(path: str | Path) -> Path:
    """Devuelve la ruta resuelta o lanza ProtectedPathError si no se puede escribir."""
    resolved = _resolve(path)
    reason = protection_reason(resolved)
    if reason:
        raise ProtectedPathError(
            f"Ruta de salida no permitida: {resolved} ({reason}). "
            f"Escribe en una carpeta nueva bajo {DEFAULT_OUTPUT_DIR}."
        )
    return resolved


def new_output_dir(path: str | Path) -> Path:
    """Crea una carpeta de resultados NUEVA (falla si existe) y la registra."""
    target = ensure_safe_output_path(path)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ProtectedPathError(f"ya existe {target}; no se sobrescribe") from None
    _OWNED.add(_key(target))
    return target


def claim_output_dir(path: str | Path) -> Path:
    """Reclama una carpeta de salida (la crea si falta) que no esté sellada.

    Si ya es propia de este proceso se devuelve tal cual.
    """
    target = _resolve(path)
    if _key(target) in _OWNED:
        return target
    target = ensure_safe_output_path(target)
    target.mkdir(parents=True, exist_ok=True)
    _OWNED.add(_key(target))
    return target


def is_owned(path: str | Path) -> bool:
    return _key(_resolve(path)) in _OWNED
