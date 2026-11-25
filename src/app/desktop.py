"""Aplicación de escritorio de beam2ac (fase C3): servidor local con varios proyectos.

    python -m src.app desktop [--projects output/projects] [--port 8766]
    .venv-ui/Scripts/python.exe -m src.app.desktop_window     (ventana nativa, opcional)

Solo escucha en 127.0.0.1 y no abre nada por sí mismo. Flujo de la interfaz: proyecto →
vehículo y referencias → etapas → decisiones (editor de atributos existente, por
proyecto) → exportación (confirmación explícita, intento nuevo) → resultados
(manifiesto, hashes, errores y advertencias) → vista 3D esquemática.

Rutas GET: ``/`` (desktop.html), ``/static/…``, ``/api/workspace``,
``/api/project?id=``,
``/api/configs?zip=``, ``/api/references?vehicle=``, ``/api/job?id=``,
``/api/export?project=&attempt=``, ``/api/file?project=&path=``,
``/api/precheck?project=``, ``/api/scene?project=&attempt=`` y las del editor con
``?project=``: ``/api/state``, ``/api/attributes``, ``/api/attribute``,
``/api/history``.

Rutas POST (JSON): ``/api/projects`` (crear), ``/api/run``, ``/api/export``
(``confirm: true``), ``/api/decision`` (``project`` + cuerpo del editor), ``/api/open``
(``project`` + ``path``, o ``job`` = carpeta de salida de una conversión) y
``/api/convert`` (G2.28: análisis → proyecto → exportación → copia al destino) y
``/api/cancel`` (``job``; G2.29: se detiene entre etapas, nunca a mitad de una; los bancos de
BeamNG también DENTRO de una sesión) y ``/api/bench_start`` (G2.46; GET ``/api/bench``).
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .post_guard import content_length, get_problem, post_problem
from .server import MAX_BODY, send_static
from .service import ServiceError
from . import diagnostics
from .workspace import Workspace, WorkspaceError


def _need_confirm(body: dict[str, Any], why: str) -> None:
    """Las acciones con efectos exigen ``confirm: true`` (la interfaz lo pide con un diálogo)."""
    if body.get("confirm") is not True:
        raise WorkspaceError(f"{why}: confírmalo (confirm: true)")


class _LocalServer(ThreadingHTTPServer):
    # En Windows SO_REUSEADDR deja que otro proceso local ocupe el mismo puerto.
    allow_reuse_address = False


def make_desktop_handler(ws: Workspace, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        server_version = "beam2ac-desktop/1"
        timeout = 120  # un cliente lento no retiene el hilo para siempre

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def handle(self) -> None:
            # el cliente cerró la conexión a mitad de respuesta (iframe sustituido, recarga):
            # no hay a quién contestar; sin esto socketserver imprime el traceback entero
            try:
                super().handle()
            except ConnectionError:
                pass

        def _json(self, code: int, data: Any) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            bad = get_problem(self.headers, self.server.server_address[1])
            if bad:
                return self._json(bad[0], {"error": bad[1]})
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            p = url.path
            try:
                if p in ("/", "/index.html", "/desktop.html"):
                    return send_static(self, "desktop.html")
                if p.startswith("/static/"):
                    return send_static(self, p[len("/static/") :])
                if p == "/api/workspace":
                    return self._json(
                        200, {"projects": ws.projects(), "sources": ws.sources()}
                    )
                if p == "/api/project":
                    return self._json(200, ws.detail(q.get("id", "")))
                if p == "/api/settings":
                    from . import settings

                    return self._json(200, settings.status())
                if p == "/api/configs":
                    return self._json(200, ws.configs(q.get("zip", "")))
                if p == "/api/references":
                    return self._json(200, ws.references(q.get("vehicle", "")))
                if p == "/api/job":
                    return self._json(200, ws.job(q.get("id", "")))
                if p == "/api/export":
                    return self._json(
                        200,
                        ws.export_detail(q.get("project", ""), q.get("attempt", "")),
                    )
                if p == "/api/file":
                    return self._json(
                        200, ws.read_file(q.get("project", ""), q.get("path", ""))
                    )
                if p == "/api/inspect":  # análisis previo de una carpeta (solo lectura)
                    return self._json(
                        200,
                        ws.inspect(
                            q.get("path", ""),
                            q.get("pc") or None,
                            q.get("driver_mass"),
                            q.get("project") or None,
                            q.get("mass_choice"),
                        ),
                    )
                if (
                    p == "/api/bench"
                ):  # estado/recuperación de los bancos (solo lectura)
                    return self._json(
                        200, ws.bench_info(q.get("path", ""), q.get("pc") or None)
                    )
                if p == "/api/car_id":  # ¿id ya instalado en AC? (solo lectura)
                    return self._json(
                        200, diagnostics.car_id_status(q.get("car_id", ""))
                    )
                if (
                    p == "/api/tyre_compounds"
                ):  # compuestos de AC disponibles (lee la instalación, solo lectura)
                    from src.ac import tyre_compounds as TC

                    from .workspace import _require_ac_root

                    try:
                        root = _require_ac_root(q.get("ac_root") or None)
                    except WorkspaceError:
                        root = None
                    return self._json(
                        200, {"ac_root": root, "compounds": TC.available(root)}
                    )
                if p == "/api/environment":
                    return self._json(200, diagnostics.environment())
                if p == "/api/cockpit":
                    return self._json(200, ws.cockpit(q.get("project", "")))
                if p == "/api/anim":
                    return self._json(200, ws.anim(q.get("project", "")))
                if p in ("/api/cockpit_geometry", "/api/steering_geometry"):
                    blob = (
                        ws.steering_geometry(
                            q.get("project", ""), q.get("material", "")
                        )
                        if p == "/api/steering_geometry"
                        else ws.cockpit_geometry(q.get("project", ""))
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(blob)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(blob)
                    return
                if (
                    p == "/api/steering"
                ):  # materiales del KN5 y volante detectado (solo lectura)
                    return self._json(200, ws.steering_info(q.get("project", "")))
                if (
                    p == "/api/model_parts"
                ):  # piezas del KN5 que se pueden animar (solo lectura)
                    return self._json(200, ws.model_parts(q.get("project", "")))
                if p == "/api/model_options":
                    return self._json(200, ws.model_options_info(q.get("project", "")))
                if (
                    p == "/api/parts_geometry"
                ):  # mallas del KN5 como piezas (editor de animaciones)
                    blob = ws.parts_geometry(q.get("project", ""))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(blob)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(blob)
                    return
                if p == "/api/details":  # detalles del coche guardados (solo lectura)
                    return self._json(200, ws.details_info(q.get("project", "")))
                if (
                    p == "/api/details_preview"
                ):  # render de la portada automática (tarda unos segundos)
                    # lanza Blender: un GET entre sitios (p. ej. <img>) no debe poder dispararlo
                    if self.headers.get("Sec-Fetch-Site", "same-origin") not in (
                        "same-origin",
                        "none",
                    ):
                        raise WorkspaceError("petición entre sitios no permitida")
                    return self._json(200, ws.details_preview(q.get("project", "")))
                if p == "/api/precheck":
                    return self._json(200, ws.precheck(q.get("project", "")))
                if p == "/api/scene":
                    return self._json(
                        200, ws.scene(q.get("project", ""), q.get("attempt", ""))
                    )
                if p in (
                    "/api/state",
                    "/api/attributes",
                    "/api/attribute",
                    "/api/history",
                ):
                    with lock:
                        svc = ws.review(q.get("project", ""))
                        if p == "/api/state":
                            return self._json(200, svc.state())
                        if p == "/api/attributes":
                            return self._json(
                                200, svc.attributes(q.get("status", ""), q.get("q", ""))
                            )
                        if p == "/api/attribute":
                            return self._json(200, svc.attribute(q.get("target", "")))
                        return self._json(200, svc.history(q.get("target", "")))
                self._json(HTTPStatus.NOT_FOUND, {"error": "ruta desconocida"})
            except (WorkspaceError, ServiceError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # error inesperado: se muestra, no se oculta
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            bad = post_problem(self.headers, self.server.server_address[1])
            if bad:
                return self._json(bad[0], {"error": bad[1]})
            length = content_length(self.headers)
            if length <= 0 or length > MAX_BODY:
                return self._json(400, {"error": "cuerpo vacío o demasiado grande"})
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise WorkspaceError("se esperaba un objeto JSON")
                p = url.path
                if p == "/api/projects":
                    return self._json(200, ws.create(body))
                if p == "/api/run":
                    return self._json(
                        200,
                        ws.run(body.get("project", ""), bool(body.get("with_model"))),
                    )
                if p == "/api/export":
                    return self._json(200, ws.export(body.get("project", ""), body))
                if p == "/api/convert":
                    _need_confirm(body, "convertir crea un proyecto y escribe archivos")
                    return self._json(200, ws.convert(body))
                if p == "/api/delete_project":
                    return self._json(200, ws.delete(body.get("project", ""), body))
                if p == "/api/driver_mass":
                    return self._json(
                        200, ws.set_driver_mass(body.get("project", ""), body)
                    )
                if p == "/api/settings":
                    from . import settings

                    if body.get("projects_dir") or body.get("export_dir"):
                        _need_confirm(body, "cambiar las carpetas de salida")
                    return self._json(200, settings.status(settings.save(body)))
                if p == "/api/blender_addon":  # descarga + instala (pide confirmación)
                    from . import blender_addon

                    if body.get("confirm") is not True:
                        raise WorkspaceError("falta confirm: true")
                    try:
                        return self._json(200, blender_addon.install())
                    except (blender_addon.AddonError, OSError) as exc:
                        raise WorkspaceError(str(exc)) from exc
                if p == "/api/bench_start":
                    _need_confirm(body, "el banco lanza BeamNG.drive")
                    return self._json(200, ws.bench_start(body))
                if p == "/api/steering":
                    return self._json(
                        200, ws.steering_save(body.get("project", ""), body)
                    )
                if p == "/api/model_options":
                    return self._json(
                        200, ws.model_options_save(body.get("project", ""), body)
                    )
                if p == "/api/details":
                    return self._json(
                        200, ws.details_save(body.get("project", ""), body)
                    )
                if p == "/api/cockpit":
                    return self._json(
                        200, ws.cockpit_save(body.get("project", ""), body)
                    )
                if p == "/api/anim_preview":
                    return self._json(
                        200, ws.anim_preview(body.get("project", ""), body)
                    )
                if p == "/api/anim":
                    return self._json(200, ws.anim_save(body.get("project", ""), body))
                if p == "/api/cancel":
                    return self._json(200, ws.cancel(str(body.get("job", ""))))
                if p == "/api/open" and body.get("builds"):
                    return self._json(200, ws.open_builds(body.get("job") or None))
                if p == "/api/open" and body.get("job"):
                    return self._json(200, ws.open_output(str(body["job"])))
                if p == "/api/open":
                    return self._json(
                        200, ws.open_path(body.get("project", ""), body.get("path", ""))
                    )
                if p == "/api/decision":
                    pid = body.pop("project", "")
                    with lock:
                        return self._json(200, ws.review(pid).decide(body))
                return self._json(404, {"error": "ruta desconocida"})
            except (
                WorkspaceError,
                ServiceError,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def make_desktop_server(ws: Workspace, port: int = 0) -> ThreadingHTTPServer:
    """Servidor ligado SOLO a 127.0.0.1 (``port=0``: puerto libre)."""
    return _LocalServer(("127.0.0.1", port), make_desktop_handler(ws, threading.Lock()))
