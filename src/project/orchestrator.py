"""Orquestador de etapas con caché por huella, reanudación y salidas inmutables.

Cada etapa declara: nombre, etapas previas, módulos de código de los que depende y una
función ``run(ctx, out_dir) -> summary``. Su CLAVE es el sha256 de:

* la versión de la etapa y el sha256 de sus módulos de código;
* la huella del origen (carpeta/zip/``common.zip``/``.pc``) y de las opciones;
* la huella de reglas (catálogo + decisiones del proyecto) si la etapa la usa;
* las claves de las etapas previas.

Resultado en ``stages/<etapa>/<clave16>/`` con ``stage.json``. Escritura atómica: se
escribe en ``<clave16>.tmp-<pid>`` y se renombra al terminar; un fallo se renombra a
``<clave16>.failed-<n>`` (se conserva, no cuenta como caché). Nunca se sobrescribe una
carpeta existente. Si la clave ya tiene un resultado correcto, la etapa no se repite.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.safe_paths import PROJECT_ROOT, ensure_safe_output_path

from .manifest import (
    FingerprintCache,
    Project,
    ProjectError,
    reference_fingerprint,
    rules_fingerprint,
    source_fingerprint,
)


@dataclass(frozen=True)
class Stage:
    name: str
    version: int
    run: Callable[["RunContext", Path], dict[str, Any]]
    after: tuple[str, ...] = ()
    code: tuple[str, ...] = ()  # rutas relativas al proyecto de código
    # Dependencias opcionales: la etapa se ejecuta aunque fallen (p. ej. el informe,
    # que debe existir precisamente cuando algo falla) y recibe su estado.
    optional: tuple[str, ...] = ()
    uses_rules: bool = False
    uses_reference: bool = (
        False  # la carpeta de referencia del proyecto entra en la clave
    )
    description: str = ""
    # huella de entradas externas (herramientas, perfiles): entra en la clave
    extra_inputs: Callable[[Project], dict[str, Any]] | None = None


@dataclass
class StageResult:
    name: str
    key: str
    status: str  # "cached" | "ok" | "failed" | "skipped"
    dir: Path | None
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class RunContext:
    """Estado compartido de una ejecución: proyecto y objetos caros (una vez)."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.results: dict[str, StageResult] = {}
        self._memo: dict[str, Any] = {}

    def memo(self, name: str, fn: Callable[[], Any]) -> Any:
        if name not in self._memo:
            self._memo[name] = fn()
        return self._memo[name]

    def stage_dir(self, name: str) -> Path:
        r = self.results.get(name)
        if r is None or r.dir is None or r.status not in ("ok", "cached"):
            raise ProjectError(f"la etapa {name!r} no tiene un resultado válido")
        return r.dir

    def optional_dir(self, name: str) -> Path | None:
        """Carpeta de una dependencia opcional, o ``None`` si no tiene resultado."""
        r = self.results.get(name)
        ok = r is not None and r.dir is not None and r.status in ("ok", "cached")
        return r.dir if ok else None

    def failure(self, name: str) -> str | None:
        r = self.results.get(name)
        if r is None:
            return "no ejecutada"
        return None if r.status in ("ok", "cached") else f"{r.status}: {r.error}"


def _code_hash(paths: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for rel in sorted(paths):
        p = PROJECT_ROOT / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.is_file() else b"<ausente>")
    return h.hexdigest()


