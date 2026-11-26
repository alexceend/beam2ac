"""v15li: luces funcionales sobre test14ha (no modifica nada existente).

    python tools/variants/build_v15li.py --out-root DIR [--base-root DIR] [--repro]

Cambios (src/ac/lights.py, desde el glowMap de BeamNG):
  * seat_ibiza_acfr.kn5: 19 mallas de luz pasan de "35b84_body0" a B2AC_<FUNCIÓN>_<n>;
    sus 19 materiales reciben ksEmissive = 0. Geometría, texturas y resto intactos.
  * data/lights.ini (nuevo): faros y posición (LIGHT_n), freno (BRAKE_n, OFF_COLOR = posición).
  * extension/ext_config.ini: se AÑADEN las secciones CSP de marcha atrás e intermitentes.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.steam_paths import find_game  # noqa: E402
from src.ac.car_validate import read_ini, validate_references, validate_structure  # noqa: E402
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes  # noqa: E402
from src.ac.kn5_materials import split_kn5  # noqa: E402
from src.ac.kn5_tree import load, walk  # noqa: E402
from src.ac.lights import light_functions, render_csp_emissive, render_lights_ini, rename_light_meshes  # noqa: E402
from src.jbeam import PartIndex, lax_json, load_pc, resolve  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
BASE_MAN = IC / "pkg_v14ha_alternating_grip/manifest.json"
OUT = OUT_IC / "pkg_v15li_lights"
NEW_ID = "beam2ac_test15li_seat_ibiza_acfr"
AC = (find_game("assettocorsa") or Path("assettocorsa"))
V = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
KN5, LIGHTS, EXT = "seat_ibiza_acfr.kn5", "data/lights.ini", "extension/ext_config.ini"
INTERIOR_Z = (-1.2, 1.0)  # habitáculo en z (marco KN5): faros > 1,2; pilotos < −1,6


def stop(msg):
    sys.exit("STOP: " + msg)


def crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("test14ha no coincide con su manifiesto")
if OUT.exists():
    stop(f"ya existe {OUT}")
if NEW_ID in existing_car_ids(AC) and not ARGS.repro:
    stop("el id ya existe en la instalación")
if (base / LIGHTS).exists():
    stop("la base ya tiene lights.ini")
ext_old = (base / EXT).read_bytes()
if b"EMISSIVE_" in ext_old or not ext_old.endswith(b"\r\n"):
    stop("ext_config.ini de la base distinto del esperado")

resolved = resolve(PartIndex.from_directories(V), load_pc(V / "35b84.pc"))
functions = light_functions(resolved)
bm = lax_json.load(V / "35b84.materials.json").value
kn5_old = (base / KN5).read_bytes()
kn5_new, lights = rename_light_meshes(kn5_old, functions, bm, INTERIOR_Z)
lights_ini = crlf(render_lights_ini(lights))
ext_new = ext_old + crlf(render_csp_emissive(lights))

new_output_dir(OUT)
dst = OUT / NEW_ID
try:
    shutil.copytree(base, dst)
    if tree_hashes(dst) != before:
        raise RuntimeError("la copia no es idéntica a test14ha")
    (dst / KN5).write_bytes(kn5_new)
    (dst / LIGHTS).write_bytes(lights_ini)
    (dst / EXT).write_bytes(ext_new)
    after = tree_hashes(dst)
    diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
    if diff != sorted([KN5, LIGHTS, EXT]):
        raise RuntimeError(f"diferencias inesperadas: {diff}")
    # KN5: misma estructura y mismas cargas; solo cambian los nombres de las 19 mallas
    pa, ra = load(kn5_old)
    pb, rb = load(kn5_new)
    na, nb = list(walk(ra)), list(walk(rb))
    renamed = [(x.name, y.name) for (x, _), (y, _) in zip(na, nb) if x.name != y.name]
    same_tree = len(na) == len(nb) and all(
        x.type == y.type and x.payload == y.payload and x.active == y.active
        and len(x.children) == len(y.children) for (x, _), (y, _) in zip(na, nb)
    )
    ma, mb = split_kn5(kn5_old), split_kn5(kn5_new)
    mats_ok = [t.data for t in ma.textures] == [t.data for t in mb.textures] and all(
        x.name == y.name and x.shader == y.shader and x.slots == y.slots
        and [p for p in y.props if p[0] != "ksEmissive"] == x.props
        and (y.props[len(x.props):] == [("ksEmissive", (0.0,) * 10)]) == (x.name in functions)
        for x, y in zip(ma.materials, mb.materials)
    )
    checks = {
        "19 materiales de luz en el glowMap": len(functions) == 19,
        "KN5: mismo árbol, mismas cargas (geometría intacta)": same_tree,
        "KN5: solo 19 mallas renombradas, todas desde 35b84_body0": len(renamed) == 19
        and all(a == "35b84_body0" and b.startswith("B2AC_") for a, b in renamed),
        "KN5: texturas idénticas; materiales iguales salvo ksEmissive en los 19 de luz": mats_ok,
        "ext_config.ini: el contenido anterior se conserva byte a byte": ext_new.startswith(ext_old),
    }
    old_ext, new_ext = read_ini(base / EXT), read_ini(dst / EXT)
    checks["ext_config.ini: secciones anteriores sin cambios"] = all(new_ext[s] == old_ext[s] for s in old_ext)
    vr = validate_references(dst, AC / "content/driver")
    light_checks = [c for c in vr.checks if "NAME existe" in c["check"]]
    checks["validate_references: nombres de luces correctos"] = bool(light_checks) and all(
        c["ok"] for c in light_checks
    )
    checks["validate_references sin errores"] = vr.ok
    if not all(checks.values()):
        raise RuntimeError(f"comprobación fallida: {checks} {vr.errors}")
    if tree_hashes(base) != before:
        raise RuntimeError("test14ha cambió")
except Exception as e:
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")
req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini",
       "tyres.ini", "lods.ini", "setup.ini", "driver3d.ini", "aero.ini", "lights.ini")
vs = validate_structure(dst, req)
manifest = {
    "test_id": NEW_ID, "source_dir": str(base), "source_manifest": str(BASE_MAN), "package_dir": str(dst),
    "installed": False, "distributable": False,
    "purpose": "DIAGNOSTICO: luces (faros, posición, freno en AC; marcha atrás e intermitentes con CSP)",
    "diff_vs_test14ha": diff, "files": after,
    "lights": [vars(li) for li in lights], "renamed": renamed,
    "checks": checks,
    "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors],
                   "light_name_checks": len(light_checks)},
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diff_vs_test14ha", "checks", "validation")},
                 indent=1, ensure_ascii=False))
for f in (KN5, LIGHTS, EXT):
    print(sha256(dst / f)[:16], f)
print(sha256(OUT / "manifest.json")[:16], "manifest.json")
