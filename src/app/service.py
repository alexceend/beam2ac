"""Servicio de revisión de atributos: estado de un vehículo, consulta y decisiones (sin
HTTP).

Mantiene en memoria la vista previa del vehículo (cara de calcular) y vuelve a resolver
con el motor cada vez que se guarda una decisión (rápido). Las decisiones se AÑADEN al
JSONL del proyecto (``<proyecto>/decisions/<vehículo>.jsonl``); el catálogo del
repositorio no se toca.

Verbos de la interfaz → decisiones (``schema.Decision``):

* ``accept``: aceptar la propuesta o el candidato mostrado → ``select_reference`` con la
regla o el
  candidato como referencia.
* ``manual_value``: valor manual con unidad explícita.
* ``transform``: transformación explícita (``transforms.TRANSFORMS``) sobre hechos del
vehículo.
* ``no_correspondence``: declarar que no existe correspondencia conocida.
* ``defer``: dejar pendiente (estado ``pending``).
* ``reject``: rechazar la propuesta (``defer`` con la regla rechazada como referencia).
* ``revert``: sustituir la decisión vigente por otra que restaura la anterior (historial
intacto).

Ámbitos: ``variant`` y ``vehicle`` se aplican. ``family`` y ``architecture`` se GUARDAN
como propuesta de regla pero el motor no las aplica: hace falta una regla aprobada (plan
§6.1).
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.attrs import schema as S
from src.attrs import transforms, units
from src.attrs.engine import ResolutionEngine
from src.attrs.report import build_report, context_from_preview
from src.attrs.store import REPO_CATALOG, Catalog, StoreError, append_decision
from src.catalog.ac_refs import reference_values
from src.pipeline.physics_preview import PhysicsPreview
from src.safe_paths import claim_output_dir

ACTIONS = (
    "accept",
    "manual_value",
    "transform",
    "no_correspondence",
    "defer",
    "reject",
    "revert",
)


class ServiceError(ValueError):
    """Petición inválida (se devuelve al cliente con su motivo)."""


@dataclass
class ReviewService:
    preview: PhysicsPreview
    project_dir: Path
    catalog_root: Path = REPO_CATALOG
    driver_mass_kg: float = 70.0
    reference_index: dict[str, Any] | None = None
    target_set: str = "ac_physics"
    report: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project_dir = claim_output_dir(self.project_dir)
        (self.project_dir / "decisions").mkdir(exist_ok=True)
        self._counter = itertools.count(1)
        self.refresh()

    # --- estado -------------------------------------------------------------------
    def refresh(self) -> None:
        self.catalog = Catalog.load(self.catalog_root, self.project_dir)
        self.ctx = context_from_preview(self.preview, self.catalog, self.driver_mass_kg)
        engine = ResolutionEngine(self.catalog, self.ctx)
        self.results = engine.resolve_all(self.catalog.targets[self.target_set])
        self.report = build_report(self.preview, self.ctx, self.results, self.catalog)
        self._by_target = {r["target"]: r for r in self.report["results"]}

    @property
    def vehicle(self) -> str:
        return self.ctx.vehicle

    def state(self) -> dict[str, Any]:
        r = self.report
        return {
            "vehicle": r["vehicle"],
            "variant": r["variant"],
            "source": r["source"],
            "preview_ok": r["preview_ok"],
            "failed_stages": self.preview.failed_stages,
            "families": r["families"],
            "summary": r["summary"],
            "validations": r["validations"],
            "project_dir": str(self.project_dir),
            "reference_index_loaded": self.reference_index is not None,
            "statuses": list(S.RESOLUTION_STATUS),
            "units": units.known_units(),
            "transforms": sorted(transforms.TRANSFORMS),
            "scopes": list(S.SCOPE_LEVELS),
            "facts": sorted(self.ctx.facts),
        }

    def attributes(self, status: str = "", text: str = "") -> list[dict[str, Any]]:
        out = []
        for r in self.report["results"]:
            if status and r["status"] != status:
                continue
            if text and text.lower() not in r["target"].lower():
                continue
            file, _, key = r["target"][3:].partition("/")
            out.append(
                {
                    "target": r["target"],
                    "file": file,
                    "key": key,
                    "status": r["status"],
                    "value": _short(r["value"]),
                    "unit": r["unit"],
                    "confidence": r["confidence"],
                    "source": r["source"],
                    "requires_approval": r["requires_approval"],
                    "verifiable": _verifiable(r),
                    "has_candidates": r["target"] in self.report["candidates"],
                }
            )
        return out

    def attribute(self, target: str) -> dict[str, Any]:
        if target not in self._by_target:
            raise ServiceError(f"atributo desconocido: {target}")
        res = self._by_target[target]
        attr = self.catalog.attributes.get(target)
        rules = [
            S._to_plain(r) | {"key": r.key}
            for r in self.catalog.rules.values()
            if r.target == target
        ]
        history = [
            S._to_plain(d) for d in self.catalog.decision_history(target, self.vehicle)
        ]
        refs = (
            reference_values(self.reference_index, target)
            if self.reference_index
            else []
        )
        return {
            "result": res,
            "attribute": S._to_plain(attr) if attr else None,
            "rules": sorted(rules, key=lambda r: r["key"]),
            "decisions": history,
            "candidates": self.report["candidates"].get(target, []),
            "ac_references": sorted(
                refs, key=lambda g: (-len(g["cars"]), str(g["value"]))
            )[:40],
            "ac_references_total": len(refs),
            "compatible_units": _compatible_units(attr),
            "verifiable": _verifiable(res),
        }

    def history(self, target: str) -> dict[str, Any]:
        return {
            "decisions": [
                S._to_plain(d) for d in self.catalog.decision_history(target)
            ],
            "rules": [
                {
                    "key": r.key,
                    "status": r.status,
                    "supersedes": r.supersedes,
                    "history": S._to_plain(r.history),
                }
                for r in self.catalog.rules.values()
                if r.target == target
            ],
        }

    # --- decisiones ---------------------------------------------------------------
    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        action = body.get("action")
        if action not in ACTIONS:
            raise ServiceError(f"acción desconocida {action!r}; válidas: {ACTIONS}")
        target = body.get("target")
        if target not in self.catalog.attributes:
            raise ServiceError(f"atributo desconocido: {target!r}")
        justification = str(body.get("justification", "")).strip()
        if not justification:
            raise ServiceError("la justificación es obligatoria")
        level = body.get("scope", "vehicle")
        if level not in S.SCOPE_LEVELS or level == "generic":
            raise ServiceError(
                "ámbito inválido (variant, vehicle, family o architecture)"
            )
        author = str(body.get("author") or "usuario").strip()
        extra_evidence = _evidence_refs(body.get("evidence"))
        attr = self.catalog.attributes[target]
        current = self._by_target[target]
        active = self.catalog.active_decisions(self.vehicle, target)
        kwargs: dict[str, Any] = {}
        status = body.get("status", "provisional")
        if status not in ("provisional", "validated", "pending"):
            raise ServiceError("estado inválido (provisional, validated o pending)")

        if action == "accept":
            source = body.get("source", "result")
            if source == "result":
                if current["value"] is None:
                    raise ServiceError("no hay propuesta con valor que aceptar")
                kwargs = dict(
                    action="select_reference",
                    value=current["value"],
                    unit=current["unit"],
                    reference={
                        "rule": current["rule"],
                        "value": current["value"],
                        "unit": current["unit"],
                        "inputs": current["inputs"],
                    },
                )
            else:
                idx = int(body.get("candidate_index", -1))
                cands = self.report["candidates"].get(target, [])
                if not 0 <= idx < len(cands) or cands[idx]["value"] is None:
                    raise ServiceError("candidato inexistente o sin valor")
                c = cands[idx]
                kwargs = dict(
                    action="select_reference",
                    value=c["value"],
                    unit=c["unit"],
                    reference={
                        "candidate": c["method"],
                        "value": c["value"],
                        "unit": c["unit"],
                        "assumptions": c["assumptions"],
                    },
                )
        elif action == "manual_value":
            unit = body.get("unit")
            try:
                units.validate_unit(unit)
            except units.UnitError as exc:
                raise ServiceError(str(exc)) from None
            if (
                attr.unit not in ("undocumented",)
                and unit != attr.unit
                and not _compat(unit, attr.unit)
            ):
                raise ServiceError(
                    f"unidad {unit!r} incompatible con la del atributo {attr.unit!r}"
                )
            kwargs = dict(
                action="manual_value", value=_parse(body.get("value")), unit=unit
            )
            if kwargs["value"] is None:
                raise ServiceError("falta el valor")
            if attr.dtype == "table":
                from src.project.resolved_ini import table_problems

                problems = table_problems(kwargs["value"])
                if problems:
                    raise ServiceError("tabla inválida: " + "; ".join(problems))
        elif action == "transform":
            tid = body.get("transform_id")
            if tid not in transforms.TRANSFORMS:
                raise ServiceError(f"transformación desconocida {tid!r}")
            inputs = body.get("inputs") or {}
            params = body.get("params") or {}
            if not isinstance(inputs, dict) or not isinstance(params, dict):
                raise ServiceError("inputs y params deben ser objetos")
            for name, ref in inputs.items():
                if ref not in self.ctx.facts and ref not in self.catalog.attributes:
                    raise ServiceError(
                        f"entrada '{name}': hecho o atributo inexistente {ref!r}"
                    )
            out_unit = body.get("unit") or attr.unit
            tr = S.Transform(
                id=tid,
                params=params,
                output_unit=out_unit,
                input_units={
                    n: self.ctx.facts[r].unit
                    for n, r in inputs.items()
                    if r in self.ctx.facts
                },
                formula=str(body.get("formula", "")),
            )
            # comprobación previa: debe poder calcularse con los hechos de este vehículo
            vals = {
                n: (self.ctx.facts[r].value, self.ctx.facts[r].unit)
                for n, r in inputs.items()
                if r in self.ctx.facts
            }
            try:
                preview_value = transforms.apply(tid, vals, params, out_unit)
            except (transforms.TransformError, units.UnitError) as exc:
                raise ServiceError(
                    f"la transformación no se puede calcular: {exc}"
                ) from None
            kwargs = dict(
                action="transform",
                transform=tr,
                inputs=inputs,
                unit=out_unit,
                value=preview_value,
            )
        elif action in ("no_correspondence", "defer", "reject"):
            kwargs = dict(
                action=(
                    "no_correspondence" if action == "no_correspondence" else "defer"
                ),
                value=None,
                unit=attr.unit,
            )
            if action == "defer":
                status = "pending"
            if action == "reject":
                kwargs["reference"] = {
                    "rejected_rule": current["rule"],
                    "rejected_value": current["value"],
                }
        elif action == "revert":
            if not active:
                raise ServiceError("no hay decisión vigente que revertir")
            prev = next(
                (d for d in self.catalog.decisions if d.id == active[-1].supersedes),
                None,
            )
            if prev is None:
                raise ServiceError(
                    "la decisión vigente no sustituye a ninguna: no hay valor anterior"
                )
            kwargs = dict(
                action=prev.action,
                value=prev.value,
                unit=prev.unit,
                reference=prev.reference,
                transform=prev.transform,
                inputs=prev.inputs,
            )
            status = prev.status

        scope = S.Scope(
            level=level,
            vehicle=self.vehicle,
            variant=self.ctx.variant if level == "variant" else "",
        )
        did = self._new_id(target)
        decision = S.Decision(
            id=did,
            target=target,
            vehicle=self.vehicle,
            variant=self.ctx.variant if level == "variant" else "",
            justification=justification,
            author=author,
            date=_dt.date.today().isoformat(),
            scope=scope,
            status=status,
            supersedes=active[-1].id if active else "",
            evidence=[
                S.EvidenceRef(
                    type="report",
                    path=str(self.project_dir),
                    locator=f"interfaz: {action}",
                )
            ]
            + extra_evidence,
            history=[
                S.HistoryEntry(
                    date=_dt.date.today().isoformat(),
                    author=author,
                    action=action,
                    reason=justification,
                    status_to=status,
                )
            ],
            **kwargs,
        )
        try:
            append_decision(self.project_dir, decision, self.catalog)
        except (StoreError, S.SchemaError) as exc:
            raise ServiceError(str(exc)) from None
        self.refresh()
        note = (
            (
                "ámbito de familia/arquitectura: guardado "
                "como propuesta de regla; el motor no lo aplica "
                "hasta que exista una regla aprobada"
            )
            if level in ("family", "architecture")
            else ""
        )
        return {
            "decision": S._to_plain(decision),
            "result": self._by_target[target],
            "note": note,
        }

    def _new_id(self, target: str) -> str:
        existing = {d.id for d in self.catalog.decisions}
        stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        while True:
            key = target.split(":", 1)[1].replace("/", ".")
            did = f"ui.{self.vehicle}.{key}.{stamp}.{next(self._counter)}"
            if did not in existing:
                return did


def _evidence_refs(raw: Any) -> list[S.EvidenceRef]:
    """Evidencia adicional opcional de una decisión: lista de
    ``{type, path, locator, sha256, note}`` (``path`` obligatorio)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ServiceError("evidence debe ser una lista")
    out = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("path", "")).strip():
            raise ServiceError(f"evidencia inválida (falta path): {item!r}")
        out.append(
            S.EvidenceRef(
                type=str(item.get("type") or "file"),
                path=str(item["path"]),
                locator=str(item.get("locator") or ""),
                sha256=str(item.get("sha256") or ""),
                note=str(item.get("note") or ""),
            )
        )
    return out


