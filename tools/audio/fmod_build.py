"""HERRAMIENTA DE MANTENIMIENTO (no la usa la aplicación): proyecto FMOD (copia de la plantilla del SDK de AC)
y construcción de un banco, solo para regenerar el esqueleto de ``src/audio/data`` (``make_skeleton``).

Nunca escribe bytes de banco: solo FMOD Studio 1.08.12 (``fmodstudiocl``) los produce. Los
GUID los genera FMOD (``studio.project.create``); aquí no se escribe ninguno a mano.

Éxito = ``GUARDADO`` y ningún ``ERROR`` en ``build_events_log.txt`` (el script captura sus
propias excepciones: el código de salida de FMOD no prueba nada) + banco + ``GUIDs.txt`` +
``load_prebuilt`` correcto. ``common.bank`` (sonidos globales de Kunos) nunca se copia.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.audio.plan import template
from src.safe_paths import ensure_safe_output_path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).parent / "data"  # build_events.template.js (solo para regenerar el esqueleto)
FMOD_REQUIRED = "1.08.12"
DEFAULT_FMOD = Path(r"C:\Program Files\FMOD SoundSystem\FMOD Studio 1.08.12")
SDK_LOCAL = (
    ROOT / "docs/references/assetto-corsa/audio/ac_fmod_sdk_1_9"
)  # copia de desarrollo (no se redistribuye)
SDK_IN_AC = (
    Path("sdk") / "audio" / "ac_fmod_sdk_1_9"
)  # dentro de la instalación de AC del usuario
FSPRO = "ac_fmod_sdk_1_9.fspro"
TIMEOUT_S = 1800


class AudioBuildError(RuntimeError):
    """El proyecto o el banco no se pudo preparar o construir (mensaje explicado)."""


def sdk_dir() -> Path:
    """Plantilla del SDK de audio (solo lectura): la copia de desarrollo si existe y, si no, la que trae
    la instalación de AC configurada (``<ac_root>/sdk/audio/ac_fmod_sdk_1_9``)."""
    from src.project.export import default_ac_root

    if SDK_LOCAL.is_dir():
        return SDK_LOCAL
    ac = default_ac_root()
    return ac / SDK_IN_AC if ac else SDK_LOCAL


def sdk_tree_sha256(sdk: Path | None = None) -> str:
    sdk = sdk or sdk_dir()
    tree = {
        p.relative_to(sdk).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(sdk.rglob("*"))
        if p.is_file()
    }
    return hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest()


def commands(out: Path, fmod_cl: str, car_id: str) -> list[list[str]]:
    fspro = str(out / "fmod_project" / "ac_fmod_sdk_1_9" / FSPRO)
    return [
        [fmod_cl, "-script", "build_events.js", fspro],
        [
            fmod_cl,
            "-build",
            "-banks",
            car_id,
            "-platforms",
            "Desktop",
            "-export-guids",
            fspro,
        ],
    ]


def prepare_project(spec: dict[str, Any], out: Path, fmod_cl: str) -> None:
    """Copia la plantilla (verificada por hash: los GUID del spec salen de ella), escribe
    ``spec.json``, ``build_events.js`` y ``LEEME.md`` con las órdenes reproducibles."""
    sdk = sdk_dir()
    if not sdk.is_dir():
        raise AudioBuildError(
            "falta el SDK de FMOD de Assetto Corsa (<AC>/sdk/audio/ac_fmod_sdk_1_9): configura la ruta de "
            "Assetto Corsa en Ajustes o use un banco precompilado"
        )
    if sdk_tree_sha256(sdk) != template()["sdk_tree_sha256"]:
        raise AudioBuildError(
            "la plantilla del SDK cambió: los GUID de carpetas/grupos ya no valen"
        )
    missing = [f for f in spec["audio_files"] if not Path(f).is_file()]
    if missing:
        raise AudioBuildError(f"faltan WAV: {missing[:3]}")
    prj = out / "fmod_project" / "ac_fmod_sdk_1_9"
    shutil.copytree(sdk, ensure_safe_output_path(prj))
    # WAV de trabajo DENTRO de Assets/ con nombres cortos: con rutas externas FMOD deriva un
    # Assets/<ruta larga> que no existe (>260 caracteres en Windows). Los originales no se tocan.
    wavs = prj / "Assets" / "beam2ac_audio"
    wavs.mkdir()
    short = {}
    for i, f in enumerate(spec["audio_files"]):
        short[f] = (wavs / f"s{i:03d}_{Path(f).name}").as_posix()
        shutil.copyfile(f, short[f])
    spec = json.loads(json.dumps(spec))
    spec["audio_files"] = [short[f] for f in spec["audio_files"]]
    spec["source_files"] = {short[f]: f for f in short}
    for ev in spec["engine"].values():
        for tr in ev["tracks"]:
            for snd in tr["sounds"]:
                snd["file"] = short[snd["file"]]
    (out / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    js = (DATA / "build_events.template.js").read_text(encoding="utf-8")
    (out / "build_events.js").write_text(
        js.replace("/*SPEC*/null", json.dumps(spec)), encoding="utf-8"
    )
    cmds = "\n".join(
        " ".join(f'"{a}"' for a in c) for c in commands(out, fmod_cl, spec["car"])
    )
    (out / "LEEME.md").write_text(
        f"# Construcción del banco `{spec['car']}`\n\nDesde esta carpeta (FMOD Studio 1.08.12):\n\n"
        f"```\n{cmds}\n```\n\nNo use `-diagnostic` (falla con la plantilla intacta). Después: "
        "`python -m src.audio collect <esta carpeta>`.\n",
        encoding="utf-8",
    )


def _script_log(out: Path) -> str:
    log = out / "fmod_project" / "build_events_log.txt"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def run_build(out: Path, fmod_cl: str, car_id: str) -> dict[str, Any]:
    """Ejecuta las dos órdenes de FMOD con tiempo límite y comprueba el resultado real."""
    logs = []
    for c in commands(out, fmod_cl, car_id):
        # fmodstudiocl falla de forma intermitente (0xC0000374/0xC0000005) al importar audio, antes
        # de guardar: sin GUARDADO el proyecto en disco sigue intacto y se reintenta (3 veces).
        for attempt in range(1, 4):
            try:
                r = subprocess.run(
                    c, cwd=out, capture_output=True, text=True, timeout=TIMEOUT_S
                )
            except subprocess.TimeoutExpired as exc:
                raise AudioBuildError(
                    f"FMOD no terminó en {TIMEOUT_S} s: {c[1]}"
                ) from exc
            logs.append(
                f"$ {' '.join(c)} (intento {attempt})\n{r.stdout}{r.stderr}\n[exit {r.returncode}]\n"
            )
            (out / "fmod_output.txt").write_text("\n".join(logs), encoding="utf-8")
            if r.returncode == 0 or c[1] != "-script" or "GUARDADO" in _script_log(out):
                break
        if r.returncode:
            raise AudioBuildError(
                f"{c[1]} terminó con código {r.returncode} (ver fmod_output.txt)"
            )
        if c[1] == "-script":
            text = _script_log(out)
            if "GUARDADO" not in text or "ERROR" in text:
                raise AudioBuildError(
                    f"el script de FMOD no guardó el proyecto: {text[-300:] or 'sin registro'}"
                )
    return {
        "fmod_output": "fmod_output.txt",
        "script_log": "fmod_project/build_events_log.txt",
    }


def collect(out: Path, car_id: str, expected_events: set[str]) -> Path:
    """Copia banco y GUIDs a ``out/prebuilt`` y los valida con ``load_prebuilt``."""
    from src.ac.audio import AudioInputError, load_prebuilt

    build = out / "fmod_project" / "ac_fmod_sdk_1_9" / "Build"
    bank, guids = build / "Desktop" / f"{car_id}.bank", build / "GUIDs.txt"
    if not (bank.is_file() and guids.is_file()):
        raise AudioBuildError(f"aún no hay {bank.name} y GUIDs.txt en {build}")
    dst = out / "prebuilt"
    dst.mkdir(exist_ok=False)
    shutil.copyfile(bank, dst / bank.name)
    shutil.copyfile(guids, dst / "GUIDs.txt")
    try:
        audio = load_prebuilt(dst, car_id)
    except AudioInputError as exc:
        raise AudioBuildError(f"banco inválido: {exc}") from exc
    if set(audio.events) != expected_events:
        raise AudioBuildError(
            f"eventos del banco {sorted(set(audio.events) ^ expected_events)} no coinciden con el spec"
        )
    return dst


def detect_fmod(path: Path | None = None) -> dict[str, Any]:
    """FMOD Studio por su carpeta (``BEAM2AC_FMOD_STUDIO`` o la de instalación por defecto). La versión
    sale del NOMBRE de la carpeta (``FMOD Studio 1.08.12``); el ejecutable no se ejecuta."""
    import os

    root = Path(path or os.environ.get("BEAM2AC_FMOD_STUDIO") or DEFAULT_FMOD)
    exes = {
        name: (root / name).is_file()
        for name in ("fmodstudiocl.exe", "FMOD Studio.exe")
    }
    version = (
        root.name.removeprefix("FMOD Studio ").strip()
        if root.name.startswith("FMOD Studio")
        else ""
    )
    return {
        "path": str(root),
        "found": all(exes.values()),
        "version": version or None,
        "compatible": all(exes.values()) and version == FMOD_REQUIRED,
        "required": FMOD_REQUIRED,
    }
