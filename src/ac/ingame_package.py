"""Fase 7.1: paquete aislado para probar el coche en Assetto Corsa.

Uso:
    python -m src.ac.ingame_package <carpeta_coche_ensamblado>
        [--test-id beam2ac_test_<id>] [--out output/<id>/ingame_test]
        [--ac-root RUTA_AC]

Copia la carpeta del coche ensamblado (sin modificarla) a
``<out>/<test_id>/`` con un identificador único y escribe ``<out>/manifest.json``
con los pasos de instalación y de marcha atrás. Si ``<out>/<test_id>/`` ya existe o
``<out>`` ya está sellado con un ``manifest.json``, se niega: nunca borra ni sobrescribe
un paquete anterior (``src.safe_paths``).

**No instala nada**: nunca escribe en la instalación del juego. Con ``--ac-root``
solo LEE ``content/cars`` para comprobar que el identificador no existe.

Cambios respecto a la carpeta de origen (solo en la copia):

* ``ui/ui_car.json``: ``name`` lleva el sufijo `` [beam2ac test]`` para que el coche
  se distinga en el menú.

El KN5 conserva su nombre: ``data/lods.ini`` lo referencia por nombre. Observado
(solo lectura): 346 de 466 coches instalados no tienen ``<id>.kn5``, entre ellos el
oficial ``abarth500_s1``, que usa ``abarth500.kn5``. En cambio, los 466 tienen
``sfx/<id>.bank`` con el id de la carpeta, así que el sonido sí depende del id de
prueba (ensamblado.md §8).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from src.safe_paths import claim_output_dir, ensure_safe_output_path

TEST_PREFIX = "beam2ac_test_"
UI_SUFFIX = " [beam2ac test]"


class PackageError(RuntimeError):
    """El paquete de prueba no se puede preparar con seguridad."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    """Rutas relativas (con ``/``) → sha256 de cada archivo bajo ``root``."""
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def existing_car_ids(ac_root: Path) -> set[str]:
    """Identificadores (en minúsculas) de ``content/cars``. Solo lectura."""
    cars = ac_root / "content" / "cars"
    if not cars.is_dir():
        raise PackageError(f"no existe {cars}")
    return {d.name.lower() for d in cars.iterdir() if d.is_dir()}


def _inside(path: Path, root: Path) -> bool:
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def prepare_test_package(
    car_dir: str | Path,
    out_dir: str | Path,
    test_id: str | None = None,
    ac_root: str | Path | None = None,
) -> dict[str, Any]:
    """Prepara ``<out_dir>/<test_id>/`` y ``<out_dir>/manifest.json``."""
    src = Path(car_dir).resolve()
    if not (src / "data" / "lods.ini").is_file():
        raise PackageError(f"{src} no parece una carpeta de coche (sin lods.ini)")
    test_id = test_id or TEST_PREFIX + src.name
    if not test_id.replace("_", "").isalnum() or test_id.lower() != test_id:
        raise PackageError(f"id no válido (minúsculas, dígitos y _): {test_id!r}")
    out = ensure_safe_output_path(out_dir)
    dst = ensure_safe_output_path(out / test_id)
    if _inside(dst, src) or _inside(src, dst):
        raise PackageError("la copia y el origen se solapan")

    id_check: dict[str, Any] = {"checked": False}
    target = None
    if ac_root is not None:
        ac = Path(ac_root).resolve()
        if _inside(out, ac):
            raise PackageError("la salida no puede estar dentro de la instalación")
        ids = existing_car_ids(ac)
        id_check = {
            "checked": True,
            "cars_in_install": len(ids),
            "id_free": test_id.lower() not in ids,
            "similar_ids": sorted(i for i in ids if src.name.lower() in i),
        }
        if not id_check["id_free"]:
            raise PackageError(f"el id {test_id} ya existe en {ac}/content/cars")
        target = str(ac / "content" / "cars" / test_id)

    source_before = tree_hashes(src)
    # Nunca se borra ni se sobrescribe un paquete anterior (fase A).
    if dst.exists():
        raise PackageError(f"ya existe {dst}; no se sobrescribe")
    out = claim_output_dir(out)
    shutil.copytree(src, dst)

    ui_path = dst / "ui" / "ui_car.json"
    changes = []
    if ui_path.is_file():
        ui = json.loads(ui_path.read_text(encoding="utf-8"))
        if not str(ui.get("name", "")).endswith(UI_SUFFIX):
            ui["name"] = f"{ui.get('name', test_id)}{UI_SUFFIX}"
        ensure_safe_output_path(ui_path).write_text(
            json.dumps(ui, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        changes.append(f"ui/ui_car.json: name = {ui['name']!r}")

    source_after = tree_hashes(src)
    if source_after != source_before:
        raise PackageError("la carpeta de origen cambió durante la copia")

    files = tree_hashes(dst)
    missing_sfx = f"sfx/{test_id}.bank" not in files
    manifest: dict[str, Any] = {
        "test_id": test_id,
        "source_dir": str(src),
        "package_dir": str(dst),
        "installed": False,
        "install_target": target,
        "id_check": id_check,
        "changes_vs_source": changes,
        "files": files,
        "source_unchanged": True,
        "known_gaps": (
            [f"sfx/{test_id}.bank ausente (466/466 coches instalados lo tienen)"]
            if missing_sfx
            else []
        ),
        "procedure": [
            "1. Cerrar Assetto Corsa y Content Manager.",
            "2. Copiar Documents/Assetto Corsa/logs a una carpeta de respaldo.",
            f"3. Copiar {dst} a <AC>/content/cars/{test_id} (carpeta nueva; "
            "no sobrescribe nada porque el id no existe).",
            "4. Iniciar una práctica con ese coche en un circuito oficial.",
            "5. Guardar log.txt, errors.txt y custom_shaders_patch.log; buscar "
            f"'{test_id}', 'KEY_NOT_FOUND', 'not found', 'ERROR'.",
            f"6. Marcha atrás: borrar SOLO <AC>/content/cars/{test_id} y "
            "restaurar la copia de logs si se quiere.",
        ],
        "rollback_check": "comparar la lista de content/cars con id_check "
        "(no debe quedar el id de prueba)",
    }
    ensure_safe_output_path(out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m src.ac.ingame_package", description=__doc__
    )
    parser.add_argument("car_dir")
    parser.add_argument("--test-id")
    parser.add_argument("--out")
    parser.add_argument("--ac-root")
    args = parser.parse_args(argv)
    car = Path(args.car_dir)
    out = Path(args.out or f"output/{car.name}/ingame_test")
    m = prepare_test_package(car, out, args.test_id, args.ac_root)
    print(f"Paquete: {m['package_dir']}")
    print(
        f"Id libre en la instalación: {m['id_check'].get('id_free', 'sin comprobar')}"
    )
    for gap in m["known_gaps"]:
        print(f"  FALTA {gap}")
    print("No se ha instalado nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
