"""Detección de las bibliotecas de Steam y de los juegos instalados (solo lectura).

Sustituye a las rutas fijas (p. ej. ``G:/SteamLibrary``) para que funcione en cualquier
equipo: carpeta de Steam del registro de Windows, ``Program Files`` y, de cada una,
las bibliotecas extra que lista ``steamapps/libraryfolders.vdf``. Los ajustes del usuario
(``BEAM2AC_*``) siguen mandando; esto es solo el respaldo automático.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_PATH_LINE = re.compile(r'^\s*"path"\s+"([^"]+)"', re.M)


def _registry_steam() -> list[Path]:
    """Carpetas de instalación de Steam según el registro (solo Windows)."""
    try:
        import winreg
    except ImportError:
        return []
    found: list[Path] = []
    for hive, sub, name in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, sub) as k:
                found.append(Path(winreg.QueryValueEx(k, name)[0]))
        except OSError:
            continue
    return found


def _default_steam_dirs() -> list[Path]:
    dirs = [Path.home() / ".steam" / "steam", Path.home() / ".local/share/Steam"]
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(var):
            dirs.append(Path(os.environ[var]) / "Steam")
    return dirs


def parse_library_folders(vdf_text: str) -> list[Path]:
    """Rutas ``"path"`` de un ``libraryfolders.vdf`` (con las barras escapadas ``\\\\``)."""
    bs = chr(92)
    return [Path(p.replace(bs * 2, bs)) for p in _PATH_LINE.findall(vdf_text)]


def steam_libraries() -> list[Path]:
    """Bibliotecas de Steam que existen (sin repetir, en orden de aparición)."""
    roots: list[Path] = []
    for steam in _registry_steam() + _default_steam_dirs():
        roots.append(steam)
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            roots.extend(
                parse_library_folders(vdf.read_text(encoding="utf-8", errors="replace"))
            )
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = os.path.normcase(os.path.normpath(str(r)))
        if key not in seen and (r / "steamapps").is_dir():
            seen.add(key)
            out.append(r)
    return out


def find_game(folder: str) -> Path | None:
    """``<biblioteca>/steamapps/common/<folder>`` de la primera biblioteca que lo tenga."""
    for lib in steam_libraries():
        cand = lib / "steamapps" / "common" / folder
        if cand.is_dir():
            return cand
    return None
