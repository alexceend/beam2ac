"""pkg_v16sn_sound = test15li + sfx/<id>.bank + sfx/GUIDs.txt (recortado) + data/sounds.ini.

    python tools/variants/build_v16sn.py --out-root DIR [--base-root DIR] [--repro]

Entradas fijas (solo lectura): spec.json, template_objs.json y el banco ya construido de
output/seat_ibiza_acfr/sound_work_2026-10-25/ (el banco no se reconstruye aquí).

No modifica nada existente; si una comprobación falla, borra solo la carpeta nueva.
GUIDs.txt recortado según el PDF (p. 11): la línea bank:/<id>, los eventos de
event:/cars/<id>/ y las líneas bus:/ (el resto de la plantilla se elimina).
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "output/seat_ibiza_acfr/sound_work_2026-10-25"
sys.path.insert(0, str(ROOT))
from src.steam_paths import find_game  # noqa: E402
from src.ac.car_validate import read_ini, validate_references, validate_structure  # noqa: E402
from src.ac.ingame_package import existing_car_ids, sha256, tree_hashes  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

spec = json.loads((WORK / "spec.json").read_text(encoding="utf-8"))
CAR = spec["car"]
ARGS = parse(__doc__)
IC, OUT_IC = ARGS.base_root, ARGS.out_root  # beam2ac fase A: raíces por argumento
BASE_MAN = IC / "pkg_v15li_lights/manifest.json"
OUT = OUT_IC / "pkg_v16sn_sound"
AC = (find_game("assettocorsa") or Path("assettocorsa"))
PRJ = WORK / "fmod_project" / "ac_fmod_sdk_1_9"
BANK_SRC = PRJ / "Build" / "Desktop" / f"{CAR}.bank"
GUIDS_SRC = PRJ / "Build" / "GUIDs.txt"
SDK = ROOT / "docs/references/assetto-corsa/audio/ac_fmod_sdk_1_9"
NEW = [f"sfx/{CAR}.bank", "sfx/GUIDs.txt", "data/sounds.ini"]
SOUNDS_INI = ("; beam2ac: posicion del emisor del motor (AC Audio Pipeline 1.9, p. 5 y 8); Ibiza = motor delantero\r\n"
              "[ENGINE]\r\nPOSITION=front\r\n")


def stop(msg):
    sys.exit("STOP: " + msg)


m = json.loads(BASE_MAN.read_text(encoding="utf-8"))
base = Path(m["package_dir"])
before = tree_hashes(base)
if before != m["files"]:
    stop("test15li no coincide con su manifiesto")
if OUT.exists():
    stop(f"ya existe {OUT}")
if CAR in existing_car_ids(AC) and not ARGS.repro:
    stop("el id ya existe en la instalación")
if any(f in before for f in NEW) or any(k.startswith("sfx/") for k in before):
    stop("la base ya tiene sonido")
raw = GUIDS_SRC.read_bytes()
eol = b"\r\n" if b"\r\n" in raw else b"\n"
lines = raw.decode("utf-8").splitlines()
keep = [ln for ln in lines if ln.endswith(f" bank:/{CAR}") or f" event:/cars/{CAR}/" in ln or re.search(r" bus:/", ln)]
guids_txt = (eol.decode().join(keep) + eol.decode()).encode("utf-8")

new_output_dir(OUT)
dst = OUT / CAR
try:
    shutil.copytree(base, dst)
    if tree_hashes(dst) != before:
        raise RuntimeError("la copia no es idéntica a test15li")
    (dst / "sfx").mkdir()
    shutil.copyfile(BANK_SRC, dst / f"sfx/{CAR}.bank")
    (dst / "sfx/GUIDs.txt").write_bytes(guids_txt)
    (dst / "data/sounds.ini").write_bytes(SOUNDS_INI.encode("utf-8"))
    after = tree_hashes(dst)
    diff = sorted(f for f in set(after) | set(before) if after.get(f) != before.get(f))
    # GUIDs: coherencia con el proyecto construido y con el banco maestro de AC
    proj = json.loads((WORK / "fmod_project" / "project_objs.json").read_text(encoding="utf-8"))
    ev_ids = {o["props"]["name"][0]: i for i, o in proj.items() if o["class"] == "Event"
              and o["rels"].get("folder") and (proj[o["rels"]["folder"][0]]["props"].get("name") or [""])[0] == CAR}
    bank_id = [i for i, o in proj.items() if o["class"] == "Bank" and o["props"].get("name") == [CAR]]
    g_lines = {ln.split(None, 1)[1]: ln.split(None, 1)[0] for ln in keep}
    ac_bus = {ln.split(None, 1)[1]: ln.split(None, 1)[0] for ln in
              open(AC / "content/sfx/GUIDs.txt", encoding="utf-8", errors="replace").read().splitlines() if " bus:/" in ln}
    tmpl_ids = set(json.loads((WORK / "template_objs.json").read_text(encoding="utf-8")))
    car_events = {k.split("/")[-1]: v for k, v in g_lines.items() if k.startswith(f"event:/cars/{CAR}/")}
    kunos = sorted({os.path.splitext(os.path.basename(f))[0] for f in SDK.glob("Assets/**/*.wav")})
    bank_bytes = (dst / f"sfx/{CAR}.bank").read_bytes()
    checks = {
        "diferencia con test15li = solo los 3 archivos nuevos": diff == sorted(NEW),
        "banco = el construido (byte a byte)": sha256(dst / f"sfx/{CAR}.bank") == sha256(BANK_SRC),
        "GUIDs: 1 banco + 19 eventos + 23 bus, nada más": len(keep) == 43 and len(car_events) == 19
        and sum(" bus:/" in k for k in keep) == 23,
        "GUIDs: banco y eventos = GUID del proyecto construido": bank_id and g_lines.get(f"bank:/{CAR}") == bank_id[0]
        and all(ev_ids.get(n) == g for n, g in car_events.items()),
        "GUIDs: ningún GUID de evento reutilizado de la plantilla": not (set(car_events.values()) & tmpl_ids),
        "GUIDs: bus = los del banco maestro de AC (content/sfx/GUIDs.txt)": all(
            ac_bus.get(k) == v for k, v in g_lines.items() if k.startswith("bus:/")),
        "sin audio de Kunos: ningún .wav/.ogg en el paquete": not any(f.lower().endswith((".wav", ".ogg", ".mp3")) for f in after),
        "sin audio de Kunos: ningún nombre de muestra Kunos en el banco": not [k for k in kunos if k.encode() in bank_bytes],
        "sounds.ini: ENGINE POSITION=front": read_ini(dst / "data/sounds.ini") == {"ENGINE": {"POSITION": "front"}},
    }
    vr = validate_references(dst, AC / "content/driver")
    sfx_checks = [c for c in vr.checks if "sfx" in c["check"]]
    checks["validate_references: banco y GUIDs con el nombre de la carpeta"] = bool(sfx_checks) and all(c["ok"] for c in sfx_checks)
    checks["validate_references sin errores"] = vr.ok
    if not all(checks.values()):
        raise RuntimeError(f"comprobación fallida: {checks} {vr.errors}")
    if tree_hashes(base) != before:
        raise RuntimeError("test15li cambió")
except Exception as e:
    shutil.rmtree(OUT)
    stop(f"{e}; carpeta nueva eliminada")
req = ("car.ini", "engine.ini", "power.lut", "drivetrain.ini", "suspensions.ini", "brakes.ini", "tyres.ini",
       "lods.ini", "setup.ini", "driver3d.ini", "aero.ini", "lights.ini", "sounds.ini")
vs = validate_structure(dst, req)
manifest = {
    "test_id": CAR, "source_dir": str(base), "source_manifest": str(BASE_MAN), "package_dir": str(dst),
    "installed": False, "distributable": False,
    "purpose": "DIAGNOSTICO: sonido de motor propio (76 grabaciones de Automation) en FMOD 1.08.12; "
               "17 eventos secundarios silenciosos; sin audio de Kunos",
    "diff_vs_test15li": diff, "files": after,
    "fmod": {"project": str(PRJ), "bank_sha256": sha256(BANK_SRC), "guids_source": str(GUIDS_SRC)},
    "checks": checks,
    "validation": {"structure": [len(vs.checks), vs.errors], "references": [len(vr.checks), vr.errors]},
}
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("package_dir", "diff_vs_test15li", "checks", "validation")}, indent=1, ensure_ascii=False))
for f in NEW:
    print(sha256(dst / f)[:16], f)
print(sha256(OUT / "manifest.json")[:16], "manifest.json")
