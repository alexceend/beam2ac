"""``builds/``: acceso rápido a la última build ESTRUCTURALMENTE funcional de cada ``car_id``.

``output/`` sigue siendo el histórico permanente (nunca se mueve ni se borra nada de él);
``builds/<car_id>/`` es una COPIA del último intento que pasó :func:`check_promotable`.
Funcional aquí = exportación completa, sin errores, íntegra frente a su manifiesto. NO
significa probada en AC, validada físicamente ni sonido escuchado (``build.json`` lo dice).

Sustitución: copia a ``.tmp_<car_id>_*`` (según ``manifest["files"]``, con sha256, sin seguir
enlaces) → comprobación de integridad → ``<car_id>`` → ``.old_<car_id>_*`` → temporal →
``<car_id>`` → borrar la ``.old``. Si algo falla se restaura la anterior; una ``.old`` huérfana
(corte de luz) se recupera al empezar la siguiente promoción. Bloqueo por ``car_id`` con
``O_EXCL``. Los nombres ``.tmp_``/``.old_``/``.lock_`` no casan con el formato de ``car_id``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from src.safe_paths import (
    BUILDS_DIR,
    DEFAULT_OUTPUT_DIR,
    _inside,
    _resolve,
    new_output_dir,
)

CAR_ID = re.compile(r"^beam2ac_[a-z0-9_]+$")
LOG = "promociones.jsonl"
# el relleno inicial ignora ensayos de ventana, copias de comprobación y ramas experimentales
SCAN_EXCLUDE = ("ventana", "_copia", "experimental")
STALE_LOCK_S = 900


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_promotable(
    attempt: Path, options: dict[str, Any] | None = None
) -> tuple[dict, list[str]]:
    """(manifiesto, motivos de rechazo). Lista vacía = promocionable."""
    from .export import SCHEMA, VERDICT_COMPLETE, inspect_export

    why: list[str] = []
    try:
        m = json.loads((attempt / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"manifest.json ilegible: {exc}"]
    car_id = str(m.get("car_id", ""))
    if m.get("schema") != SCHEMA or m.get("kind") != "beam2ac_export":
        why.append("manifiesto con esquema/tipo desconocido")
    if not CAR_ID.match(car_id):
        why.append(f"car_id inválido: {car_id!r}")
    if m.get("verdict") != VERDICT_COMPLETE or m.get("errors"):
        why.append(f"veredicto {m.get('verdict')!r} con {m.get('errors')} errores")
    files = m.get("files") or {}
    for rel in files:
        parts = Path(rel).parts
        if Path(rel).is_absolute() or ".." in parts or not parts:
            why.append(f"ruta no permitida en el manifiesto: {rel!r}")
    if (
        not any(r.lower().endswith(".kn5") for r in files)
        or "data/car.ini" not in files
    ):
        why.append("faltan el KN5 o data/car.ini")
    # audio: lo declarado debe ser lo construido; escuchado/validado nunca lo marca el código
    banks = [
        r for r in files if r.lower().startswith("sfx/") and r.lower().endswith(".bank")
    ]
    st = ((m.get("audio") or {}).get("stages")) or {}
    if (st.get("banco_construido") or st.get("instalado_en_exportacion")) and not banks:
        why.append("el manifiesto declara banco de audio pero no hay .bank")
    if banks and not st.get("instalado_en_exportacion", bool(banks)):
        why.append("hay .bank pero el estado de audio no lo declara instalado")
    if st.get("escuchado") or st.get("validado_en_ac"):
        why.append("estado de audio «escuchado/validado» no verificable por el código")
    if (
        (options or {}).get("audio_build") or m.get("options", {}).get("audio_build")
    ) and not banks:
        why.append(
            "se pidió --audio-build y no hay banco: no se tapa una build con sonido"
        )
    if not why:
        try:
            info = inspect_export(attempt)
        except (OSError, KeyError, ValueError) as exc:
            return m, [f"no se pudo comprobar la integridad: {exc}"]
        if not info["intact"]:
            why.append(
                f"origen no íntegro: cambiados {info['changed'][:3]}, extra {info['unlisted'][:3]}"
            )
        if any(p.is_symlink() for p in attempt.rglob("*")):
            why.append("el intento contiene enlaces simbólicos")
    return m, why


def _lock(root: Path, car_id: str) -> Path:
    lock = root / f".lock_{car_id}"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > STALE_LOCK_S:
            lock.unlink()  # bloqueo abandonado por un proceso muerto
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise RuntimeError(f"otra promoción de {car_id} en curso") from None
    return lock


def _recover(root: Path, car_id: str) -> None:
    """Una ``.old_`` huérfana sin carpeta final = promoción interrumpida: se restaura."""
    dest = root / car_id
    for t in root.glob(
        f".tmp_{car_id}_*"
    ):  # copias a medias de una promoción cortada (bajo bloqueo)
        shutil.rmtree(t, ignore_errors=True)
    olds = sorted(root.glob(f".old_{car_id}_*"), key=lambda p: p.stat().st_mtime)
    if olds and not dest.exists():
        os.replace(olds[-1], dest)
        olds = olds[:-1]
    for o in olds:  # copias ya sustituidas con éxito
        shutil.rmtree(o, ignore_errors=True)


def _log(root: Path, entry: dict[str, Any]) -> None:
    try:
        with (root / LOG).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def promote(
    attempt: Path | str,
    options: dict[str, Any] | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Promociona ``attempt`` a ``builds/<car_id>``. Nunca lanza: devuelve
    ``{"promoted": bool, "reason"/"path": …}`` y deja el histórico intacto."""
    attempt = Path(attempt)
    res: dict[str, Any] = {"promoted": False}
    try:
        if root is None:  # por defecto solo intentos del histórico real
            if not _inside(_resolve(attempt), _resolve(DEFAULT_OUTPUT_DIR)):
                return {**res, "reason": "intento fuera de output/: no se promociona"}
            root = BUILDS_DIR
        root = _resolve(root)
        m, why = check_promotable(attempt, options)
        if why:
            res = {
                **res,
                "reason": "; ".join(why),
            }  # se registra abajo, como cualquier otro fallo
        else:
            car_id = m["car_id"]
            root.mkdir(parents=True, exist_ok=True)
            lock = _lock(root, car_id)
            try:
                _recover(root, car_id)
                res = _swap(root, attempt, m, car_id)
            finally:
                lock.unlink(missing_ok=True)
    except (
        Exception
    ) as exc:  # la exportación histórica ya existe; la promoción no la rompe
        res = {"promoted": False, "reason": f"{type(exc).__name__}: {exc}"}
    if root is not None and Path(root).is_dir():
        _log(
            Path(root),
            {
                "when": _dt.datetime.now().isoformat(timespec="seconds"),
                "attempt": str(attempt),
                **res,
            },
        )
    return res


