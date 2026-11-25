"""Catálogo reproducible de referencias de Assetto Corsa (``content/cars``), en solo
lectura.

Qué registra por coche:

* **Origen** (clasificación por señales explícitas, todas guardadas en
``origin.signals``):
  - ``beam2ac_generated``: id con prefijo ``beam2ac_`` (paquetes del propio proyecto).
  Se EXCLUYE
    como evidencia (no se valida un coche contra sí mismo).
  - ``ac_ui_declares_automation``: el ``ui_car.json`` del coche DE AC declara el autor
  «Camshaft Software» o una descripción «Exported/Exportado … Automation» (antes se
  llamaba ``automation_export``, el mismo nombre que la familia de procedencia de
  BeamNG, que es otro concepto; renombrada el 2026-11-02, D-P4).
  - ``kunos_probable``: prefijo ``ks_`` y sin autor declarado (los coches oficiales no
  traen
    ``author``) o autor que contiene «Kunos» sin «edit»/«upd».
  - ``mod_derived_from_kunos``: prefijo ``ks_`` con otro autor o con «edit»/«upd».
  - ``origin_undeclared``: sin prefijo ``ks_`` y sin autor (pueden ser coches antiguos
  de Kunos o
    mods; NO se adivina).
  - ``mod``: el resto (autor declarado).
  Todas son heurísticas (``origin.method``); ninguna es una prueba de procedencia.
* **Estado de los datos**: ``readable`` (``data/``), ``encrypted`` (solo ``data.acd``:
no se lee ni
  se intenta descifrar), ``both`` (``data/`` y ``data.acd``: qué usa el juego NO está
  verificado, así que ``data/`` puede no ser lo que carga) o ``absent``.
* **Archivos legibles**: sha256 de cada archivo de ``data/`` y, para los INI de física,
sus claves
  y valores tal cual (texto).
* **Familia declarada**: ``ui_car.json`` ``parent`` (variantes y pieles).

Independencia: dos referencias con el MISMO contenido de archivo cuentan como una
(``independent_values`` agrupa por sha256 del archivo). Nada de este módulo infiere
equivalencias físicas entre coches.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.pipeline.physics_preview import parse_ini_text
from src.steam_paths import find_game

SCHEMA = "beam2ac.ac_reference/1"
INDEX_SCHEMA = "beam2ac.ac_reference_index/1"
PHYSICS_INIS = (
    "engine.ini",
    "drivetrain.ini",
    "suspensions.ini",
    "tyres.ini",
    "brakes.ini",
    "aero.ini",
    "car.ini",
    "electronics.ini",
    "setup.ini",
)
# Clase de origen de un coche de AC cuyo ui_car.json declara Automation. NO es la
# familia automation_export (procedencia de una exportación a BeamNG, catálogo).
AC_UI_DECLARES_AUTOMATION = "ac_ui_declares_automation"
_AC_DETECTED = find_game(
    "assettocorsa"
)  # biblioteca de Steam del usuario, no una ruta fija
DEFAULT_AC_CARS = (_AC_DETECTED or Path("assettocorsa")) / "content" / "cars"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ui(car: Path) -> tuple[dict[str, Any], str]:
    f = car / "ui" / "ui_car.json"
    if not f.is_file():
        return {}, "missing"
    text = f.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
    try:
        data = json.loads(text)
        return (data if isinstance(data, dict) else {}), "strict"
    except json.JSONDecodeError:
        pass
    fields = {}
    for key in ("name", "brand", "author", "class", "parent", "description", "year"):
        m = re.search(rf'"{key}"\s*:\s*("([^"\\]|\\.)*"|-?\d+)', text)
        if m:
            raw = m.group(1)
            fields[key] = raw.strip('"') if raw.startswith('"') else int(raw)
    return fields, "fields_by_pattern" if fields else "unreadable"


def classify_origin(car_id: str, ui: dict[str, Any]) -> dict[str, Any]:
    author = ui.get("author") if isinstance(ui.get("author"), str) else None
    desc = ui.get("description") if isinstance(ui.get("description"), str) else ""
    signals = {
        "prefix_ks": car_id.startswith("ks_"),
        "prefix_beam2ac": car_id.startswith("beam2ac_"),
        "author": author,
    }
    low = (author or "").lower()
    if signals["prefix_beam2ac"]:
        cls = "beam2ac_generated"
    elif "camshaft" in low or re.search(
        r"(export(ed|ado)\s+(from|de)\s+automation)", desc, re.I
    ):
        cls = AC_UI_DECLARES_AUTOMATION
    elif signals["prefix_ks"] and (
        author is None or ("kunos" in low and not re.search(r"edit|upd", low))
    ):
        cls = "kunos_probable"
    elif signals["prefix_ks"]:
        cls = "mod_derived_from_kunos"
    elif author is None:
        cls = "origin_undeclared"
    else:
        cls = "mod"
    return {
        "class": cls,
        "signals": signals,
        "method": "heurística por prefijo y autor declarado (ac_refs.py)",
    }


@dataclass
class AcReference:
    car_id: str
    origin: dict[str, Any]
    data_state: str
    ui: dict[str, Any]
    ui_parse: str
    files: dict[str, str] = field(default_factory=dict)  # data/<archivo> -> sha256
    physics: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # archivo -> {SECCIÓN/CLAVE: valor}
    acd_sha256: str = ""
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, **self.__dict__}


def scan_car(car: Path) -> AcReference:
    ui, how = _ui(car)
    data_dir, acd = car / "data", car / "data.acd"
    has_dir, has_acd = data_dir.is_dir(), acd.is_file()
    state = (
        "both"
        if has_dir and has_acd
        else "readable" if has_dir else "encrypted" if has_acd else "absent"
    )
    ref = AcReference(
        car.name,
        classify_origin(car.name, ui),
        state,
        {
            k: ui.get(k)
            for k in ("name", "brand", "class", "parent", "year", "tags")
            if k in ui
        },
        how,
    )
    if has_acd:
        ref.acd_sha256 = _sha(acd)
        ref.limitations.append("data.acd cifrado: no se lee ni se intenta descifrar")
    if state == "both":
        ref.limitations.append(
            "data/ y data.acd a la vez: no se ha verificado cuál carga el juego"
        )
    if state == "absent":
        ref.limitations.append("sin data/ ni data.acd")
    if has_dir:
        for f in sorted(data_dir.iterdir()):
            if f.is_file():
                ref.files[f.name] = _sha(f)
        for name in PHYSICS_INIS:
            f = data_dir / name
            if f.is_file():
                parsed = parse_ini_text(f.read_text(encoding="utf-8", errors="replace"))
                ref.physics[name] = {
                    f"{s}/{k}": v for (s, k), (v, _c) in sorted(parsed.items())
                }
    return ref


def default_ac_cars() -> Path:
    """``content/cars`` de la instalación de AC de los ajustes, o el respaldo de desarrollo."""
    from src.project.export import default_ac_root

    root = default_ac_root()
    return root / "content" / "cars" if root else DEFAULT_AC_CARS


def scan(cars_root: Path | None = None) -> list[AcReference]:
    cars_root = cars_root or default_ac_cars()
    return [scan_car(d) for d in sorted(Path(cars_root).iterdir()) if d.is_dir()]


def build_index(refs: list[AcReference]) -> dict[str, Any]:
    """Índice por clave con independencia por contenido de archivo; excluye
    beam2ac_generated."""
    usable = [r for r in refs if r.origin["class"] != "beam2ac_generated"]
    by_key: dict[str, dict[str, Any]] = {}
    for r in usable:
        for file, keys in r.physics.items():
            digest = r.files.get(file, "")
            for key, value in keys.items():
                entry = by_key.setdefault(f"ac:{file}/{key}", {"groups": {}})
                g = entry["groups"].setdefault(
                    digest, {"value": value, "cars": [], "origins": []}
                )
                g["cars"].append(r.car_id)
                g["origins"].append(r.origin["class"])
    for entry in by_key.values():
        groups = entry["groups"]
        entry["independent_values"] = len(groups)  # contenidos de archivo distintos
        entry["cars"] = sum(len(g["cars"]) for g in groups.values())
        entry["by_origin"] = {}
        for g in groups.values():
            for o in sorted(set(g["origins"])):
                entry["by_origin"][o] = entry["by_origin"].get(o, 0) + 1
    file_dups: dict[str, dict[str, list[str]]] = {}
    for r in usable:
        for file, digest in r.files.items():
            if file in PHYSICS_INIS:
                file_dups.setdefault(file, {}).setdefault(digest, []).append(r.car_id)
    summary = {
        "cars": len(refs),
        "excluded_beam2ac": [
            r.car_id for r in refs if r.origin["class"] == "beam2ac_generated"
        ],
        "by_origin": _count(r.origin["class"] for r in refs),
        "by_data_state": _count(r.data_state for r in refs),
        "readable_by_origin": _count(
            r.origin["class"] for r in usable if r.data_state in ("readable", "both")
        ),
        "distinct_physics_files": {f: len(d) for f, d in sorted(file_dups.items())},
        "largest_duplicate_groups": {
            f: max((len(v) for v in d.values()), default=0)
            for f, d in sorted(file_dups.items())
        },
        AC_UI_DECLARES_AUTOMATION: [
            r.car_id for r in refs if r.origin["class"] == AC_UI_DECLARES_AUTOMATION
        ],
    }
    return {
        "schema": INDEX_SCHEMA,
        "summary": summary,
        "keys": dict(sorted(by_key.items())),
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items()))


def reference_values(index: dict[str, Any], attribute: str) -> list[dict[str, Any]]:
    """Valores de referencia de un atributo, uno por contenido independiente, con sus
    coches."""
    entry = index["keys"].get(attribute)
    if not entry:
        return []
    return [
        {
            "value": g["value"],
            "file_sha256": digest,
            "cars": g["cars"],
            "origins": sorted(set(g["origins"])),
        }
        for digest, g in entry["groups"].items()
    ]
