"""Orquestación del audio: WAV de origen → plan → proyecto FMOD → banco → carpeta lista.

La salida (``<out>/prebuilt/{<car_id>.bank, GUIDs.txt, audio_build.json}``) es exactamente lo
que ``src.ac.audio.load_prebuilt`` y ``export --audio-bank`` ya consumen: un banco generado y
uno manual siguen el MISMO camino de exportación.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from src.audio import fmod_build
from src.audio.plan import DATA, build_plan
from src.extract.audio_sources import scan_audio
from src.pipeline.contracts import detect_fmod
from src.safe_paths import new_output_dir

BUILD_META = "audio_build.json"


class _Reused(Exception):
    """Salida del bloque de construcción cuando se reutiliza un banco."""


def ladder(
    plan_status: str = "sin_fuente",
    project: bool = False,
    bank: bool = False,
    installed: bool = False,
    detected: bool | None = None,
) -> dict[str, Any]:
    """Escalera única de estados (informe de exportación, inspección y UI). Cada peldaño solo
    es cierto si ocurrió; «escuchado» y «validado en AC» nunca los marca el código."""
    usable = plan_status in ("listo", "listo_con_fallback")
    st = {
        "detectado": plan_status != "sin_fuente" if detected is None else detected,
        "validado": usable,
        "plan_generado": usable,
        "proyecto_generado": project,
        "banco_construido": bank,
        "instalado_en_exportacion": installed,
        "escuchado": False,
        "validado_en_ac": False,
        "validado_acusticamente": False,
    }
    reached = [k for k, v in st.items() if v]
    return {"stages": st, "state": reached[-1] if reached else "sin_fuente"}


def _norm(obj: Any, names: dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _norm(v, names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(v, names) for v in obj]
    return names.get(obj, obj) if isinstance(obj, str) else obj


def reuse_key(
    spec: dict[str, Any], plan: dict[str, Any], fmod_version: str | None
) -> str:
    """Huella de TODO lo que determina el banco: spec con cada WAV sustituido por su sha256
    (no por su ruta), plantilla del SDK, plantilla del script, datos de plantilla y FMOD.
    """
    names = {k: v["sha256"] for k, v in plan.get("wav", {}).items() if "sha256" in v}
    names |= {str(Path(k)): v for k, v in names.items()}
    names |= {k.replace("/", "\\"): v for k, v in names.items()}
    blob = {
        "spec": _norm(spec, names),
        "sdk": fmod_build.sdk_tree_sha256(),
        "script": hashlib.sha256(
            (DATA / "build_events.template.js").read_bytes()
        ).hexdigest(),
        "template_spec": hashlib.sha256(
            (DATA / "template_spec.json").read_bytes()
        ).hexdigest(),
        "fmod": fmod_version,
    }
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def find_reusable(parent: Path, key: str, car_id: str) -> Path | None:
    """``prebuilt`` de una construcción hermana con la MISMA huella, banco íntegro y válido
    (``load_prebuilt``); solo en ``parent`` (audio del proyecto), sin caché global."""
    from src.ac.audio import AudioInputError, load_prebuilt

    for meta in sorted(parent.glob(f"*/prebuilt/{BUILD_META}"), reverse=True):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            bank = meta.parent / f"{car_id}.bank"
            if (
                m.get("reuse_key") == key
                and m.get("car_id") == car_id
                and bank.is_file()
                and hashlib.sha256(bank.read_bytes()).hexdigest()
                == m.get("bank_sha256")
            ):
                load_prebuilt(meta.parent, car_id)
                return meta.parent
        except (OSError, ValueError, AudioInputError):
            continue
    return None


MAX_ZIP_MEMBER = (
    512 << 20
)  # un WAV de motor pesa decenas de MiB; esto frena una bomba de compresión
MAX_ZIP_AUDIO = 2 << 30
MAX_ZIP_ENTRIES = 20000


def extract_zip_audio(zip_path: Path | str, dest: Path) -> Path:
    """Copia ``art/sound/`` de un zip de mod a ``dest`` (si ya existe, se reutiliza) y devuelve la raíz
    que espera ``scan_audio``. Solo extrae miembros bajo ``art/sound/`` y nunca fuera de ``dest``.
    """
    import zipfile

    from src.safe_paths import ensure_safe_output_path

    dest = ensure_safe_output_path(dest)
    done = dest / ".extraido"  # sin la marca, una extracción interrumpida se rehace
    if done.is_file():
        return dest
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        if len(z.infolist()) > MAX_ZIP_ENTRIES:
            raise ValueError(f"el zip tiene más de {MAX_ZIP_ENTRIES} entradas")
        for info in z.infolist():
            parts = Path(info.filename.replace("\\", "/")).parts
            low = [p.lower() for p in parts]
            # el mod puede llevar una carpeta raíz antes de art/sound
            i = next(
                (k for k in range(len(low) - 1) if low[k : k + 2] == ["art", "sound"]),
                None,
            )
            if info.is_dir() or i is None or ".." in parts:
                continue
            target = dest.joinpath(*parts[i:])
            if not target.resolve().is_relative_to(dest.resolve()):
                continue
            total += info.file_size
            if info.file_size > MAX_ZIP_MEMBER or total > MAX_ZIP_AUDIO:
                raise ValueError(f"audio del zip demasiado grande ({total >> 20} MiB)")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    done.parent.mkdir(parents=True, exist_ok=True)
    done.write_text("ok", "utf-8")
    return dest


BUILD_ATTEMPTS = 3


def _keep_logs(work: Path, out: Path, attempt: int) -> None:
    """Órdenes y registros del intento a la carpeta definitiva (el proyecto FMOD temporal se borra)."""
    for name in ("spec.json", "build_events.js", "LEEME.md"):
        if (work / name).is_file() and not (out / name).exists():
            shutil.copyfile(work / name, out / name)
    for name, dst in (
        ("fmod_output.txt", f"fmod_output_intento{attempt}.txt"),
        ("fmod_project/build_events_log.txt", f"build_events_log_intento{attempt}.txt"),
    ):
        if (work / name).is_file():
            shutil.copyfile(work / name, out / dst)


def _staged_build(
    spec: dict[str, Any], out: Path, fmod_cl: str, car_id: str, report: dict[str, Any]
) -> Path:
    """Construye el banco en una carpeta temporal CORTA y lo copia a ``out/prebuilt``.

    FMOD Studio 1.08 falla de forma intermitente al escribir ``.unsaved/Metadata/AudioFile/{guid}.xml`` («Error writing
    to … Make sure you have write permission») y entonces ``-build`` termina con «Could not locate bank»: pasó con el
    proyecto de la app (ruta de 108 caracteres, Ibiza ``exports/007``) y también en una ruta corta. Cada intento usa un
    proyecto nuevo (el parcial no sirve); tras ``BUILD_ATTEMPTS`` fallos se informa con los registros de todos.
    """
    import tempfile

    events = set(spec["secondary"]) | set(spec["engine"])
    last: Exception | None = None
    for attempt in range(1, BUILD_ATTEMPTS + 1):
        work = Path(tempfile.mkdtemp(prefix="b2a_fm_"))
        try:
            fmod_build.prepare_project(spec, work, fmod_cl)
            try:
                report["build"] = fmod_build.run_build(work, fmod_cl, car_id)
                dst = fmod_build.collect(work, car_id, events)
            finally:
                _keep_logs(work, out, attempt)
            shutil.copytree(dst, out / "prebuilt")
            report["build_attempts"] = attempt
            return out / "prebuilt"
        except fmod_build.AudioBuildError as exc:
            last = exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
    raise fmod_build.AudioBuildError(
        f"{last} (tras {BUILD_ATTEMPTS} intentos; ver fmod_output_intento*.txt)"
    )


def use_python_builder(build: bool, fmod: dict[str, Any]) -> bool:
    """``BEAM2AC_AUDIO_BUILDER``: ``python`` (sin FMOD), ``fmod`` o ``auto`` (por defecto:
    FMOD compatible si lo hay y, si no, el generador propio)."""
    mode = builder_mode()
    if not build or mode == "fmod":
        return False
    return mode == "python" or not fmod["compatible"]


def builder_mode() -> str:
    mode = os.environ.get("BEAM2AC_AUDIO_BUILDER", "auto").strip().lower()
    if mode not in ("auto", "fmod", "python"):
        raise ValueError(
            f"BEAM2AC_AUDIO_BUILDER={mode!r} no es válido (auto, fmod o python)"
        )
    return mode


def _python_build(spec: dict[str, Any], out: Path, car_id: str) -> Path:
    """Banco sin FMOD (``engine_gen`` sobre el esqueleto versionado) en ``out/prebuilt``."""
    from src.ac.audio import AudioInputError, load_prebuilt
    from src.audio import bank_graph, engine_gen
    from src.audio.bank_patch import read_wav_mono16

    raw, guids = engine_gen.generate_from_skeleton(
        spec, lambda f: read_wav_mono16(Path(f))
    )
    problems = bank_graph.check_consistency(raw)
    if problems:
        raise ValueError(f"banco generado incoherente: {problems[:3]}")
    dst = out / "prebuilt"
    dst.mkdir(exist_ok=False)
    (dst / f"{car_id}.bank").write_bytes(raw)
    (dst / "GUIDs.txt").write_text(guids, encoding="utf-8")
    try:
        audio = load_prebuilt(dst, car_id)
    except AudioInputError as exc:
        raise ValueError(f"banco inválido: {exc}") from exc
    expected = set(spec["secondary"]) | set(spec["engine"])
    if set(audio.events) != expected:
        raise ValueError("los eventos del banco no coinciden con el spec")
    (out / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    (dst / BUILD_META).write_text(
        json.dumps(
            {
                "generator": "src.audio.engine_gen (sin FMOD)",
                "car_id": car_id,
                "skeleton": json.loads(
                    (engine_gen.DATA / "skeleton_provenance.json").read_text("utf-8")
                ),
                "bank_sha256": hashlib.sha256(raw).hexdigest(),
                "reuse_key": None,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return dst


def run(
    vehicle_dir: Path | str,
    car_id: str,
    out: Path | str,
    idle_rpm: float | None = None,
    limiter_rpm: float | None = None,
    build: bool = True,
    fmod_path: Path | None = None,
) -> dict[str, Any]:
    """Genera ``out`` (carpeta NUEVA). Con FMOD ausente/incompatible o ``build=False`` deja el
    proyecto preparado (``proyecto_generado``) y las órdenes en ``LEEME.md``; no simula nada.
    """
    scan = scan_audio(vehicle_dir)
    plan = build_plan(scan, car_id, idle_rpm, limiter_rpm)
    fmod = detect_fmod(fmod_path)
    report: dict[str, Any] = {
        "car_id": car_id,
        "source": scan.get("root"),
        "plan_status": plan["status"],
        "warnings": plan.get("warnings", []),
        "dropped": plan.get("dropped", []),
        "rpm_range": plan.get("rpm_range"),
        "series_used": plan.get("series_used"),
        "wav": plan.get("wav", {}),
        "fmod": fmod,
        "error": None,
    }
    project = bank = False
    out = Path(out)
    out0, py_done = out, False
    if plan["status"] in ("listo", "listo_con_fallback") and use_python_builder(
        build, fmod
    ):
        out = new_output_dir(out)
        try:
            report["prebuilt"] = str(_python_build(plan["spec"], out, car_id))
            project = bank = py_done = True
            report["note"] = "banco generado sin FMOD (src.audio.engine_gen)"
        except (
            Exception
        ) as exc:  # noqa: BLE001 - cualquier fallo del generador se informa o se respalda
            if (
                builder_mode() == "python"
            ):  # elegido a propósito: el error es el resultado
                report["error"] = f"{type(exc).__name__}: {exc}"
                py_done = True
                shutil.rmtree(out, ignore_errors=True)
            else:  # auto: el generador no puede; se prepara el proyecto de FMOD como antes
                report["warnings"] = [
                    *report["warnings"],
                    f"el generador sin FMOD no pudo construir el banco: {exc}",
                ]
                shutil.rmtree(out, ignore_errors=True)
                out = out0
    if py_done:
        pass
    elif plan["status"] in ("listo", "listo_con_fallback"):
        spec = plan["spec"]
        key = (
            reuse_key(spec, plan, fmod["version"])
            if build and fmod["compatible"]
            else None
        )
        reuse = (
            find_reusable(out.parent, key, car_id)
            if key and out.parent.is_dir()
            else None
        )
        out = new_output_dir(out)
        try:
            if reuse:
                shutil.copytree(reuse, out / "prebuilt")
                project = bank = True
                report["prebuilt"] = str(out / "prebuilt")
                report["reused_from"] = str(reuse.parent)
                report["note"] = (
                    "banco reutilizado: mismas entradas, plantilla y FMOD (sin reconstruir)"
                )
                raise _Reused
            fmod_cl = str(Path(fmod["path"]) / "fmodstudiocl.exe")
            if not build or not fmod["compatible"]:
                fmod_build.prepare_project(spec, out, fmod_cl)
                project = True
                report["note"] = (
                    "construcción no pedida: órdenes en LEEME.md"
                    if not build
                    else f"FMOD 1.08.12 no disponible ({fmod['path']}): proyecto preparado, banco NO construido"
                )
            else:
                project = True
                dst = _staged_build(spec, out, fmod_cl, car_id, report)
                bank = True
                bank_sha = hashlib.sha256(
                    (dst / f"{car_id}.bank").read_bytes()
                ).hexdigest()
                report["prebuilt"] = str(dst)
                (dst / BUILD_META).write_text(
                    json.dumps(
                        {
                            "generator": "src.audio.pipeline",
                            "car_id": car_id,
                            "fmod": fmod["version"],
                            "spec_sha256": hashlib.sha256(
                                json.dumps(spec, sort_keys=True).encode()
                            ).hexdigest(),
                            "reuse_key": key,
                            "bank_sha256": bank_sha,
                            "plan_status": plan["status"],
                            "warnings": report["warnings"],
                            "wav_sha256": sorted(
                                {
                                    v["sha256"]
                                    for v in plan["wav"].values()
                                    if "sha256" in v
                                }
                            ),
                        },
                        indent=1,
                    ),
                    encoding="utf-8",
                )
        except _Reused:
            pass
        except (fmod_build.AudioBuildError, OSError) as exc:
            report["error"] = str(exc)
    report.update(ladder(plan["status"], project, bank))
    if project:
        (out / "audio_report.json").write_text(
            json.dumps(report, indent=1, default=str), encoding="utf-8"
        )
    report["out"] = str(out)
    return report
