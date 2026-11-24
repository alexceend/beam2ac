"""Contrato único de los bancos de BeamNG (G2.46): CLI y aplicación de escritorio.

Un «trabajo» de banco es ``(tipo, carpeta del vehículo, .pc)``; su carpeta de trabajo
depende de la HUELLA del origen (no del nombre), así que repetir la petición REANUDA:

* una sesión ya terminada (``session_done``) no se repite;
* una sesión a medias (BeamNG cerrado a mano, corte de luz) se vuelve a preparar y a
  lanzar desde cero (la carpeta de usuario de esa sesión se conserva como ``*_parcial``);
* con las sesiones listas :func:`run` recoge el resultado (``collect``) y escribe
  ``measurements/beamng_bench/<tipo>/<id>.json``, que nunca se sobrescribe.

No hay lógica de medida aquí: delega en ``tyre_runner`` y ``aero_runner``.
"""

from __future__ import annotations

import json
import os
import shutil
import re
import time
from pathlib import Path
from typing import Any, Callable

from src.bench import aero_runner, tyre_runner
from src.bench.tyre_runner import BenchError
from src.importers import measurements as M
from src.safe_paths import DEFAULT_OUTPUT_DIR, ensure_safe_output_path
from src.steam_paths import find_game


def default_beamng_root() -> Path:
    """BeamNG de los ajustes del usuario (``BEAM2AC_BEAMNG_ROOT``) o el detectado en
    sus bibliotecas de Steam; si no hay ninguno, una ruta relativa que no existe."""
    env = os.environ.get("BEAM2AC_BEAMNG_ROOT")
    return Path(env) if env else (find_game("BeamNG.drive") or Path("BeamNG.drive"))


JOBS_ROOT = DEFAULT_OUTPUT_DIR / "bench_jobs"
SESSIONS = ("s1", "s2")
KINDS = {
    # tipo: (módulo, tipo de registro en measurements/, minutos por sesión)
    "tyres": (tyre_runner, "beamng_tyre_bench", 10),
    "aero": (aero_runner, "beamng_aero_bench", 2),
}


_SHA: dict[tuple[str, int, int], str] = {}
_MATERIALIZE_LOCK = __import__("threading").Lock()  # dos peticiones no se pisan


def _zip_sha(p: Path) -> str:
    """sha256 del zip, por bloques y memorizado por (ruta, tamaño, mtime)."""
    import hashlib

    st = p.stat()
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _SHA:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA[key] = h.hexdigest()
    return _SHA[key]


def materialize(path: Path | str, root: Path | None = None) -> Path:
    """Carpeta de vehículo utilizable por los bancos a partir de una carpeta o de un zip.

    Un zip se extrae UNA vez (todos sus archivos, ``vehicles/<id>/``) a
    ``output/bench_jobs/zips/<sha256 del zip>/vehicles/<id>``: el zip no se toca, el
    nombre depende del contenido (extraer de nuevo reutiliza), la extracción es la
    segura de ``project.model.extract_vehicle`` y un directorio a medias (corte durante
    la extracción) se aparta como ``*_parcial_*`` y se rehace. Con una carpeta devuelve
    la misma ruta. La huella de medida es la misma que la del zip leído sin extraer."""
    import zipfile

    from src.jbeam import vehicle_dirs_in_zip

    p = Path(str(path).strip().strip('"'))
    if not (p.is_file() and p.suffix.lower() == ".zip"):
        return p
    try:
        prefixes = vehicle_dirs_in_zip(p)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BenchError(f"zip no utilizable ({p.name}): {exc}") from None
    if len(prefixes) != 1:
        raise BenchError(
            f"el zip debe contener UN vehículo en vehicles/<id>/ (hay {len(prefixes)}: "
            f"{prefixes})"
        )
    vid = prefixes[0].strip("/").split("/")[-1]
    sha = _zip_sha(p)
    with _MATERIALIZE_LOCK:
        return _extract_once(p, prefixes[0], vid, sha, root)


def _extract_once(p: Path, prefix: str, vid: str, sha: str, root: Path | None) -> Path:
    import zipfile

    from src.project.model import ModelError, extract_vehicle

    base = (Path(root) if root else JOBS_ROOT) / "zips" / sha[:16]
    ensure_safe_output_path(base / "x")
    vehicle = base / "vehicles" / vid
    done = base / ".extraido"
    if done.is_file() and vehicle.is_dir():
        return vehicle
    if base.exists():  # extracción interrumpida: se aparta, nunca se borra
        shutil.move(str(base), f"{base}_parcial_{time.time_ns()}")
    try:
        extract_vehicle(p, prefix, base, suffixes=None, root_name=f"vehicles/{vid}")
    except (ModelError, zipfile.BadZipFile, OSError, KeyError) as exc:
        raise BenchError(f"no se pudo extraer {p.name}: {exc}") from None
    if not any(vehicle.glob("*.pc")):
        raise BenchError(f"el zip {p.name} no trae ningún .pc en vehicles/{vid}/")
    done.write_text(json.dumps({"zip": p.name, "sha256": sha}), "utf-8")
    return vehicle


