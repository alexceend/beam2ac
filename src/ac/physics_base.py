"""Base física congelada: la física y los requisitos de carga de un paquete aprobado.

Uso:
    python -m src.ac.physics_base freeze <carpeta_coche_del_paquete> --base-id ID
        [--car-id seat_ibiza_acfr] [--out-root output/<car>/physics_bases]

``freeze`` copia byte a byte, a una carpeta NUEVA y sellada con ``manifest.json``,
los archivos de un paquete aprobado (que debe coincidir con su propio manifiesto)
clasificados uno a uno con la tabla de este módulo; nunca «todo ``data/``»:

* **física**: ``aero.ini``, ``brakes.ini``, ``drivetrain.ini``, ``engine.ini``,
  ``suspensions.ini``, ``tyres.ini`` y las tablas que referencian (``power.lut``,
  ``*.lut``/``*.rto`` de esos INI);
* **requisito de carga, no física**: ``setup.ini`` (sin él CSP falla al crear el menú de
  boxes, ensamblado.md §9.12);
* **plantilla**: ``car.ini`` sin su sección ``[GRAPHICS]`` (ojos y cámaras). El resto de
  secciones (``HEADER``, ``INFO``, ``BASIC``, ``FUEL``, ``CONTROLS``,
  ``EXPLICIT_INERTIA``) se conserva byte a byte;
* **excluidos**, con su motivo: cámaras, luces, audio, piloto/animación, LOD, modelo,
  collider, pose, texturas e interfaz. El ensamblador genera los suyos.

Un archivo que la tabla no conoce detiene la congelación: hay que clasificarlo a mano.
Antes de escribir se exige ``[GEARBOX] VALID_SHIFT_RPM_WINDOW`` > 0 y los requisitos de
carga de ``car_validate.load_requirements`` (``[REAR] TYPE`` y ``aero.ini`` con
``WING_n``/``DATA``).

La base es **funcional y congelada**: es la física con la que el paquete se probó en el
juego. No está calibrada ni validada en precisión (ver ``STATUS``).

``--physics-base`` del ensamblador acepta la carpeta de la base o la de su coche; con
una base declarada copia exactamente ``physics_files`` y verifica cada hash.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.ac.car_validate import (
    FILE_SUFFIXES,
    load_requirements,
    read_ini,
    shift_window_problem,
)
from src.ac.ingame_package import sha256, tree_hashes
from src.safe_paths import ensure_safe_output_path, new_output_dir

KIND = "beam2ac_physics_base"
VERSION = 1
STATUS = (
    "base física funcional congelada: la física con la que el paquete de origen se "
    "probó en el juego. NO está calibrada ni validada en precisión (fase 8 pendiente); "
    "contiene valores DIAG/EXPERIMENTAL marcados en sus comentarios."
)
PHYSICS_INIS = {
    "aero.ini": "aerodinámica (requisito de carga: WING_n o DATA, §9.6-9.7)",
    "brakes.ini": "frenos",
    "drivetrain.ini": "transmisión (incluye [GEARBOX] VALID_SHIFT_RPM_WINDOW, §9.15)",
    "engine.ini": "motor",
    "suspensions.ini": "suspensión (requisito de carga: [REAR] TYPE, §9.4)",
    "tyres.ini": "neumáticos",
}
LOAD_ONLY = {"setup.ini": "requisito de carga, no física (menú de boxes, §9.12)"}
TEMPLATE = "car.ini"
TEMPLATE_DROPPED_SECTION = "GRAPHICS"
EXCLUDED_DATA = {
    "dash_cam.ini": "cámara Dash (cámaras; fuera de la base)",
    "lights.ini": "luces (fuera de la base)",
    "sounds.ini": "audio (fuera de la base)",
    "driver3d.ini": "piloto y animación de manos (lo genera el ensamblador)",
    "lods.ini": "LOD del modelo (lo genera el ensamblador)",
}
EXCLUDED_TOP = {
    ".kn5": "modelo o collider (lo genera el ensamblador)",
    ".knh": "pose del piloto (la genera el ensamblador)",
    "animations": "animación de manos",
    "sfx": "audio",
    "extension": "configuración visual de CSP (materiales, luces)",
    "skins": "texturas e interfaz de la pintura",
    "ui": "interfaz",
}


class PhysicsBaseError(RuntimeError):
    """La base física no se puede congelar o no coincide con su manifiesto."""


# ------------------------------------------------------------------ clasificación
def _referenced_tables(data: Path, ini_names: list[str]) -> dict[str, str]:
    """Tablas (``.lut``/``.rto``) de ``data/`` referenciadas por los INI de física."""
    found: dict[str, str] = {}
    for name in ini_names:
        for section, keys in read_ini(data / name).items():
            for key, value in keys.items():
                low = value.lower()
                if low.endswith(FILE_SUFFIXES) and low.endswith((".lut", ".rto")):
                    found.setdefault(value, f"tabla de {name} [{section}] {key}")
    return found


def classify(car_dir: Path) -> dict[str, Any]:
    """Clasifica cada archivo del paquete. Error si alguno no está en la tabla."""
    data = car_dir / "data"
    physics_inis = [n for n in PHYSICS_INIS if (data / n).is_file()]
    missing = sorted(set(PHYSICS_INIS) - set(physics_inis))
    if missing:
        raise PhysicsBaseError(f"faltan INI de física en data/: {missing}")
    tables = _referenced_tables(data, physics_inis)
    physics: dict[str, dict[str, str]] = {}
    excluded: dict[str, str] = {}
    unknown: list[str] = []
    for rel in sorted(tree_hashes(car_dir)):
        top, _, rest = rel.partition("/")
        if top == "data" and "/" not in rest:
            if rest in PHYSICS_INIS:
                physics[rel] = {"category": "física", "reason": PHYSICS_INIS[rest]}
            elif rest in tables:
                physics[rel] = {"category": "física", "reason": tables[rest]}
            elif rest in LOAD_ONLY:
                physics[rel] = {
                    "category": "requisito de carga",
                    "reason": LOAD_ONLY[rest],
                }
            elif rest == TEMPLATE:
                physics[rel] = {
                    "category": "plantilla",
                    "reason": f"car.ini sin [{TEMPLATE_DROPPED_SECTION}] "
                    "(ojos y cámaras)",
                }
            elif rest in EXCLUDED_DATA:
                excluded[rel] = EXCLUDED_DATA[rest]
            else:
                unknown.append(rel)
        elif not rest and Path(top).suffix.lower() in EXCLUDED_TOP:
            excluded[rel] = EXCLUDED_TOP[Path(top).suffix.lower()]
        elif rest and top in EXCLUDED_TOP:
            excluded[rel] = EXCLUDED_TOP[top]
        else:
            unknown.append(rel)
    absent = sorted(t for t in tables if f"data/{t}" not in physics)
    if absent:
        raise PhysicsBaseError(f"tablas referenciadas que no están en data/: {absent}")
    if unknown:
        raise PhysicsBaseError(
            f"archivos sin clasificar (añadirlos a la tabla de physics_base): {unknown}"
        )
    if f"data/{TEMPLATE}" not in physics:
        raise PhysicsBaseError("falta data/car.ini")
    return {"physics": physics, "excluded": excluded}


# ------------------------------------------------------------------ car.ini
def strip_section(raw: bytes, section: str) -> tuple[bytes, list[str]]:
    """``raw`` sin la sección ``[section]`` (cabecera incluida) hasta la siguiente o el
    final. Devuelve los bytes y las líneas quitadas. El resto queda byte a byte."""
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.split(nl)
    heads = [
        i for i, ln in enumerate(lines) if ln.strip().upper() == f"[{section}]".encode()
    ]
    odd = [
        ln
        for ln in lines
        if ln.partition(b";")[0].strip().upper() == f"[{section}]".encode()
        and ln.strip().upper() != f"[{section}]".encode()
    ]
    if odd or len(heads) > 1:
        raise PhysicsBaseError(
            f"car.ini: sección {section} repetida o con formato raro"
        )
    if not heads:
        return raw, []
    start = heads[0]
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip().startswith(b"[") and lines[i].strip().endswith(b"]")
        ),
        len(lines),
    )
    if end == len(lines) and lines[-1] == b"":
        end -= 1  # conservar el salto de línea final
    removed = [ln.decode("utf-8") for ln in lines[start:end]]
    return nl.join(lines[:start] + lines[end:]), removed


# ------------------------------------------------------------------ congelar
def freeze(
    package_car_dir: Path,
    out_root: Path,
    base_id: str,
    car_id: str,
) -> dict[str, Any]:
    src = Path(package_car_dir).resolve()
    pkg_manifest = src.parent / "manifest.json"
    if not pkg_manifest.is_file():
        raise PhysicsBaseError(f"{src}: el paquete de origen no tiene manifest.json")
    pm = json.loads(pkg_manifest.read_text(encoding="utf-8"))
    if Path(pm.get("package_dir", "")).resolve() != src:
        raise PhysicsBaseError(f"{pkg_manifest} no describe {src}")
    before = tree_hashes(src)
    if before != pm.get("files"):
        raise PhysicsBaseError(f"{src} no coincide con su manifiesto")
    if not base_id.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise PhysicsBaseError(f"id de base no válido: {base_id!r}")

    cls = classify(src)
    data = src / "data"
    inis = {n: read_ini(data / n) for n in PHYSICS_INIS}
    problem = shift_window_problem(inis["drivetrain.ini"])
    if problem:
        raise PhysicsBaseError(f"drivetrain.ini: {problem}")
    load = load_requirements(inis)
    bad = [f"{n}: {d}" for n, ok, d in load if not ok]
    if bad:
        raise PhysicsBaseError(f"requisitos de carga incumplidos: {bad}")
    template, removed = strip_section(
        (data / TEMPLATE).read_bytes(), TEMPLATE_DROPPED_SECTION
    )

    out = new_output_dir(Path(out_root) / base_id)
    car = out / car_id
    physics_files: dict[str, Any] = {}
    for rel, info in cls["physics"].items():
        target = ensure_safe_output_path(car / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = template if rel == f"data/{TEMPLATE}" else (src / rel).read_bytes()
        target.write_bytes(body)
        physics_files[rel] = {
            **info,
            "sha256": sha256(target),
            "source_sha256": before[rel],
            "transform": (
                f"copia sin la sección [{TEMPLATE_DROPPED_SECTION}] ({len(removed)} "
                "líneas); el resto, byte a byte"
                if rel == f"data/{TEMPLATE}"
                else "copia byte a byte"
            ),
        }
        if rel != f"data/{TEMPLATE}" and physics_files[rel]["sha256"] != before[rel]:
            raise PhysicsBaseError(f"copia distinta del origen: {rel}")
    if tree_hashes(src) != before:
        raise PhysicsBaseError("el paquete de origen cambió durante la copia")
    manifest = {
        "kind": KIND,
        "version": VERSION,
        "base_id": base_id,
        "car_id": car_id,
        "status": STATUS,
        "package_dir": str(car),
        "source": {
            "package_dir": str(src),
            "manifest": str(pkg_manifest),
            "manifest_sha256": sha256(pkg_manifest),
            "test_id": pm.get("test_id"),
            "variant": pm.get("variant"),
        },
        "physics_files": physics_files,
        "car_ini_removed_lines": removed,
        "excluded": cls["excluded"],
        "checks": {
            "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW": inis["drivetrain.ini"][
                "GEARBOX"
            ]["VALID_SHIFT_RPM_WINDOW"],
            **{n: d for n, _, d in load},
        },
        "files": tree_hashes(car),
    }
    ensure_safe_output_path(out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


# ------------------------------------------------------------------ cargar
def resolve_base_dir(path: Path) -> Path:
    """Carpeta del coche de una base: admite la carpeta de la base o la del coche."""
    p = Path(path)
    m = p / "manifest.json"
    if m.is_file():
        data = json.loads(m.read_text(encoding="utf-8"))
        if data.get("kind") == KIND:
            return Path(data["package_dir"])
    return p


def declared_manifest(base: Path) -> dict[str, Any] | None:
    """Manifiesto de base declarada de la carpeta del coche ``base``, o ``None``."""
    m = Path(base).parent / "manifest.json"
    if not m.is_file():
        return None
    data = json.loads(m.read_text(encoding="utf-8"))
    return data if data.get("kind") == KIND else None


def load_declared(base: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Verifica una base declarada y devuelve lo que copia el ensamblador."""
    base = Path(base)
    if Path(manifest["package_dir"]).resolve() != base.resolve():
        raise PhysicsBaseError(f"el manifiesto de la base no describe {base}")
    files = tree_hashes(base)
    if files != manifest["files"]:
        raise PhysicsBaseError(f"{base} no coincide con el manifiesto de la base")
    pf = manifest["physics_files"]
    wrong = sorted(r for r, i in pf.items() if files.get(r) != i["sha256"])
    if wrong or set(pf) != set(files):
        raise PhysicsBaseError(f"physics_files no coincide con la carpeta: {wrong}")
    return {
        "dir": str(base),
        "declared": True,
        "base_id": manifest["base_id"],
        "status": manifest["status"],
        "source": manifest["source"],
        "files": {r: i["sha256"] for r, i in pf.items() if r != f"data/{TEMPLATE}"},
        "car_ini_template": pf[f"data/{TEMPLATE}"]["sha256"],
        "manifest": str(base.parent / "manifest.json"),
        "matches": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="congela la física de un paquete aprobado")
    f.add_argument("package_car_dir")
    f.add_argument("--base-id", required=True)
    f.add_argument("--car-id", default="seat_ibiza_acfr")
    f.add_argument("--out-root", default=None)
    args = ap.parse_args(argv)
    out_root = Path(args.out_root or f"output/{args.car_id}/physics_bases")
    try:
        m = freeze(Path(args.package_car_dir), out_root, args.base_id, args.car_id)
    except PhysicsBaseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Base: {m['package_dir']}")
    for rel, info in m["physics_files"].items():
        print(
            f"  {info['sha256'][:16]}  {rel}  ({info['category']}; {info['transform']})"
        )
    for rel, why in m["excluded"].items():
        print(f"  excluido  {rel}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
