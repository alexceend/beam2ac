"""Inventario de los archivos de un vehículo de BeamNG: rol y uso en el conversor.

Responde, para cada archivo del origen, «qué es» y «qué hace el conversor con él»:

* ``interpretado``: un módulo lo lee y sus valores entran en la conversión;
* ``contrastado``: se lee solo para comprobar otros datos (no aporta valores);
* ``solo_huella``: se registra su sha256 (p. ej. el Lua: no se ejecuta);
* ``no_interpretado``: rol conocido, pero el conversor aún no lo usa (se conserva);
* ``desconocido``: ninguna regla lo reconoce (se informa; nunca se ignora en silencio).

Las reglas se comprueban en orden y la primera que coincide gana. Coinciden por la
ruta relativa a la carpeta del vehículo (``PurePosixPath.match``, sin leer el
contenido). Añadir un tipo de archivo nuevo = añadir una regla con su evidencia, no un
parser por vehículo.

Las texturas se clasifican después por su referencia en el ``materials.json`` que usa
el modelo: ``diffuseMap`` (interpretado, se embebe), otros mapas (normal, rugosidad,
opacidad…: no interpretados) o sin referencia.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

STATUSES = (
    "interpretado",
    "contrastado",
    "solo_huella",
    "no_interpretado",
    "desconocido",
)


@dataclass(frozen=True)
class Rule:
    patterns: tuple[str, ...]  # PurePosixPath.match; "dir/" = todo bajo esa carpeta
    role: str
    status: str
    consumer: str  # módulo que lo usa, o por qué no


# Orden: de lo más específico a lo más general.
RULES: tuple[Rule, ...] = (
    Rule(
        ("dynamicDecals/*.materials.json",),
        "materiales de calcomanías dinámicas",
        "no_interpretado",
        "sin uso: el KN5 no lleva calcomanías",
    ),
    Rule(
        ("*.materials.json",),
        "materiales del vehículo",
        "interpretado",
        "src/ac/kn5_materials.py, src/ac/cockpit_config.py, src/ac/lights.py",
    ),
    Rule(("*.jbeam",), "pieza JBeam", "interpretado", "src/jbeam (PartIndex)"),
    Rule(("*.pc",), "configuración", "interpretado", "src/jbeam/pc.py"),
    Rule(
        ("info.json",),
        "información del modelo",
        "interpretado",
        "nombre, pinturas (src/extract/paints.py)",
    ),
    Rule(
        ("info_*.json",),
        "información de la configuración",
        "contrastado",
        "src/ac/engine.py y drivetrain.py (compare_with_info); pintura por defecto",
    ),
    Rule(
        ("paints.meta",),
        "pinturas DefaultN (binario del exportador de Automation)",
        "contrastado",
        "src/extract/paints.py (solo se contrasta con info.json)",
    ),
    Rule(
        ("*.dae",),
        "malla",
        "interpretado",
        "src/mesh (flexbodies → KN5)",
    ),
    Rule(("lua/",), "Lua del vehículo", "solo_huella", "no se ejecuta"),
    Rule(
        ("*.car",),
        "diseño de origen de Automation (tabla Lua)",
        "no_interpretado",
        "sin uso: datos de Automation, no de BeamNG",
    ),
    Rule(
        ("license.txt", "LICENSE*", "*.license"),
        "licencia",
        "no_interpretado",
        "se debe conservar al distribuir (no lo copia el exportador)",
    ),
    Rule(
        ("tiredebug.txt",),
        "agarre del neumático calculado por Automation (texto)",
        "no_interpretado",
        "sin uso; posible referencia para tyres.ini (sin verificar)",
    ),
    Rule(("*.png", "*.jpg"), "imagen", "no_interpretado", "miniatura o textura"),
    Rule(("*.dds",), "textura DDS", "no_interpretado", "ver la clasificación por uso"),
)

TEXTURE_SUFFIXES = (".dds", ".png", ".jpg")


@dataclass(frozen=True)
class FileEntry:
    path: str
    role: str
    status: str
    consumer: str


def classify(path: str) -> FileEntry:
    p = PurePosixPath(path)

    def hit(pat: str) -> bool:
        if pat.endswith("/"):  # carpeta en la raíz del vehículo, a cualquier nivel
            return len(p.parts) > 1 and p.parts[0] == pat[:-1]
        return p.match(pat)

    for rule in RULES:
        if any(hit(pat) for pat in rule.patterns):
            return FileEntry(path, rule.role, rule.status, rule.consumer)
    return FileEntry(path, "desconocido", "desconocido", "ninguna regla")


def _texture_refs(materials: dict[str, Any]) -> dict[str, set[str]]:
    """Nombre de archivo de textura → claves de stage que lo referencian."""
    refs: dict[str, set[str]] = {}
    for mat in materials.values():
        if not isinstance(mat, dict):
            continue
        for stage in mat.get("Stages") or []:
            if not isinstance(stage, dict):
                continue
            for key, val in stage.items():
                if isinstance(val, str) and val.lower().endswith(TEXTURE_SUFFIXES):
                    refs.setdefault(val.split("/")[-1].lower(), set()).add(key)
    return refs


def inventory(
    files: Iterable[str],
    materials: dict[str, Any] | None = None,
    materials_name: str | None = None,
) -> dict[str, Any]:
    """Clasifica ``files`` (rutas relativas). ``materials``: el ``materials.json``
    que usa el modelo (ya leído), para clasificar las texturas por uso."""
    entries = [classify(f) for f in sorted(files)]
    refs = _texture_refs(materials) if materials is not None else None
    out = []
    for e in entries:
        if refs is not None and e.path.lower().endswith(TEXTURE_SUFFIXES):
            keys = refs.get(PurePosixPath(e.path).name.lower(), set())
            if "diffuseMap" in keys:
                e = FileEntry(
                    e.path,
                    "textura difusa",
                    "interpretado",
                    "src/ac/kn5_materials.py (se embebe en el KN5)",
                )
            elif keys:
                e = FileEntry(
                    e.path,
                    f"textura ({', '.join(sorted(keys))})",
                    "no_interpretado",
                    "sin uso: solo color difuso en el KN5",
                )
            elif e.path.lower().endswith(".dds"):
                e = FileEntry(
                    e.path,
                    "textura sin referencia",
                    "no_interpretado",
                    f"no la cita {materials_name or 'el materials.json'}",
                )
        out.append(e)
    by_status = Counter(e.status for e in out)
    by_role = Counter(e.role for e in out)
    return {
        "files": [asdict(e) for e in out],
        "by_status": {s: by_status.get(s, 0) for s in STATUSES},
        "by_role": dict(sorted(by_role.items())),
        "unknown": [e.path for e in out if e.status == "desconocido"],
        "materials_file": materials_name,
    }


def inventory_dir(vehicle_dir: Path, materials_file: Path | None = None) -> dict:
    from src.jbeam import lax_json

    files = [
        p.relative_to(vehicle_dir).as_posix()
        for p in vehicle_dir.rglob("*")
        if p.is_file()
    ]
    mats = lax_json.load(materials_file).value if materials_file else None
    return inventory(files, mats, materials_file.name if materials_file else None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vehicle_dir", type=Path)
    ap.add_argument("--materials", type=Path, help="materials.json del modelo")
    ap.add_argument("--json", type=Path, help="escribir el inventario (en output/)")
    args = ap.parse_args(argv)
    inv = inventory_dir(args.vehicle_dir, args.materials)
    if args.json:
        from src.safe_paths import ensure_safe_output_path

        target = ensure_safe_output_path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(inv, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps({k: inv[k] for k in ("by_status", "unknown")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