def _module(kind: str):
    if kind not in KINDS:
        raise BenchError(f"banco desconocido {kind!r}: {sorted(KINDS)}")
    return KINDS[kind][0]


def fingerprint(vehicle_dir: Path, pc: str | None = None) -> str:
    return M.source_fingerprint(_source(vehicle_dir, pc))


def _is_zip(p: Path | str) -> bool:
    q = Path(str(p).strip().strip('"'))
    return q.is_file() and q.suffix.lower() == ".zip"


def vehicle_id(vehicle: Path | str) -> str:
    """Id del vehículo de una carpeta ``…/<id>`` o de un zip ``vehicles/<id>/``."""
    if _is_zip(vehicle):
        from src.jbeam import vehicle_dirs_in_zip

        found = vehicle_dirs_in_zip(Path(vehicle))
        if len(found) != 1:
            raise BenchError(f"el zip debe contener UN vehículo (hay {len(found)})")
        return found[0].strip("/").split("/")[-1]
    return Path(vehicle).name


def _pc(vehicle: Path | str, pc: str | None) -> str:
    if _is_zip(vehicle):
        from src.project.manifest import list_zip_configs

        pcs = list_zip_configs(Path(vehicle))
        if pc and pc not in pcs:
            raise BenchError(f"la configuración {pc!r} no está en el zip: {pcs}")
        if not pc and len(pcs) != 1:
            raise BenchError(f"indique la configuración (.pc): {pcs}")
        return pc or pcs[0]
    return tyre_runner.pc_of(Path(vehicle), pc)


def _source(vehicle: Path | str, pc: str | None):
    """Origen cargado SIN extraer nada (carpeta o zip, con la misma huella: G2.47)."""
    from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip

    pc = _pc(vehicle, pc)
    if _is_zip(vehicle):
        return load_vehicle_zip(Path(vehicle), None, pc)
    return load_vehicle_dir(Path(vehicle), pc)


def measurement_match(
    kind: str,
    vehicle_dir: Path,
    pc: str | None = None,
    root: Path | None = None,
    src: Any = None,
) -> tuple[Path, str] | None:
    """``(registro, "exacta"|"compatible")`` de la medida válida para ESTE origen o
    ``None``. «Compatible» = otra huella completa pero la misma huella acotada del tipo
    (``measurements.scoped_fingerprint``): p. ej. se añadió una pieza de interior."""
    _module(kind)
    hit = M.find(src or _source(vehicle_dir, pc), KINDS[kind][1], root)
    return (hit[0], hit[1]["_match"]) if hit else None


def measurement(
    kind: str,
    vehicle_dir: Path,
    pc: str | None = None,
    root: Path | None = None,
    src: Any = None,
) -> Path | None:
    """Ruta del registro ya medido y válido para ESTE origen, o ``None``."""
    hit = measurement_match(kind, vehicle_dir, pc, root, src)
    return hit[0] if hit else None


def stale_measurements(
    kind: str,
    vehicle_dir: Path,
    pc: str | None = None,
    root: Path | None = None,
    src: Any = None,
) -> list[Path]:
    """Registros del tipo de OTRO origen con el mismo ``label``/vehículo (medidas
    obsoletas: el vehículo cambió en algo que afecta a la medida). Solo informativo:
    nunca se borran ni se reutilizan."""
    _module(kind)
    src = src or _source(vehicle_dir, pc)
    vid, fp, scoped = vehicle_id(vehicle_dir), M.source_fingerprint(src), {}
    out = []
    for path in M.measurement_files(root):
        rec = M.load_record(path)
        label = rec.get("label")
        label = str(label.get("vehicle_id") if isinstance(label, dict) else label)
        if (
            rec["kind"] == KINDS[kind][1]
            and re.fullmatch(
                re.escape(vid) + r"(_[0-9a-f]{8})?", label
            )  # «<id>» o «<id>_<huella>»
            and M.match_kind(rec, src, fp, scoped) is None
        ):
            out.append(path)
    return out