def _compatible_units(attr: Any) -> list[str]:
    """Unidades que el editor ofrece. Las de tabla (``table:x->y``) no están en
    ``known_units()``: la propia unidad del atributo va siempre la primera (sin esto,
    una LUT como ``power.lut`` no se podía decidir desde la interfaz)."""
    out = [
        u
        for u in units.known_units()
        if attr is None or u == attr.unit or _compat(u, attr.unit)
    ]
    if attr is not None and attr.unit not in out:
        out.insert(0, attr.unit)
    return out


def _compat(a: str, b: str) -> bool:
    try:
        return units.compatible(a, b)
    except units.UnitError:
        return False


def _parse(value: Any) -> Any:
    if isinstance(value, (int, float, list)) or value is None:
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _short(value: Any) -> Any:
    if isinstance(value, list) and len(value) > 4:
        return f"tabla de {len(value)} puntos"
    return value


def _verifiable(r: dict[str, Any]) -> str:
    """Etiqueta explícita de lo que todavía no se puede verificar."""
    if r["status"] in ("format_requirement", "not_applicable"):
        return "no aplica"
    if r["status"] == "manual_required":
        return "sin valor"
    if r["confidence"] in ("E3", "E4") and r["status"] == "known":
        return "verificado"
    if r["requires_approval"]:
        return "propuesta sin aprobar"
    return "no verificado"
