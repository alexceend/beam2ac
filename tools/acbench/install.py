"""G2.22: preparación, instalación y retirada del banco en AC; lanzamiento y cierre de AC.

Autorizado por el usuario el 2026-11-07 (instalar carpetas NUEVAS, activar Custom AI en su
configuración de usuario con copia de seguridad, lanzar y cerrar AC). Reglas:

* solo se crean carpetas que NO existían: ``content/tracks/beam2ac_g222_pad``,
  ``apps/lua/beam2ac_lsd_bench`` y ``content/cars/beam2ac_g221lsd_*`` (copias de los paquetes
  de G2.21); nunca se modifica un archivo original de AC, de un coche o de un circuito;
* archivos de configuración del usuario que se tocan (``Documents/Assetto Corsa/cfg``):
  ``race.ini``, ``assists.ini`` y ``extension/new_behaviour.ini``. Antes se copian a
  ``<estado>/respaldo/`` con su sha256; ``restore_configs`` los devuelve byte a byte;
* la retirada borra una carpeta instalada SOLO si todos sus archivos coinciden con el registro
  (los CSV que la app crea en ``registros/`` se copian antes a ``output/``); si algo no
  coincide, no borra y lo informa.

El registro (``<estado>/instalacion.json``) es la única fuente para retirar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.project.export import default_ac_root  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402
from tools.acbench import bridge, cai, track  # noqa: E402

AC_ROOT = default_ac_root() or Path("assettocorsa")  # instalación detectada (Steam)
DOCS_AC = Path.home() / "Documents" / "Assetto Corsa"
CFG = DOCS_AC / "cfg"
G221_CARS = ROOT / "output" / "g221_2026-11-07" / "banco_ac" / "paquetes" / "content" / "cars"
LUA_SRC = Path(__file__).resolve().parent / "lua" / "beam2ac_lsd_bench"
APP_ID = "beam2ac_lsd_bench"
CAR_IDS = ["beam2ac_g221lsd_a0", "beam2ac_g221lsd_a1", "beam2ac_g221lsd_a2", "beam2ac_g221lsd_a3",
           "beam2ac_g221lsd_a4", "beam2ac_g221lsd_a5", "beam2ac_g221lsd_b0"]
CONFIG_FILES = {"race.ini": CFG / "race.ini", "assists.ini": CFG / "assists.ini",
                "new_behaviour.ini": CFG / "extension" / "new_behaviour.ini"}


# Archivos que el propio juego crea en una carpeta instalada (no son modificaciones nuestras ni
# del usuario): AC escribe data/proview_nodes.ini al cargar un coche por primera vez (visto en F0).
GAME_GENERATED = {"data/proview_nodes.ini"}


# Carpetas que crean NUESTRAS apps en su propia carpeta (CSV del registrador; capturas y volcados de
# geometría de la app de G2.25). Se copian a output/ antes de retirar.
APP_OUTPUT_DIRS = ("registros/", "capturas/")


def _ours_extra(k: str) -> bool:
    return k.startswith(APP_OUTPUT_DIRS) or k in GAME_GENERATED


class InstallError(RuntimeError):
    pass


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tree_hashes(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*")) if p.is_file()}


# --- preparación (solo output/) ---------------------------------------------------------


def render_lua() -> str:
    tpl = (LUA_SRC / "beam2ac_lsd_bench.lua.in").read_text("utf-8")
    cai_cdef = _cai_controls_cdef()
    return (tpl.replace("@CMD_CDEF@", bridge.lua_cdef(bridge.CMD_FIELDS))
            .replace("@TEL_CDEF@", bridge.lua_cdef(bridge.TEL_FIELDS))
            .replace("@CAI_CDEF@", cai_cdef))


def _cai_controls_cdef() -> str:
    """Cuerpo C de ``cai_car_controls`` para LuaJIT (bool → ``bool`` de 1 byte, igual que ctypes)."""
    import ctypes
    names = {ctypes.c_float: "float", ctypes.c_bool: "bool", ctypes.c_uint8: "uint8_t"}
    parts = []
    for n, t in cai.CarControls._fields_:
        if hasattr(t, "_length_"):
            parts.append(f"{names[t._type_]} {n}[{t._length_}];")
        else:
            parts.append(f"{names[t]} {n};")
    return " ".join(parts)


def copy_manifest(src: Path, dst: Path, always_on: bool) -> None:
    """Copia el manifest del app. Por defecto ``LAZY = FULL`` (el script solo corre con su ventana abierta, no
    llena los logs de AC); ``always_on`` deja ``LAZY = NONE`` para los bancos automáticos, que no abren la ventana."""
    text = src.read_text("utf-8")
    dst.write_text(text.replace("LAZY = FULL", "LAZY = NONE") if always_on else text, "utf-8")


def stage(dest: Path, always_on: bool = False) -> dict:
    """Prepara en ``dest`` (bajo output/) todo lo que se instalará y devuelve el manifiesto."""
    dest = ensure_safe_output_path(dest)
    if dest.exists():
        raise InstallError(f"{dest} ya existe (las preparaciones no se sobrescriben)")
    (dest / "content" / "tracks").mkdir(parents=True)
    track.build(dest / "content" / "tracks")
    app = dest / "apps" / "lua" / APP_ID
    app.mkdir(parents=True)
    (app / f"{APP_ID}.lua").write_text(render_lua(), "utf-8")
    copy_manifest(LUA_SRC / "manifest.ini", app / "manifest.ini", always_on)
    cars = dest / "content" / "cars"
    for cid in CAR_IDS:
        shutil.copytree(G221_CARS / cid, cars / cid)
        if tree_hashes(cars / cid) != tree_hashes(G221_CARS / cid):
            raise InstallError(f"copia de {cid} distinta del paquete de G2.21")
    targets = {f"content/tracks/{track.TRACK_ID}": tree_hashes(dest / "content" / "tracks" / track.TRACK_ID),
               f"apps/lua/{APP_ID}": tree_hashes(app)}
    for cid in CAR_IDS:
        targets[f"content/cars/{cid}"] = tree_hashes(cars / cid)
    manifest = {"schema": "beam2ac.g222.bench_stage/1", "created": datetime.now().isoformat(timespec="seconds"),
                "targets": targets}
    (dest / "manifest_preparacion.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    return manifest


# --- instalación ------------------------------------------------------------------------


def _record_path(state: Path) -> Path:
    return state / "instalacion.json"


def load_record(state: Path) -> dict:
    p = _record_path(state)
    return json.loads(p.read_text("utf-8")) if p.is_file() else {"installed": {}, "configs": {}}


def save_record(state: Path, rec: dict) -> None:
    ensure_safe_output_path(_record_path(state))
    _record_path(state).write_text(json.dumps(rec, indent=1), "utf-8")


# Fase A (revisión 2026-11-10): destinos admitidos en AC. Solo carpetas propias
# (prefijo beam2ac_) directamente bajo estas tres raíces; nada de rutas con «..», de
# subcarpetas de un coche ajeno ni de archivos sueltos de AC.
TARGET_PATTERN = re.compile(r"^(content/cars|content/tracks|apps/lua)/beam2ac_[a-z0-9_]+$")


def check_target(rel: str, stage_dir: Path) -> None:
    """Lanza ``InstallError`` si ``rel`` no es un destino admitido o no está preparado."""
    if not TARGET_PATTERN.match(rel):
        raise InstallError(
            f"destino no admitido {rel!r}: solo content/cars|content/tracks|apps/lua/"
            "beam2ac_<id>"
        )
    if not (stage_dir / rel).is_dir():
        raise InstallError(f"{rel} no está en la preparación {stage_dir}")


def install(stage_dir: Path, state: Path, ac_root: Path = AC_ROOT) -> dict:
    manifest = json.loads((stage_dir / "manifest_preparacion.json").read_text("utf-8"))
    for rel in manifest["targets"]:  # todos antes de copiar nada
        check_target(rel, stage_dir)
    rec = load_record(state)
    for rel, hashes in manifest["targets"].items():
        target = ac_root / rel
        if rel in rec["installed"]:
            old = rec["installed"][rel]
            current = tree_hashes(target) if target.exists() else {}
            extra = {k for k in current if k not in old and not _ours_extra(k)}
            if extra or any(current.get(k) != v for k, v in old.items()):
                raise InstallError(f"{target} instalado pero modificado: no se toca")
            if old == hashes:
                continue  # ya instalado por nosotros, intacto y al día
            # nuestra copia, intacta, pero la preparación cambió: se sustituye (CSV preservados)
            logs = sorted(k for k in current if k.startswith(APP_OUTPUT_DIRS))
            if logs:
                keep = ensure_safe_output_path(state / "registros_preservados" / Path(rel).name)
                _copy_app_outputs(target, logs, keep)
            shutil.rmtree(target)
            rec["installed"].pop(rel)
            save_record(state, rec)
        if target.exists():
            raise InstallError(f"{target} ya existe y no es nuestro: no se toca")
        shutil.copytree(stage_dir / rel, target)
        if tree_hashes(target) != hashes:
            raise InstallError(f"copia incompleta en {target}")
        rec["installed"][rel] = hashes
        save_record(state, rec)
    return rec


def uninstall(state: Path, logs_out: Path, ac_root: Path = AC_ROOT) -> dict:
    rec = load_record(state)
    report = {}
    for rel, hashes in list(rec["installed"].items()):
        target = ac_root / rel
        if not target.exists():
            report[rel] = "ya_no_existe"
            rec["installed"].pop(rel)
            continue
        current = tree_hashes(target)
        extra = {k for k in current if k not in hashes}
        logs = {k for k in extra if k.startswith(APP_OUTPUT_DIRS)}
        if {k for k in extra if not _ours_extra(k)} or any(current.get(k) != v for k, v in hashes.items()):
            report[rel] = "modificado: NO se borra"
            continue
        if logs:
            _copy_app_outputs(target, sorted(logs), ensure_safe_output_path(logs_out / Path(rel).name))
        shutil.rmtree(target)
        rec["installed"].pop(rel)
        report[rel] = "retirado" + (f" ({len(logs)} registros copiados)" if logs else "")
    save_record(state, rec)
    return report


def _copy_app_outputs(target: Path, keys: list[str], dst: Path) -> None:
    """Copia los archivos que generó la app (``registros/``: plano, como en G2.22; ``capturas/``:
    con su subruta) y comprueba cada copia por hash."""
    dst.mkdir(parents=True, exist_ok=True)
    for k in keys:
        out = dst / Path(k).name if k.startswith("registros/") else dst / k
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target / k, out)
        if sha256(out) != sha256(target / k):
            raise InstallError(f"copia distinta de {target / k}")


# --- configuración del usuario -------------------------------------------------------


def backup_configs(state: Path) -> dict:
    rec = load_record(state)
    if rec["configs"]:
        return rec  # ya respaldado: no se pisa la copia original
    bdir = ensure_safe_output_path(state / "respaldo")
    bdir.mkdir(parents=True, exist_ok=True)
    for name, p in CONFIG_FILES.items():
        if p.is_file():
            shutil.copy2(p, bdir / name)
            rec["configs"][name] = {"path": str(p), "sha256": sha256(p), "existed": True}
        else:
            rec["configs"][name] = {"path": str(p), "existed": False}
    save_record(state, rec)
    return rec


def _set_ini(text: str, section: str, values: dict[str, str]) -> str:
    """Pone ``values`` en ``[section]`` conservando el resto del archivo (claves exactas)."""
    lines = text.splitlines()
    out, cur, done = [], None, set()
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur == section:
                out += [f"{k}={v}" for k, v in values.items() if k not in done]
                done |= set(values)
            cur = s[1:-1]
            out.append(ln)
            continue
        if cur == section and "=" in s and s.split("=", 1)[0].strip() in values:
            k = s.split("=", 1)[0].strip()
            out.append(f"{k}={values[k]}")
            done.add(k)
            continue
        out.append(ln)
    if cur == section:
        out += [f"{k}={v}" for k, v in values.items() if k not in done]
        done |= set(values)
    if section not in {ln.strip()[1:-1] for ln in lines if ln.strip().startswith("[")}:
        out += ["", f"[{section}]"] + [f"{k}={v}" for k, v in values.items()]
    return "\n".join(out) + "\n"


def configure_session(state: Path, car_id: str, custom_ai: bool = True,
                      race_extra: dict[str, dict[str, str]] | None = None,
                      track_id: str = track.TRACK_ID) -> None:
    """race.ini (práctica en el circuito ``track_id`` con ``car_id``), assists.ini (sin TC ni
    estabilidad, cambio manual) y Custom AI. Exige el respaldo previo. ``track_id`` por defecto
    = el circuito de G2.22 (comportamiento de G2.22 sin cambios)."""
    rec = load_record(state)
    if not rec["configs"]:
        raise InstallError("sin respaldo de la configuración del usuario")
    race = CONFIG_FILES["race.ini"]
    t = race.read_text("utf-8", errors="replace")
    t = _set_ini(t, "RACE", {"MODEL": car_id, "MODEL_CONFIG": "", "TRACK": track_id, "CONFIG_TRACK": "",
                             "CARS": "1", "SKIN": "default1", "PENALTIES": "0", "FIXED_SETUP": "0"})
    t = _set_ini(t, "CAR_0", {"MODEL": "-", "SKIN": "default1", "SETUP": "", "BALLAST": "0", "RESTRICTOR": "0"})
    t = _set_ini(t, "REMOTE", {"ACTIVE": "0"})
    t = _set_ini(t, "REPLAY", {"ACTIVE": "0"})
    t = _set_ini(t, "BENCHMARK", {"ACTIVE": "0"})
    t = _set_ini(t, "RESTART", {"ACTIVE": "0"})
    t = _set_ini(t, "GHOST_CAR", {"ENABLED": "0", "PLAYING": "0", "RECORDING": "0", "LOAD": "0"})
    t = _set_ini(t, "WIND", {"SPEED_KMH_MIN": "0", "SPEED_KMH_MAX": "0"})
    t = _set_ini(t, "SESSION_0", {"NAME": "Practice", "TYPE": "1", "DURATION_MINUTES": "0", "SPAWN_SET": "PIT"})
    for sec, vals in (race_extra or {}).items():
        t = _set_ini(t, sec, vals)
    race.write_text(t, "utf-8")
    a = CONFIG_FILES["assists.ini"]
    at = a.read_text("utf-8", errors="replace") if a.is_file() else ""
    a.write_text(_set_ini(at, "ASSISTS", {"TRACTION_CONTROL": "0", "STABILITY_CONTROL": "0", "AUTO_SHIFTER": "0",
                                         "AUTO_CLUTCH": "0", "AUTO_BRAKE": "0", "DAMAGE": "0", "ABS": "1"}), "utf-8")
    nb = CONFIG_FILES["new_behaviour.ini"]
    nt = nb.read_text("utf-8", errors="replace") if nb.is_file() else ""
    nb.write_text(_set_ini(nt, "CUSTOM_AI", {"ENABLED": "1" if custom_ai else "0"}), "utf-8")


def restore_configs(state: Path) -> dict:
    rec = load_record(state)
    out = {}
    for name, info in rec["configs"].items():
        p = Path(info["path"])
        if info["existed"]:
            shutil.copy2(state / "respaldo" / name, p)
            out[name] = "restaurado" if sha256(p) == info["sha256"] else "ERROR: hash distinto"
        else:
            if p.exists():
                p.unlink()
            out[name] = "eliminado (no existía)"
    return out


# --- lanzamiento ------------------------------------------------------------------------


def ac_running() -> list[int]:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq acs.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    pids = []
    for ln in r.stdout.splitlines():
        parts = [p.strip('"') for p in ln.split(",")]
        if parts and parts[0].lower() == "acs.exe":
            pids.append(int(parts[1]))
    return pids


def steam_running() -> bool:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    return "steam.exe" in r.stdout.lower()


def steam_logged_in() -> bool:
    """G2.25 (2026-11-10): Steam con sesión iniciada (``HKCU\\Software\\Valve\\Steam\\ActiveProcess``
    ``ActiveUser`` ≠ 0). Con Steam recién abierto y sin sesión, ``acs.exe`` lanzado directamente fue
    reiniciado por Steam a través del lanzador oficial (``AssettoCorsa.exe``), que reescribió archivos de
    ``Documents/Assetto Corsa/cfg`` y ``launcherdata``."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as k:
            return int(winreg.QueryValueEx(k, "ActiveUser")[0]) != 0
    except OSError:
        return False