def stage_key(stage: Stage, inputs: dict[str, Any]) -> str:
    blob = json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def _files_sha(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        if p.name == "stage.json":
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _next_failed(base: Path) -> Path:
    n = 1
    while (base.parent / f"{base.name}.failed-{n}").exists():
        n += 1
    return base.parent / f"{base.name}.failed-{n}"


def run_project(
    project: Project,
    stages: tuple[Stage, ...],
    only: tuple[str, ...] | None = None,
    log: Callable[[str], None] = print,
) -> list[StageResult]:
    """Ejecuta las etapas en orden; reutiliza las que ya tienen resultado con su clave.

    ``only``: ejecutar solo esas etapas y las previas que necesiten. Una etapa cuyas
    previas fallaron se marca ``skipped`` (no se ejecuta con datos incompletos).
    """
    names = [s.name for s in stages]
    if only:
        unknown = [n for n in only if n not in names]
        if unknown:
            raise ProjectError(f"etapas desconocidas: {unknown}")
        wanted: set[str] = set()
        by_name = {s.name: s for s in stages}

        def need(n: str) -> None:
            if n not in wanted:
                wanted.add(n)
                for a in by_name[n].after + by_name[n].optional:
                    need(a)

        for n in only:
            need(n)
    else:
        wanted = set(names)

    fp = FingerprintCache(project)
    src_fp = source_fingerprint(project, fp)
    rules_fp: dict[str, str] | None = None
    ctx = RunContext(project)
    started = _dt.datetime.now().isoformat(timespec="seconds")
    results: list[StageResult] = []
    for stage in stages:
        if stage.name not in wanted:
            continue
        failed_deps = [
            a
            for a in stage.after
            if ctx.results.get(a) is None
            or ctx.results[a].status not in ("ok", "cached")
        ]
        inputs: dict[str, Any] = {
            "stage": stage.name,
            "version": stage.version,
            "code": _code_hash(stage.code),
            "source": src_fp,
            "options": {"driver_mass_kg": project.driver_mass_kg},
        }
        if stage.uses_rules:
            if rules_fp is None:
                rules_fp = rules_fingerprint(project, fp)
            inputs["rules"] = rules_fp
        if stage.uses_reference:
            inputs["reference"] = reference_fingerprint(project, fp)
        if stage.extra_inputs is not None:
            inputs["extra"] = stage.extra_inputs(project)
        if failed_deps:
            res = StageResult(
                stage.name,
                "",
                "skipped",
                None,
                error=f"etapas previas sin resultado: {failed_deps}",
            )
            ctx.results[stage.name] = res
            results.append(res)
            log(f"[omitida] {stage.name}: {res.error}")
            continue
        inputs["after"] = {a: ctx.results[a].key for a in stage.after}
        # el estado de las opcionales entra en la clave: si una se arregla, se rehace
        inputs["optional"] = {
            o: (
                ctx.results[o].key
                if ctx.failure(o) is None
                else f"sin resultado ({ctx.failure(o)})"
            )
            for o in stage.optional
            if o in ctx.results
        }
        key = stage_key(stage, inputs)
        base = ensure_safe_output_path(project.root / "stages" / stage.name / key[:16])
        meta_path = base / "stage.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            res = StageResult(stage.name, key, "cached", base, meta.get("summary", {}))
            ctx.results[stage.name] = res
            results.append(res)
            log(f"[caché]   {stage.name} ({key[:16]})")
            continue
        # aviso de inicio (la etapa del modelo tarda minutos): antes de crear nada, así cancelar aquí es seguro
        log(f"[en curso] {stage.name}")
        tmp = ensure_safe_output_path(base.parent / f"{base.name}.tmp-{os.getpid()}")
        if tmp.exists():  # resto de una ejecución interrumpida: se aparta, no se borra
            n = 1
            while (base.parent / f"{base.name}.abandonada-{n}").exists():
                n += 1
            os.replace(tmp, base.parent / f"{base.name}.abandonada-{n}")
        tmp.mkdir(parents=True)
        t0 = time.perf_counter()
        meta = {
            "schema": "beam2ac.stage/1",
            "stage": stage.name,
            "key": key,
            "inputs": inputs,
            "description": stage.description,
            "started": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        try:
            summary = stage.run(ctx, tmp)
            status, error = "ok", None
        except Exception as exc:  # se registra y se conserva, nunca se oculta
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            summary, status = {}, "failed"
            error = (
                f"{type(exc).__name__}: {exc} ({Path(tb.filename).name}:{tb.lineno})"
            )
        meta.update(
            status=status,
            error=error,
            summary=summary,
            seconds=round(time.perf_counter() - t0, 3),
            outputs=_files_sha(tmp),
        )
        ensure_safe_output_path(tmp / "stage.json").write_text(
            json.dumps(meta, indent=1, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        final = base if status == "ok" else _next_failed(base)
        os.replace(tmp, final)
        res = StageResult(
            stage.name, key, status, final if status == "ok" else None, summary, error
        )
        ctx.results[stage.name] = res
        results.append(res)
        log(
            f"[{'hecha' if status == 'ok' else 'FALLO'}]  {stage.name} ({key[:16]})"
            + (f": {error}" if error else "")
        )
    fp.save()
    with open(
        ensure_safe_output_path(project.root / "runs.jsonl"), "a", encoding="utf-8"
    ) as f:
        f.write(
            json.dumps(
                {
                    "started": started,
                    "finished": _dt.datetime.now().isoformat(timespec="seconds"),
                    "stages": {
                        r.name: {
                            "status": r.status,
                            "key": r.key[:16],
                            "error": r.error,
                        }
                        for r in results
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return results


def project_status(project: Project) -> dict[str, Any]:
    """Resultados guardados por etapa (correctos y fallidos), sin ejecutar nada."""
    out: dict[str, Any] = {}
    stages_dir = project.root / "stages"
    for d in (
        sorted(p for p in stages_dir.iterdir() if p.is_dir())
        if stages_dir.is_dir()
        else []
    ):
        entries = []
        for r in sorted(p for p in d.iterdir() if p.is_dir()):
            meta_path = r / "stage.json"
            if not meta_path.is_file():
                entries.append({"dir": r.name, "status": "incompleto"})
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            entries.append(
                {
                    "dir": r.name,
                    "status": meta.get("status"),
                    "started": meta.get("started"),
                    "error": meta.get("error"),
                }
            )
        out[d.name] = entries
    return out
