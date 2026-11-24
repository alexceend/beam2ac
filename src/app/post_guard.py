"""Comprobación del origen de las peticiones POST de la interfaz local (G2.33, defecto 3 de G2.32).

El servidor solo escucha en 127.0.0.1, pero cualquier página abierta en el navegador puede enviar un POST
«simple» a ``http://127.0.0.1:<puerto>`` (CSRF) o, tras un cambio de DNS, llegar con otro ``Host``. Se exige:

* ``Host`` = ``127.0.0.1:<puerto>`` o ``localhost:<puerto>`` (contra el cambio de DNS);
* si hay ``Origin``, igual a ``http://<Host>`` (un sitio ajeno o ``null`` se rechaza);
* ``Content-Type: application/json`` (un formulario o ``text/plain`` entre sitios no puede enviarlo sin
  petición previa, que el servidor no atiende).

Sin ``Origin`` se acepta (clientes que no son navegadores: pruebas, ``curl``). Todos los ``fetch`` POST de
``static/*.js`` ya envían ``application/json``.
"""

from __future__ import annotations

from typing import Mapping


def post_problem(headers: Mapping[str, str], port: int) -> tuple[int, str] | None:
    """``(código HTTP, mensaje)`` si la petición no debe atenderse; ``None`` si es válida."""
    host = (headers.get("Host") or "").strip().lower()
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if host not in allowed:
        return 403, f"Host no permitido: {host!r}"
    origin = headers.get("Origin")
    if origin is not None and origin.strip().lower() != f"http://{host}":
        return 403, f"origen no permitido: {origin!r}"
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return 415, "se esperaba Content-Type: application/json"
    return None


def get_problem(headers: Mapping[str, str], port: int) -> tuple[int, str] | None:
    """Igual que ``post_problem`` para GET (G2.34, cota de G2.33): solo ``Host`` (y ``Origin`` si viene).

    Un GET de navegación no lleva ``Origin``; un ``fetch`` entre sitios sí. Sin comprobar ``Host``, tras un
    cambio de DNS (rebinding) una página ajena podría leer ``/api/file`` o ``/api/inspect``. No se exige
    ``Content-Type`` (un GET no tiene cuerpo).
    """
    host = (headers.get("Host") or "").strip().lower()
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return 403, f"Host no permitido: {host!r}"
    origin = headers.get("Origin")
    if origin is not None and origin.strip().lower() != f"http://{host}":
        return 403, f"origen no permitido: {origin!r}"
    return None


def content_length(headers: Mapping[str, str]) -> int:
    """Tamaño declarado del cuerpo; ``-1`` si no es un entero (no se lee el cuerpo)."""
    try:
        return int(headers.get("Content-Length") or 0)
    except ValueError:
        return -1
