"""Fase 7.1: variantes DIAGNÓSTICAS del tipo de suspensión trasera.

Uso:
    python -m src.ac.diag_variant <paquete_de_referencia> --rear-type AXLE|DWB
        --ref-suspensions <suspensions.ini oficial> [--test-id ID] [--out DIR]
        [--ac-root RUTA_AC]

Objetivo: averiguar qué claves pide el cargador de Assetto Corsa para cada tipo
(el registro del juego lista cada clave leída como KEY_NOT_FOUND). **No** elige la
suspensión del Ibiza ni la valida: que el juego avance más no demuestra nada sobre
la física (ensamblado.md §9.4–9.6).

Qué hace:
1. Copia el paquete de referencia con ``prepare_test_package`` (id único,
   comprobación de que el id no existe en la instalación, origen intacto).
2. En la COPIA escribe solo ``TYPE=<tipo>`` en ``[REAR]``, marcado como
   diagnóstico. No añade puntos de montaje, secciones ni valores.
3. Compara los NOMBRES de clave (nunca los valores) con un ``suspensions.ini``
   oficial del mismo tipo y documenta en el manifiesto qué falta.

Con ``--diag-aero-minimal`` añade además ``data/aero.ini`` con solo
``[WING_0] NAME=BODY`` (``AERO_MINIMAL``): sin tablas, ganancias ni medidas.
Sirve para leer qué claves de aero.ini exige el cargador; no es aerodinámica.

Subcomando ``aero`` (paquetes que ya tienen ``[REAR] TYPE``, como el ensamblado
editado a mano del Ibiza):

    python -m src.ac.diag_variant aero <paquete_base> --test-id ID --out DIR
        [--ac-root RUTA_AC]

Copia un paquete de prueba existente (de ``src.ac.ingame_package``) y añade SOLO
``data/aero.ini`` con ``AERO_MINIMAL``. No toca ``suspensions.ini`` ni ningún otro
archivo. Todas las comprobaciones se hacen antes de copiar: si falla algo, no se
escribe nada (ver ``prepare_aero_variant``).

Referencias oficiales usadas (solo lectura, en ``sample_assetto_unpacked/``):
``ks_toyota_ae86`` para AXLE y ``ks_abarth500_assetto_corse`` para DWB.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from src.ac.car_validate import read_ini
from src.ac.ingame_package import (
    UI_SUFFIX,
    PackageError,
    prepare_test_package,
    tree_hashes,
)
from src.safe_paths import ensure_safe_output_path

REAR_TYPES = ("AXLE", "DWB")

# aero.ini mínimo para leer qué exige el cargador (ensamblado.md §9.7). Solo
# estructura: una sección WING_0 con su etiqueta. Sin HEADER, tablas ni valores
# físicos: el coche no tiene aerodinámica real con este archivo.
AERO_MINIMAL = (
    "; DIAGNOSTICO fase 7.1: solo para leer los requisitos del cargador; "
    "sin aerodinamica real\n"
    "[WING_0]\n"
    "NAME=BODY\n"
)
# driver3d.ini mínimo para comprobar si AC crea DriverModel (ensamblado.md §9.9).
# Estructura y POSITION=0,0,0 de los 4 driver3d.ini oficiales inspeccionados;
# NAME=driver referencia (sin copiarlo) el modelo oficial content/driver/driver.kn5
# de la instalación. Sin driver_base_pos.knh, animaciones ni HIDE_OBJECT.
DRIVER3D_MINIMAL = (
    "; DIAGNOSTICO fase 7.1: solo para comprobar si se crea DriverModel\n"
    "[MODEL]\n"
    "NAME=driver\n"
    "POSITION=0,0,0\n"
)
# Secciones cuyas claves dependen del tipo trasero.
TYPE_SECTIONS = {"AXLE": ("REAR", "AXLE"), "DWB": ("REAR",)}


def insert_rear_type(text: str, rear_type: str) -> str:
    """Inserta ``TYPE=`` justo después de la cabecera ``[REAR]``.

    Falla si no hay exactamente una cabecera ``[REAR]`` o si ya hay ``TYPE``.
    El comentario no lleva corchetes (ensamblado.md §9.3).
    """
    if rear_type not in REAR_TYPES:
        raise PackageError(f"tipo no previsto: {rear_type}")
    lines = text.split("\n")
    headers = [i for i, line in enumerate(lines) if line.strip().upper() == "[REAR]"]
    if len(headers) != 1:
        raise PackageError(f"se esperaba una cabecera REAR, hay {len(headers)}")
    start = headers[0] + 1
    end = next(
        (
            i
            for i in range(start, len(lines))
            if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")
        ),
        len(lines),
    )
    for line in lines[start:end]:
        if line.partition(";")[0].split("=")[0].strip().upper() == "TYPE":
            raise PackageError("REAR ya tiene TYPE")
    lines.insert(
        start,
        f"TYPE={rear_type}\t\t; DIAGNOSTICO fase 7.1: solo para leer los requisitos "
        "del cargador; no es la suspension elegida",
    )
    return "\n".join(lines)


def missing_keys(
    ours: dict[str, dict[str, str]],
    reference: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    """Claves de la referencia que no están en nuestro INI, por sección."""
    out = {}
    for section, keys in reference.items():
        miss = sorted(k for k in keys if k not in ours.get(section, {}))
        if miss:
            out[section] = miss
    return out


def prepare_variant(
    reference_package: str | Path,
    rear_type: str,
    ref_suspensions: str | Path,
    out_dir: str | Path,
    test_id: str,
    ac_root: str | Path | None = None,
    aero_minimal: bool = False,
) -> dict[str, Any]:
    ref_pkg = Path(reference_package).resolve()
    before = tree_hashes(ref_pkg)
    if aero_minimal and (ref_pkg / "data" / "aero.ini").exists():
        raise PackageError("el paquete ya tiene aero.ini; no se sustituye")
    # Validar ANTES de copiar: si [REAR] ya tiene TYPE (o el INI no sirve), no debe
    # quedar ninguna copia parcial ni manifiesto (antes fallaba tras la copia).
    src_susp = ref_pkg / "data" / "suspensions.ini"
    if not src_susp.is_file():
        raise PackageError(f"falta {src_susp}")
    if not Path(ref_suspensions).is_file():
        raise PackageError(f"falta el suspensions.ini de referencia: {ref_suspensions}")
    new_susp = insert_rear_type(src_susp.read_text(encoding="utf-8"), rear_type)
    manifest = prepare_test_package(ref_pkg, out_dir, test_id, ac_root)
    pkg = Path(manifest["package_dir"])
    susp = ensure_safe_output_path(pkg / "data" / "suspensions.ini")
    susp.write_text(new_susp, encoding="utf-8")
    if aero_minimal:
        aero = ensure_safe_output_path(pkg / "data" / "aero.ini")
        if aero.exists():
            raise PackageError("la copia ya tiene aero.ini")
        aero.write_text(AERO_MINIMAL, encoding="utf-8")
        manifest["changes_vs_source"].append(
            "data/aero.ini: mínimo estructural (DIAGNÓSTICO, sin aerodinámica)"
        )
    if tree_hashes(ref_pkg) != before:
        raise PackageError("el paquete de referencia cambió")

    ours = read_ini(susp)
    reference = read_ini(ref_suspensions)
    missing = missing_keys(ours, reference)
    manifest["files"] = tree_hashes(pkg)
    manifest["changes_vs_source"].append(
        f"data/suspensions.ini: REAR TYPE={rear_type} (DIAGNÓSTICO)"
    )
    manifest["diagnostic"] = {
        "purpose": "leer qué claves pide el cargador para este tipo; "
        "no valida ninguna suspensión",
        "rear_type": rear_type,
        "reference_suspensions": str(Path(ref_suspensions).resolve()),
        "only_key_names_compared": True,
        "missing_vs_reference": missing,
        "missing_type_specific": {
            s: missing.get(s, []) for s in TYPE_SECTIONS[rear_type]
        },
        "invented_values": [],
        "aero_minimal": aero_minimal,
    }
    ensure_safe_output_path(Path(out_dir) / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def prepare_driver3d_variant(
    base_package: str | Path,
    out_dir: str | Path,
    test_id: str,
    ac_root: str | Path | None = None,
) -> dict[str, Any]:
    """Copia un paquete de variante existente y añade SOLO ``data/driver3d.ini``.

    El paquete base no se toca; la copia debe diferir de él únicamente en ese
    archivo (se comprueba aquí y queda en el manifiesto).
    """
    base = Path(base_package).resolve()
    before = tree_hashes(base)
    if (base / "data" / "driver3d.ini").exists():
        raise PackageError("el paquete base ya tiene driver3d.ini")
    manifest = prepare_test_package(base, out_dir, test_id, ac_root)
    pkg = Path(manifest["package_dir"])
    target = ensure_safe_output_path(pkg / "data" / "driver3d.ini")
    if target.exists():
        raise PackageError("la copia ya tiene driver3d.ini")
    target.write_text(DRIVER3D_MINIMAL, encoding="utf-8")
    if tree_hashes(base) != before:
        raise PackageError("el paquete base cambió")
    files = tree_hashes(pkg)
    diff = sorted(f for f in set(files) | set(before) if files.get(f) != before.get(f))
    if diff != ["data/driver3d.ini"]:
        raise PackageError(f"la copia difiere de la base en más archivos: {diff}")
    manifest["files"] = files
    manifest["changes_vs_source"].append(
        "data/driver3d.ini: mínimo (DIAGNÓSTICO: MODEL NAME=driver, POSITION=0,0,0)"
    )
    manifest["diagnostic"] = {
        "purpose": "comprobar si AC crea DriverModel con solo driver3d.ini",
        "base_package": str(base),
        "diff_vs_base": diff,
        "not_added": [
            "driver_base_pos.knh",
            "animations/steer.ksanim",
            "STEER_ANIMATION",
            "SHIFT_ANIMATION",
            "HIDE_OBJECT_n",
        ],
        "invented_values": [],
    }
    ensure_safe_output_path(Path(out_dir) / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def prepare_aero_variant(
    base_package: str | Path,
    out_dir: str | Path,
    test_id: str,
    ac_root: str | Path | None = None,
) -> dict[str, Any]:
    """Copia un paquete de prueba existente y añade SOLO ``data/aero.ini`` mínimo.

    Para paquetes cuyo ``[REAR]`` ya tiene ``TYPE`` (el ensamblado editado a mano):
    no inserta ni modifica ``TYPE`` ni ningún otro archivo; la copia debe diferir
    de la base únicamente en ``data/aero.ini`` (se comprueba).

    Comprobaciones antes de copiar (si fallan, no se escribe nada):

    * la base no tiene ``data/aero.ini`` (no se sustituye nunca);
    * la base es un paquete de prueba: ``ui/ui_car.json`` ya lleva el sufijo
      `` [beam2ac test]``, así que la copia no cambia ese archivo;
    * no existen ``<out>/<test_id>/`` ni ``<out>/manifest.json`` (nunca se borra ni
      se sobrescribe un artefacto existente);
    * el resto de ``prepare_test_package``: id válido, sin solapamientos, fuera de
      las muestras, id libre en la instalación si se da ``ac_root``.

    Si algo falla después de copiar, se eliminan la copia y el manifiesto creados
    en esta llamada.
    """
    base = Path(base_package).resolve()
    if not (base / "data" / "lods.ini").is_file():
        raise PackageError(f"{base} no parece un paquete de coche (sin lods.ini)")
    if (base / "data" / "aero.ini").exists():
        raise PackageError("el paquete base ya tiene aero.ini; no se sustituye")
    ui_path = base / "ui" / "ui_car.json"
    if ui_path.is_file():
        name = str(json.loads(ui_path.read_text(encoding="utf-8")).get("name", ""))
        if not name.endswith(UI_SUFFIX):
            raise PackageError(
                "la base no es un paquete de prueba (ui_car.json sin el sufijo "
                f"{UI_SUFFIX!r}); prepara antes uno con src.ac.ingame_package"
            )
    out = ensure_safe_output_path(out_dir)
    dst = ensure_safe_output_path(out / test_id)
    manifest_path = out / "manifest.json"
    if dst.exists():
        raise PackageError(f"ya existe {dst}; no se sobrescribe")
    if manifest_path.exists():
        raise PackageError(f"ya existe {manifest_path}; no se sobrescribe")
    out_created = not out.exists()
    before = tree_hashes(base)

    manifest = prepare_test_package(base, out, test_id, ac_root)
    try:
        pkg = Path(manifest["package_dir"])
        target = ensure_safe_output_path(pkg / "data" / "aero.ini")
        if target.exists():
            raise PackageError("la copia ya tiene aero.ini")
        target.write_text(AERO_MINIMAL, encoding="utf-8")
        if tree_hashes(base) != before:
            raise PackageError("el paquete base cambió")
        files = tree_hashes(pkg)
        diff = sorted(
            f for f in set(files) | set(before) if files.get(f) != before.get(f)
        )
        if diff != ["data/aero.ini"]:
            raise PackageError(f"la copia difiere de la base en más archivos: {diff}")
        manifest["files"] = files
        manifest["changes_vs_source"].append(
            "data/aero.ini: mínimo estructural (DIAGNÓSTICO, sin aerodinámica)"
        )
        manifest["diagnostic"] = {
            "purpose": "pasar la carga de aero.ini sin aerodinámica real; "
            "no valida la aerodinámica ni la suspensión",
            "base_package": str(base),
            "diff_vs_base": diff,
            "rear_type_untouched": True,
            "aero_minimal": True,
            "invented_values": [],
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        # Sin copias parciales: se borra solo lo creado en esta llamada.
        if dst.exists():
            shutil.rmtree(dst)
        if manifest_path.exists():
            manifest_path.unlink()
        if out_created and out.exists() and not any(out.iterdir()):
            out.rmdir()
        raise
    return manifest


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "aero":
        p = argparse.ArgumentParser(prog="python -m src.ac.diag_variant aero")
        p.add_argument("base_package")
        p.add_argument("--test-id", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--ac-root")
        a = p.parse_args(argv[1:])
        m = prepare_aero_variant(a.base_package, a.out, a.test_id, a.ac_root)
        print(f"Variante: {m['package_dir']}")
        print(f"Id libre: {m['id_check'].get('id_free', 'sin comprobar')}")
        print(f"Diferencia con la base: {m['diagnostic']['diff_vs_base']}")
        print("No se ha instalado nada.")
        return 0
    if argv and argv[0] == "driver3d":
        p = argparse.ArgumentParser(prog="python -m src.ac.diag_variant driver3d")
        p.add_argument("base_package")
        p.add_argument("--test-id", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--ac-root")
        a = p.parse_args(argv[1:])
        m = prepare_driver3d_variant(a.base_package, a.out, a.test_id, a.ac_root)
        print(f"Variante: {m['package_dir']}")
        print(f"Id libre: {m['id_check'].get('id_free', 'sin comprobar')}")
        print(f"Diferencia con la base: {m['diagnostic']['diff_vs_base']}")
        print("No se ha instalado nada.")
        return 0
    parser = argparse.ArgumentParser(
        prog="python -m src.ac.diag_variant", description=__doc__
    )
    parser.add_argument("reference_package")
    parser.add_argument("--rear-type", required=True, choices=REAR_TYPES)
    parser.add_argument("--ref-suspensions", required=True)
    parser.add_argument("--test-id")
    parser.add_argument("--out")
    parser.add_argument("--ac-root")
    parser.add_argument(
        "--diag-aero-minimal",
        action="store_true",
        help="añade un aero.ini mínimo estructural (solo diagnóstico)",
    )
    args = parser.parse_args(argv)
    t = ("aero_" if args.diag_aero_minimal else "") + args.rear_type.lower()
    test_id = args.test_id or f"beam2ac_diag_{t}_seat_ibiza_acfr"
    out = Path(args.out or f"output/seat_ibiza_acfr/ingame_test/diag_{t}")
    m = prepare_variant(
        args.reference_package,
        args.rear_type,
        args.ref_suspensions,
        out,
        test_id,
        args.ac_root,
        args.diag_aero_minimal,
    )
    print(f"Variante: {m['package_dir']}")
    print(f"Id libre: {m['id_check'].get('id_free', 'sin comprobar')}")
    for section, keys in m["diagnostic"]["missing_vs_reference"].items():
        print(f"  FALTA [{section}]: {', '.join(keys)}")
    print("No se ha instalado nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
