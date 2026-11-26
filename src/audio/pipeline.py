"""Orquestación del audio: WAV de origen → plan → banco de sonido listo para AC, sin programas externos.

La salida (``<out>/prebuilt/{<car_id>.bank, GUIDs.txt, audio_build.json}``) es exactamente lo
que ``src.ac.audio.load_prebuilt`` y ``export --audio-bank`` ya consumen: un banco generado y
uno manual siguen el MISMO camino de exportación. El banco se genera en Python
(:mod:`src.audio.engine_gen`) sobre el esqueleto versionado de ``src/audio/data``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from src.audio.plan import build_plan
from src.extract.audio_sources import scan_audio
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


def _build(spec: dict[str, Any], out: Path, car_id: str) -> Path:
    """Banco de coche (``engine_gen`` sobre el esqueleto versionado) en ``out/prebuilt``."""
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
                "generator": "src.audio.engine_gen",
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
) -> dict[str, Any]:
    """Genera ``out`` (carpeta NUEVA). Con ``build=False`` solo deja el plan (``spec.json``) sin
    generar el banco; no simula nada."""
    scan = scan_audio(vehicle_dir)
    plan = build_plan(scan, car_id, idle_rpm, limiter_rpm)
    report: dict[str, Any] = {
        "car_id": car_id,
        "source": scan.get("root"),
        "plan_status": plan["status"],
        "warnings": plan.get("warnings", []),
        "dropped": plan.get("dropped", []),
        "rpm_range": plan.get("rpm_range"),
        "series_used": plan.get("series_used"),
        "wav": plan.get("wav", {}),
        "error": None,
    }
    project = bank = False
    out = Path(out)
    if plan["status"] in ("listo", "listo_con_fallback"):
        out = new_output_dir(out)
        try:
            if build:
                report["prebuilt"] = str(_build(plan["spec"], out, car_id))
                project = bank = True
                report["note"] = "banco generado (src.audio.engine_gen)"
            else:
                (out / "spec.json").write_text(
                    json.dumps(plan["spec"], indent=1), encoding="utf-8"
                )
                project = True
                report["note"] = "construcción no pedida: solo el plan (spec.json)"
        except (
            Exception
        ) as exc:  # noqa: BLE001 - cualquier fallo del generador se informa
            report["error"] = f"{type(exc).__name__}: {exc}"
            shutil.rmtree(out, ignore_errors=True)
    report.update(ladder(plan["status"], project, bank))
    if project:
        (out / "audio_report.json").write_text(
            json.dumps(report, indent=1, default=str), encoding="utf-8"
        )
    report["out"] = str(out)
    return report
