"""Configuración común de pytest (``tests/`` y ``tests_not_required/``).

Caché en disco del ajuste SCTM de neumáticos (``src.ac.tyre_fit._CACHE``). El ajuste
es determinista y ya se memoriza dentro del proceso, pero cada ejecución de pytest lo
recalcula en frío (~3 min por vehículo de 40340). Aquí solo se persiste ese mismo
diccionario entre ejecuciones, en ``output/_test_cache/tyre_fit.pkl``.

Correctitud:
1. Producción indexa cada entrada por el sha256 de TODAS sus entradas (muestras, fz0,
   referencia de κ): no se mezclan vehículos, configuraciones ni parámetros.
2. El archivo lleva el sha256 del cierre de importaciones de ``src/ac/tyre_fit.py``
   (``src.project.code_deps``: tyre_fit, tyre_sctm y los ``__init__``) y la versión de
   Python/plataforma; cualquier cambio invalida todo y se recalcula de verdad.
Sin caché: ``BEAM2AC_NO_TEST_CACHE=1`` (ni lee ni escribe) o borrar la carpeta.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "output" / "_test_cache" / "tyre_fit.pkl"
_loaded_keys: set[str] = set()


def _code_hash() -> str:
    from src.project.code_deps import import_closure

    h = hashlib.sha256(f"{sys.version}|{platform.platform()}|v2".encode())
    for rel in import_closure(("src/ac/tyre_fit.py",)):
        h.update(rel.encode())
        h.update((ROOT / rel).read_bytes())
    return h.hexdigest()


def pytest_sessionstart(session) -> None:
    if os.environ.get("BEAM2AC_NO_TEST_CACHE") == "1":
        return
    try:
        from src.ac import tyre_fit

        blob = pickle.loads(CACHE_FILE.read_bytes())
        if blob.get("code") == _code_hash():
            tyre_fit._CACHE.update(blob["cache"])
            _loaded_keys.update(blob["cache"])
    except Exception:  # sin caché, corrupta o incompatible: se recalcula
        pass


def pytest_sessionfinish(session, exitstatus) -> None:
    if os.environ.get("BEAM2AC_NO_TEST_CACHE") == "1":
        return
    try:
        from src.ac import tyre_fit

        if set(tyre_fit._CACHE) <= _loaded_keys:
            return
        from src.safe_paths import ensure_safe_output_path

        # toda escritura del proyecto pasa por el guardián (CLAUDE.md)
        tmp = ensure_safe_output_path(CACHE_FILE.with_suffix(".tmp"))
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(
            pickle.dumps({"code": _code_hash(), "cache": dict(tyre_fit._CACHE)})
        )
        tmp.replace(CACHE_FILE)
    except Exception:
        pass
