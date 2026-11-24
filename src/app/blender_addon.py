"""Complemento de Blender que necesita la conversión: ``assetto_corsa_tools`` (exportador KN5).

Es de terceros (Moppius, GPL-3.0): NO se incluye en el repositorio (``.gitignore`` de lista
blanca). La aplicación lo descarga del origen, solo cuando el usuario lo pide, lo comprueba y
lo copia a la carpeta de complementos de usuario de Blender 3.6 (la misma que mira
``src.project.model.tool_inputs``). ``blender_build.py`` lo activa en cada ejecución.

Seguridad: URL fija (commit concreto), sin redirecciones a otros dominios que no sean los de
GitHub, límite de tamaño, extracción sin salir de la carpeta temporal y huella sha256 de los
``.py`` (mismo algoritmo que ``tool_inputs``) que debe coincidir con la fijada. Nunca
sobrescribe una instalación existente.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

ADDON_NAME = "assetto_corsa_tools"
REPO_URL = "https://github.com/moppius/blender-assetto-corsa-tools"
# Último commit del origen (v0.2.0, 2022-01-02). La copia ya usada por el proyecto es idéntica.
COMMIT = "5f0c163cd75f071b21b7f8f98651d50870cf8e85"
ZIP_URL = f"{REPO_URL}/archive/{COMMIT}.zip"
# sha256 de (ruta relativa + bytes) de los .py ordenados; igual que model.tool_inputs()["addon"].
TREE_SHA256 = "de5b18cabd6629873e179be047f2e298e9c7baa52718133297a3e135a08ad7cc"
MAX_ZIP_BYTES = 5_000_000
MAX_UNZIPPED_BYTES = 2_000_000
MAX_ENTRIES = 200
# Solo se instalan los .py (los de la huella) y textos inertes (licencia, README).
_COPY_SUFFIXES = {".py", ".txt", ".md"}
_ALLOWED_HOSTS = ("github.com", "codeload.github.com")


class AddonError(Exception):
    pass


def addons_dir() -> Path:
    """Carpeta de complementos de Blender 3.6 en ``%APPDATA%`` (la que miran ``tool_inputs`` y Blender).

    ``BEAM2AC_BLENDER_ADDONS`` es solo para pruebas: instala donde Blender no mira.
    """
    env = os.environ.get("BEAM2AC_BLENDER_ADDONS")
    if env:
        return Path(env)
    return (
        Path(os.environ.get("APPDATA", ""))
        / "Blender Foundation"
        / "Blender"
        / "3.6"
        / "scripts"
        / "addons"
    )


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def status() -> dict[str, Any]:
    """Estado (solo lectura)."""
    dest = addons_dir() / ADDON_NAME
    out: dict[str, Any] = {
        "installed": dest.is_dir(),
        "path": str(dest),
        "pinned_commit": COMMIT,
        "license": "GPL-3.0",
        "source": REPO_URL,
        "matches_pinned": None,
    }
    if dest.is_dir():
        out["matches_pinned"] = tree_hash(dest) == TREE_SHA256
    return out


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Solo sigue redirecciones https hacia los dominios de GitHub (antes de pedirlas)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlparse

        u = urlparse(newurl)
        if u.scheme != "https" or u.hostname not in _ALLOWED_HOSTS:
            raise AddonError(f"redirección no permitida: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _download(url: str) -> bytes:
    from urllib.parse import urlparse

    if urlparse(url).hostname not in _ALLOWED_HOSTS:
        raise AddonError(f"origen no permitido: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "beam2ac"})
    with _OPENER.open(req, timeout=30) as r:
        if urlparse(r.geturl()).hostname not in _ALLOWED_HOSTS:
            raise AddonError("la descarga se redirigió a otro dominio")
        data = r.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise AddonError("la descarga supera el tamaño esperado")
    return data


def install(fetch: Callable[[str], bytes] = _download) -> dict[str, Any]:
    """Descarga, verifica y copia el complemento. No sobrescribe nada existente."""
    dest = addons_dir() / ADDON_NAME
    if dest.exists():
        raise AddonError(
            f"ya existe {dest}; no se sobrescribe (bórralo a mano si quieres reinstalarlo)"
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(fetch(ZIP_URL)))
    except zipfile.BadZipFile as exc:
        raise AddonError("el archivo descargado no es un zip válido") from exc
    with tempfile.TemporaryDirectory(prefix="beam2ac_addon_") as tmp:
        base = Path(tmp).resolve()
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES or sum(m.file_size for m in infos) > (
            MAX_UNZIPPED_BYTES
        ):
            raise AddonError("el zip es demasiado grande o tiene demasiados archivos")
        for m in infos:  # sin salir de la carpeta temporal (zip-slip) ni enlaces
            if (m.external_attr >> 16) & 0o170000 == 0o120000:
                raise AddonError(f"enlace simbólico en el zip: {m.filename}")
            target = (base / m.filename).resolve()
            if base != target and base not in target.parents:
                raise AddonError(f"ruta insegura en el zip: {m.filename}")
        zf.extractall(base)
        roots = [
            p.parent
            for p in base.rglob("__init__.py")
            if "bl_info" in p.read_text(encoding="utf-8", errors="replace")
        ]
        if len(roots) != 1:
            raise AddonError("el zip no contiene un único complemento de Blender")
        src = roots[0]
        if tree_hash(src) != TREE_SHA256:
            raise AddonError(
                "la huella del complemento descargado no coincide con la fijada; "
                "no se instala"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        # lista explícita: nunca .pyc, .pyd, .pth ni nada que la huella no cubra
        shutil.copytree(
            src,
            dest,
            ignore=lambda d, names: [
                n
                for n in names
                if (Path(d) / n).is_file() and Path(n).suffix not in _COPY_SUFFIXES
            ],
        )
    return status()
