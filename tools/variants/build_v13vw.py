"""v13vw: copia aislada de v13 con solo [GEARBOX] VALID_SHIFT_RPM_WINDOW=800 añadido.

    python tools/variants/build_v13vw.py --out-root DIR [--base-root DIR] [--repro]
"""
import json, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.ac.car_validate import duplicate_keys, read_ini, validate_references, validate_structure
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes
from src.safe_paths import ensure_safe_output_path
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
SRC_MAN = IC / "pkg_v13_integrated/manifest.json"
NEW_ID = "beam2ac_test13vw_seat_ibiza_acfr"
OUT = ensure_safe_output_path(OUT_IC / "pkg_v13vw_shiftwindow")
DST = ensure_safe_output_path(OUT / NEW_ID)
AC = Path(r"G:\SteamLibrary\steamapps\common\assettocorsa")
REL = "data/drivetrain.ini"
ADD = b"\r\n[GEARBOX]\r\nVALID_SHIFT_RPM_WINDOW=800\r\n"

def stop(msg):
    sys.exit("STOP: " + msg)

# 1. origen
m = json.loads(SRC_MAN.read_text(encoding="utf-8"))
src = Path(m["package_dir"])
before = tree_hashes(src)
if before != m["files"]:
    stop("v13 no coincide con su manifiesto")
if OUT.exists():
    stop(f"ya existe {OUT}")
if NEW_ID in existing_car_ids(AC) and not ARGS.repro:
    stop("el id ya existe en la instalación")
old = (src / REL).read_bytes()
txt = old.decode("utf-8")
secs = [l.strip() for l in txt.split("\r\n") if l.strip().startswith("[")]
if "[GEARBOX]" in secs or "VALID_SHIFT_RPM_WINDOW" in txt or not old.endswith(b"\r\n") or b"\n" in old.replace(b"\r\n", b""):
    stop(f"precondición: secciones {secs}")
# 2. copia
new_output_dir(OUT)
try:
    shutil.copytree(src, DST)
    if tree_hashes(DST) != before:
        raise RuntimeError("la copia no es idéntica a v13")
    # 3. edición
    (DST / REL).write_bytes(old + ADD[2:] if False else old.rstrip(b"\r\n") + ADD)
    new = (DST / REL).read_bytes()
    # 4. comparación
    after = tree_hashes(DST)
    diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
    if diff != [REL]:
        raise RuntimeError(f"difieren más archivos: {diff}")
    if not new.startswith(old.rstrip(b"\r\n")) or new[len(old.rstrip(b"\r\n")):] != ADD:
        raise RuntimeError("la diferencia no es exactamente el bloque añadido")
    a, b = read_ini(src / REL), read_ini(DST / REL)
    extra = {s: {k: v for k, v in kv.items() if a.get(s, {}).get(k) != v} for s, kv in b.items()}
    extra = {s: kv for s, kv in extra.items() if kv}
    if extra != {"GEARBOX": {"VALID_SHIFT_RPM_WINDOW": "800"}} or any(a[s] != b[s] for s in a):
        raise RuntimeError(f"cambio de claves inesperado: {extra}")
    nsec = [l.strip() for l in new.decode().split("\r\n") if l.strip().startswith("[")]
    if nsec.count("[GEARBOX]") != 1 or duplicate_keys(DST / REL):
        raise RuntimeError("sección o clave duplicada")
    if tree_hashes(src) != before:
        raise RuntimeError("v13 cambió")
except Exception as e:
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")
# 5. validaciones
req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini", "tyres.ini", "lods.ini", "setup.ini", "driver3d.ini", "aero.ini")
vs = validate_structure(DST, req)
vr = validate_references(DST, AC / "content" / "driver")
manifest = {
    "test_id": NEW_ID, "source_dir": str(src), "source_manifest": str(SRC_MAN),
    "source_manifest_sha256": sha256(SRC_MAN), "package_dir": str(DST), "installed": False,
    "purpose": "DIAGNOSTICO: parpadeo rojo del indicador de transmision. Unico cambio: [GEARBOX] VALID_SHIFT_RPM_WINDOW=800 (valor de ks_toyota_celica_st185 y ks_toyota_ae86). No es una correccion confirmada.",
    "diff_vs_source": diff, "added_bytes": ADD.decode(),
    "drivetrain_sha256": {"before": before[REL], "after": after[REL]},
    "files": after,
    "validation": {"structure_checks": len(vs.checks), "structure_errors": vs.errors,
                   "reference_checks": len(vr.checks), "reference_errors": vr.errors},
    "procedure": [
        "1. Cerrar AC y Content Manager; copiar Documents/Assetto Corsa/logs a un respaldo.",
        f"2. Copiar {DST} a <AC>/content/cars/{NEW_ID} (carpeta nueva).",
        "3. Misma pista, ayudas y HUD que en la prueba de v13; coche parado en neutral y luego en 1.a.",
        "4. Anotar si el indicador de transmision parpadea, su color y que app/elemento lo dibuja.",
        f"5. Marcha atras: borrar solo <AC>/content/cars/{NEW_ID}.",
    ],
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diff_vs_source", "drivetrain_sha256", "validation")}, indent=1, ensure_ascii=False))
print("manifest", sha256(OUT / "manifest.json"))
