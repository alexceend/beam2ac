"""Ventana nativa de la aplicación de escritorio (pywebview, opcional).

    .venv-ui/Scripts/python.exe -m src.app.desktop_window [--projects DIR]
        [--selftest --out output/<nueva>]

Requiere el entorno aislado ``.venv-ui`` (``requirements-ui.txt``, ya usado por
``src.app.webview_spike``). Arranca ``src.app.desktop`` en 127.0.0.1 (puerto libre) y lo
muestra en una ventana propia. No abre el navegador ni otras aplicaciones.

``--selftest``: abre la ventana, espera a que la lista de proyectos se pinte, comprueba
que la página no muestra errores, escribe ``desktop_selftest.json`` y cierra la ventana.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

from src.safe_paths import new_output_dir

from .desktop import make_desktop_server
from .workspace import PROJECTS_ROOT, Workspace

DOWNLOAD_URLS = (  # lista cerrada: las únicas páginas externas que la ventana abre
    "https://github.com/alexceend/beam2ac",
    "https://ko-fi.com/alexceend",
)


def main(argv: list[str] | None = None) -> int:
    import webview  # solo en .venv-ui

    ap = argparse.ArgumentParser(prog="python -m src.app.desktop_window")
    ap.add_argument("--projects", type=Path, default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    from . import settings

    settings.apply()  # rutas guardadas por el usuario (AC, Steam, BeamNG)
    server = make_desktop_server(
        Workspace(a.projects or settings.projects_dir() or PROJECTS_ROOT), 0
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"

    class Api:  # lo único que la página puede pedir a la ventana: elegir carpeta o zip
        def pick_folder(self) -> str | None:
            r = win.create_file_dialog(webview.FOLDER_DIALOG)
            return r[0] if r else None

        def open_url(
            self, url: str
        ) -> bool:  # solo las páginas fijas de la lista cerrada
            if url not in DOWNLOAD_URLS:
                return False
            return webbrowser.open(url)

        def set_title(
            self, title: str
        ) -> None:  # la versión va en el título de la ventana
            win.set_title(str(title)[:80])

        def open_window(self, query: str) -> bool:
            # un panel de la pestaña Conversión a pantalla completa; solo consultas de la propia aplicación
            m = re.fullmatch(
                r"\?project=([A-Za-z0-9_.\-]{1,64})&panel=(resumen|diagnostico|resultados|asistente)",
                str(query),
            )
            if not m:
                return False
            webview.create_window(
                f"beam2ac · {m.group(1)} · {m.group(2)}",
                url + query,
                width=1100,
                height=800,
                js_api=Api(),
            )
            return True

        def pick_zip(self) -> str | None:  # G2.29: mod de BeamNG sin descomprimir
            r = win.create_file_dialog(
                webview.OPEN_DIALOG, file_types=("Mod de BeamNG (*.zip)",)
            )
            return r[0] if r else None

    win = webview.create_window("beam2ac", url, width=1400, height=900, js_api=Api())
    result: dict = {"url": url}

    def selftest() -> None:
        try:
            for _ in range(60):
                n = win.evaluate_js("document.querySelectorAll('.pcard').length")
                if n:
                    break
                time.sleep(0.5)
            result["project_cards"] = n
            result["error_text"] = win.evaluate_js(
                "document.getElementById('projects').textContent.startsWith('Error')"
            )
            result["ok"] = bool(n) and not result["error_text"]
        except Exception as exc:  # se registra; la ventana se cierra igualmente
            result["ok"], result["error"] = False, f"{type(exc).__name__}: {exc}"
        finally:
            if a.out:
                out = new_output_dir(a.out)
                (out / "desktop_selftest.json").write_text(
                    json.dumps(result, indent=1), encoding="utf-8"
                )
            win.destroy()

    icon = Path(__file__).parent / "static" / "icon.ico"
    webview.start(selftest if a.selftest else None, icon=str(icon))
    server.shutdown()
    if a.selftest:
        print(json.dumps(result, indent=1))
        return 0 if result.get("ok") else 1
    return 0


if __name__ == "__main__":
    from src.ac import tyre_fit

    tyre_fit.PARALLEL = True  # este módulo tiene guarda: seguro con ``spawn``
    sys.exit(main())
