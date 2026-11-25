"""Un recurso local ausente (muestras, ``output/``, ``builds/``) es una omisión, no un fallo.

En un clon limpio no existen ``sample_beamng/``, ``samples_beamng/``, ``output/`` ni ``builds/``
(evidencia local fuera de git). Un ``FileNotFoundError`` sobre una ruta de esas carpetas, en
la fase que sea (importación, ``setUpClass``, la prueba), se informa como omitida.
Cualquier otro error sigue siendo un fallo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRS = {"output", "sample_beamng", "samples_beamng", "sample_assetto", "builds"}


def _missing_local_resource(exc: BaseException | None) -> str | None:
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, FileNotFoundError) and exc.filename:
            try:
                rel = Path(str(exc.filename)).resolve().relative_to(ROOT)
            except (ValueError, OSError):
                rel = None
            if rel is not None and rel.parts and rel.parts[0] in LOCAL_DIRS:
                return str(rel).replace("\\", "/")
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


def _skip_report(rep, excinfo, where: str) -> None:
    path = _missing_local_resource(excinfo.value) if excinfo else None
    if rep.failed and path:
        rep.outcome = "skipped"
        rep.longrepr = (where, 0, f"Skipped: recurso local no disponible: {path}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    _skip_report(outcome.get_result(), call.excinfo, str(item.path))


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    rep = outcome.get_result()
    if rep.failed and getattr(collector, "path", None) is not None:
        # el error de importación llega ya formateado: se reintenta la comprobación en el texto
        text = str(rep.longrepr)
        for d in LOCAL_DIRS:
            if "FileNotFoundError" in text and f"/{d}/" in text.replace("\\", "/"):
                rep.outcome = "skipped"
                rep.longrepr = (
                    str(collector.path),
                    0,
                    f"Skipped: recurso local no disponible ({d})",
                )
                break
