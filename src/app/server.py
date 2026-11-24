"""API HTTP local de la interfaz de atributos (biblioteca estándar; solo 127.0.0.1).

    python -m src.app serve (--vehicle-dir DIR --pc ARCHIVO.pc | --vehicle-zip ZIP
    [--common-zip ZIP])
                            --project output/projects/<nombre> [--refs index.json]
                            [--port 8765]

No abre el navegador ni ninguna otra aplicación: imprime la URL. Rutas:

    GET  /                          frontal (static/index.html) GET  /static/<archivo>
    solo archivos de src/app/static (sin rutas relativas) GET  /api/state GET
    /api/attributes?status=&q= GET  /api/attribute?target= GET  /api/history?target=
    POST /api/decision              JSON (ver service.ReviewService.decide)
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .post_guard import content_length, get_problem, post_problem
from .service import ReviewService, ServiceError

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1 << 20


# Tipos MIME explícitos: en Windows ``mimetypes`` lee el registro y puede dar un tipo
# erróneo para ``.js``, lo que impide cargar módulos ES.
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def resolve_static(name: str, root: Path = STATIC) -> Path | None:
    """Archivo dentro de ``root`` (con subcarpetas) o ``None``; nunca fuera de él."""
    if "\\" in name or name.startswith("/"):
        return None
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    if path.suffix.lower() not in MIME and path.name != "LICENSE":
        return None
    return path


def send_static(
    handler: BaseHTTPRequestHandler, name: str, root: Path = STATIC
) -> None:
    path = resolve_static(name, root)
    if path is None:
        body = json.dumps({"error": "no encontrado"}).encode("utf-8")
        handler.send_response(HTTPStatus.NOT_FOUND)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    body = path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header(
        "Content-Type", MIME.get(path.suffix.lower(), "text/plain; charset=utf-8")
    )
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(service: ReviewService, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        server_version = "beam2ac-review/1"

        def log_message(
            self, fmt: str, *args: Any
        ) -> None:  # silencioso (sin registros en consola)
            return

        def _json(self, code: int, data: Any) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, name: str) -> None:
            send_static(self, name)

        def do_GET(self) -> None:  # noqa: N802
            bad = get_problem(self.headers, self.server.server_address[1])
            if bad:
                return self._json(bad[0], {"error": bad[1]})
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    return self._static("index.html")
                if url.path.startswith("/static/"):
                    return self._static(url.path[len("/static/") :])
                with lock:
                    if url.path == "/api/state":
                        return self._json(HTTPStatus.OK, service.state())
                    if url.path == "/api/attributes":
                        return self._json(
                            HTTPStatus.OK,
                            service.attributes(q.get("status", ""), q.get("q", "")),
                        )
                    if url.path == "/api/attribute":
                        return self._json(
                            HTTPStatus.OK, service.attribute(q.get("target", ""))
                        )
                    if url.path == "/api/history":
                        return self._json(
                            HTTPStatus.OK, service.history(q.get("target", ""))
                        )
                self._json(HTTPStatus.NOT_FOUND, {"error": "ruta desconocida"})
            except ServiceError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path != "/api/decision":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "ruta desconocida"})
            bad = post_problem(self.headers, self.server.server_address[1])
            if bad:
                return self._json(bad[0], {"error": bad[1]})
            length = content_length(self.headers)
            if length <= 0 or length > MAX_BODY:
                return self._json(
                    HTTPStatus.BAD_REQUEST, {"error": "cuerpo vacío o demasiado grande"}
                )
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ServiceError("se esperaba un objeto JSON")
                with lock:
                    return self._json(HTTPStatus.OK, service.decide(body))
            except (ServiceError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def make_server(service: ReviewService, port: int = 0) -> ThreadingHTTPServer:
    """Servidor ligado SOLO a 127.0.0.1 (``port=0``: puerto libre)."""
    return ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(service, threading.Lock())
    )