LAUNCHER_EXE = "AssettoCorsa.exe"  # lanzador oficial de Kunos (no es Content Manager)


def launcher_running() -> list[int]:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {LAUNCHER_EXE}", "/FO", "CSV", "/NH"],
                       capture_output=True, text=True)
    return [int(p[1]) for p in (ln.replace('"', "").split(",") for ln in r.stdout.splitlines())
            if p and p[0].lower() == LAUNCHER_EXE.lower()]


def close_launcher() -> list[int]:
    pids = launcher_running()
    for pid in pids:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    return pids


def launch_ac(ac_root: Path = AC_ROOT) -> subprocess.Popen:
    if ac_running():
        raise InstallError("AC ya se está ejecutando")
    if not steam_running():
        raise InstallError("Steam no está abierto")
    if not steam_logged_in():
        raise InstallError("Steam sin sesión iniciada (ActiveUser = 0): Steam relanzaría AC con su lanzador")
    if launcher_running():
        raise InstallError("el lanzador oficial de AC está abierto")
    return subprocess.Popen([str(ac_root / "acs.exe")], cwd=str(ac_root),
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def close_ac(proc: subprocess.Popen | None = None, timeout: float = 20.0) -> None:
    pids = ac_running()
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
    t0 = time.time()
    while ac_running() and time.time() - t0 < timeout:
        time.sleep(0.5)
    for pid in ac_running():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    if proc is not None:
        try:
            proc.wait(5)
        except Exception:
            pass


def copy_ac_logs(dest: Path) -> None:
    dest = ensure_safe_output_path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("log.txt", "custom_shaders_patch.log"):
        p = DOCS_AC / "logs" / name
        if p.is_file():
            shutil.copy2(p, dest / name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--always-on", action="store_true", help="LAZY = NONE: obligatorio en los bancos automáticos")
    i = sub.add_parser("install")
    i.add_argument("--stage", type=Path, required=True)
    i.add_argument("--state", type=Path, required=True)
    u = sub.add_parser("uninstall")
    u.add_argument("--state", type=Path, required=True)
    u.add_argument("--logs-out", type=Path, required=True)
    r = sub.add_parser("restore-configs")
    r.add_argument("--state", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "stage":
        print(json.dumps(list(stage(a.out, a.always_on)["targets"]), indent=1))
    elif a.cmd == "install":
        print(json.dumps(list(install(a.stage, a.state)["installed"]), indent=1))
    elif a.cmd == "uninstall":
        print(json.dumps(uninstall(a.state, a.logs_out), indent=1, ensure_ascii=False))
    elif a.cmd == "restore-configs":
        print(json.dumps(restore_configs(a.state), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
