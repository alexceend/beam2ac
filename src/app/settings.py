"""Ajustes del usuario de la aplicación de escritorio (rutas de AC, Steam, Blender y FMOD, carpeta de
entrega y masa del piloto por defecto).

Se guardan en la carpeta de usuario (``%APPDATA%/beam2ac/settings.json``; ``BEAM2AC_SETTINGS`` la
cambia; si falta se lee el antiguo ``output/app_settings.json``) y se aplican como variables de entorno
``BEAM2AC_*`` que el resto del código ya consulta (``default_ac_root``, ``blender_path``,
``detect_fmod``, ``BEAM2AC_BEAMNG_ROOT``): no hay una segunda vía de configuración. Los ajustes
guardados mandan sobre el entorno; sin ajuste, nada cambia.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.safe_paths import PROJECT_ROOT, ensure_safe_output_path


def _default_settings_file() -> Path:
    env = os.environ.get("BEAM2AC_SETTINGS")
    if env:
        return Path(env)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "beam2ac" / "settings.json"


SETTINGS_FILE = _default_settings_file()
LEGACY_FILE = PROJECT_ROOT / "output" / "app_settings.json"
PATH_KEYS = (
    "ac_root",
    "steam_root",
    "beamng_root",
    "beamng_mods_dir",
    "blender_path",
    "fmod_path",
    "projects_dir",
    "export_dir",
)
KEYS = PATH_KEYS + ("driver_mass",)
# ajuste → variable de entorno que lee el código; steam_root se traduce a las raíces de AC y BeamNG
ENV = {
    "ac_root": "BEAM2AC_AC_ROOT",
    "beamng_root": "BEAM2AC_BEAMNG_ROOT",  # va tras steam_root: manda sobre el derivado
    "beamng_mods_dir": "BEAM2AC_BEAMNG_MODS",
    "projects_dir": "BEAM2AC_PROJECTS_DIR",
    "blender_path": "BEAM2AC_BLENDER",
    "fmod_path": "BEAM2AC_FMOD_STUDIO",
}
STEAM_ENV = {
    "BEAM2AC_AC_ROOT": "assettocorsa",
    "BEAM2AC_BEAMNG_ROOT": "BeamNG.drive",
}
_applied: dict[str, str] = {}  # variable → valor que puso apply()
_orig: dict[str, str | None] = {}  # variable → valor previo del usuario


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or SETTINGS_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        if path != SETTINGS_FILE or not LEGACY_FILE.is_file():
            return {}
        try:
            data = json.loads(LEGACY_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    out = {k: data[k] for k in PATH_KEYS if isinstance(data.get(k), str) and data[k]}
    if isinstance(data.get("driver_mass"), (int, float)):
        out["driver_mass"] = data["driver_mass"]
    return out


def clean(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in PATH_KEYS:
        v = str(body.get(k) or "").strip().strip('"')
        if v:
            out[k] = v
    if str(body.get("driver_mass") or "").strip():
        from .workspace import parse_driver_mass

        out["driver_mass"] = parse_driver_mass(body["driver_mass"])
    return out


def _too_broad(folder: str) -> str | None:
    """Motivo si la carpeta es demasiado amplia para ser una raíz de salida (unidad, perfil, sistema)."""
    p = Path(folder).expanduser().resolve()
    if p == Path(p.anchor):
        return "es la raíz de una unidad"
    if p == Path.home().resolve():
        return "es la carpeta personal completa"
    system = [
        os.environ.get(v)
        for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
    ]
    system += [
        "/usr",
        "/etc",
        "/bin",
        "/sbin",
        "/var",
        "/boot",
        "/lib",
        "/opt",
        "/proc",
        "/sys",
    ]
    for sysdir in filter(None, system):
        r = Path(sysdir).resolve()
        if p == r or r in p.parents:
            return f"está dentro de una carpeta del sistema ({r})"
    return None


def save(body: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    data = clean(body)
    for k in (
        "projects_dir",
        "export_dir",
    ):  # se rechaza ANTES de ampliar las raíces permitidas
        if k in data:
            why = _too_broad(data[k])
            if why:
                raise ValueError(f"{k}: {why}")
    apply(
        data
    )  # las carpetas elegidas pasan a ser raíces permitidas antes de comprobarlas
    for k in (
        "projects_dir",
        "export_dir",
    ):  # una carpeta protegida bloquearía el arranque
        if k in data:
            try:
                ensure_safe_output_path(Path(data[k]) / "x")
            except ValueError as exc:
                apply(load(path))
                raise ValueError(f"{k}: {exc}") from None
    target = ensure_safe_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1, ensure_ascii=False), "utf-8")
    apply(data)
    return data


def _remember_originals(names: set[str]) -> None:
    """Guarda el valor del usuario de cada variable salvo que sea el que puso ``apply``."""
    for var in names:
        if os.environ.get(var) != _applied.get(var):
            _orig[var] = os.environ.get(var)


def apply(data: dict[str, Any] | None = None) -> None:
    """Vuelca los ajustes en ``os.environ``; lo que se quita del ajuste devuelve el valor previo."""
    from src.safe_paths import OUTPUT_ROOTS_ENV

    data = load() if data is None else data
    want: dict[str, str] = {}
    steam = data.get("steam_root")
    if steam:
        for var, folder in STEAM_ENV.items():
            want[var] = str(Path(steam) / "steamapps" / "common" / folder)
    for key, var in ENV.items():
        if data.get(key):
            want[var] = str(data[key])
    bl = want.get("BEAM2AC_BLENDER")
    if bl and Path(bl).is_dir():  # basta con la carpeta de Blender
        want["BEAM2AC_BLENDER"] = str(Path(bl) / "blender.exe")
    _remember_originals(set(ENV.values()) | set(STEAM_ENV) | {OUTPUT_ROOTS_ENV})
    # carpetas de salida elegidas: se añaden a las del usuario (las protecciones de AC/BeamNG siguen)
    extra = [str(data[k]) for k in ("projects_dir", "export_dir") if data.get(k)]
    if extra:
        base = [r for r in (_orig.get(OUTPUT_ROOTS_ENV) or "").split(os.pathsep) if r]
        want[OUTPUT_ROOTS_ENV] = os.pathsep.join(base + extra)
    for var in set(ENV.values()) | set(STEAM_ENV) | {OUTPUT_ROOTS_ENV}:
        if var in want:
            os.environ[var] = want[var]
        elif _orig.get(var) is not None:
            os.environ[var] = _orig[var]  # type: ignore[assignment]
        else:
            os.environ.pop(var, None)
    _applied.clear()
    _applied.update({v: os.environ[v] for v in want})


def projects_dir() -> Path | None:
    """Carpeta de proyectos elegida por el usuario (tras :func:`apply`), o ``None``."""
    v = os.environ.get("BEAM2AC_PROJECTS_DIR")
    return Path(v) if v else None


def status(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ajustes guardados + qué se está usando de verdad y si existe (solo lectura)."""
    from src.pipeline.contracts import detect_fmod
    from src.project.export import default_ac_root
    from src.project.model import blender_path

    data = load() if data is None else data
    ac, bl, fm = default_ac_root(), blender_path(), detect_fmod()
    from src.bench.service import default_beamng_root

    bng = default_beamng_root()
    from . import blender_addon

    return {
        "saved": data,
        "blender_addon": blender_addon.status(),
        "effective": {
            "ac_root": str(ac) if ac else None,
            "beamng_root": str(bng) if bng.is_dir() else None,
            "projects_dir": os.environ.get("BEAM2AC_PROJECTS_DIR"),
            "blender_path": bl,
            "fmod_path": fm.get("path"),
            "fmod_compatible": bool(fm.get("compatible")),
            "fmod_version": fm.get("version"),
            "export_dir_exists": bool(data.get("export_dir"))
            and Path(data["export_dir"]).is_dir(),
        },
    }
