"""Etapas del flujo de conversión, como envoltorios del código existente.

* ``source``: ``physics_preview.load_vehicle_*`` + listado del origen, con el rol y el
  uso de cada archivo (``pipeline.source_inventory``) y las pinturas con su
  diagnóstico (``extract.paints``) → ``source.json``;
* ``physics``: ``physics_preview.preview_physics`` → ``preview.json`` y
  ``drafts/*.ini``/``*.lut``;
* ``attributes``: ``attrs.report.resolve_vehicle`` + ``build_report`` (catálogo +
  decisiones del proyecto) → ``report.json``, ``summary.md``;
* ``geometry``: ``mesh.build.plan_for_vehicle``/``plan_for_zip``
  → ``geometry.json``;
* ``draft``: ``ac.car_validate`` sobre los borradores → ``data/``, ``validation.json``;
* ``report``: agrega lo anterior → ``compatibility.json``, ``compatibility.md``.

Ninguna etapa inventa valores: si un generador no existe o falla, la etapa lo registra.
El borrador de ``draft`` NO es un paquete instalable (sin KN5, interfaz ni skins).
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from src.safe_paths import PROJECT_ROOT, ensure_safe_output_path

from .code_deps import import_closure
from .orchestrator import RunContext, Stage

EXT_GROUPS = {
    ".jbeam": "jbeam",
    ".pc": "configuraciones",
    ".dae": "mallas_dae",
    ".cdae": "mallas_cdae",
    ".dds": "texturas",
    ".png": "texturas",
    ".jpg": "texturas",
    ".lua": "lua",
    ".ogg": "sonido",
    ".wav": "sonido",
    ".sfxblend2": "sonido",
    ".json": "json",
}


def _write_json(path: Path, data: Any) -> None:
    ensure_safe_output_path(path).write_text(
        json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _load_source(ctx: RunContext):
    from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip

    s = ctx.project.source

    def load():
        if s.kind == "dir":
            return load_vehicle_dir(Path(s.path), s.pc)
        return load_vehicle_zip(
            Path(s.path), Path(s.common_zip) if s.common_zip else None, s.pc
        )

    return ctx.memo("source", load)


def _preview(ctx: RunContext):
    from src.pipeline.physics_preview import preview_physics

    return ctx.memo(
        "preview",
        lambda: preview_physics(_load_source(ctx), ctx.project.driver_mass_kg),
    )


def _listing(ctx: RunContext) -> list[str]:
    s = ctx.project.source
    if s.kind == "dir":
        root = Path(s.path)
        return sorted(
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        )
    from src.jbeam import vehicle_dirs_in_zip
    import zipfile

    prefix = vehicle_dirs_in_zip(Path(s.path))[0]
    with zipfile.ZipFile(s.path) as zf:
        return sorted(
            n[len(prefix) :]
            for n in zf.namelist()
            if n.startswith(prefix) and not n.endswith("/")
        )


def _read_member(ctx: RunContext, rel: str) -> bytes | None:
    """Bytes de un archivo del origen (carpeta o zip), o ``None`` si no existe."""
    s = ctx.project.source
    if s.kind == "dir":
        p = Path(s.path) / rel
        return p.read_bytes() if p.is_file() else None
    from src.jbeam import vehicle_dirs_in_zip
    import zipfile

    prefix = vehicle_dirs_in_zip(Path(s.path))[0]
    with zipfile.ZipFile(s.path) as zf:
        try:
            from src.jbeam.sources import read_member_capped

            return read_member_capped(zf, prefix + rel)
        except KeyError:
            return None


def _source_inventory(ctx: RunContext, src: Any, files: list[str]) -> dict[str, Any]:
    """Rol y uso de cada archivo + pinturas (diagnóstico de importación)."""
    from src.extract.paints import read_paints
    from src.jbeam import lax_json
    from src.pipeline.source_inventory import inventory

    root_mats = [f for f in files if f.endswith(".materials.json") and "/" not in f]
    preferred = f"{src.variant}.materials.json"
    mat_name = (
        preferred
        if preferred in root_mats
        else (root_mats[0] if len(root_mats) == 1 else None)
    )
    mats = None
    if mat_name:
        raw = _read_member(ctx, mat_name)
        mats = lax_json.loads(raw.decode("utf-8", "replace")).value if raw else None
    inv = inventory(files, mats if isinstance(mats, dict) else None, mat_name)
    pc_raw = _read_member(ctx, f"{src.variant}.pc")
    pc_data = (
        lax_json.loads(pc_raw.decode("utf-8", "replace")).value if pc_raw else None
    )
    paints = read_paints(
        src.model_info,
        src.info,
        pc_data if isinstance(pc_data, dict) else None,
        _read_member(ctx, "paints.meta"),
        config_info_name=f"info_{src.variant}.json",
        pc_name=f"{src.variant}.pc",
    )
    return {"inventory": inv, "paints": paints.to_json()}


# --- etapas --------------------------------------------------------------------------


def run_source(ctx: RunContext, out: Path) -> dict[str, Any]:
    src = _load_source(ctx)
    files = _listing(ctx)
    groups = Counter(
        EXT_GROUPS.get(PurePosixPath(f).suffix.lower(), "otros") for f in files
    )
    configs = sorted(f for f in files if f.lower().endswith(".pc"))
    materials = sorted(f for f in files if f.lower().endswith("materials.json"))
    data = {
        "vehicle_id": src.vehicle_id,
        "variant": src.variant,
        "description": src.description,
        "input_sha256": src.input_sha256,
        "lua_sha256": src.lua_sha256,
        "info": {
            k: src.info.get(k)
            for k in sorted(src.info)
            if not isinstance(src.info.get(k), (dict, list))
        },
        "model_info": {
            k: src.model_info.get(k)
            for k in sorted(src.model_info)
            if not isinstance(src.model_info.get(k), (dict, list))
        },
        "files_total": len(files),
        "files_by_group": dict(sorted(groups.items())),
        "configurations": configs,
        "materials_files": materials,
        "parts_indexed": len(src.index.parts),
        "index_file_errors": len(src.index.file_errors),
        **_source_inventory(ctx, src, files),
    }
    _write_json(out / "source.json", data)
    return {
        "vehicle": f"{src.vehicle_id}/{src.variant}",
        "files": len(files),
        "configs": len(configs),
        "parts": len(src.index.parts),
        "groups": data["files_by_group"],
        "files_by_status": data["inventory"]["by_status"],
        "unknown_files": len(data["inventory"]["unknown"]),
        "paint_1": (data["paints"]["selection"] or [{}])[0].get("name"),
    }


def run_physics(ctx: RunContext, out: Path) -> dict[str, Any]:
    pv = _preview(ctx)
    drafts = out / "drafts"
    drafts.mkdir()
    for name, text in sorted(pv.rendered.items()):
        ensure_safe_output_path(drafts / name).write_text(text, encoding="utf-8")
    failed = {k: v for k, v in pv.stages.items() if not v.get("ok")}
    data = {
        "ok": pv.ok,
        "stages": pv.stages,
        "rendered": sorted(pv.rendered),
        "engine_peak": (
            {"torque_nm": pv.engine.peak_torque.net, "rpm": pv.engine.peak_torque.rpm}
            if pv.engine is not None
            else None
        ),
        "traction": getattr(pv.drivetrain, "traction", None) if pv.drivetrain else None,
        "mass_kg": getattr(pv.geometry, "total_mass", None) if pv.geometry else None,
    }
    _write_json(out / "preview.json", data)
    return {
        "ok": pv.ok,
        "rendered": sorted(pv.rendered),
        "failed_steps": sorted(failed),
    }


def run_attributes(ctx: RunContext, out: Path) -> dict[str, Any]:
    from src.attrs.report import build_report, markdown_summary, resolve_vehicle
    from src.attrs.store import Catalog

    proj = ctx.project
    catalog = Catalog.load(proj.catalog_path, proj.root)
    pv = _preview(ctx)
    vctx, results = resolve_vehicle(pv, catalog, "ac_physics", proj.driver_mass_kg)
    report = build_report(pv, vctx, results, catalog)
    _write_json(out / "report.json", report)
    ensure_safe_output_path(out / "summary.md").write_text(
        markdown_summary(report), encoding="utf-8"
    )
    by_status = report["summary"]["by_status"]
    cvt_script = _write_cvt_script(vctx, out)
    return {
        "by_status": by_status,
        "cvt_script": cvt_script,
        "targets": len(report["results"]),
        # decisiones del vehículo: todas (catálogo + proyecto) y solo las del proyecto
        "vehicle_decisions": sum(
            1 for d in catalog.decisions if d.vehicle == report["vehicle"]
        ),
        "project_decisions": sum(
            1
            for f in sorted(proj.decisions_dir.glob("*.jsonl"))
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ),
    }


def _write_cvt_script(vctx: Any, out: Path) -> str | None:
    """``cvt_script.lua`` junto al informe si el vehículo tiene una CVT (2026-11-19).
    Devuelve ``None`` sin CVT, ``"ok"`` o el motivo si faltan datos (nunca se rellena).
    """
    from src.ac.cvt_script import CvtScriptError, params_from_facts, render_cvt_script

    try:
        params = params_from_facts({k: f.value for k, f in vctx.facts.items()})
        if params is None:
            return None
    except CvtScriptError as exc:
        return str(exc)
    ensure_safe_output_path(out / "cvt_script.lua").write_text(
        render_cvt_script(params), encoding="utf-8"
    )
    return "ok"


def run_geometry(ctx: RunContext, out: Path) -> dict[str, Any]:
    from src.mesh.build import plan_for_vehicle, plan_for_zip

    s = ctx.project.source
    if s.kind == "dir":
        plan = plan_for_vehicle(Path(s.path), Path(s.path) / s.pc)
    else:
        plan = plan_for_zip(
            Path(s.path), Path(s.common_zip) if s.common_zip else None, pc_name=s.pc
        )
    data = {
        "objects": len(plan.objects),
        "vertices": sum(o.vertex_count for o in plan.objects),
        "empties": sorted(e.name for e in plan.empties),
        "wheels": sorted(plan.wheel_centers),
        "unit_meter": plan.unit_meter,
        "up_axis": plan.up_axis,
        "issues": list(plan.issues),
    }
    _write_json(out / "geometry.json", data)
    return {k: data[k] for k in ("objects", "vertices", "wheels")} | {
        "issues": len(plan.issues)
    }


def run_draft(ctx: RunContext, out: Path) -> dict[str, Any]:
    from src.ac.car_validate import (
        critical_key_problems,
        load_requirements,
        read_ini,
        shift_window_problem,
    )

    drafts = ctx.stage_dir("physics") / "drafts"
    data = out / "data"
    data.mkdir()
    for f in sorted(drafts.iterdir()):
        shutil.copyfile(f, ensure_safe_output_path(data / f.name))
    inis = {p.name: read_ini(p) for p in sorted(data.glob("*.ini"))}
    checks: list[dict[str, Any]] = []
    for name, ok, detail in load_requirements(inis):
        checks.append({"check": name, "ok": ok, "detail": detail})
    if "drivetrain.ini" in inis:
        problem = shift_window_problem(inis["drivetrain.ini"])
        checks.append(
            {
                "check": "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW > 0",
                "ok": problem is None,
                "detail": problem or "ok",
            }
        )
    for name, ok, detail in critical_key_problems(data):
        checks.append({"check": name, "ok": ok, "detail": detail})
    expected = (
        "engine.ini",
        "power.lut",
        "drivetrain.ini",
        "suspensions.ini",
        "brakes.ini",
        "tyres.ini",
        "car.ini",
        "aero.ini",
        "setup.ini",
    )
    missing_files = [n for n in expected if not (data / n).is_file()]
    failed = [c for c in checks if not c["ok"]]
    _write_json(
        out / "validation.json",
        {
            "kind": "BORRADOR NO INSTALABLE: solo INI de física generados; "
            "sin KN5, ui ni skins",
            "files": sorted(p.name for p in data.iterdir()),
            "missing_files": missing_files,
            "checks": checks,
            "failed": failed,
        },
    )
    return {
        "files": len(list(data.iterdir())),
        "missing_files": missing_files,
        "failed_checks": [c["check"] for c in failed],
    }


def _evidence_index(catalog: Any) -> dict[str, list[dict[str, Any]]]:
    """Evidencia citada por cada regla y decisión (``resolved_ini.evidence_index``)."""
    from .resolved_ini import evidence_index

    return evidence_index(catalog)


def _ini_pairs(text: str) -> dict[tuple[str, str], str]:
    """Pares (sección, clave) → valor de un texto INI leído como AC (read_ini_ac)."""
    import tempfile

    from src.ac.car_validate import read_ini_ac

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ini"
        p.write_text(text, encoding="utf-8")
        ini = read_ini_ac(p)
    return {(s, k): v for s, keys in ini.sections.items() for k, v in keys.items()}


def run_resolved_ini(ctx: RunContext, out: Path) -> dict[str, Any]:
    """INI de AC desde los atributos resueltos, con trazabilidad, bloqueos, conflictos
    con los generadores y comparación con la referencia del proyecto (si existe)."""
    from src.ac.car_validate import (
        critical_key_problems,
        load_requirements,
        read_ini,
        shift_window_problem,
    )
    from src.attrs import schema as S
    from src.attrs.store import Catalog

    from . import resolved_ini as R

    proj = ctx.project
    report = json.loads(
        (ctx.stage_dir("attributes") / "report.json").read_text(encoding="utf-8")
    )
    catalog = Catalog.load(proj.catalog_path, proj.root)
    attrs = {k: S._to_plain(a) for k, a in catalog.attributes.items()}
    res = R.build(report["results"], attrs, _evidence_index(catalog))
    data = out / "data"
    data.mkdir()
    for name, text in res.files.items():
        ensure_safe_output_path(data / name).write_text(text, encoding="utf-8")

    # validación del resultado con el validador existente (lectura como AC)
    inis = {p.name: read_ini(p) for p in sorted(data.glob("*.ini"))}
    validation: list[dict[str, Any]] = [
        {"check": n, "ok": ok, "detail": d} for n, ok, d in load_requirements(inis)
    ]
    if "drivetrain.ini" in inis:
        problem = shift_window_problem(inis["drivetrain.ini"])
        validation.append(
            {
                "check": "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW > 0",
                "ok": problem is None,
                "detail": problem or "ok",
            }
        )
    validation += [
        {"check": n, "ok": ok, "detail": d} for n, ok, d in critical_key_problems(data)
    ]
    for c in validation:
        if not c["ok"]:
            res.blockers.append(
                R.Blocker("validación", "validación", f"{c['check']}: {c['detail']}")
            )

    # conflictos con los borradores de los generadores (no se resuelven: se informan)
    drafts = ctx.stage_dir("physics") / "drafts"
    conflicts = []
    gen_cache: dict[str, dict[tuple[str, str], str]] = {}
    for t in res.traces:
        gen = drafts / t.file
        if not gen.is_file():
            continue
        if t.section is None:
            cmp = R.compare_tables(res.files[t.file], gen.read_text(encoding="utf-8"))
            if cmp["status"] == "diferente":
                conflicts.append(
                    {
                        "target": t.target,
                        "generator": "power.lut del borrador",
                        "detail": cmp,
                        "written": "atributo",
                        "epistemic": t.epistemic,
                    }
                )
            continue
        pairs = gen_cache.setdefault(
            t.file, _ini_pairs(gen.read_text(encoding="utf-8"))
        )
        g = pairs.get((t.section, t.key))
        if g is not None and R.compare_value(t.rendered, g) == "diferente":
            conflicts.append(
                {
                    "target": t.target,
                    "attribute_value": t.rendered,
                    "generator_value": g,
                    "written": "atributo",
                    "epistemic": t.epistemic,
                    "source": t.rule or t.decision,
                }
            )

    # comparación con la referencia del proyecto (solo lectura)
    comparison: dict[str, Any] | None = None
    if proj.reference_data:
        ref_dir = Path(proj.reference_data)
        keys = []
        for t in res.traces:
            ref = ref_dir / t.file
            if not ref.is_file():
                keys.append({"target": t.target, "status": "sin_archivo_de_referencia"})
                continue
            if t.section is None:
                c = R.compare_tables(res.files[t.file], ref.read_text(encoding="utf-8"))
                keys.append({"target": t.target, "status": c["status"], "detail": c})
                continue
            pairs = _ini_pairs(ref.read_text(encoding="utf-8"))
            rv = pairs.get((t.section, t.key))
            keys.append(
                {
                    "target": t.target,
                    "ours": t.rendered,
                    "reference": rv,
                    "status": (
                        "sin_clave_en_referencia"
                        if rv is None
                        else R.compare_value(t.rendered, rv)
                    ),
                    "epistemic": t.epistemic,
                }
            )
        written = {(t.file, t.section, t.key) for t in res.traces}
        not_covered = []
        for ref in sorted(ref_dir.iterdir()):
            if ref.suffix.lower() != ".ini":
                if ref.name not in res.files:
                    not_covered.append(
                        {"file": ref.name, "reason": "archivo no generado"}
                    )
                continue
            for (sec, key), val in sorted(
                _ini_pairs(ref.read_text(encoding="utf-8")).items()
            ):
                if (ref.name, sec, key) not in written:
                    not_covered.append(
                        {"file": ref.name, "section": sec, "key": key, "reference": val}
                    )
        counts: dict[str, int] = {}
        for k in keys:
            counts[k["status"]] = counts.get(k["status"], 0) + 1
        comparison = {
            "reference": str(ref_dir),
            "keys": keys,
            "counts": counts,
            "not_covered": not_covered,
            "not_covered_count": len(not_covered),
        }
        _write_json(out / "comparison.json", comparison)

    trace = res.to_trace_json()
    trace["files"] = sorted(res.files)
    trace["validation"] = validation
    trace["conflicts_with_generators"] = conflicts
    _write_json(out / "trace.json", trace)
    kinds: dict[str, int] = {}
    for b in res.blockers:
        kinds[b.kind] = kinds.get(b.kind, 0) + 1
    ep: dict[str, int] = {}
    for t in res.traces:
        ep[t.epistemic] = ep.get(t.epistemic, 0) + 1
    return {
        "files": sorted(res.files),
        "keys": len(res.traces),
        "by_epistemic": dict(sorted(ep.items())),
        "blockers": dict(sorted(kinds.items())),
        "conflicts_with_generators": len(conflicts),
        "comparison": comparison["counts"] if comparison else None,
        "not_covered_in_reference": (
            comparison["not_covered_count"] if comparison else None
        ),
    }


def run_report(ctx: RunContext, out: Path) -> dict[str, Any]:
    def load(stage: str, name: str) -> dict[str, Any] | None:
        d = ctx.optional_dir(stage) if stage != "source" else ctx.stage_dir(stage)
        return None if d is None else json.loads((d / name).read_text(encoding="utf-8"))

    src = load("source", "source.json")
    assert src is not None
    phys = load("physics", "preview.json")
    attrs = load("attributes", "report.json")
    geo = load("geometry", "geometry.json")
    draft = load("draft", "validation.json")
    resolved = load("resolved_ini", "trace.json")
    blockers = [
        f"etapa «{s}» sin resultado: {ctx.failure(s)}"
        for s in ("physics", "attributes", "geometry", "draft", "resolved_ini")
        if ctx.failure(s) is not None
    ]
    manual = (
        [r["target"] for r in attrs["results"] if r["status"] == "manual_required"]
        if attrs
        else []
    )
    proposals = (
        [r["target"] for r in attrs["results"] if r.get("requires_approval")]
        if attrs
        else []
    )
    if phys:
        blockers += [
            f"física: paso «{k}» falló: {v.get('error_type')}: {v.get('error')}"
            for k, v in phys["stages"].items()
            if not v.get("ok")
        ]
    if draft:
        blockers += [f"borrador: falta {n}" for n in draft["missing_files"]]
        blockers += [f"borrador: {c['check']}" for c in draft["failed"]]
    if geo:
        blockers += [f"geometría: {i}" for i in geo["issues"]]
    resolved_summary = None
    if resolved:
        # las decisiones/propuestas pendientes ya figuran arriba; aquí solo lo nuevo
        kinds: dict[str, int] = {}
        for b in resolved["blockers"]:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        blockers += [
            f"INI resueltos: {b['target']}: {b['detail']}"
            for b in resolved["blockers"]
            if b["kind"] not in ("decisión_necesaria", "propuesta_pendiente")
        ]
        resolved_summary = {
            "keys": len(resolved["keys"]),
            "files": resolved["files"],
            "blockers_by_kind": dict(sorted(kinds.items())),
            "conflicts_with_generators": len(resolved["conflicts_with_generators"]),
        }
    data = {
        "vehicle": f"{src['vehicle_id']}/{src['variant']}",
        "source": {
            "files": src["files_total"],
            "groups": src["files_by_group"],
            "configurations": len(src["configurations"]),
        },
        "physics_ok": phys["ok"] if phys else None,
        "attributes": attrs["summary"]["by_status"] if attrs else None,
        "manual_required": manual,
        "proposals_requiring_approval": proposals,
        "geometry": (
            {k: geo[k] for k in ("objects", "vertices", "wheels", "unit_meter")}
            if geo
            else None
        ),
        "draft_failed_checks": [c["check"] for c in draft["failed"]] if draft else None,
        "resolved_ini": resolved_summary,
        "blockers": blockers,
        "installable_package": False,
        "why_not_installable": "la exportación aislada aún no existe: el ensamblador "
        "actual (src.ac.assemble) solo admite carpeta + perfil + "
        "base física",
    }
    _write_json(out / "compatibility.json", data)
    md = [
        f"# Compatibilidad: {data['vehicle']}",
        "",
        f"- Origen: {data['source']['files']} archivos, "
        f"{data['source']['configurations']} "
        f"configuraciones; grupos {data['source']['groups']}",
        "- Física (vista previa): "
        + {True: "completa", False: "INCOMPLETA", None: "sin resultado"}[
            data["physics_ok"]
        ],
        f"- Atributos: {data['attributes']}",
        f"- Requieren intervención manual: {len(manual)}; propuestas por aprobar: "
        f"{len(proposals)}",
        f"- Geometría: {data['geometry']}",
        f"- INI desde atributos resueltos: {resolved_summary}",
        "- Paquete instalable: **no** (" + data["why_not_installable"] + ")",
        "",
        "## Bloqueos",
        "",
    ]
    md += [f"- {b}" for b in blockers] or ["- ninguno"]
    ensure_safe_output_path(out / "compatibility.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8"
    )
    return {
        "blockers": len(blockers),
        "manual_required": len(manual),
        "proposals": len(proposals),
        "physics_ok": data["physics_ok"],
    }


_PREVIEW_CODE = (
    "src/pipeline/physics_preview.py",
    "src/ac/engine.py",
    "src/ac/coast_ref.py",
    "src/ac/drivetrain.py",
    "src/ac/chassis.py",
    "src/ac/assemble.py",
    "src/extract/engine_camso.py",
    "src/extract/drivetrain.py",
    "src/extract/traction.py",
    "src/extract/brakes_tyres.py",
    "src/extract/suspension.py",
    "src/extract/mass_geometry.py",
    "src/extract/aero.py",
)


def _package_code(*packages: str) -> tuple[str, ...]:
    """Todos los ``*.py`` de esos paquetes (sin ``__main__.py``, que es la CLI).

    La etapa ``attributes`` importa ``src.ir`` y ``src.importers`` de forma diferida
    (dentro de ``facts_bng.build_facts``), así que una lista escrita a mano se queda
    atrás (G2.6, informe §13.2.5). Con el paquete entero, un archivo nuevo también
    entra en la huella. ``tests/test_g27_stage_code.py`` comprueba que toda
    importación de estos paquetes alcanzable desde la etapa está cubierta.
    """
    out = []
    for pkg in packages:
        for p in sorted((PROJECT_ROOT / pkg).glob("*.py")):
            if p.name != "__main__.py":
                out.append(p.relative_to(PROJECT_ROOT).as_posix())
    return tuple(out)


# + lo que esos paquetes importan directamente y no cubre ninguna etapa previa
# (``physics`` → ``source``; sus claves entran en la de ``attributes``).
_ATTRIBUTES_CODE = _package_code("src/attrs", "src/ir", "src/importers") + (
    "src/ac/cvt_script.py",  # plantilla del script de la CVT (cvt_script.lua)
    "src/coords.py",
    "src/extract/components.py",
    "src/extract/nodes.py",
    "src/jbeam/__init__.py",
    "src/jbeam/lax_json.py",
    "src/safe_paths.py",
)

_STAGES_PY = "src/project/stages.py"


def _measurement_inputs(project: Any) -> dict[str, Any]:
    """G2.19: el importador lee las medidas del juego de ``measurements/`` (asociadas
    por la huella del origen): un registro nuevo o cambiado invalida la caché."""
    from src.importers.measurements import tree_sha256

    return {"measurements_sha256": tree_sha256()}


def _code(*roots: str) -> tuple[str, ...]:
    """Huella de código de una etapa (G2.8): los bytes de ``stages.py`` + el cierre
    de importaciones de sus raíces (``code_deps.import_closure``), incluidas las
    diferidas. Las listas a mano anteriores se conservan como raíces: la huella solo
    puede crecer. ``stages.py`` no se expande (importa de forma diferida el código de
    TODAS las etapas); lo que importa cada ``run_*`` está en sus raíces y
    ``tests/test_g28_stage_code.py`` lo comprueba."""
    closure = import_closure(tuple(r for r in roots if r != _STAGES_PY))
    return (_STAGES_PY,) + tuple(p for p in closure if p != _STAGES_PY)


STAGES: tuple[Stage, ...] = (
    Stage(
        "source",
        1,
        run_source,
        (),
        _code(
            "src/jbeam/parts.py",
            "src/jbeam/sources.py",
            "src/pipeline/source_inventory.py",
            "src/extract/paints.py",
            # _load_source: load_vehicle_dir/zip (G2.8: no estaba en la huella)
            "src/pipeline/physics_preview.py",
        ),
        description="inventario del origen",
    ),
    Stage(
        "physics",
        1,
        run_physics,
        ("source",),
        _code(*_PREVIEW_CODE),
        description="vista previa de física y borradores INI",
    ),
    Stage(
        "attributes",
        1,
        run_attributes,
        ("physics",),
        _code(*_ATTRIBUTES_CODE),
        uses_rules=True,
        description="resolución de atributos (catálogo + decisiones)",
        extra_inputs=_measurement_inputs,
    ),
    Stage(
        "geometry",
        1,
        run_geometry,
        ("source",),
        _code(
            "src/mesh/build.py",
            "src/mesh/plan.py",
            "src/mesh/dae.py",
            "src/mesh/flexbody.py",
        ),
        description="plan de mallas",
    ),
    Stage(
        "draft",
        1,
        run_draft,
        ("physics",),
        _code("src/ac/car_validate.py"),
        description="borrador de data/ y validación",
    ),
    Stage(
        "resolved_ini",
        1,
        run_resolved_ini,
        ("physics", "attributes"),
        _code(
            "src/project/resolved_ini.py",
            "src/ac/car_validate.py",
            "src/attrs/units.py",
        ),
        uses_rules=True,
        uses_reference=True,
        description="INI de AC desde los atributos resueltos, con trazabilidad",
    ),
    Stage(
        "report",
        2,
        run_report,
        ("source",),
        _code(),
        description="informe de compatibilidad y bloqueos (también si algo falla)",
        optional=("physics", "attributes", "geometry", "draft", "resolved_ini"),
    ),
)


# --- etapa del modelo 3D (KN5 en Python puro) ------------------------------------------


def run_model(ctx: RunContext, out: Path) -> dict[str, Any]:
    from . import model as M

    pv = _preview(ctx)
    if pv.resolved is None:
        raise M.ModelError("configuración sin resolver (etapa physics)")
    rep = M.build_model(
        _load_source(ctx),
        pv.resolved,
        ctx.project.source,
        out,
        M.wheel_override_of(ctx.project),
        M.model_options_of(ctx.project),
    )
    return {
        "verification_ok": rep["verification"]["ok"],
        "verification_errors": len(rep["verification"]["errors"]),
        "textures": rep["textures"] is not None,
        "profile": rep["profile"]["path"],
        "warnings": len(rep["warnings"]),
    }


def _model_tool_inputs(project: Any) -> dict[str, Any]:
    from .model import tool_inputs

    return tool_inputs(project)


MODEL_STAGE = Stage(
    "model",
    1,
    run_model,
    ("physics", "geometry"),
    _code(
        "src/project/model.py",
        "src/mesh/build.py",
        "src/mesh/pure_kn5.py",
        "src/mesh/plan.py",
        "src/mesh/dae.py",
        "src/mesh/flexbody.py",
        "src/ac/assemble.py",
        "src/ac/kn5_materials.py",
        "src/extract/paints.py",
        "src/ac/kn5_write.py",
        "src/ac/steer_wheel.py",
        "src/ac/lights.py",
        "src/ac/flames.py",
        "src/ac/double_sided.py",
        "src/ac/part_anim.py",
        "src/ac/collider_profile.py",
        "src/ac/cockpit_config.py",
    ),
    description="KN5, collider y CSP (los pide la exportación)",
    extra_inputs=_model_tool_inputs,
)

# Etapas con el modelo 3D: las que usa ``python -m src.project export``.
STAGES_WITH_MODEL: tuple[Stage, ...] = STAGES + (MODEL_STAGE,)