def uses_triangle_aero(
    vehicle_dir: Path, pc: str | None = None, src: Any = None
) -> bool:
    """``aero.model == triangles`` (el mismo criterio del IR, ``extract.aero.aero_model``):
    el C++ del juego calcula la aerodinámica y solo se obtiene midiendo. Con ``cefaero``
    (38022/40340, Lua analítico) no hay banco."""
    from src.extract.aero import aero_model
    from src.jbeam import resolve

    src = src or _source(vehicle_dir, pc)
    return aero_model(resolve(src.index, src.pc)) == "triangles"


def needs(
    vehicle_dir: Path,
    pc: str | None = None,
    blockers: list[dict] | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Bancos que el análisis de un vehículo pide y aún no tienen medida.

    ``blockers`` = claves críticas sin valor de ``diagnostics.inspect_vehicle``; sin él,
    se informa solo qué tipos NO tienen medida. Cada elemento: ``{kind, reason,
    minutes, work}``."""
    out = []
    src = _source(vehicle_dir, pc)  # una sola lectura del origen
    targets = [str(b.get("target", "")) for b in (blockers or [])]
    for kind, (_, _, minutes) in KINDS.items():
        if measurement(kind, vehicle_dir, pc, root, src) is not None:
            continue
        if kind == "tyres":
            hit = [t for t in targets if t.startswith("ac:tyres.ini/")]
        else:
            if not uses_triangle_aero(vehicle_dir, pc, src):
                continue
            hit = [t for t in targets if t.startswith("ac:aero.ini/")]
        if blockers is not None and not hit:
            continue
        out.append(
            {
                "kind": kind,
                "reason": (
                    f"{len(hit)} claves sin valor ({hit[0]}…)"
                    if hit
                    else "sin medida del origen"
                ),
                "minutes": minutes * len(SESSIONS),
                "work": str(work_dir(kind, vehicle_dir, pc, src)),
            }
        )
    return out


def work_dir(
    kind: str, vehicle_dir: Path, pc: str | None = None, src: Any = None
) -> Path:
    fp = M.source_fingerprint(src) if src else fingerprint(vehicle_dir, pc)
    return JOBS_ROOT / f"{kind}_{fp[:16]}"


def session_done(kind: str, work: Path, vid: str, s: str) -> bool:
    """La sesión dejó TODOS sus resultados (los que ``collect`` va a leer)."""
    user = Path(work) / f"userpath_{s}" / "current"
    if kind == "aero":
        return (user / aero_runner.AERO_DIR / f"{vid}.json").is_file()
    stem = user / "beam2ac_tyre" / f"{vid}_{s}"
    return (
        Path(str(stem) + ".csv").is_file() and Path(str(stem) + ".meta.json").is_file()
    )


def status(kind: str, vehicle_dir: Path, pc: str | None = None) -> dict[str, Any]:
    """Estado recuperable del trabajo: qué sesiones están hechas."""
    vd = Path(vehicle_dir)
    src = _source(vd, pc)  # una sola lectura del origen (carpeta o zip, sin extraer)
    vid = vehicle_id(vd)
    work = work_dir(kind, vd, pc, src)
    hit = measurement_match(kind, vd, pc, None, src)
    sessions = {
        s: (
            "hecha"
            if session_done(kind, work, vid, s)
            else "preparada" if (work / f"userpath_{s}").exists() else "pendiente"
        )
        for s in SESSIONS
    }
    # estado en disco: la tarea en curso/cancelada/fallida lo añade la aplicación
    state = (
        "completado"
        if hit
        else (
            "incompleto"
            if any(v != "pendiente" for v in sessions.values())
            else "pendiente"
        )
    )
    stale = [] if hit else [str(p) for p in stale_measurements(kind, vd, pc, None, src)]
    return {
        "kind": kind,
        # G2.47: «reutilizable» (medida de este origen), «compatible» (de otro origen
        # con la misma huella acotada), «obsoleta» (hay una de otro origen que ya no
        # corresponde) o «falta»
        "reuse": hit[1] if hit else ("obsoleta" if stale else "falta"),
        "state": state,
        "stale": stale,
        "work": str(work),
        "sessions": sessions,
        "measurement": str(hit[0]) if hit else "",
    }


def _lock_path() -> Path:
    return JOBS_ROOT / ".lock"


def _acquire_lock() -> None:
    """Un único banco a la vez en TODA la máquina (BeamNG es uno solo)."""
    lock = ensure_safe_output_path(_lock_path())
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            pid = int(json.loads(lock.read_text("utf-8"))["pid"])
        except Exception:
            pid = 0
        if pid and _pid_alive(pid) and pid != os.getpid():
            raise BenchError(f"ya hay un banco en curso (proceso {pid}): {lock}")
        if pid == os.getpid():
            raise BenchError("ya hay un banco en curso en este proceso")
        lock.unlink()  # el dueño murió: bloqueo huérfano
    try:
        with open(lock, "x", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "t": time.time()}, f)
    except FileExistsError:
        raise BenchError(f"ya hay un banco en curso: {lock}") from None


def _release_lock() -> None:
    try:
        _lock_path().unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    import subprocess

    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True
    ).stdout
    return str(pid).encode() in out


def run(
    kind: str,
    vehicle_dir: Path,
    pc: str | None = None,
    work: Path | None = None,
    label: str | None = None,
    beamng_root: Path | None = None,
    log: Callable[[str], None] = lambda _: None,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """Prepara → lanza (en serie, reanudando) → recoge. Devuelve el registro escrito.

    ``log(línea)`` puede lanzar para cancelar entre pasos; ``cancel()`` se atiende
    también DENTRO de una sesión (cierra el juego). ``progress`` recibe
    ``{session, seconds, segment}`` cada ≈ 2 s."""
    beamng_root = beamng_root or default_beamng_root()
    mod = _module(kind)
    pc = _pc(vehicle_dir, pc)
    done = measurement(kind, vehicle_dir, pc)  # carpeta o zip, sin extraer
    if done is not None:
        raise BenchError(f"ya hay una medida de este origen: {done}")
    if _is_zip(vehicle_dir):
        log("zip: extrayendo el vehículo a una carpeta de trabajo")
    vd = materialize(vehicle_dir)  # un zip se extrae aquí, no al analizar
    # varias variantes de un vehículo comparten carpeta («<id>»): el registro lleva la
    # huella en el nombre si «<id>.json» ya existe (nunca se sobrescribe una medida)
    taken = (
        Path(M.MEASUREMENTS_ROOT)
        / "beamng_bench"
        / ("aero" if kind == "aero" else "tyre")
    )
    if label and (taken / f"{label}.json").exists():  # antes de gastar sesiones
        raise BenchError(f"ya existe (no se sobrescribe): {taken / (label + '.json')}")
    if not label:
        if (taken / f"{vd.name}.json").exists():
            label = f"{vd.name}_{fingerprint(vd, pc)[:8]}"
    work = Path(work) if work else work_dir(kind, vd, pc)
    ensure_safe_output_path(work / "x")
    if tyre_runner.beamng_running():
        raise BenchError("BeamNG ya está en ejecución: ciérrelo antes de medir")
    _acquire_lock()
    try:
        for s in SESSIONS:
            if session_done(kind, work, vd.name, s):
                log(f"sesión {s}: ya hecha, se reutiliza")
                continue
            user = work / f"userpath_{s}"
            if user.exists():  # sesión a medias: se aparta, nunca se borra
                part = work / f"userpath_{s}_parcial_{time.time_ns()}"
                shutil.move(str(user), str(part))
                log(f"sesión {s}: había una carpeta a medias → {part.name}")
            log(f"sesión {s}: preparando")
            mod.prepare(work, s, vd, pc)
            log(f"sesión {s}: BeamNG en marcha (≈ {KINDS[kind][2]} min)")

            def tick(sec: float, s=s) -> None:
                if progress:
                    progress(
                        {
                            "session": s,
                            "seconds": round(sec),
                            "expected": KINDS[kind][2] * 60,
                            "segment": last_segment(work / f"userpath_{s}"),
                        }
                    )

            info = mod.launch(work, s, beamng_root, cancel=cancel, tick=tick)
            log(f"sesión {s}: terminada {info}")
            if not session_done(kind, work, vd.name, s):
                raise BenchError(
                    f"la sesión {s} terminó sin dejar resultados (revise "
                    f"{work / f'userpath_{s}/current/beamng.log'})"
                )
        log("recogiendo resultados")
        rec = mod.collect(work, list(SESSIONS), vd, pc, label=label)
        log(f"medida escrita: {rec['_target']}")
        return rec
    finally:
        _release_lock()


def last_segment(user: Path) -> str:
    """Último segmento registrado por el banco en ``beamng.log`` (supervisión)."""
    log = Path(user) / "current" / "beamng.log"
    try:
        lines = log.read_text("utf-8", errors="replace").splitlines()[-60:]
    except OSError:
        return ""
    for line in reversed(lines):
        if "libbeamng.beam2ac" in line and ("segmento" in line or "terminado" in line):
            return line.split("|", 3)[-1].strip()
    return ""