def _swap(root: Path, attempt: Path, m: dict, car_id: str) -> dict[str, Any]:
    from .export import inspect_export

    tag = uuid.uuid4().hex[:8]
    tmp = new_output_dir(root / f".tmp_{car_id}_{tag}")
    dest, old = root / car_id, root / f".old_{car_id}_{tag}"
    try:
        for rel, info in m["files"].items():
            src = attempt / m["car_id"] / rel
            if src.is_symlink() or not src.is_file():
                raise RuntimeError(f"falta o es un enlace: {rel}")
            (tmp / car_id / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, tmp / car_id / rel)
            if _sha(tmp / car_id / rel) != info["sha256"]:
                raise RuntimeError(f"copia corrupta: {rel}")
        for name in ("manifest.json", "export_report.md", "export_report.json"):
            if (attempt / name).is_file():
                shutil.copyfile(attempt / name, tmp / name)
        audio = (m.get("audio") or {}).get("stages") or {}
        (tmp / "build.json").write_text(
            json.dumps(
                {
                    "car_id": car_id,
                    "promoted": _dt.datetime.now().isoformat(timespec="seconds"),
                    "source_attempt": str(attempt),
                    "source_manifest_sha256": _sha(attempt / "manifest.json"),
                    "levels": {
                        "export_completed": True,
                        "structurally_functional": True,
                        "tested_in_ac": bool(m.get("tested_in_game")),
                        "physically_validated": False,
                        "audio_bank_installed": bool(
                            audio.get("instalado_en_exportacion")
                        ),
                        "audio_listened": False,
                        "audio_validated": False,
                    },
                    "note": "copia de acceso rápido; el histórico permanece en el intento de origen",
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        if not inspect_export(tmp)["intact"]:
            raise RuntimeError("la copia temporal no es íntegra")
        had = dest.exists()
        if had:
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if had:
                os.replace(old, dest)
            raise
        if had:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"promoted": True, "path": str(dest), "replaced": had}


def scan_history(output: Path | str = DEFAULT_OUTPUT_DIR) -> list[dict[str, Any]]:
    """Última exportación promocionable por vehículo (por ``created`` de attempt.json).
    Solo lectura; ``python -m src.project builds scan --apply`` promociona."""
    best: dict[str, tuple[str, Path, dict]] = {}
    for man in Path(output).glob("**/exports/*/manifest.json"):
        att = man.parent
        if any(x in p.lower() for p in att.parts for x in SCAN_EXCLUDE):
            continue
        try:
            m, why = check_promotable(att)
        except (OSError, ValueError):
            continue
        try:
            created = json.loads(
                (att / "attempt.json").read_text(encoding="utf-8")
            ).get("created", "")
        except (
            OSError,
            ValueError,
        ):  # intentos antiguos sin attempt.json: fecha del manifiesto
            created = _dt.datetime.fromtimestamp(man.stat().st_mtime).isoformat(
                timespec="seconds"
            )
        if why:
            continue
        key = str(m.get("vehicle"))
        if key not in best or created > best[key][0]:
            best[key] = (created, att, m)
    return [
        {"vehicle": k, "car_id": m["car_id"], "attempt": str(a), "created": c}
        for k, (c, a, m) in sorted(best.items())
    ]
