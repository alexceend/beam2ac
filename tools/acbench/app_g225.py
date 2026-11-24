"""G2.25: preparación del banco de validación funcional (solo ``output/``; no toca AC).

    python -m tools.acbench.app_g225 stage --out output/<nueva>/preparacion [--ac-root …]

Prepara, con el mismo manifiesto que :func:`tools.acbench.install.stage` (así
``install.install``/``uninstall`` sirven sin cambios):

* ``content/cars/beam2ac_g225_<coche>``: copia BYTE A BYTE de la carpeta del coche de
  ``output/g224_2026-11-09/proyectos/<coche>/exports/003/`` (comprobada por hashes contra el
  ``manifest.json`` sellado del export). Solo cambia el nombre de la carpeta: en AC ya están
  instalados con su id original los exports de G2.23 del usuario, que no se tocan. AC carga el
  KN5 que nombra ``lods.ini`` (igual que en G2.21/G2.22 con ``beam2ac_g221lsd_*``);
* ``content/tracks/beam2ac_g225_pad`` (:mod:`tools.acbench.track_g225`);
* ``apps/lua/beam2ac_g225_bench``: la app de G2.22 (:func:`install.render_lua`, sin cambios de
  comportamiento) + el bloque de G2.25 (``g225_extension.lua.in``, estructura
  :mod:`tools.acbench.bridge_geo`).

Se niega si una carpeta de destino ya existe en AC (``--ac-root``) o si la copia no coincide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.safe_paths import ensure_safe_output_path  # noqa: E402
from tools.acbench import bridge_geo, install, track_g225  # noqa: E402

APP_ID = "beam2ac_g225_bench"
LUA_DIR = Path(__file__).resolve().parent / "lua" / APP_ID
G224 = ROOT / "output" / "g224_2026-11-09" / "proyectos"
EXPORT = "003"
# id corto → carpeta del proyecto G2.24 (orden de la prueba: Atlanta primero)
CARS: dict[str, str] = {
    "atlanta": "2020_rpm_atlanta_gt_3_6t",
    "legion": "2020_rpm_legion_grand_touring",
    "prothean": "2020_rpm_prothean_s",
    "omega": "rpm_omega_one_concept",
}


PREFIX = "beam2ac_g225_"


def car_id(short: str, prefix: str = PREFIX) -> str:
    return f"{prefix}{short}"


def export_dir(short: str, root: Path = G224, export: str = EXPORT) -> Path:
    return root / CARS[short] / "exports" / export


def package_dir(short: str, root: Path = G224, export: str = EXPORT) -> Path:
    return export_dir(short, root, export) / f"beam2ac_{CARS[short]}"


def render_lua() -> str:
    base = install.render_lua()
    for old, new in (("beam2ac banco LSD (G2.22)", "beam2ac banco G2.25 (base G2.22)"),):
        if old not in base:
            raise install.InstallError(f"plantilla de G2.22 inesperada: falta {old!r}")
        base = base.replace(old, new)
    ext = (LUA_DIR / "g225_extension.lua.in").read_text("utf-8")
    ext = ext.replace("@GEO_CDEF@", bridge_geo.lua_cdef(bridge_geo.GEO_FIELDS))
    out = base.rstrip("\n") + "\n" + ext
    for marker in ("@CMD_CDEF@", "@TEL_CDEF@", "@CAI_CDEF@", "@GEO_CDEF@"):
        if marker in out:
            raise install.InstallError(f"marcador sin sustituir: {marker}")
    return out


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_export(short: str, root: Path = G224, export: str = EXPORT) -> dict:
    """Los archivos del paquete = los del ``manifest.json`` sellado del export (G2.24)."""
    man = json.loads((export_dir(short, root, export) / "manifest.json").read_text("utf-8"))
    files = man.get("files") or man.get("package_files") or {}
    pkg = package_dir(short, root, export)
    actual = install.tree_hashes(pkg)
    listed = {}
    if isinstance(files, dict):
        listed = {k: (v["sha256"] if isinstance(v, dict) else v) for k, v in files.items()}
    elif isinstance(files, list):
        listed = {f["path"]: f["sha256"] for f in files}
    prefix = f"beam2ac_{CARS[short]}/"
    listed = {k[len(prefix):] if k.startswith(prefix) else k: v for k, v in listed.items()}
    return {"files": len(actual), "listed": len(listed),
            "match": bool(listed) and all(actual.get(k) == v for k, v in listed.items())
            and set(actual) <= set(listed),
            "tree_sha256": hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest(),
            "manifest_sha256": _sha(export_dir(short, root, export) / "manifest.json")}


def check_free(ac_root: Path, targets: list[str]) -> list[str]:
    return [t for t in targets if (ac_root / t).exists()]


def stage(dest: Path, ac_root: Path | None = install.AC_ROOT, cars: list[str] | None = None,
          root: Path = G224, export: str = EXPORT, prefix: str = PREFIX, always_on: bool = False) -> dict:
    """``root``/``export``/``prefix``: otra serie de paquetes (G2.25: proyectos regenerados,
    ``exports/004``, ids ``beam2ac_g225b_*``)."""
    dest = ensure_safe_output_path(dest)
    if dest.exists():
        raise install.InstallError(f"{dest} ya existe (las preparaciones no se sobrescriben)")
    cars = cars or list(CARS)
    root = Path(root).resolve()
    exports = {s: verify_export(s, root, export) for s in cars}
    bad = [s for s, v in exports.items() if not v["match"]]
    if bad:
        raise install.InstallError(f"export distinto de su manifest.json sellado: {bad}")
    rels = [f"content/tracks/{track_g225.TRACK_ID}", f"apps/lua/{APP_ID}"]
    rels += [f"content/cars/{car_id(s, prefix)}" for s in cars]
    if ac_root is not None:
        taken = check_free(ac_root, rels)
        if taken:
            raise install.InstallError(f"ya existen en AC (no se tocan): {taken}")
    (dest / "content" / "tracks").mkdir(parents=True)
    track_g225.build(dest / "content" / "tracks")
    app = dest / "apps" / "lua" / APP_ID
    app.mkdir(parents=True)
    (app / f"{APP_ID}.lua").write_text(render_lua(), "utf-8")
    install.copy_manifest(LUA_DIR / "manifest.ini", app / "manifest.ini", always_on)
    for s in cars:
        dst = dest / "content" / "cars" / car_id(s, prefix)
        shutil.copytree(package_dir(s, root, export), dst)
        if install.tree_hashes(dst) != install.tree_hashes(package_dir(s, root, export)):
            raise install.InstallError(f"copia de {s} distinta del export")
    targets = {r: install.tree_hashes(dest / r) for r in rels}
    manifest = {"schema": "beam2ac.g222.bench_stage/1", "phase": "G2.25",
                "created": datetime.now().isoformat(timespec="seconds"), "targets": targets,
                "cars": {car_id(s, prefix): {
                    "source": str(package_dir(s, root, export).relative_to(ROOT)).replace("\\", "/"),
                    **exports[s]} for s in cars}}
    (dest / "manifest_preparacion.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--ac-root", type=Path, default=install.AC_ROOT)
    s.add_argument("--cars", nargs="*", default=None)
    s.add_argument("--source-root", type=Path, default=G224)
    s.add_argument("--export", default=EXPORT)
    s.add_argument("--prefix", default=PREFIX)
    s.add_argument("--no-ac-check", action="store_true",
                   help="no comprobar AC (nueva preparación con el banco ya instalado: install() solo "
                        "sustituye copias nuestras intactas)")
    s.add_argument("--always-on", action="store_true", help="LAZY = NONE: obligatorio en los bancos automáticos")
    a = ap.parse_args(argv)
    m = stage(a.out, None if a.no_ac_check else a.ac_root, a.cars, a.source_root, a.export, a.prefix, a.always_on)
    print(json.dumps({"targets": list(m["targets"]), "cars": m["cars"]}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
