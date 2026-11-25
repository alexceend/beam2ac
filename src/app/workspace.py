"""Espacio de trabajo de la aplicación de escritorio (fase C3).

Proyectos, tareas y resultados.

Lógica sin HTTP (la usa ``src/app/desktop.py``). Reglas de seguridad:

* solo lee y escribe dentro de la carpeta de proyectos (por defecto
  ``output/projects``),
  siempre a través de ``src.project`` y ``src.safe_paths``;
* los orígenes de BeamNG y los coches de AC solo se LEEN;
* nada destructivo: ejecutar etapas crea resultados nuevos con caché; exportar crea un
  intento nuevo y exige ``confirm: true``; no hay borrado ni instalación;
* las tareas largas (etapas, Blender, exportación) van en un hilo, una por proyecto, con
  su registro; la interfaz consulta su estado.
"""

from __future__ import annotations

import array
import datetime as _dt
import functools
import itertools
import json
import math
import os
import re
import shutil
import struct
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.safe_paths import DEFAULT_OUTPUT_DIR, PROJECT_ROOT, ensure_safe_output_path

PROJECTS_ROOT = DEFAULT_OUTPUT_DIR / "projects"
PAIRING = DEFAULT_OUTPUT_DIR / "pairing_2026-10-26_run2" / "pairing.json"
# Nombres de proyecto: los existentes usan mayúsculas (p. ej. ``etk800_854_190_M``).
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
READABLE = (".md", ".json", ".ini", ".txt", ".lut", ".jsonl")
MAX_READ = 2 << 20


