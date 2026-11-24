"""Detalles del coche en la interfaz de AC (``ui/ui_car.json``) editables por el usuario, y su portada.

Se guardan en ``<proyecto>/car_details.json`` (y ``cover.jpg`` si sube una imagen). Lo que el usuario deja vacío
se queda como lo calcula el exportador; lo que rellena lo sustituye. La portada es ``skins/<skin>/preview.jpg``:
la imagen del usuario o, si no hay, un render con texturas (``cover.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.safe_paths import ensure_safe_output_path

FILE = "car_details.json"
COVER = "cover.jpg"
TEXT = {"name": 80, "description": 4000, "class": 40, "brand": 60, "country": 60, "author": 80}
CLASSES = ("street", "race", "drift", "semislicks", "openwheel", "special", "tuned", "kart")


class DetailsError(ValueError):
    pass


def clean(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, limit in TEXT.items():
        v = raw.get(k)
        v = "" if v is None else str(v).strip()
        if len(v) > limit:
            raise DetailsError(f"{k}: máximo {limit} caracteres")
        if v:
            out[k] = v
    if "class" in out:
        out["class"] = out["class"].lower()
        if out["class"] not in CLASSES:
            raise DetailsError(f"class: una de {', '.join(CLASSES)}")
    year = raw.get("year")
    if year not in (None, ""):
        try:
            y = int(str(year).strip())
        except ValueError:
            raise DetailsError("year: un número entero") from None
        if not 1800 <= y <= 2100:
            raise DetailsError("year: entre 1800 y 2100")
        out["year"] = y
    tags = raw.get("tags")
    if isinstance(tags, str):
        tags = tags.replace(";", ",").split(",")
    tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if len(tags) > 20 or any(len(t) > 30 for t in tags):
        raise DetailsError("tags: máximo 20, de hasta 30 caracteres")
    if tags:
        out["tags"] = tags
    return out


def load(project: Any) -> dict[str, Any]:
    f = Path(project.root) / FILE
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def save(
    project: Any, raw: dict[str, Any], cover: bytes | None = None, clear_cover: bool = False
) -> dict[str, Any]:
    from .cover import to_jpeg

    d = clean(raw)
    root = Path(project.root)
    new_cover = to_jpeg(cover) if cover else None  # valida la imagen antes de escribir nada
    ensure_safe_output_path(root / FILE).write_text(
        json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    cf = ensure_safe_output_path(root / COVER)
    if (clear_cover or new_cover) and cf.is_file():
        cf.unlink()
    if new_cover:
        cf.write_bytes(new_cover)
    return d


def cover_bytes(project: Any) -> bytes | None:
    f = Path(project.root) / COVER
    return f.read_bytes() if f.is_file() else None


def apply_to_ui(ui: dict[str, Any], details: dict[str, Any]) -> list[str]:
    """Sustituye en ``ui`` lo que el usuario rellenó; devuelve las claves cambiadas."""
    changed = []
    for k, v in details.items():
        if v not in ("", None, []):
            ui[k] = v
            changed.append(k)
    return changed
