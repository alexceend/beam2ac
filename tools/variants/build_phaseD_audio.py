"""Fase D: paquete de prueba con el audio validado (banco FMOD precompilado, sin compilar).

    python tools/variants/build_phaseD_audio.py --out-root output/seat_ibiza_acfr/<carpeta nueva>
        [--source output/seat_ibiza_acfr/phaseD_2026-10-25/run2/seat_ibiza_acfr]
        [--audio-pkg output/seat_ibiza_acfr/ingame_cycle/pkg_v16sn_sound]
        [--ac-root G:/SteamLibrary/steamapps/common/assettocorsa]

1. ``src.ac.ingame_package.prepare_test_package`` copia el ensamblado validado de la Fase D
   con el id ``beam2ac_test16sn_seat_ibiza_acfr`` (el del banco compilado), en una carpeta
   nueva; con ``--ac-root`` comprueba que el id no está instalado.
2. Añade SIN CAMBIAR UN BYTE ``data/sounds.ini``, ``sfx/<id>.bank`` y ``sfx/GUIDs.txt`` de
   ``pkg_v16sn_sound``. Sus sha256 deben coincidir con los manifiestos de ``pkg_v16sn_sound``
   y ``pkg_v18bu`` antes y después de copiarlos. No se renombra el banco ni se edita ningún
   GUID, ruta de evento ni id.
3. Valida estructura, referencias (banco = carpeta, ``bank:/<carpeta>`` en GUIDs.txt) y el
   manifiesto, y que las fuentes no cambian. Si algo falla, borra SOLO la carpeta creada.

No ejecuta Blender, FMOD ni el juego, y no instala nada. Variante de diagnóstico, no un
cambio del generador (el ``--audio prebuilt`` del contrato sigue sin implementar).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ac.car_validate import validate_references, validate_structure  # noqa: E402
from src.ac.ingame_package import prepare_test_package, tree_hashes  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

CAR_ID = "beam2ac_test16sn_seat_ibiza_acfr"
AUDIO_FILES = ("data/sounds.ini", f"sfx/{CAR_ID}.bank", "sfx/GUIDs.txt")
REQUIRED_DATA = (
    "car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini",
    "brakes.ini", "tyres.ini", "lods.ini", "setup.ini", "driver3d.ini", "aero.ini",
    "lights.ini", "dash_cam.ini", "sounds.ini",
)


class VariantError(RuntimeError):
    """Una comprobación de la variante falló."""


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_files(pkg_dir: Path) -> tuple[Path, dict[str, str]]:
    m = json.loads((Path(pkg_dir) / "manifest.json").read_text(encoding="utf-8"))
    car = Path(m["package_dir"])
    files = m["files"]
    if tree_hashes(car) != files:
        raise VariantError(f"{car} no coincide con su manifiesto")
    return car, files


def expected_audio(audio_pkgs: list[Path]) -> tuple[Path, dict[str, str]]:
    """Hashes del audio validado; deben coincidir en todos los paquetes de referencia."""
    ref_car, expected = None, None
    for pkg in audio_pkgs:
        car, files = manifest_files(pkg)
        if car.name != CAR_ID:
            raise VariantError(f"{pkg}: carpeta {car.name}, se esperaba {CAR_ID}")
        got = {k: files.get(k) for k in AUDIO_FILES}
        if None in got.values():
            raise VariantError(f"{pkg}: le falta audio {got}")
        if expected is None:
            ref_car, expected = car, got
        elif got != expected:
            raise VariantError(f"el audio de {pkg} difiere del de {audio_pkgs[0]}")
    return ref_car, expected


def build(
    source_car: Path,
    audio_pkgs: list[Path],
    out_root: Path,
    ac_root: Path | None = None,
    driver_dir: Path | None = None,
    reference_pkg: Path | None = None,
) -> dict:
    source_car = Path(source_car).resolve()
    src_before = tree_hashes(source_car)
    audio_car, expected = expected_audio([Path(p) for p in audio_pkgs])
    for rel, h in expected.items():  # antes de copiar
        if sha256(audio_car / rel) != h:
            raise VariantError(f"{audio_car / rel}: sha256 distinto del manifiesto")
    if any(rel in src_before for rel in AUDIO_FILES) or any(
        k.startswith("sfx/") for k in src_before
    ):
        raise VariantError("el ensamblado de origen ya tiene audio")
    out = new_output_dir(out_root)  # falla si existe; queda como propia de este proceso
    try:
        m = prepare_test_package(source_car, out, CAR_ID, ac_root)
        pkg = Path(m["package_dir"])
        if pkg.name != CAR_ID or sorted(p.name for p in out.iterdir()) != sorted(
            [CAR_ID, "manifest.json"]
        ):
            raise VariantError(f"salida inesperada en {out}: {list(out.iterdir())}")
        before_audio = tree_hashes(pkg)
        for rel in AUDIO_FILES:
            target = ensure_safe_output_path(pkg / rel)
            if target.exists():
                raise VariantError(f"ya existe {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(audio_car / rel, target)
        files = tree_hashes(pkg)
        checks = {
            "audio copiado = manifiestos de referencia (sha256, después)": all(
                files[rel] == expected[rel] for rel in AUDIO_FILES
            ),
            "audio de origen sin cambios": all(
                sha256(audio_car / rel) == expected[rel] for rel in AUDIO_FILES
            ),
            "solo se añadieron los 3 archivos de audio": sorted(
                k for k in set(files) | set(before_audio)
                if files.get(k) != before_audio.get(k)
            ) == sorted(AUDIO_FILES),
            "ensamblado de origen sin cambios": tree_hashes(source_car) == src_before,
            "banco con el nombre de la carpeta": (pkg / f"sfx/{pkg.name}.bank").is_file(),
            "GUIDs.txt con bank:/<carpeta>": f"bank:/{pkg.name}"
            in (pkg / "sfx/GUIDs.txt").read_text(encoding="utf-8"),
            "eventos del coche con el id de la carpeta": all(
                line.split()[1].split("/")[2] == pkg.name
                for line in (pkg / "sfx/GUIDs.txt").read_text(encoding="utf-8").splitlines()
                if " event:/cars/" in line
            ),
        }
        vs = validate_structure(pkg, REQUIRED_DATA)
        vr = validate_references(pkg, driver_dir)
        checks["validate_structure sin errores"] = vs.ok
        checks["validate_references sin errores"] = vr.ok
        reference = None
        if reference_pkg is not None:
            _, ref_files = manifest_files(reference_pkg)
            keys = sorted(set(ref_files) | set(files))
            reference = {
                "package": str(reference_pkg),
                "identical": sum(ref_files.get(k) == files.get(k) for k in keys),
                "total": len(keys),
                "different": [k for k in keys if k in ref_files and k in files
                              and ref_files[k] != files[k]],
                "only_in_reference": [k for k in keys if k not in files],
                "only_in_new": [k for k in keys if k not in ref_files],
            }
        if not all(checks.values()):
            raise VariantError(f"comprobación fallida: {checks} {vs.errors} {vr.errors}")
        m["files"] = files
        m["changes_vs_source"].append(
            "data/sounds.ini, sfx/<id>.bank y sfx/GUIDs.txt: copia byte a byte del audio "
            "validado de pkg_v16sn_sound (igual en pkg_v18bu); sin compilar FMOD"
        )
        m["known_gaps"] = []
        m["audio"] = {
            "source_package": str(audio_car),
            "reference_packages": [str(p) for p in audio_pkgs],
            "sha256": expected,
            "status": "construido y validado; audio pendiente de prueba manual en el juego",
        }
        m["checks"] = checks
        m["validation"] = {"structure": [len(vs.checks), vs.errors],
                           "references": [len(vr.checks), vr.errors]}
        m["reference_comparison"] = reference
        ensure_safe_output_path(out / "manifest.json").write_text(
            json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if tree_hashes(pkg) != m["files"]:
            raise VariantError("el paquete no coincide con el manifiesto final")
        return m
    except Exception:
        shutil.rmtree(out)  # solo la carpeta creada aquí
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    out_dir = ROOT / "output" / "seat_ibiza_acfr"
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--source", default=str(out_dir / "phaseD_2026-10-25/run2/seat_ibiza_acfr"))
    ap.add_argument("--audio-pkg", action="append",
                    default=None, help="paquetes con el audio validado (por defecto v16sn y v18bu)")
    ap.add_argument("--reference", default=str(out_dir / "ingame_cycle/pkg_v18bu"))
    ap.add_argument("--ac-root", default=None)
    args = ap.parse_args(argv)
    audio = args.audio_pkg or [str(out_dir / "ingame_cycle/pkg_v16sn_sound"),
                               str(out_dir / "ingame_cycle/pkg_v18bu")]
    ac = Path(args.ac_root) if args.ac_root else None
    try:
        m = build(Path(args.source), [Path(a) for a in audio], Path(args.out_root), ac,
                  ac / "content/driver" if ac else None, Path(args.reference))
    except Exception as e:  # noqa: BLE001
        print(f"STOP: {e}", file=sys.stderr)
        return 1
    print(json.dumps({k: m[k] for k in ("package_dir", "id_check", "audio", "checks",
                                        "validation", "reference_comparison")},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