def _mesh_blob(k: Any, keep: Callable[[Any], bool]) -> bytes:
    """``u32 nv, u32 ni`` + ``f32[nv*3]`` + ``u32[ni]`` de las mallas (type 2) que cumplen ``keep``."""
    pos: list[float] = []
    idx: list[int] = []
    for n in k.nodes:
        if n.type != 2 or not keep(n):
            continue
        base = len(pos) // 3
        for p in k.mesh_world_vertices(n):
            pos.extend(p)
        idx.extend(base + i for i in n.indices)
    # array('f'/'I') empaqueta en C sin desplegar millones de argumentos (little-endian en x86/ARM)
    fl, ui = array.array("f", pos), array.array("I", idx)
    if sys.byteorder == "big":
        fl.byteswap()
        ui.byteswap()
    return struct.pack("<II", len(pos) // 3, len(idx)) + fl.tobytes() + ui.tobytes()


def _default_fuel_tank(wheels: dict[str, list[float]]) -> list[float]:
    """Posición PROVISIONAL del depósito (no medida): eje, y = 0,3 m y entre ejes, a 35 % de la distancia desde el trasero.
    El depósito se coloca de forma manual (nota de uso); BeamNG no tiene nodos del depósito.
    """
    zs = {n[-2:]: w[2] for n, w in wheels.items()}
    front = [zs[k] for k in ("LF", "RF") if k in zs]
    rear = [zs[k] for k in ("LR", "RR") if k in zs]
    if not front or not rear:
        return [0.0, 0.3, 0.0]
    zf, zr = sum(front) / len(front), sum(rear) / len(rear)
    return [0.0, 0.3, round(zr + 0.35 * (zf - zr), 3)]


class WorkspaceError(ValueError):
    """Petición inválida (se muestra al usuario)."""


def _write_wheel(target: Path, rim: str | None, keep: bool = False) -> None:
    """Escribe ``steering_wheel.json`` con el volante activado. ``keep``: conserva el aro ya elegido."""
    data: dict[str, Any] = {"enabled": True}
    if rim:
        data["rim_material"] = rim
    elif keep and target.is_file():
        old = _read_json(target).get("rim_material")
        if old:
            data["rim_material"] = old
    target.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _require_ac_root(given: Any) -> str:
    """Ruta de Assetto Corsa (la del formulario o la de Ajustes): obligatoria, de ella se toma el piloto."""
    from src.project import export as E

    raw = str(given or "").strip().strip('"') or str(E.default_ac_root() or "")
    if not raw or not (Path(raw) / "content" / "driver").is_dir():
        raise WorkspaceError(
            "Indica la ruta de Assetto Corsa (obligatoria): de ella se toma el piloto. "
            "Debe contener content/driver; se configura en Ajustes o en el formulario."
        )
    return raw


def _parse_extras(extras: Any) -> tuple[dict[str, Any], list[str]]:
    """«Detalles del coche»: ``{abs, abs_levels, tc, tc_levels, tyre_compounds}`` → opciones de exportación.

    Todo es opcional; una casilla marcada se incluye en el coche (ABS/TC con niveles ajustables en boxes).
    """
    if extras is None:
        return {}, []
    if not isinstance(extras, dict):
        raise WorkspaceError("extras: se esperaba un objeto")
    aids: dict[str, Any] = {}
    for key, lv in (("abs", "abs_levels"), ("tc", "tc_levels")):
        flag = extras.get(key, False)
        if not isinstance(flag, bool):
            raise WorkspaceError(f"{key}: se esperaba true o false")
        if flag:
            n = extras.get(lv) or 0
            # 0 = sin niveles (solo on/off); si no, de 2 a 20 (driver_aids.AidSpec); sin booleanos ni decimales
            if (
                isinstance(n, bool)
                or not isinstance(n, int)
                or not (n == 0 or 2 <= n <= 20)
            ):
                raise WorkspaceError(f"{lv}: 0 o un entero de 2 a 20")
            aids[key], aids[lv] = "on", n
    comps = extras.get("tyre_compounds") or []
    if not isinstance(comps, list) or not all(isinstance(c, str) for c in comps):
        raise WorkspaceError("tyre_compounds: se esperaba una lista de nombres")
    from src.ac.tyre_compounds import BY_ID

    bad = [c for c in comps if c not in BY_ID]
    if bad:
        raise WorkspaceError(f"compuestos desconocidos: {bad}")
    if len(set(comps)) != len(comps):
        raise WorkspaceError("tyre_compounds: compuestos repetidos")
    return aids, comps


EXTRA_FLAGS = {
    "turbo": "adjustable_turbo",
    "setup_brake_bias": "setup_brake_bias",
    "setup_tyre_pressure": "setup_tyre_pressure",
}


def _parse_flags(extras: Any) -> dict[str, bool]:
    """Casillas de «Extras del coche» que son simples sí/no → kwargs de ``export_project`` (solo las marcadas)."""
    if extras is None:
        return {}
    if not isinstance(extras, dict):
        raise WorkspaceError("extras: se esperaba un objeto")
    out: dict[str, bool] = {}
    for key, kwarg in EXTRA_FLAGS.items():
        v = extras.get(key, False)
        if not isinstance(v, bool):
            raise WorkspaceError(f"{key}: se esperaba true o false")
        if v:
            out[kwarg] = True
    return out


def _num(v: Any) -> bool:
    """Número JSON finito (no bool)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_backfire(extras: Any) -> dict[str, Any]:
    """``extras.backfire`` (bool, por defecto sí) y ``backfire_min_rpm/max_rpm/max_gas`` → kwargs de ``export_project``."""
    if not isinstance(extras, dict):
        return {}
    out: dict[str, Any] = {}
    if extras.get("backfire") is False:
        out["backfire"] = False
    params = {}
    for key, name in (
        ("backfire_min_rpm", "min_rpm"),
        ("backfire_max_rpm", "max_rpm"),
        ("backfire_max_gas", "max_gas"),
    ):
        v = extras.get(key)
        if v in (None, ""):
            continue
        if not _num(v) or v <= 0:
            raise WorkspaceError(f"{key}: se esperaba un número positivo")
        params[name] = float(v)
    if params:
        out["backfire_params"] = params
    return out


def _parse_model_options(extras: Any) -> dict[str, Any] | None:
    """Opciones del MODELO (``normal_maps``, ``part_animations``) → ``None`` si ``extras`` no las menciona
    (se conserva ``model_options.json``). Cada animación: ``{kind, node, pivot[3], axis[3], angle_deg}``.
    """
    from src.ac.part_anim import KINDS

    if not isinstance(extras, dict) or not (
        "normal_maps" in extras or "part_animations" in extras
    ):
        return None
    nm = extras.get("normal_maps", False)
    if not isinstance(nm, bool):
        raise WorkspaceError("normal_maps: se esperaba true o false")
    anims = extras.get("part_animations") or []
    if not isinstance(anims, list):
        raise WorkspaceError("part_animations: se esperaba una lista")
    for a in anims:
        if (
            not isinstance(a, dict)
            or a.get("kind") not in KINDS
            or not isinstance(a.get("node"), str)
            or not a["node"]
        ):
            raise WorkspaceError(
                f"part_animations: cada entrada necesita kind ({sorted(KINDS)}) y node"
            )
        if (
            "hinge" in a
        ):  # forma simple de la interfaz: lado de la pieza y eje por nombre
            from src.ac.part_anim import AXES, HINGES

            if a["hinge"] not in HINGES:
                raise WorkspaceError(f"part_animations: hinge en {list(HINGES)}")
            if a.get("axis_name") not in (None, *AXES):
                raise WorkspaceError(f"part_animations: axis_name en {sorted(AXES)}")
        else:
            for key, n in (("pivot", 3), ("axis", 3)):
                v = a.get(key)
                if (
                    not isinstance(v, list)
                    or len(v) != n
                    or not all(_num(c) for c in v)
                    or (key == "axis" and not any(v))
                ):
                    raise WorkspaceError(f"part_animations: {key} = 3 números finitos")
        if not _num(a.get("angle_deg")) or not -180 <= a["angle_deg"] <= 180:
            raise WorkspaceError("part_animations: angle_deg entre -180 y 180")
    return {"normal_maps": nm, "part_animations": anims}


def _parse_shifter(extras: Any) -> dict[str, bool]:
    """``extras.shifter`` (true/false) → ``supports_shifter`` de ``export_project``; ausente = no se escribe."""
    v = extras.get("shifter") if isinstance(extras, dict) else None
    if v is None:
        return {}
    if not isinstance(v, bool):
        raise WorkspaceError("shifter: se esperaba true o false")
    return {"supports_shifter": v}


def _cockpit_errors(fn):
    """Los fallos esperables del editor de cámaras (modelo ausente o corrupto, ojos sin techo, ajustes ilegibles,
    ``CameraError``/``EditsError``/``JSONDecodeError`` son ``ValueError``) responden 400 con el motivo, no 500 (G2.33).
    Un error de programación (``TypeError``, ``AttributeError``…) sigue siendo 500: no se oculta.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except WorkspaceError:
            raise
        except (ValueError, KeyError, OSError, struct.error) as exc:
            raise WorkspaceError(
                f"{fn.__name__}: {type(exc).__name__}: {exc}"
            ) from None

    return wrapper


class Cancelled(Exception):
    """Tarea cancelada por el usuario en una frontera segura (G2.29)."""


MAX_DRIVER_MASS = 150.0  # el límite que ya declaraba el formulario del análisis


def _save_mass_decisions(root: Path, analysis: dict[str, Any]) -> None:
    """Guarda en ``decisions/`` del proyecto las decisiones de masa del análisis (solo añadir)."""
    from src.attrs import schema as S
    from src.attrs.store import append_decision

    for d in analysis.get("mass_decisions") or []:
        append_decision(root, S.from_dict(d))


def parse_driver_mass(value: Any, default: float = 0.0) -> float:
    """Masa del piloto (kg) de una petición (G2.29.2): la ÚNICA validación, común al
    análisis previo, a la creación de proyectos y a la conversión. Ausente o vacía
    = ``default``; lo demás debe ser un número finito en [0, 150]."""
    if value is None or value == "":
        return default
    try:
        if isinstance(value, bool):
            raise TypeError
        mass = float(value)
    except (TypeError, ValueError):
        raise WorkspaceError(f"masa del piloto no numérica: {value!r}") from None
    if not 0.0 <= mass <= MAX_DRIVER_MASS:  # NaN e infinito tampoco pasan
        raise WorkspaceError(
            f"masa del piloto fuera de rango: {value!r} (0–{MAX_DRIVER_MASS:g} kg)"
        )
    return mass


@dataclass
class Job:
    id: str
    project: str
    kind: str
    status: str = "en_curso"  # en_curso | hecha | fallida | cancelada
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    log: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    started: str = ""
    finished: str = ""
    progress: dict[str, Any] = field(
        default_factory=dict
    )  # G2.46: supervisión del banco
    meta: dict[str, Any] = field(
        default_factory=dict
    )  # G2.47: p. ej. {"bench": "aero", "work": …}

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "kind": self.kind,
            "status": self.status,
            "cancel_requested": self.cancel.is_set(),
            "log": self.log[-400:],
            "result": self.result,
            "error": self.error,
            "started": self.started,
            "finished": self.finished,
            "progress": self.progress,
            "meta": self.meta,
        }


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Workspace:
    def __init__(
        self,
        root: Path = PROJECTS_ROOT,
        source_dirs: list[Path] | None = None,
        source_zips: list[Path] | None = None,
        pairing: Path | None = PAIRING,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.root = ensure_safe_output_path(root)
        self._mass_lock = threading.Lock()  # cambios de masa del piloto: uno cada vez
        self.source_dirs = (
            source_dirs
            if source_dirs is not None
            else [PROJECT_ROOT / "sample_beamng" / "vehicles"]
        )
        mods = (
            Path(os.environ["BEAM2AC_BEAMNG_MODS"])
            if os.environ.get("BEAM2AC_BEAMNG_MODS")
            else Path(os.environ.get("LOCALAPPDATA", ""))
            / "BeamNG"
            / "BeamNG.drive"
            / "current"
            / "mods"
        )
        self.source_zips = (
            source_zips
            if source_zips is not None
            else sorted(mods.glob("*.zip")) if mods.is_dir() else []
        )
        self.pairing = pairing
        # «borrar» un proyecto lo mueve aquí (los paquetes sellados no se destruyen)
        self.trash = DEFAULT_OUTPUT_DIR / "_papelera"
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._reviews: dict[str, Any] = {}
        self._runner = runner  # sustituible en pruebas

    # ------------------------------------------------------------------ proyectos
    def _dir(self, pid: str) -> Path:
        if not isinstance(pid, str) or not NAME.match(pid):
            raise WorkspaceError(f"identificador de proyecto inválido: {pid!r}")
        d = self.root / pid
        if not (d / "project.json").is_file():
            raise WorkspaceError(f"no existe el proyecto {pid!r}")
        return d

    def _project(self, pid: str):
        from src.project.manifest import load_project

        return load_project(self._dir(pid))

    def projects(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir()):
            if (d / "project.json").is_file() and NAME.match(d.name):
                try:
                    out.append(self.summary(d.name))
                except Exception as exc:  # un proyecto roto no tumba la lista
                    out.append({"id": d.name, "error": f"{type(exc).__name__}: {exc}"})
        return out

    def summary(self, pid: str) -> dict[str, Any]:
        d = self._dir(pid)
        meta = _read_json(d / "project.json")
        last_run = self._last_run(d)
        exports = self.exports(pid)
        decisions = (
            sum(
                1
                for f in (d / "decisions").glob("*.jsonl")
                for line in f.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if (d / "decisions").is_dir()
            else 0
        )
        return {
            "id": pid,
            "source": meta.get("source"),
            "created": meta.get("created"),
            "notes": meta.get("notes", ""),
            "driver_mass_kg": meta.get("driver_mass_kg"),
            "reference_data": meta.get("reference_data"),
            "last_run": last_run,
            "decisions": decisions,
            "exports": len(exports),
            "last_export": exports[-1] if exports else None,
            "job": self._active_job(pid),
        }

    def _last_run(self, d: Path) -> dict[str, Any] | None:
        runs = d / "runs.jsonl"
        if not runs.is_file():
            return None
        lines = [x for x in runs.read_text(encoding="utf-8").splitlines() if x.strip()]
        return json.loads(lines[-1]) if lines else None

    def inspect(
        self,
        path: str,
        pc: str | None,
        driver_mass: Any,
        project: str | None = None,
        mass_choice: Any = None,
    ) -> dict[str, Any]:
        """Análisis previo; con ``project`` usa SU configuración (common.zip, masa del
        piloto) y SUS decisiones, como la exportación del proyecto. ``mass_choice``:
        elección del usuario ante un conflicto de masa (``mass_choice.py``)."""
        from . import diagnostics

        if not project:
            return diagnostics.inspect_vehicle(
                path, pc, driver_mass, mass_choice=mass_choice
            )
        d = self._dir(project)
        meta = _read_json(d / "project.json")
        src = meta.get("source") or {}
        res = diagnostics.inspect_vehicle(
            src.get("path") or path,
            src.get("pc") or pc,
            meta.get("driver_mass_kg", 70.0),
            src.get("common_zip"),
            d,
        )
        res["project"] = project  # el cliente descarta respuestas de otro proyecto
        return res

    def detail(self, pid: str) -> dict[str, Any]:
        """Resumen + estado de cada etapa en la última ejecución (con su resumen)."""
        d = self._dir(pid)
        info = self.summary(pid)
        from src.project.stages import STAGES_WITH_MODEL

        stages = []
        run = info["last_run"] or {"stages": {}}
        for st in STAGES_WITH_MODEL:
            r = run["stages"].get(st.name)
            entry: dict[str, Any] = {
                "name": st.name,
                "description": st.description,
                "status": r["status"] if r else "no_ejecutada",
                "key": r["key"] if r else None,
                "error": r.get("error") if r else None,
                "summary": None,
            }
            if r and r.get("key") and r["status"] in ("ok", "cached"):
                meta = d / "stages" / st.name / r["key"] / "stage.json"
                if meta.is_file():
                    entry["summary"] = _read_json(meta).get("summary")
            stages.append(entry)
        info["stages"] = stages
        from src.project.model import wheel_override_of

        info["steering_enabled"] = wheel_override_of(self._project(pid)) is not None
        from src.project.model import model_options_of

        info["model_options"] = model_options_of(self._project(pid))
        eng = sorted(
            (self._dir(pid) / "stages" / "resolved_ini").glob("*/data/engine.ini"),
            key=lambda p: p.stat().st_mtime,
        )
        info["engine_limiter"] = None
        if eng:
            from src.ac.gearbox_templates import read_ini

            lim = read_ini(eng[-1].read_text(encoding="utf-8")).get(
                ("ENGINE_DATA", "LIMITER")
            )
            try:
                info["engine_limiter"] = float(lim) if lim else None
            except ValueError:
                pass
        info["has_boost"] = self._has_boost(d, run)
        info["has_manual_gearbox"] = self._has_manual_gearbox(d, run)
        info["has_model"] = any(
            (self._dir(pid) / "stages" / "model").glob("*/model.kn5")
        )
        info["exports_list"] = self.exports(pid)
        info["precheck"] = self.precheck(pid)
        return info

    def _last_extras(self, project: Any) -> dict[str, Any] | None:
        """Los «Extras del coche» del último intento exportado (manifest ``options``), o ``None``."""
        found = sorted((project.root / "exports").glob("[0-9][0-9][0-9]/manifest.json"))
        if not found:
            return None
        o = _read_json(found[-1]).get("options") or {}
        da = o.get("driver_aids") or {}
        return {
            **({"backfire": False} if o.get("backfire") is False else {}),
            **{f"backfire_{k}": v for k, v in (o.get("backfire_params") or {}).items()},
            "abs": da.get("abs") == "on",
            "abs_levels": da.get("abs_levels", 0),
            "tc": da.get("tc") == "on",
            "tc_levels": da.get("tc_levels", 0),
            "tyre_compounds": o.get("tyre_compounds") or [],
            "turbo": bool(o.get("adjustable_turbo")),
            "setup_brake_bias": bool(o.get("setup_brake_bias")),
            "setup_tyre_pressure": bool(o.get("setup_tyre_pressure")),
            **({"shifter": o["supports_shifter"]} if "supports_shifter" in o else {}),
        }

    def _has_manual_gearbox(self, d: Path, run: dict[str, Any]) -> bool:
        """¿Caja manual o secuencial? (hecho ``ir.drivetrain.gearbox.kind`` de la última etapa «attributes»)."""
        att = (run.get("stages") or {}).get("attributes") or {}
        if not att.get("key") or att.get("status") not in ("ok", "cached"):
            return False
        rep = d / "stages" / "attributes" / att["key"] / "report.json"
        f = (_read_json(rep).get("facts") or {}).get("ir.drivetrain.gearbox.kind") or {}
        return f.get("value") in ("manualGearbox", "sequentialGearbox")

    def _has_boost(self, d: Path, run: dict[str, Any]) -> bool:
        """¿El coche lleva turbo/compresor con boost? (hechos de la última etapa «attributes»)."""
        att = (run.get("stages") or {}).get("attributes") or {}
        if not att.get("key") or att.get("status") not in ("ok", "cached"):
            return False
        rep = d / "stages" / "attributes" / att["key"] / "report.json"
        f = (_read_json(rep).get("facts") or {}).get("ir.engine.boost_max") or {}
        v = f.get("value")
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        from src.project.manifest import ProjectError, SourceSpec, create_project

        name = str(body.get("name", "")).strip()
        if not NAME.match(name):
            raise WorkspaceError(
                "nombre: minúsculas, dígitos, «_», «.» o «-» (máx. 64)"
            )
        if (self.root / name).exists():
            raise WorkspaceError(f"ya existe {name!r}: no se sobrescribe")
        kind = body.get("kind")
        if kind not in ("dir", "zip"):
            raise WorkspaceError("kind debe ser dir o zip")
        spec = SourceSpec(
            kind,
            str(body.get("path", "")),
            body.get("pc") or None,
            body.get("common_zip") or None,
        )
        mass = parse_driver_mass(body.get("driver_mass"), 70.0)
        analysis = None
        if body.get("mass_choice"):  # decisiones de masa (conflicto con la ficha)
            from . import diagnostics

            analysis = diagnostics.inspect_vehicle(
                spec.path,
                spec.pc,
                mass,
                spec.common_zip,
                mass_choice=body["mass_choice"],
            )
        try:
            proj = create_project(
                self.root / name,
                spec,
                mass,
                str(body.get("notes", "")),
                reference_data=body.get("reference_data") or None,
            )
            if analysis:
                _save_mass_decisions(proj.root, analysis)
            from src.project.model import WHEEL_FILE

            # volante y piloto van siempre en el coche: no hay opción «sin volante»
            _write_wheel(ensure_safe_output_path(proj.root / WHEEL_FILE), None)
        except (ProjectError, OSError, ValueError) as exc:
            raise WorkspaceError(str(exc)) from None
        return self.summary(proj.root.name)

    def _idle(self, pid: str, what: str) -> Path:
        d = self._dir(pid)
        if any(j.project == pid and j.status == "en_curso" for j in self.jobs.values()):
            raise WorkspaceError(
                f"hay una tarea en curso en {pid!r}: no se puede {what}"
            )
        return d

    def delete(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Quita un proyecto de la lista (con confirmación): mueve su carpeta a ``output/_papelera`` en vez
        de destruirla (los intentos sellados de ``exports/`` siguen intactos y recuperables). Lo exportado a
        ``builds/`` o a una carpeta de entrega no se toca. Se niega si contiene una evidencia histórica.
        """
        from src.safe_paths import HISTORICAL_DIRS

        if body.get("confirm") is not True:
            raise WorkspaceError(
                "borrar un proyecto lo mueve a la papelera: confírmalo (confirm: true)"
            )
        d = self._idle(pid, "borrar").resolve()
        if d.parent != self.root.resolve():
            raise WorkspaceError(f"{pid!r} no cuelga de la carpeta de proyectos")
        for h in HISTORICAL_DIRS:
            if h.resolve().is_relative_to(d):
                raise WorkspaceError(
                    f"{pid!r} contiene una evidencia histórica protegida ({h.name}): no se borra"
                )
        dest = ensure_safe_output_path(
            self.trash / f"{pid}_{_dt.datetime.now():%Y%m%d_%H%M%S}"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), str(dest))
        self._reviews.pop(pid, None)
        return {"deleted": pid, "moved_to": str(dest)}

    def set_driver_mass(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Cambia la masa del piloto de un proyecto existente. Los intentos ya exportados no cambian (están
        sellados); las etapas se recalculan solas porque la masa entra en su clave."""
        d = self._idle(pid, "cambiar la masa del piloto")
        mass = parse_driver_mass(body.get("driver_mass"))
        # las decisiones de masa (mass_choice) fijan valores calculados con la masa de entonces: se recalculan
        # con la misma elección del usuario y sustituyen a las anteriores (no se pierde la historia)
        from src.attrs.store import append_decision

        from . import mass_choice as MC

        with (
            self._mass_lock
        ):  # dos peticiones a la vez intercalaban versiones y repetían ids
            meta = _read_json(d / "project.json")
            if (
                float(meta.get("driver_mass_kg", -1)) != mass
            ):  # misma masa: nada que recalcular
                project = self._project(pid)
                for dec in MC.rederive(d, project.source, mass):
                    append_decision(d, dec)
                meta["driver_mass_kg"] = mass
                ensure_safe_output_path(d / "project.json").write_text(
                    json.dumps(meta, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        return self.summary(pid)

    # ------------------------------------------------------------- orígenes y refs
    def sources(self) -> list[dict[str, Any]]:
        """Vehículos de BeamNG disponibles (solo lectura): carpetas y zips."""
        out = []
        for base in self.source_dirs:
            if not base.is_dir():
                continue
            for d in sorted(p for p in base.iterdir() if p.is_dir()):
                pcs = sorted(p.name for p in d.glob("*.pc"))
                if pcs:
                    out.append(
                        {
                            "kind": "dir",
                            "path": str(d),
                            "vehicle": d.name,
                            "configs": pcs,
                        }
                    )
        for z in self.source_zips:
            out.append(
                {"kind": "zip", "path": str(z), "vehicle": z.stem, "configs": None}
            )
        return out

    def configs(self, zip_path: str) -> list[str]:
        from src.project.manifest import list_zip_configs

        p = Path(zip_path)
        if p not in self.source_zips and not p.is_file():
            raise WorkspaceError("zip desconocido")
        return list_zip_configs(p)

    def references(self, vehicle: str) -> list[dict[str, Any]]:
        """Candidatos de AC del emparejamiento (nunca confirmados)."""
        if not self.pairing or not self.pairing.is_file():
            return []
        data = _read_json(self.pairing)
        rows = data.get("candidates", []) if isinstance(data, dict) else []
        key = Path(vehicle).stem if vehicle else ""
        return [r for r in rows if key and Path(str(r.get("bng", ""))).stem == key][:20]

    # ----------------------------------------------------------------- tareas
    def _active_job(self, pid: str) -> dict[str, Any] | None:
        for j in reversed(list(self.jobs.values())):
            if j.project == pid:
                return {"id": j.id, "kind": j.kind, "status": j.status}
        return None

    def _start(
        self,
        pid: str,
        kind: str,
        fn: Callable[[Callable[[str], None]], Any],
        meta: dict[str, Any] | None = None,
    ):
        with self._lock:
            for j in self.jobs.values():
                if j.project == pid and j.status == "en_curso":
                    raise WorkspaceError(
                        f"ya hay una tarea en curso: {j.kind} ({j.id})"
                    )
            job = Job(f"t{next(self._ids)}", pid, kind, started=_now(), meta=meta or {})
            self.jobs[job.id] = job

        def log(line: str) -> None:
            # G2.29: las tareas solo llaman a ``log`` entre pasos (una etapa ya está
            # guardada entera con os.replace; el intento y la copia aún no existen):
            # es el único punto donde cancelar no deja nada a medias. Blender no se
            # interrumpe: la cancelación espera al final de la etapa en curso.
            job.log.append(line)
            if job.cancel.is_set():
                raise Cancelled

        def target() -> None:
            try:
                job.result = fn(log)
                job.status = "hecha"
            except Cancelled:
                job.log.append(
                    "cancelada: no se copió nada al destino; lo ya terminado (etapas "
                    "y, si llegó a crearse, el intento) queda en el proyecto"
                )
                job.status = "cancelada"
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {exc}"
                job.log.append(traceback.format_exc(limit=3))
                job.status = "fallida"
            finally:
                job.finished = _now()
                self._reviews.pop(pid, None)  # la próxima revisión relee decisiones

        threading.Thread(target=target, daemon=True).start()
        return job.public()

    def job(self, jid: str) -> dict[str, Any]:
        if jid not in self.jobs:
            raise WorkspaceError(f"tarea desconocida {jid!r}")
        return self.jobs[jid].public()

    def cancel(self, jid: str) -> dict[str, Any]:
        """Pide cancelar una tarea en curso; se detiene en el siguiente paso seguro."""
        self.job(jid)
        job = self.jobs[jid]
        if job.status != "en_curso":
            raise WorkspaceError(f"la tarea {jid} ya terminó ({job.status})")
        job.cancel.set()
        return job.public()

    @staticmethod
    def _auto_audio(body: dict[str, Any]) -> bool:
        """El banco de sonido se genera solo si hay FMOD compatible y no se da un banco hecho."""
        if "audio_build" in body:
            return bool(body["audio_build"])
        if body.get("audio_bank"):
            return False
        from src.pipeline.contracts import detect_fmod

        return bool(detect_fmod().get("compatible"))

    def run(self, pid: str, with_model: bool = False) -> dict[str, Any]:
        from src.project.orchestrator import run_project
        from src.project.stages import STAGES, STAGES_WITH_MODEL

        project = self._project(pid)
        stages = STAGES_WITH_MODEL if with_model else STAGES
        runner = self._runner or run_project

        def fn(log):
            res = runner(project, stages, log=log)
            return {r.name: {"status": r.status, "error": r.error} for r in res}

        return self._start(pid, "etapas" + (" + modelo 3D" if with_model else ""), fn)

    def export(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("confirm") is not True:
            raise WorkspaceError(
                "la exportación crea un intento nuevo: confírmela (confirm: true)"
            )
        from src.project.export import export_project

        project = self._project(pid)
        car_id = str(body.get("car_id") or "").strip().lower() or None
        if car_id is not None and not re.match(r"^[a-z0-9_]+$", car_id):
            raise WorkspaceError("car_id: [a-z0-9_]+")
        if car_id is not None and not car_id.startswith("beam2ac_"):
            car_id = (
                "beam2ac_" + car_id
            )  # el prefijo es obligatorio (export.check_car_id): se añade solo
        # el piloto se toma de la instalación de AC (solo lectura): la ruta es obligatoria
        ac_root_in = _require_ac_root(body.get("ac_root"))
        body = {**body, "ac_root": ac_root_in, "local_driver": True}
        extras = body.get("extras")
        if extras is None and body.get(
            "reexport"
        ):  # guardar cámaras: se repiten los extras del último intento
            extras = self._last_extras(project)
        driver_aids, tyre_compounds = _parse_extras(extras)
        flags = _parse_flags(extras) | _parse_shifter(extras) | _parse_backfire(extras)
        model_opts = _parse_model_options(extras)
        # G2.28: con errores bloqueantes conocidos no se exporta desde la interfaz
        # (el CLI `python -m src.project export` sigue dando el intento «bloqueado»)
        blocking = self.precheck(pid).get("errors") or []
        if blocking:
            raise WorkspaceError(
                f"exportación no permitida: {len(blocking)} errores bloqueantes "
                f"(p. ej. {blocking[0]['target']}); resuélvalos en «Atributos y "
                "decisiones»"
            )

        # proyectos antiguos sin volante: se activa (conserva el aro); solo tras pasar las comprobaciones y sin tarea en curso
        from src.project.model import WHEEL_FILE, wheel_override_of

        if wheel_override_of(project) is None:
            self._idle(pid, "exportar")
            _write_wheel(
                ensure_safe_output_path(project.root / WHEEL_FILE), None, keep=True
            )

        if (
            model_opts is not None
        ):  # cambia la clave de la etapa del modelo: se reconstruye en esta exportación
            from src.project.model import MODEL_OPTIONS_FILE

            self._idle(pid, "exportar")
            ensure_safe_output_path(project.root / MODEL_OPTIONS_FILE).write_text(
                json.dumps(model_opts, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        # casilla «Instalar en Assetto Corsa y sobrescribir…»: usa la ruta del formulario o la de Ajustes
        install = bool(body.get("install_overwrite"))
        from src.project import export as E

        ac_target = body.get("ac_root") or (str(E.default_ac_root() or "") or None)
        if install and not ac_target:
            raise WorkspaceError(
                "para instalar en Assetto Corsa configura su ruta en Ajustes (o escríbela en el formulario)"
            )

        def fn(log):
            res = export_project(
                project,
                car_id,
                with_model=True,  # el KN5 es obligatorio: sin modelo no hay coche jugable
                log=log,
                local_driver=bool(body.get("local_driver")),
                ac_root=body.get("ac_root") or None,
                audio_bank=body.get("audio_bank") or None,
                audio_source_id=body.get("audio_source_id") or None,
                audio_build=self._auto_audio(body),
                # sobrescribir es lo pedido; solo beam2ac_* (install_to_ac). «reexport» (guardar cámaras): el id ya
                # instalado no es un error, pero NO se toca la instalación de AC
                allow_installed_car_id=install or bool(body.get("reexport")),
                driver_aids=driver_aids or None,
                tyre_compounds=tyre_compounds or None,
                **flags,
            )
            out = {
                "attempt": res.dir.name,
                "verdict": res.verdict,
                "errors": len(res.errors),
                "warnings": len(res.warnings),
                "build": res.build,
            }
            if install:
                from src.project.install import install_to_ac

                if res.verdict == "bloqueado":
                    log("no se instala: el veredicto es «bloqueado»")
                else:
                    out.update(
                        install_to_ac(res.car_dir, ac_target, res.car_dir.name, True)
                    )
                    log(f"instalado en {out['installed_to']}")
            return out

        return self._start(pid, "exportación", fn)

    # ------------------------------------------------- conversión directa (G2.28)
    def convert(self, body: dict[str, Any]) -> dict[str, Any]:
        """Flujo de un paso: análisis → proyecto nuevo → etapas + modelo →
        exportación → copia al destino elegido. No contiene lógica de conversión:
        encadena ``diagnostics.inspect_vehicle``, ``create_project`` y
        ``export_project``. Con errores bloqueantes en el análisis no crea nada.
        """
        from src.project import export as E
        from src.project.manifest import SourceSpec, create_project
        from src.safe_paths import ProtectedPathError

        from . import diagnostics

        kind, vd, vehicle, configs = diagnostics.find_source(body.get("path", ""))
        pc = body.get("pc") or (configs[0] if len(configs) == 1 else None)
        if pc not in configs:
            raise WorkspaceError(
                f"elige una configuración de la carpeta: {', '.join(configs)}"
            )
        mass = parse_driver_mass(body.get("driver_mass"))
        # G2.29.2: lo que el usuario vio en pantalla debe ser el análisis de ESTA masa
        if body.get("analysis_driver_mass") is not None and (
            parse_driver_mass(body["analysis_driver_mass"]) != mass
        ):
            raise WorkspaceError(
                f"el análisis en pantalla se hizo con un piloto de "
                f"{body['analysis_driver_mass']} kg y la conversión pide {mass:g} kg: "
                "vuelve a analizar con esa masa antes de convertir"
            )
        name = str(body.get("name") or "").strip()
        if not name:  # nombre libre derivado del vehículo; nunca uno existente
            base = re.sub(r"[^a-z0-9_.-]", "_", vehicle.lower())[:56].strip("_.-")
            name = next(
                n
                for n in (
                    f"{base or 'v'}{s}" for s in ["", *map("_{}".format, range(2, 999))]
                )
                if not (self.root / n).exists()
            )
        if not NAME.match(name):
            raise WorkspaceError("nombre: letras, dígitos, «_», «.» o «-» (máx. 64)")
        if (self.root / name).exists():
            raise WorkspaceError(f"ya existe el proyecto {name!r}: no se sobrescribe")
        car_id = str(body.get("car_id") or "").strip() or E.default_car_id(vehicle)
        ac_root = E.default_ac_root()
        target = None
        try:
            E.check_car_id(car_id, ac_root)
            if str(body.get("dest") or "").strip():
                dest = Path(str(body["dest"]).strip().strip('"'))
                if not dest.is_dir():
                    raise WorkspaceError(f"el destino no es una carpeta: {dest}")
                target = ensure_safe_output_path(dest / car_id)
                if target.exists():
                    raise WorkspaceError(f"ya existe {target}: no se sobrescribe")
        except ProtectedPathError as exc:
            raise WorkspaceError(
                f"{exc} Elige otra carpeta (no puede estar dentro de AC ni de BeamNG)."
            ) from None
        except ValueError as exc:
            if isinstance(exc, WorkspaceError):
                raise
            raise WorkspaceError(str(exc)) from None
        ac_root = Path(
            _require_ac_root(body.get("ac_root"))
        )  # el piloto se toma de AC: ruta obligatoria

        def fn(log):
            log(f"1/4 análisis de {vehicle} ({pc})…")
            r = diagnostics.inspect_vehicle(
                str(vd), pc, mass, mass_choice=body.get("mass_choice")
            )
            if not r["ok"]:
                raise WorkspaceError(f"formato no reconocido: {r['error']}")
            if r.get("driver_mass") != mass:  # nunca exportar con otra masa
                raise WorkspaceError(
                    "conversión bloqueada: el análisis usó un piloto de "
                    f"{r.get('driver_mass')!r} kg y el exportador usaría {mass:g} kg; "
                    "las reglas que dependen de la masa del piloto no serían las mismas"
                )
            if not r["exportable_today"]:
                first = ", ".join(b["target"] for b in r["blockers"][:3])
                raise WorkspaceError(
                    "conversión bloqueada por el análisis: "
                    + (
                        r["scope_note"]
                        or f"{len(r['blockers'])} claves críticas "
                        f"sin valor ({first}…)"
                    )
                )
            log(f"2/4 proyecto {name}")
            proj = create_project(
                self.root / name,
                SourceSpec(kind, str(vd), pc, None),
                mass,
                "conversión directa desde la interfaz",
            )
            _save_mass_decisions(proj.root, r)
            from src.project.model import WHEEL_FILE

            _write_wheel(ensure_safe_output_path(proj.root / WHEEL_FILE), None)
            from src.project import cockpit_edits as CE

            ce = CE.for_project(proj)  # mismos ajustes que export.collect aplicará
            log(
                f"ajustes de cabina guardados: versión {ce['version']} "
                f"({', '.join(ce['items']) or 'ninguno: valores calculados'})"
            )
            log("3/4 etapas, modelo 3D (Blender) y exportación…")
            extra = {"local_driver": True, "ac_root": str(ac_root)}
            if self._auto_audio(body):
                extra = {**extra, "audio_build": True}
            res = E.export_project(
                proj, car_id, log=log, car_id_check_root=ac_root, **extra
            )
            out = {
                "project": name,
                "attempt": res.dir.name,
                "car_id": car_id,
                "driver_mass": proj.driver_mass_kg,
                "verdict": res.verdict,
                "errors": [vars(i) for i in res.errors][:200],
                "warnings": len(res.warnings),
                "output": str(res.car_dir),
                "delivered": False,
                "build": res.build,
                "cockpit_version": ce["version"],
                "cockpit_items": sorted(ce["items"]),
            }
            if target is not None and res.verdict != "bloqueado":
                log(f"4/4 copia a {target}")
                import shutil

                shutil.copytree(res.car_dir, target)  # falla si ya existe
                out["output"], out["delivered"] = str(target), True
            return out

        return self._start(name, "conversión", fn)

    # ------------------------------------------------------ detalles del coche (ui_car.json)
    def details_info(self, pid: str) -> dict[str, Any]:
        """Detalles guardados por el usuario, los valores del último intento (como referencia) y la portada
        subida (data URL) si existe."""
        import base64

        from src.project import details as D

        proj = self._project(pid)
        last: dict[str, Any] = {}
        for a in sorted(
            (self._dir(pid) / "exports").glob("*/*/ui/ui_car.json"), reverse=True
        ):
            last = _read_json(a)
            break
        cover = D.cover_bytes(proj)
        logo = D.logo_bytes(proj)
        return {
            "project": pid,
            "logo": (
                "data:image/png;base64," + base64.b64encode(logo).decode()
                if logo
                else None
            ),
            "saved": D.load(proj),
            "last_export": {
                k: last.get(k)
                for k in (
                    "name",
                    "brand",
                    "description",
                    "tags",
                    "author",
                    "year",
                    "class",
                    "country",
                )
            },
            "classes": list(D.CLASSES),
            "cover": (
                "data:image/jpeg;base64," + base64.b64encode(cover).decode()
                if cover
                else None
            ),
        }

    def details_preview(self, pid: str) -> dict[str, Any]:
        """Portada automática (render con texturas desde arriba en diagonal) del último modelo del proyecto,
        para verla en la ventana de detalles antes de exportar. Tarda unos segundos (Blender).
        """
        import base64

        from src.project.cover import CoverError, render_cover
        from src.project.model import blender_path

        kn5s = sorted(
            (self._dir(pid) / "stages" / "model").glob("*/model.kn5"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not kn5s:
            raise WorkspaceError(
                "el proyecto no tiene modelo 3D todavía: ejecuta «Ejecutar etapas (incluye el modelo 3D)» primero"
            )
        exe = blender_path()
        if not exe:
            raise WorkspaceError("Blender no disponible: configura su ruta en Ajustes")
        try:
            jpg = render_cover(kn5s[0], exe)
        except CoverError as exc:
            raise WorkspaceError(f"no se pudo renderizar la portada: {exc}") from None
        return {"cover": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}

    def details_save(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Guarda los detalles (``description``, ``class``, ``brand``, ``country``, ``year``, ``author``, ``tags``)
        y, opcionalmente, la portada (``cover``: data URL de una imagen, o ``clear_cover``).
        """
        import base64

        from src.project import details as D
        from src.project.cover import CoverError

        self._idle(pid, "cambiar los detalles")
        cover = None
        raw = body.get("cover")
        if raw:
            try:
                cover = base64.b64decode(str(raw).split(",", 1)[-1], validate=True)
            except ValueError:
                raise WorkspaceError("la imagen de portada no es válida") from None
            if len(cover) > 8 << 20:
                raise WorkspaceError("la imagen de portada pesa más de 8 MB")
        logo = None
        if body.get("logo"):
            try:
                logo = base64.b64decode(
                    str(body["logo"]).split(",", 1)[-1], validate=True
                )
            except ValueError:
                raise WorkspaceError("el logo no es válido") from None
            if len(logo) > 1 << 20:
                raise WorkspaceError("el logo pesa más de 1 MB")
        try:
            saved = D.save(
                self._project(pid),
                body,
                cover,
                bool(body.get("clear_cover")),
                logo,
                bool(body.get("clear_logo")),
            )
        except (D.DetailsError, CoverError) as exc:
            raise WorkspaceError(str(exc)) from None
        return {"saved": saved}

    # ------------------------------------------------------------ volante (STEER_HR)
    def _wheel_source(self, pid: str) -> tuple[Path, tuple[float, float, float]]:
        """KN5 SIN volante montado y ojos del piloto de la última etapa ``model`` del proyecto."""
        stages = sorted(
            (self._dir(pid) / "stages" / "model").glob("*/model.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for mj in stages:
            eyes = (_read_json(mj).get("drivereyes") or {}).get("eyes_ac")
            for rel in ("_stages/textured_model.kn5", "_build/model.kn5"):
                if eyes and (mj.parent / rel).is_file():
                    return mj.parent / rel, tuple(eyes)
        raise WorkspaceError(
            "el proyecto no tiene modelo 3D todavía: ejecuta «con modelo» (o exporta) una vez "
            "para poder elegir el volante"
        )

    _PARTS_CACHE: dict[str, Any] = {}

    def model_parts(self, pid: str) -> dict[str, Any]:
        """Piezas (nodos con nombre único) del último KN5 del proyecto, para elegir qué animar. Solo lectura."""
        from src.ac.kn5_inspect import read_kn5

        kn5s = sorted(
            (self._dir(pid) / "stages" / "model").glob("*/model.kn5"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not kn5s:
            return {"built": False, "parts": []}
        key = f"{kn5s[0]}:{kn5s[0].stat().st_mtime_ns}"
        if key not in self._PARTS_CACHE:
            kn = read_kn5(kn5s[0], keep_vertices=False)
            counts: dict[str, int] = {}
            for n in kn.nodes:
                counts[n.name] = counts.get(n.name, 0) + 1
            skip = ("WHEEL_", "SUSP_", "STEER_", "COCKPIT_", "BlenderFile", "B2AC_")
            parts = sorted(
                n.name
                for n in kn.nodes
                if counts[n.name] == 1 and not n.name.startswith(skip)
            )
            self._PARTS_CACHE.clear()
            self._PARTS_CACHE[key] = parts
        return {"built": True, "parts": self._PARTS_CACHE[key]}

    _GEO_CACHE: dict[str, Any] = {}

    def parts_geometry(self, pid: str) -> bytes:
        """Todas las mallas del KN5 más reciente como piezas separables, para el editor de animaciones.

        Binario: ``u32 n`` (bytes del JSON) + JSON ``{pieces:[{ref,name,material,tris,nv,ni}]}`` + por pieza
        ``f32[nv*3]`` (marco del KN5) y ``u32[ni]`` (índices locales), en el orden de ``pieces``.
        """
        from src.ac.kn5_inspect import read_kn5
        from src.ac.part_anim import node_refs

        kn5s = sorted(
            (self._dir(pid) / "stages" / "model").glob("*/model.kn5"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not kn5s:
            raise WorkspaceError(
                "el proyecto no tiene modelo 3D todavía: ejecuta la conversión con el modelo primero"
            )
        key = f"{kn5s[0]}:{kn5s[0].stat().st_mtime_ns}"
        if key in self._GEO_CACHE:
            return self._GEO_CACHE[key]
        kn = read_kn5(kn5s[0])
        refs = node_refs(kn)
        pieces, chunks = [], []
        skip = ("WHEEL_", "SUSP_", "COCKPIT_", "B2AC_LIGHT")
        for n, ref in zip(kn.nodes, refs):
            if n.type != 2 or not n.indices or n.name.upper().startswith("DRIVER"):
                continue
            pos = array.array("f", [c for p in kn.mesh_world_vertices(n) for c in p])
            idx = array.array("I", n.indices)
            if sys.byteorder == "big":
                pos.byteswap()
                idx.byteswap()
            pieces.append(
                {
                    "ref": ref,
                    "name": n.name,
                    "material": (
                        kn.materials[n.material][0] if n.material is not None else ""
                    ),
                    "tris": len(n.indices) // 3,
                    "nv": len(pos) // 3,
                    "ni": len(idx),
                    "wheel": n.name.startswith(skip),
                }
            )
            chunks.append(pos.tobytes() + idx.tobytes())
        head = json.dumps({"pieces": pieces}, ensure_ascii=False).encode("utf-8")
        head += b" " * (
            -len(head) % 4
        )  # alinea a 4 bytes: el cliente crea Float32Array/Uint32Array sobre el búfer
        blob = struct.pack("<I", len(head)) + head + b"".join(chunks)
        self._GEO_CACHE.clear()
        self._GEO_CACHE[key] = blob
        return blob

    def model_options_info(self, pid: str) -> dict[str, Any]:
        from src.project.model import model_options_of

        return model_options_of(self._project(pid))

    def model_options_save(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Guarda ``part_animations`` en ``model_options.json`` (conserva ``normal_maps``)."""
        from src.project.model import MODEL_OPTIONS_FILE, model_options_of

        project = self._project(pid)
        cur = model_options_of(project)
        new = _parse_model_options(
            {
                "normal_maps": cur["normal_maps"],
                "part_animations": body.get("part_animations"),
            }
        )
        self._idle(pid, "guardar las animaciones")
        ensure_safe_output_path(project.root / MODEL_OPTIONS_FILE).write_text(
            json.dumps(new, indent=1, ensure_ascii=False) + chr(10), encoding="utf-8"
        )
        return new

    def steering_info(self, pid: str) -> dict[str, Any]:
        """Materiales del KN5 (los más cercanos a los ojos primero), la elección guardada y lo que detecta
        el conversor ahora mismo."""
        from src.ac.steer_detect import detect_steering_wheel, list_materials
        from src.project.model import wheel_override_of

        kn5, eyes = self._wheel_source(pid)
        try:
            auto = detect_steering_wheel(kn5, eyes)["rim_material_token"]
            auto_error = None
        except ValueError as exc:
            auto, auto_error = None, str(exc)
        saved = wheel_override_of(self._project(pid))
        return {
            "project": pid,
            "enabled": saved is not None,
            "materials": list_materials(kn5, eyes),
            "saved": saved.get("rim_material") if saved else None,
            "auto": auto,
            "auto_error": auto_error,
        }

    def steering_save(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """``action``: ``detect`` (solo detecta, no guarda), ``save`` (guarda ``rim_material``) o ``clear``
        (vuelve a la detección automática). Cambiar la elección cambia la clave de la etapa del modelo: la
        próxima exportación lo reconstruye."""
        from src.ac.steer_detect import detect_steering_wheel, list_materials
        from src.project.model import WHEEL_FILE

        action = body.get("action")
        if action == "detect":
            kn5, eyes = self._wheel_source(pid)
            try:
                w = detect_steering_wheel(kn5, eyes)
            except ValueError as exc:
                raise WorkspaceError(f"no se detecta el volante: {exc}") from None
            return {
                "rim_material": w["rim_material_token"],
                "detected": w["detected"]["candidates"],
            }
        d = self._idle(pid, "cambiar el volante")
        target = ensure_safe_output_path(d / WHEEL_FILE)
        if (
            action == "enabled"
        ):  # casilla «Volante»: desmarcada = sin volante, sin manos y sin piloto
            if body.get("enabled") is True:
                _write_wheel(target, None, keep=True)
            elif body.get("enabled") is False:
                if body.get("confirm") is not True:
                    raise WorkspaceError(
                        "quitar el volante borra su archivo: confírmalo (confirm: true)"
                    )
                if target.is_file():
                    target.unlink()
            else:
                raise WorkspaceError("enabled: true o false")
            return {"enabled": body["enabled"]}
        if (
            action == "clear"
        ):  # vuelve a la detección automática (el volante sigue activado)
            if target.is_file():
                _write_wheel(target, None)
            return {"saved": None}
        if action != "save":
            raise WorkspaceError("acción: detect, save o clear")
        kn5, eyes = self._wheel_source(pid)
        name = str(body.get("rim_material") or "")
        if name not in {m["material"] for m in list_materials(kn5, eyes)}:
            raise WorkspaceError(f"el material {name!r} no está en el modelo")
        try:  # se comprueba ya que ese material sirve de aro (no esperar a la exportación)
            detect_steering_wheel(kn5, eyes, name)
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from None
        _write_wheel(target, name)
        return {"saved": name}

    # ------------------------------------------------------------------ bancos
    def _bench_state(self, service: Any, kind: str, vd: Path, pc: str | None):
        """``service.status`` + estado visible: pendiente | ejecutándose | cancelado |
        incompleto | completado | fallido (último trabajo de ESTE servidor para el mismo
        directorio de trabajo, mientras no haya medida)."""
        st = service.status(kind, vd, pc)
        last = next(
            (
                j
                for j in reversed(list(self.jobs.values()))
                if j.project == "bench" and j.meta.get("work") == st["work"]
            ),
            None,
        )
        if st["state"] != "completado" and last is not None:
            st["state"] = {
                "en_curso": "ejecutándose",
                "cancelada": "cancelado",
                "fallida": "fallido",
            }.get(last.status, st["state"])
            if last.status == "fallida":
                st["error"] = last.error
        return st

    def bench_info(self, path: str, pc: str | None = None) -> dict[str, Any]:
        """Estado de los bancos de BeamNG de un vehículo (G2.46): para cada tipo, si ya
        hay medida de ESTE origen y qué sesiones de un trabajo anterior quedan hechas
        (recuperables). Solo lectura."""
        from src.bench import service

        from . import diagnostics

        _, vd, vehicle, configs = diagnostics.find_source(path)
        pc = pc or (configs[0] if len(configs) == 1 else None)
        if pc not in configs:
            raise WorkspaceError(
                f"elige una configuración de la carpeta: {', '.join(configs)}"
            )
        return {
            "vehicle": vehicle,
            "pc": pc,
            "benches": [self._bench_state(service, k, vd, pc) for k in service.KINDS],
            "beamng_running": service.tyre_runner.beamng_running(),
            # tarea de banco en curso de ESTE servidor (recuperable tras recargar la página)
            "job": next(
                (
                    j.public()
                    for j in reversed(list(self.jobs.values()))
                    if j.project == "bench" and j.status == "en_curso"
                ),
                None,
            ),
        }

    def bench_start(self, body: dict[str, Any]) -> dict[str, Any]:
        """Lanza (o REANUDA) un banco de BeamNG como tarea. Reutiliza ``src.bench.service``
        (la misma lógica del CLI). Una sola tarea de banco a la vez; el CLI y la
        interfaz comparten el bloqueo ``output/bench_jobs/.lock``."""
        from src.bench import service
        from src.bench.tyre_runner import BenchCancelled, BenchError

        from . import diagnostics

        kind = str(body.get("kind") or "")
        if kind not in service.KINDS:
            raise WorkspaceError(f"banco desconocido {kind!r}: {sorted(service.KINDS)}")
        _, vd, vehicle, configs = diagnostics.find_source(body.get("path", ""))
        pc = body.get("pc") or (configs[0] if len(configs) == 1 else None)
        if pc not in configs:
            raise WorkspaceError(
                f"elige una configuración de la carpeta: {', '.join(configs)}"
            )
        if service.measurement(kind, vd, pc) is not None:
            raise WorkspaceError("este origen ya tiene esa medida: no se repite")
        if kind == "aero" and not service.uses_triangle_aero(vd, pc):
            raise WorkspaceError(
                "este vehículo tiene aerodinámica analítica (cefaero): no se mide"
            )
        label = str(body.get("label") or "").strip() or None
        if label and not NAME.match(label):
            raise WorkspaceError("etiqueta: letras, dígitos, «_», «.» o «-» (máx. 64)")

        def fn(log):
            job = next(
                j
                for j in reversed(list(self.jobs.values()))
                if j.project == "bench" and j.status == "en_curso"
            )
            log(f"banco {kind} de {vehicle} ({pc})")
            try:
                rec = service.run(
                    kind,
                    vd,
                    pc,
                    label=label,
                    beamng_root=service.default_beamng_root(),
                    log=log,
                    cancel=job.cancel.is_set,
                    progress=lambda p: job.progress.update(p),
                )
            except BenchCancelled:
                raise Cancelled from None
            except BenchError as exc:
                raise WorkspaceError(str(exc)) from None
            return {"kind": kind, "measurement": rec["_target"]}

        return self._start(
            "bench",
            f"banco {kind}",
            fn,
            meta={"bench": kind, "work": str(service.work_dir(kind, vd, pc))},
        )

    def open_builds(
        self, jid: str | None = None, opener: Callable[[str], Any] | None = None
    ):
        """Abre ``builds/`` (o la build recién promovida de la tarea ``jid``)."""
        from src.safe_paths import BUILDS_DIR

        b = ((self.job(jid).get("result") or {}).get("build") or {}) if jid else {}
        target = Path(b["path"]) if b.get("promoted") else BUILDS_DIR
        if not target.is_dir():
            raise WorkspaceError("todavía no hay builds promovidas")
        (opener or getattr(os, "startfile"))(str(target))
        return {"opened": str(target)}

    def open_output(self, jid: str, opener: Callable[[str], Any] | None = None):
        """Abre la carpeta de salida de una conversión terminada (clic explícito)."""
        out = (self.job(jid).get("result") or {}).get("output")
        if not out or not Path(out).is_dir():
            raise WorkspaceError("la tarea no tiene carpeta de salida")
        (opener or getattr(os, "startfile"))(out)
        return {"opened": out}

    # ----------------------------------------------------------------- resultados
    def exports(self, pid: str) -> list[dict[str, Any]]:
        d = self._dir(pid) / "exports"
        out = []
        for a in sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []:
            man = a / "manifest.json"
            if not man.is_file():
                out.append(
                    {"attempt": a.name, "verdict": "incompleto", "complete": False}
                )
                continue
            m = _read_json(man)
            att = a / "attempt.json"
            out.append(
                {
                    "attempt": a.name,
                    "car_id": m["car_id"],
                    "verdict": m["verdict"],
                    "errors": m["errors"],
                    "warnings": m["warnings"],
                    "distributable": m.get("distributable", True),
                    "tested_in_game": m.get("tested_in_game", False),
                    "created": (
                        _read_json(att).get("created") if att.is_file() else None
                    ),
                    "complete": True,
                }
            )
        return out

    def _attempt(self, pid: str, attempt: str) -> Path:
        if not re.match(r"^\d{3}$", str(attempt)):
            raise WorkspaceError("intento inválido")
        a = self._dir(pid) / "exports" / attempt
        if not (a / "manifest.json").is_file():
            raise WorkspaceError(f"el intento {attempt} no tiene manifiesto")
        return a

    def export_detail(self, pid: str, attempt: str) -> dict[str, Any]:
        from src.project.export import inspect_export

        a = self._attempt(pid, attempt)
        return {
            "attempt": attempt,
            "path": str(a),
            "manifest": _read_json(a / "manifest.json"),
            "report": _read_json(a / "export_report.json"),
            "inspect": inspect_export(a),
            "validation": "automática (estructura, referencias, hashes); "
            "NO es una prueba en el juego",
        }

    def read_file(self, pid: str, rel: str) -> dict[str, Any]:
        d = self._dir(pid).resolve()
        p = (d / rel).resolve()
        if not p.is_relative_to(d) or not p.is_file():
            raise WorkspaceError("archivo fuera del proyecto o inexistente")
        if p.suffix.lower() not in READABLE:
            raise WorkspaceError(f"tipo no legible en la interfaz: {p.suffix}")
        if p.stat().st_size > MAX_READ:
            raise WorkspaceError("archivo demasiado grande para mostrarlo")
        return {"path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")}

    def open_path(self, pid: str, rel: str, opener: Callable[[str], Any] | None = None):
        """Abre una carpeta o archivo del proyecto (solo tras un clic explícito)."""
        d = self._dir(pid).resolve()
        p = (d / rel).resolve()
        if not p.is_relative_to(d) or not p.exists():
            raise WorkspaceError("ruta fuera del proyecto o inexistente")
        (opener or getattr(os, "startfile"))(str(p))
        return {"opened": str(p)}

    def precheck(self, pid: str) -> dict[str, Any]:
        """Bloqueos de los INI resueltos clasificados como los clasificaría la
        exportación (error o advertencia), sin exportar."""
        d = self._dir(pid)
        run = self._last_run(d)
        key = (run or {}).get("stages", {}).get("resolved_ini", {}).get("key")
        trace = d / "stages" / "resolved_ini" / str(key) / "trace.json"
        if not key or not trace.is_file():
            return {"available": False, "why": "ejecuta las etapas primero"}
        from src.attrs import schema as S
        from src.attrs.store import Catalog
        from src.project.export import _blocker_severity, sctm_targets

        project = self._project(pid)
        cat = Catalog.load(project.catalog_path, project.root)
        attrs = {k: S._to_plain(a) for k, a in cat.attributes.items()}
        t = _read_json(trace)
        rows = []
        sctm = sctm_targets(t["keys"])
        for b in t["blockers"]:
            sev = (
                "error"
                if b["kind"] in ("archivo", "validación")
                else _blocker_severity(b, attrs, sctm)
            )
            rows.append({**b, "severity": sev})
        return {
            "available": True,
            "keys": len(t["keys"]),
            "files": t.get("files", []),
            "errors": [r for r in rows if r["severity"] == "error"],
            "warnings": [r for r in rows if r["severity"] == "warning"],
            "by_epistemic": _count(k["epistemic"] for k in t["keys"]),
        }

    # ----------------------------------------------------------------- escena 3D
    def scene(self, pid: str, attempt: str) -> dict[str, Any]:
        """Datos esquemáticos del paquete exportado en ejes AC (m): ruedas, caja del
        collider, ojos del piloto y cámaras. Solo lectura."""
        from src.ac.car_validate import read_ini_ac
        from src.ac.kn5_inspect import read_kn5

        a = self._attempt(pid, attempt)
        m = _read_json(a / "manifest.json")
        car = a / m["car_id"]
        kn5 = car / f"{m['car_id']}.kn5"
        if not kn5.is_file():
            raise WorkspaceError("el intento no tiene KN5")
        wheels = {
            n.name: list(n.translation)
            for n in read_kn5(kn5, keep_vertices=False).nodes
            if n.name.startswith("WHEEL_") and n.type == 1
        }
        box = None
        if (car / "collider.kn5").is_file():
            k = read_kn5(car / "collider.kn5")
            pts = [p for n in k.nodes if n.type == 2 for p in k.mesh_world_vertices(n)]
            if pts:
                box = {
                    "min": [min(p[i] for p in pts) for i in range(3)],
                    "max": [max(p[i] for p in pts) for i in range(3)],
                }
        ini = (
            read_ini_ac(car / "data" / "car.ini")
            if (car / "data" / "car.ini").is_file()
            else None
        )
        g = ini.sections.get("GRAPHICS", {}) if ini else {}
        basic = ini.sections.get("BASIC", {}) if ini else {}

        def vec(text: str | None) -> list[float] | None:
            try:
                return (
                    [float(x) for x in text.split(";")[0].split(",")] if text else None
                )
            except ValueError:
                return None

        offset = vec(basic.get("GRAPHICS_OFFSET")) or [0.0, 0.0, 0.0]
        tyres = (
            read_ini_ac(car / "data" / "tyres.ini")
            if (car / "data" / "tyres.ini").is_file()
            else None
        )

        def tyre(sec: str, key: str) -> float | None:
            try:
                return float(tyres.sections[sec][key]) if tyres else None
            except (KeyError, ValueError):
                return None

        cams = {}
        for key in ("BONNET_CAMERA_POS", "BUMPER_CAMERA_POS"):
            v = vec(g.get(key))
            # marco del cuerpo físico: KN5 = valor − GRAPHICS_OFFSET (CLAUDE.md)
            cams[key] = [v[i] - offset[i] for i in range(3)] if v else None
        return {
            "car_id": m["car_id"],
            "attempt": attempt,
            "axes": "AC: +X izquierda, +Y arriba, +Z adelante (m, marco del KN5)",
            "wheels": wheels,
            "tyre": {
                "front": {
                    "radius": tyre("FRONT", "RADIUS"),
                    "width": tyre("FRONT", "WIDTH"),
                },
                "rear": {
                    "radius": tyre("REAR", "RADIUS"),
                    "width": tyre("REAR", "WIDTH"),
                },
            },
            "collider_box": box,
            "driver_eyes": vec(g.get("DRIVEREYES")),
            "cameras": cams,
            "graphics_offset": offset,
            "driver_pose": (car / "driver_base_pos.knh").is_file(),
        }

    # ------------------------------------------------ editor de cámaras y piloto (G2.32)
    def _model_stage(self, pid: str) -> Path:
        """Etapa ``model`` más reciente del proyecto (KN5 final, marco del KN5)."""
        d = self._dir(pid) / "stages" / "model"
        found = sorted(
            (p for p in d.glob("*/model.kn5")), key=lambda p: p.stat().st_mtime
        )
        if not found:
            raise WorkspaceError(
                "el proyecto aún no tiene modelo 3D: ejecuta «con modelo» o convierte antes"
            )
        return found[-1].parent

    def _graphics_offset_without_export(self, pid: str, kn5: Path) -> tuple[float, ...]:
        """GRAPHICS_OFFSET que escribiría la exportación, sin haber exportado: el de
        ``car.ini`` de la etapa ``resolved_ini`` vigente o, si falta, el derivado de los
        nulos de rueda del KN5 y ``suspensions.ini`` (misma regla que ``export._graphics_offset``).
        """
        from src.ac import graphics_offset as gfx
        from src.ac.car_validate import parse_ini_ac

        d = self._dir(pid)
        key = (
            (self._last_run(d) or {}).get("stages", {}).get("resolved_ini") or {}
        ).get("key")
        data = d / "stages" / "resolved_ini" / str(key) / "data"
        if not key or not (data / "suspensions.ini").is_file():
            raise WorkspaceError(
                "ejecuta las etapas primero (faltan los INI resueltos)"
            )
        m = None
        car_txt = (
            (data / "car.ini").read_text("utf-8", "replace")
            if (data / "car.ini").is_file()
            else ""
        )
        pitch = re.search(r"^GRAPHICS_PITCH_ROTATION\s*=\s*([-\d.eE]+)", car_txt, re.M)
        if pitch and float(pitch.group(1)) != 0.0:
            raise WorkspaceError(
                "GRAPHICS_PITCH_ROTATION ≠ 0: la posición del modelo no se puede derivar sin exportar"
            )
        if (data / "car.ini").is_file():
            m = re.search(
                r"^GRAPHICS_OFFSET\s*=\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)",
                (data / "car.ini").read_text("utf-8", "replace"),
                re.M,
            )
        if m:
            return tuple(float(g) for g in m.groups())
        try:
            return tuple(
                gfx.derive(
                    kn5,
                    parse_ini_ac(
                        (data / "suspensions.ini").read_text("utf-8", "replace")
                    ),
                ).offset
            )
        except gfx.GraphicsOffsetError as exc:
            raise WorkspaceError(f"GRAPHICS_OFFSET no derivable: {exc}") from None

    @_cockpit_errors
    def cockpit(self, pid: str) -> dict[str, Any]:
        """Valores originales (los calcula el exportador), ajustes vigentes y versiones."""
        from src.ac import cameras, driver_seat
        from src.ac.kn5_inspect import read_kn5
        from src.project import cockpit_edits as CE
        from src.project.export import default_ac_root

        proj = self._project(pid)
        st = self._model_stage(pid)
        model = _read_json(st / "model.json")
        kn5 = st / "model.kn5"
        eyes_o = (model.get("drivereyes") or {}).get("eyes_ac")
        eyes, einfo = driver_seat.resolve_eyes(
            kn5, tuple(float(c) for c in eyes_o) if eyes_o else None
        )
        # GRAPHICS_OFFSET real = el del último intento exportado (la regla del parachoques lo usa)
        go = None
        for a in sorted(
            (self._dir(pid) / "exports").glob("[0-9][0-9][0-9]"), reverse=True
        ):
            for ini in a.glob("*/data/car.ini"):
                m = re.search(
                    r"^GRAPHICS_OFFSET\s*=\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)",
                    ini.read_text("utf-8", "replace"),
                    re.M,
                )
                if m:
                    go = tuple(float(g) for g in m.groups())
                    break
            if go:
                break
        if go is None:
            go = self._graphics_offset_without_export(pid, kn5)
        cams = cameras.cameras_from_kn5(kn5, eyes, go)
        roof = driver_seat.roof_y_at(kn5, eyes[0], eyes[2])
        prof = (model.get("profile") or {}).get("path")
        if prof:
            from src.safe_paths import PROJECT_ROOT

            hips = tuple(
                json.loads((PROJECT_ROOT / prof).read_text("utf-8"))["driver"][
                    "hips_target"
                ]
            )
            rule = "perfil del vehículo (validado en el juego)"
        elif (model.get("steering_wheel") or {}).get(
            "rim_material"
        ) and default_ac_root():
            # con volante montado el exportador (y el editor de animación) colocan las caderas respecto al
            # volante, no a los ojos: misma regla aquí para que las dos vistas muestren al mismo piloto
            c = self._anim_context(pid)
            hips = self._anim_hips(
                {**c, "hips_edit": None}, c["profile"], default_ac_root()
            )
            rule = "relación con el volante del Ibiza (la del piloto exportado)"
        else:
            hips, rep = driver_seat.default_hips(eyes, driver_seat.roof_function(kn5))
            rule = rep["rule"] + (
                f"; bajadas {rep['lowered_m']} m por holgura del techo"
                if rep["lowered_m"]
                else ""
            )
        key = CE.project_key(proj)
        wheels = {
            n.name: list(n.translation)
            for n in read_kn5(kn5, keep_vertices=False).nodes
            if n.name.startswith("WHEEL_") and n.type == 1
        }
        return {
            "project": pid,
            "key": key,
            # el modelo en caché se generó con volante montado (si no, el esqueleto/manos no existen: hay que regenerarlo)
            "model_has_wheel": bool(
                (model.get("steering_wheel") or {}).get("rim_material")
            ),
            "frame": "marco del KN5: +X izquierda, +Y arriba, +Z adelante (m); el suelo es y = 0",
            "orientation": "AC solo lee posiciones de estas cámaras (no hay orientación en "
            "car.ini ni dash_cam.ini): las flechas indican +Z, no son editables.",
            "original": {
                "driver_eyes": list(eyes),
                "bonnet": list(cams.bonnet_kn5),
                "bumper": list(cams.bumper_kn5),
                "hips": list(hips),
                "fuel_tank": _default_fuel_tank(wheels),
            },
            "hips_rule": rule,
            "eyes_note": einfo.get("reason")
            and (einfo["reason"] + ". " + einfo["rule"]),
            "items": CE.current(key)["items"],
            "version": CE.current(key)["version"],
            "versions": [
                {k: v[k] for k in ("version", "created", "note")}
                for v in CE.versions(key)
            ],
            "roof_y": roof,
            "head_bone_above_hips": driver_seat.HEAD_BONE_ABOVE_HIPS,
            "validated_roof_minus_head": driver_seat.VALIDATED_ROOF_MINUS_HEAD,
            "wheels": wheels,
        }

    def _anim_context(self, pid: str) -> dict[str, Any]:
        """Modelo, perfil (propio o genérico con los valores del Ibiza), caderas por defecto y ojos."""
        from src.project import cockpit_edits as CE
        from src.project import local_driver as LD
        from src.safe_paths import PROJECT_ROOT

        proj = self._project(pid)
        st = self._model_stage(pid)
        model = _read_json(st / "model.json")
        steer = model.get("steering_wheel")
        if not steer:
            raise WorkspaceError(
                "el modelo no tiene volante montado (sin manos que animar): elige el aro en la "
                "ventana «Volante» y vuelve a generar el modelo"
            )
        prof_path = (model.get("profile") or {}).get("path")
        if prof_path:
            profile = json.loads((PROJECT_ROOT / prof_path).read_text("utf-8"))
            hips = tuple(profile["driver"]["hips_target"])
            origin = "perfil del vehículo"
        else:
            profile = LD.generic_profile(steer)
            hips = LD.hips_from_wheel(steer)
            origin = "perfil genérico (constantes del Ibiza)"
        key = CE.project_key(proj)
        hips_edit = CE.current(key)["items"].get("hips")
        eyes_o = (model.get("drivereyes") or {}).get("eyes_ac")
        return {
            "proj": proj,
            "key": key,
            "model": model,
            "model_dir": st,
            "profile": profile,
            "hips_default": hips,
            "hips_edit": tuple(hips_edit) if hips_edit else None,
            "origin": origin,
            "eyes": eyes_o,
        }

    def _anim_hips(
        self, c: dict[str, Any], profile: dict[str, Any], root: Path
    ) -> tuple[float, ...]:
        """Caderas de la vista previa. Editadas: tal cual. Perfil propio: las del perfil. Perfil genérico: como
        el exportador, la posición relativa al volante del Ibiza y, si el IK no alcanza este aro, la más cercana
        que sí (±15 cm en y/z); si ninguna vale se devuelven las por defecto y el error sale en la vista previa.
        """
        from src.project import anim_preview as AP

        if c["hips_edit"] or not profile.get("generic"):
            return tuple(c["hips_edit"] or c["hips_default"])
        h0 = c["hips_default"]
        # la búsqueda es costosa (hasta 169 vistas previas) y solo depende del modelo y del perfil
        memo_key = (
            str(c["model_dir"]),
            str(root),
            json.dumps(profile, sort_keys=True, default=str),
        )
        memo = self.__dict__.setdefault("_hips_memo", {})
        if memo_key in memo:
            return memo[memo_key]
        steps = [i * 0.025 for i in range(-6, 7)]
        for dy, dz in sorted(
            ((a, b) for a in steps for b in steps),
            key=lambda ab: ab[0] ** 2 + ab[1] ** 2,
        ):
            h = (h0[0], round(h0[1] + dy, 4), round(h0[2] + dz, 4))
            try:
                AP.preview(c["model_dir"], c["model"], profile, root, h, 8)
                memo[memo_key] = h
                return h
            except (RuntimeError, ValueError):
                continue
        memo[memo_key] = tuple(h0)
        return memo[memo_key]

    @_cockpit_errors
    def anim(self, pid: str) -> dict[str, Any]:
        from src.project import anim_preview as AP

        from src.project.export import default_ac_root

        c = self._anim_context(pid)
        prof = c["profile"]
        root = default_ac_root()
        hips = (
            self._anim_hips(c, prof, root)
            if root
            else c["hips_edit"] or c["hips_default"]
        )
        return {
            "project": pid,
            "key": c["key"],
            "profile_origin": c["origin"],
            "frame": "marco del KN5: +X izquierda, +Y arriba, +Z adelante (m)",
            "defaults": {
                "left": prof["hands"]["left"],
                "right": prof["hands"]["right"],
                "lift_m": prof["hands"]["lift_m"],
                "knuckle_target_m": prof["driver"]["knuckle_target_m"],
            },
            "limits": {
                "cycle": AP.CYCLE_LIMITS,
                "lift_m": AP.LIFT_LIMITS,
                "knuckle_target_m": AP.KNUCKLE_LIMITS,
            },
            "edits": AP.current(c["key"])["edits"],
            "version": AP.current(c["key"])["version"],
            "versions": [
                {k: v[k] for k in ("version", "created", "note")}
                for v in AP.versions(c["key"])
            ],
            "hips": list(hips),
            "hips_is_edited": c["hips_edit"] is not None,
            "eyes": c["eyes"],
        }

    @_cockpit_errors
    def anim_preview(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        """Esqueleto del piloto por fotograma con los ajustes del cuerpo (sin guardar nada)."""
        from src.project import anim_preview as AP
        from src.project.export import default_ac_root

        c = self._anim_context(pid)
        root = default_ac_root()
        if root is None:
            raise WorkspaceError(
                "no se encuentra la instalación de AC (solo lectura) para la pose base"
            )
        profile = AP.apply_override(c["profile"], body.get("edits") or {})
        hips = body.get("hips")
        if hips is not None:
            if not (
                isinstance(hips, list)
                and len(hips) == 3
                and all(
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and abs(v) < 6
                    for v in hips
                )
            ):
                raise WorkspaceError("hips: se esperan 3 números en metros")
            hips = tuple(float(v) for v in hips)
        else:
            hips = self._anim_hips(c, profile, root)
        try:
            return AP.preview(
                c["model_dir"],
                c["model"],
                profile,
                root,
                hips,
                int(body.get("frames") or 36),
            )
        except RuntimeError as exc:  # DriverPoseError: caderas o aro inalcanzables
            raise WorkspaceError(
                f"pose no alcanzable con estos valores: {exc}"
            ) from None

    @_cockpit_errors
    def anim_save(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        from src.project import anim_preview as AP

        key = self._anim_context(pid)["key"]
        if body.get("key") is not None and body["key"] != key:
            raise WorkspaceError(
                "los ajustes pertenecen a otro vehículo: recarga el editor"
            )
        bv = body.get("base_version")
        if bv is not None and bv != AP.current(key)["version"]:
            raise WorkspaceError(
                f"los ajustes cambiaron (versión {AP.current(key)['version']}): recarga"
            )
        try:
            rec = AP.save(
                key,
                {} if body.get("reset") else body.get("edits") or {},
                (
                    "restaurado"
                    if body.get("reset")
                    else str(body.get("note") or "")[:200]
                ),
            )
        except AP.AnimError as exc:
            raise WorkspaceError(str(exc)) from None
        return {"version": rec["version"], "edits": rec["edits"]}

    @_cockpit_errors
    def cockpit_geometry(self, pid: str) -> bytes:
        """Triángulos reales del KN5 (sin piloto), marco del KN5. Binario little-endian:
        ``u32 nv, u32 ni`` + ``f32[nv*3]`` + ``u32[ni]``."""
        from src.ac.kn5_inspect import read_kn5

        k = read_kn5(self._model_stage(pid) / "model.kn5")
        return _mesh_blob(k, lambda n: "DRIVER" not in n.name.upper())

    def steering_geometry(self, pid: str, material: str = "") -> bytes:
        """Como ``cockpit_geometry`` pero del KN5 con el que se elige el volante; con ``material``,
        solo las mallas de ese material (para ver qué objeto es)."""
        from src.ac.kn5_inspect import read_kn5
        from src.ac.kn5_materials import split_kn5

        kn5, _ = self._wheel_source(pid)
        k = read_kn5(kn5)
        if not material:
            return _mesh_blob(k, lambda n: True)
        mats = [m.name for m in split_kn5(kn5.read_bytes()).materials]
        if material not in mats:
            raise WorkspaceError(f"el material {material!r} no está en el modelo")
        return _mesh_blob(k, lambda n: mats[n.material] == material)

    @_cockpit_errors
    def cockpit_save(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        from src.project import cockpit_edits as CE

        key = CE.project_key(self._project(pid))
        # el editor devuelve la clave y la versión que leyó: así una petición no escribe en otro
        # vehículo ni pisa una versión más nueva (otra ventana)
        if body.get("key") is not None and body["key"] != key:
            raise WorkspaceError(
                "los ajustes pertenecen a otro vehículo: recarga el editor"
            )
        bv = body.get("base_version")
        if bv is not None and bv != CE.current(key)["version"]:
            raise WorkspaceError(
                f"los ajustes cambiaron (versión {CE.current(key)['version']}, el editor tenía {bv}): recarga"
            )
        try:
            if body.get("reset"):
                rec = CE.save(key, {}, "restaurado a los valores originales")
            else:
                rec = CE.save(
                    key, body.get("items") or {}, str(body.get("note") or "")[:200]
                )
        except CE.EditsError as exc:
            raise WorkspaceError(str(exc)) from None
        return {"version": rec["version"], "items": rec["items"]}

    # -------------------------------------------------------- editor de atributos
    def review(self, pid: str):
        """``ReviewService`` del proyecto (se construye una vez; tarda segundos)."""
        if pid in self._reviews:
            return self._reviews[pid]
        from src.app.service import ReviewService
        from src.pipeline.physics_preview import (
            load_vehicle_dir,
            load_vehicle_zip,
            preview_physics,
        )

        project = self._project(pid)
        s = project.source
        src = (
            load_vehicle_dir(Path(s.path), s.pc)
            if s.kind == "dir"
            else load_vehicle_zip(
                Path(s.path), Path(s.common_zip) if s.common_zip else None, s.pc
            )
        )
        svc = ReviewService(
            preview_physics(src, project.driver_mass_kg),
            project.root,
            driver_mass_kg=project.driver_mass_kg,
        )
        self._reviews[pid] = svc
        return svc


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items()))
