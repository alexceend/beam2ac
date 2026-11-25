"""Ajustes de cámaras y piloto hechos en la interfaz (G2.32), con versiones de solo añadir.

Clave = huella del CONTENIDO del origen + configuración (no el proyecto): ``convert`` crea un
proyecto nuevo cada vez y los ajustes deben sobrevivir a recargar el ZIP. Los valores originales
nunca se guardan encima: son lo que calcula el exportador; «restaurar» añade una versión vacía.

Ítems (marco del KN5, ejes AC: +X izquierda, +Y arriba, +Z adelante, metros):
``driver_eyes`` (DRIVEREYES y POS de dash_cam.ini), ``bonnet``, ``bumper`` y ``hips`` (caderas del
piloto, independientes de los ojos) y ``fuel_tank`` (``[FUELTANK] POSITION`` de car.ini, mismo marco que
DRIVEREYES; nota de uso: el depósito se coloca de forma manual). AC solo lee posiciones de estas cámaras: no hay orientación.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from src.safe_paths import DEFAULT_OUTPUT_DIR, ensure_safe_output_path

ROOT = DEFAULT_OUTPUT_DIR / "cockpit_edits"
ITEMS = ("driver_eyes", "bonnet", "bumper", "hips", "fuel_tank")
LIMIT_M = 6.0
_KEY = re.compile(r"^[0-9a-f]{16}_[A-Za-z0-9_.-]{1,64}$")


class EditsError(ValueError):
    pass


def project_key(project: Any) -> str:
    from .manifest import FingerprintCache, source_fingerprint

    cache = FingerprintCache(project)
    fp = source_fingerprint(project, cache)
    if cache.dirty:  # sin guardar, cada llamada volvía a hashear el zip del vehículo (~3 s)
        cache.save()
    blob = json.dumps(fp, sort_keys=True).encode()
    pc = re.sub(r"[^A-Za-z0-9_.-]", "_", str(project.source.pc or "x"))[:64]
    return f"{hashlib.sha256(blob).hexdigest()[:16]}_{pc}"


def clean(items: dict[str, Any]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    if items is None:
        items = {}
    if not isinstance(items, dict):
        raise EditsError("items: se esperaba un objeto {ítem: [x, y, z]}")
    for k, v in items.items():
        if k not in ITEMS:
            raise EditsError(f"ítem desconocido {k!r}")
        if (
            not isinstance(v, (list, tuple))
            or len(v) != 3
            or not all(
                isinstance(c, (int, float)) and not isinstance(c, bool) for c in v
            )
            or any(abs(c) > LIMIT_M or not math.isfinite(c) for c in v)
        ):
            raise EditsError(f"{k}: se esperan 3 números finitos (|v| ≤ {LIMIT_M} m)")
        out[k] = [round(float(c), 4) for c in v]
    return out


def _dir(key: str) -> Path:
    if not _KEY.match(key):
        raise EditsError("clave de ajustes no válida")
    return ROOT / key


def versions(key: str) -> list[dict[str, Any]]:
    d = _dir(key)
    return [
        json.loads(p.read_text("utf-8"))
        for p in sorted(d.glob("v[0-9]*.json"), key=lambda p: int(p.stem[1:]))
    ]


def current(key: str) -> dict[str, Any]:
    v = versions(key)
    return v[-1] if v else {"version": 0, "items": {}}


def save(key: str, items: dict[str, Any], note: str = "") -> dict[str, Any]:
    items = clean(items)
    d = _dir(key)
    ensure_safe_output_path(d / "x").parent.mkdir(parents=True, exist_ok=True)
    n = (current(key)["version"]) + 1
    rec = {
        "schema": "beam2ac.cockpit_edits/1",
        "version": n,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        "frame": "KN5, ejes AC (+X izq., +Y arriba, +Z adelante), m",
        "items": items,
    }
    path = ensure_safe_output_path(d / f"v{n:03d}.json")
    with open(path, "x", encoding="utf-8") as f:  # nunca sobrescribe una versión
        json.dump(rec, f, indent=1, ensure_ascii=False)
    return rec


def for_project(project: Any) -> dict[str, Any]:
    """Versión vigente de los ajustes de un proyecto (vacía si no hay)."""
    key = project_key(project)
    return {**current(key), "key": key}
