"""Contexto de vehículo e informe de resolución (JSON determinista + Markdown)."""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

from src.pipeline.physics_preview import PhysicsPreview

from . import schema as S
from .candidates import build_candidates
from .engine import ResolutionEngine, Resolution, VehicleContext
from .facts_bng import build_facts
from .store import Catalog
from .validations_bng import info_validations

REPORT_SCHEMA = "beam2ac.resolution_report/1"


def context_from_preview(
    pv: PhysicsPreview, catalog: Catalog, driver_mass_kg: float | None
) -> VehicleContext:
    """Hechos (a través del IR, ``facts_bng``) + validaciones del vehículo."""
    src = pv.source
    facts = build_facts(pv, driver_mass_kg)
    return context_from_facts(src.vehicle_id, src.variant, facts, catalog)


def context_from_facts(
    vehicle: str, variant: str, facts: dict, catalog: Catalog
) -> VehicleContext:
    """Hechos + validaciones: las automáticas (recalculadas ahora) y las registradas en
    el catálogo para este vehículo. Si una registrada tiene el mismo id que una
    recalculada y el resultado difiere, se conserva la recalculada y se anota la
    discrepancia. No depende del origen: sirve para hechos derivados del IR."""
    runtime = info_validations(vehicle, variant, facts)
    by_id = {v.id: v for v in runtime}
    for v in catalog.validations.values():
        if v.vehicle != vehicle:
            continue
        if v.id in by_id:
            if by_id[v.id].result != v.result:
                by_id[
                    v.id
                ].note += f" [registrada en el catálogo con resultado '{v.result}']"
            continue
        by_id[v.id] = v
    from src.ir.legacy_facts import validation_fact_equivalents

    return VehicleContext(
        vehicle,
        variant,
        facts,
        sorted(by_id.values(), key=lambda v: v.id),
        fact_equivalents=validation_fact_equivalents(facts),
    )


def resolve_vehicle(
    pv: PhysicsPreview,
    catalog: Catalog,
    target_set: str = "ac_physics",
    driver_mass_kg: float | None = 70.0,
) -> tuple[VehicleContext, list[Resolution]]:
    ctx = context_from_preview(pv, catalog, driver_mass_kg)
    engine = ResolutionEngine(catalog, ctx)
    targets = catalog.targets[target_set]
    return ctx, engine.resolve_all(targets)


def candidates_with_contrast(
    pv: PhysicsPreview, ctx: VehicleContext, results: list[Resolution]
) -> dict[str, Any]:
    """Candidatos de la fase 5 y, si el atributo ya tiene valor resuelto (p. ej. una
    decisión
    aprobada del Ibiza), el contraste candidato ↔ valor resuelto."""
    cands = build_candidates(pv, ctx.facts)
    resolved = {r.target: r for r in results}
    for target, lst in cands["candidates"].items():
        r = resolved.get(target)
        for c in lst:
            c["resolved_value"] = None if r is None else r.value
            c["resolved_status"] = None if r is None else r.status
            c["resolved_source"] = None if r is None else (r.decision or r.rule)
            if (
                r is not None
                and isinstance(r.value, (int, float))
                and isinstance(c["value"], (int, float))
                and not isinstance(r.value, bool)
            ):
                c["difference_vs_resolved"] = round(c["value"] - r.value, 6)
    return cands


def build_report(
    pv: PhysicsPreview, ctx: VehicleContext, results: list[Resolution], catalog: Catalog
) -> dict[str, Any]:
    counts = collections.Counter(r.status for r in results)
    by_source = collections.Counter(r.source or "none" for r in results)
    cands = candidates_with_contrast(pv, ctx, results)
    return {
        "schema": REPORT_SCHEMA,
        "vehicle": ctx.vehicle,
        "variant": ctx.variant,
        "source": pv.source.description,
        "input_sha256": pv.source.input_sha256,
        "preview_ok": pv.ok,
        "preview_stages": pv.stages,
        "families": ctx.families,
        "family_evidence": ctx.family_evidence,
        "catalog": {
            "root": str(catalog.root),
            "rules": sorted(catalog.rules),
            "decisions": len(catalog.decisions),
        },
        "facts": {k: f.to_dict() for k, f in sorted(ctx.facts.items())},
        "validations": [S._to_plain(v) for v in ctx.validations],
        "summary": {
            "targets": len(results),
            "by_status": dict(sorted(counts.items())),
            "by_source": dict(sorted(by_source.items())),
            "proposals_requiring_approval": sum(r.requires_approval for r in results),
        },
        "results": [r.to_dict() for r in results],
        "candidates": cands["candidates"],
        "gaps": cands["gaps"],
    }


STATUS_ES = {
    "known": "conocido",
    "inferred": "inferido",
    "ambiguous": "ambiguo",
    "not_applicable": "no aplicable",
    "format_requirement": "requisito de formato",
    "manual_required": "intervención manual",
}


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"# Resolución de atributos: {report['vehicle']} / {report['variant']}",
        "",
        f"Origen: `{report['source']}` · vista previa válida: {report['preview_ok']} · "
        f"familias: {', '.join(report['families']) or '—'}",
        "",
        "| Estado | N.º |",
        "|---|---|",
    ]
    for status, n in report["summary"]["by_status"].items():
        lines.append(f"| {STATUS_ES.get(status, status)} | {n} |")
    lines += [
        "",
        "Propuestas que requieren aprobación: "
        f"{report['summary']['proposals_requiring_approval']}",
        "",
    ]
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in report["results"]:
        groups[r["status"]].append(r)
    for status in S.RESOLUTION_STATUS:
        rows = groups.get(status, [])
        if not rows or status == "manual_required":
            continue
        lines += [
            f"## {STATUS_ES[status]} ({len(rows)})",
            "",
            "| Atributo | Valor | Origen | Confianza | Motivo |",
            "|---|---|---|---|---|",
        ]
        for r in rows:
            value = r["value"]
            if isinstance(value, list) and len(value) > 4:
                value = f"tabla de {len(value)} puntos"
            why = (r["open_issues"] or r["assumptions"] or r["reasons"] or [""])[0]
            lines.append(
                f"| `{r['target'][3:]}` | {value} "
                f"{r['unit'] if value is not None else ''} | "
                f"{r['source']} {r['rule'] or r['decision']} | "
                f"{r['confidence']} | {why[:110]} |"
            )
        lines.append("")
    manual = groups.get("manual_required", [])
    if manual:
        by_file: dict[str, list[str]] = collections.defaultdict(list)
        for r in manual:
            file, _, key = r["target"][3:].partition("/")
            by_file[file].append(key)
        lines += [f"## {STATUS_ES['manual_required']} ({len(manual)})", ""]
        for file, keys in sorted(by_file.items()):
            lines.append(
                f"- `{file}` ({len(keys)}): " + ", ".join(f"`{k}`" for k in keys)
            )
        lines.append("")
    return "\n".join(lines)


def write_report(out_dir: Path, report: dict[str, Any]) -> dict[str, Path]:
    from src.safe_paths import ensure_safe_output_path, new_output_dir

    out = new_output_dir(out_dir)
    paths = {
        "report.json": ensure_safe_output_path(out / "report.json"),
        "summary.md": ensure_safe_output_path(out / "summary.md"),
    }
    paths["report.json"].write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    paths["summary.md"].write_text(markdown_summary(report), encoding="utf-8")
    return paths
