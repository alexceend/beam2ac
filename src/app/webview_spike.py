"""Prueba técnica mínima: ventana pywebview + vista three.js local + puente Python.

    .venv-ui/Scripts/python.exe -m src.app.webview_spike
        [--selftest] [--out output/<nueva>]

Requiere el entorno aislado ``.venv-ui`` (``requirements-ui.txt``); el Python del
proyecto
no necesita pywebview para nada más.

* Sirve ``src/app/static`` desde un servidor propio en ``127.0.0.1`` (puerto libre) y
  registra cada ruta pedida.
* La página (``static/spike/spike.html``) carga three.js 0.160.0 desde
  ``static/vendor`` con un *import map*; su CSP solo permite ``'self'`` (sin CDN).
* Python → JS: ``get_scene()`` entrega los centros de rueda del Ibiza (``ScenePlan``,
  convertidos a ejes AC con ``src.coords.beamng_to_ac``).
* JS → Python: ``report_position()`` recibe la posición del marcador del gizmo.
* ``--selftest``: mueve el marcador por programa, comprueba la ida y vuelta
  (|Δ| < 0,1 mm), que todas las peticiones fueron a rutas locales permitidas, escribe
  ``spike_result.json`` y cierra la ventana sola.

No ejecuta juegos ni interactúa con otras aplicaciones: solo abre su propia ventana.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.app.server import send_static
from src.coords import beamng_to_ac

ROOT = Path(__file__).resolve().parents[2]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
TOLERANCE_M = 1e-4  # 0,1 mm


def ibiza_scene() -> dict[str, Any]:
    """Centros de rueda del Ibiza en ejes AC (m), del mismo plan que produce el KN5."""
    from src.mesh.build import plan_for_vehicle

    plan = plan_for_vehicle(IBIZA, IBIZA / "35b84.pc")
    centers = {k: list(beamng_to_ac(v)) for k, v in sorted(plan.wheel_centers.items())}
    return {"vehicle": "seat_ibiza_acfr", "wheel_centers_ac": centers, "radius": 0.3125}


class SpikeApi:
    """Métodos expuestos a JavaScript (``window.pywebview.api``)."""

    def __init__(self, scene: dict[str, Any]) -> None:
        self._scene = scene
        self.received: list[dict[str, Any]] = []

    def get_scene(self) -> dict[str, Any]:
        return self._scene

    def report_position(
        self, name: str, x: float, y: float, z: float
    ) -> dict[str, Any]:
        item = {"name": name, "position": [x, y, z], "t": time.time()}
        self.received.append(item)
        return {"ok": True, "echo": [x, y, z]}


def make_static_server() -> tuple[ThreadingHTTPServer, list[str]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            requests.append(path)
            if path.startswith("/static/"):
                send_static(self, path[len("/static/") :])
            else:
                send_static(self, "__nada__")

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler), requests


def _versions() -> dict[str, str | None]:
    """Versiones instaladas (metadatos; ``webview`` no tiene ``__version__``)."""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {"python": sys.version.split()[0]}
    for dist in ("pywebview", "pythonnet", "clr_loader", "bottle", "proxy_tools"):
        try:
            out[dist] = version(dist)
        except PackageNotFoundError:
            out[dist] = None
    return out


def run(
    selftest: bool, out_dir: Path | None, timeout_s: float = 60.0
) -> dict[str, Any]:
    import webview  # solo en .venv-ui

    scene = ibiza_scene()
    api = SpikeApi(scene)
    server, requests = make_static_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/static/spike/spike.html" % server.server_address[1]
    window = webview.create_window(
        "beam2ac · prueba técnica 3D", url, js_api=api, width=900, height=600
    )
    report: dict[str, Any] = {"url": url, "versions": _versions()}

    def probe() -> None:
        deadline = time.time() + timeout_s
        try:
            while time.time() < deadline and not window.evaluate_js(
                "!!(window.__spike && (window.__spike.ready || window.__spike.error))"
            ):
                time.sleep(0.2)
            report["page_error"] = window.evaluate_js(
                "window.__spike && window.__spike.error"
            )
            if not selftest:
                return
            sent = [1.2345, 0.5, -0.25]
            window.evaluate_js("window.__spike.selftest(%r, %r, %r)" % tuple(sent))
            while time.time() < deadline and not window.evaluate_js(
                "!!(window.__spike.result || window.__spike.error)"
            ):
                time.sleep(0.1)
            report["js"] = json.loads(
                window.evaluate_js("JSON.stringify(window.__spike.result)") or "null"
            )
            report["page_error"] = window.evaluate_js("window.__spike.error")
            got = [r for r in api.received if r["name"] == "selftest"]
            report["python_received"] = got[-1]["position"] if got else None
            report["roundtrip_max_abs_error_m"] = (
                max(abs(a - b) for a, b in zip(sent, got[-1]["position"]))
                if got
                else None
            )
            report["requests"] = sorted(set(requests))
            allowed = ("/static/spike/", "/static/vendor/three-0.160.0/")
            report["only_local_allowed_paths"] = all(
                p.startswith(allowed) for p in requests
            )
            report["ok"] = bool(
                report["js"]
                and not report["page_error"]
                and report["js"]["revision"] == "160"
                and report["js"]["wheels_drawn"] == 4
                and report["roundtrip_max_abs_error_m"] is not None
                and report["roundtrip_max_abs_error_m"] < TOLERANCE_M
                and report["only_local_allowed_paths"]
            )
        except Exception as exc:  # se informa; la ventana se cierra igualmente
            report["exception"] = repr(exc)
            report["ok"] = False
        finally:
            if selftest:
                window.destroy()

    def watchdog() -> None:
        # Si la página o el puente se bloquean, la ventana se cierra igualmente.
        time.sleep(timeout_s + 15)
        if "ok" not in report:
            report.setdefault("exception", "vigilante: tiempo agotado")
            report["ok"] = False
            try:
                window.destroy()
            except Exception:
                pass

    threading.Thread(target=watchdog, daemon=True).start()
    webview.start(probe, private_mode=True)
    server.shutdown()
    server.server_close()
    report["scene"] = scene
    if out_dir is not None:
        from src.safe_paths import ensure_safe_output_path, new_output_dir

        out = new_output_dir(out_dir)
        ensure_safe_output_path(out / "spike_result.json").write_text(
            json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m src.app.webview_spike", description=__doc__
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)
    report = run(args.selftest, args.out, args.timeout)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "scene"},
            indent=1,
            ensure_ascii=False,
        )
    )
    return 0 if (report.get("ok") or not args.selftest) else 1


if __name__ == "__main__":
    sys.exit(main())
