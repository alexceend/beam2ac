"""Motor de resolución de atributos (núcleo, sin interfaz).

Para un vehículo (hechos + validaciones) y un catálogo (reglas, decisiones, familias)
resuelve cada atributo de destino y explica por qué. Orden de precedencia:

1. **Decisión manual vigente** de ámbito variante o vehículo (la más específica). Si una
regla
   aplicable da otro valor, se informa como conflicto resuelto por la decisión.
2. **Regla aplicada**: el vehículo está en su ámbito, no hay excepción que lo excluya,
sus
   ``conditions`` se cumplen y sus entradas existen con unidades compatibles. Entre
   varias, gana el ámbito más específico; si dos del mismo nivel dan valores distintos o
   son ``incompatible_with``, el atributo queda **ambiguo**.
3. **Propuesta**: regla compatible pero de OTRO ámbito (p. ej. validada solo en el
Ibiza). Se
   calcula el valor con los hechos de este vehículo, pero NO se aplica: estado
   ``inferred`` y ``requires_approval``. El motor nunca amplía el ámbito de una regla.
4. Nada de lo anterior → ``manual_required``, con la lista de reglas descartadas y el
motivo.

Confianza (``confidence``), niveles acumulativos:

* E1: la regla cita evidencia y la unidad de salida está documentada.
* E2: E1 + definición documentada en origen (``source_doc``) y destino (``target_doc``).
* E3: E2 + semántica de origen leída en código (``source_doc`` de tipo ``code``) y, o
bien una
  validación numérica superada (de la regla o de este vehículo), o bien una regla
  directa, medida o de formato sin supuestos.
* E4: E3 + validación superada en ESTE vehículo que cubre el atributo.
* Rebajas: conflicto abierto → −1; validación fallida que cubre el atributo o una
entrada →
  máximo E1 y estado ambiguo.

Estado: ``format_requirement`` (regla de formato), ``not_applicable`` (``applies_when``
falso), ``ambiguous`` (conflicto, ``open_issues`` o validación fallida), ``known`` (≥ E3
y sin supuestos, o validado en este vehículo), ``inferred`` (resto), ``manual_required``
(sin regla, regla con confianza < E2 o entradas ausentes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import schema as S
from . import transforms, units
from .compare import same_as_written
from .facts_bng import Fact
from .store import Catalog

LEVEL_RANK = {
    lvl: i for i, lvl in enumerate(S.SCOPE_LEVELS)
}  # variant=0 (más específico)


@dataclass
class VehicleContext:
    vehicle: str
    variant: str
    facts: dict[str, Fact]
    validations: list[S.Validation] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    family_evidence: dict[str, list[str]] = field(default_factory=dict)
    # G2.5a: hecho -> hechos equivalentes demostrados, solo para buscar validaciones
    # de las entradas de una regla (``legacy_facts.validation_fact_equivalents``).
    # Vacío = cada validación cubre exactamente lo que declara.
    fact_equivalents: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def fact(self, fid: str) -> Fact | None:
        return self.facts.get(fid)


# --- condiciones -----------------------------------------------------------------


def eval_condition(cond: S.Condition, ctx: VehicleContext) -> tuple[bool | None, str]:
    """(resultado, explicación). ``None`` = el hecho no existe y la condición lo
    necesita."""
    fact = ctx.fact(cond.fact)
    if cond.op == "exists":
        return fact is not None, f"{cond.fact} {'existe' if fact else 'no existe'}"
    if cond.op == "absent":
        return fact is None, f"{cond.fact} {'no existe' if fact is None else 'existe'}"
    if fact is None:
        return None, f"falta el hecho {cond.fact}"
    v, ref = fact.value, cond.value
    try:
        ok = {
            "eq": lambda: v == ref,
            "ne": lambda: v != ref,
            "in": lambda: v in ref,
            "not_in": lambda: v not in ref,
            "gt": lambda: v > ref,
            "ge": lambda: v >= ref,
            "lt": lambda: v < ref,
            "le": lambda: v <= ref,
        }[cond.op]()
    except TypeError:
        return False, f"{cond.fact}={v!r} no comparable con {ref!r}"
    return bool(ok), f"{cond.fact}={_short(v)} {cond.op} {_short(ref)} → {bool(ok)}"


def _short(v: Any) -> str:
    s = repr(v)
    return s if len(s) <= 40 else s[:37] + "…"


def compute_families(catalog: Catalog, ctx: VehicleContext) -> None:
    for fam in catalog.families.values():
        results = [eval_condition(c, ctx) for c in fam.membership]
        if fam.membership and all(r is True for r, _ in results):
            ctx.families.append(fam.id)
        ctx.family_evidence[fam.id] = [why for _, why in results]


def in_scope(scope: S.Scope, ctx: VehicleContext) -> tuple[bool, str]:
    if scope.level == "variant":
        ok = scope.vehicle == ctx.vehicle and scope.variant == ctx.variant
        return ok, f"variante {scope.vehicle}/{scope.variant}"
    if scope.level == "vehicle":
        return scope.vehicle == ctx.vehicle, f"vehículo {scope.vehicle}"
    if scope.level == "family":
        return scope.family in ctx.families, f"familia {scope.family}"
    if scope.level == "architecture":
        res = [eval_condition(c, ctx) for c in scope.conditions]
        return all(r is True for r, _ in res), "arquitectura: " + "; ".join(
            w for _, w in res
        )
    return True, "genérica"


# --- resultado ---------------------------------------------------------------------


@dataclass
class Resolution:
    target: str
    status: str
    value: Any = None
    unit: str = ""
    source: str = ""  # decision | rule | proposal | ""
    rule: str = ""
    decision: str = ""
    requires_approval: bool = False
    transform: dict[str, Any] = field(default_factory=dict)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "E0"
    confidence_criteria: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    promotion: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class _Candidate:
    rule: S.Rule
    in_scope: bool
    scope_why: str
    not_applicable: bool = False
    value: Any = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    error: str = ""
    applicable_only: bool = False


# --- motor ---------------------------------------------------------------------------


class ResolutionEngine:
    def __init__(self, catalog: Catalog, ctx: VehicleContext) -> None:
        self.catalog = catalog
        self.ctx = ctx
        self._memo: dict[str, Resolution] = {}
        self._stack: list[str] = []
        if not ctx.families:
            compute_families(catalog, ctx)

    # valores de entrada: hechos del vehículo o atributos ya resueltos (dependencias)
    def _input(self, ref: str) -> tuple[Any, str, dict[str, Any]] | str:
        if ref in self.catalog.attributes:
            res = self.resolve(ref)
            if (
                res.status in ("known", "inferred", "format_requirement")
                and not res.requires_approval
            ):
                return (
                    res.value,
                    res.unit,
                    {
                        "attribute": ref,
                        "value": res.value,
                        "unit": res.unit,
                        "status": res.status,
                    },
                )
            return f"depende de {ref}, que está '{res.status}'" + (
                " (propuesta sin aprobar)" if res.requires_approval else ""
            )
        fact = self.ctx.fact(ref)
        if fact is None:
            return f"falta el hecho {ref}"
        return (
            fact.value,
            fact.unit,
            {
                "fact": ref,
                "value": _trace_value(fact.value, fact.unit),
                "unit": fact.unit,
                "kind": fact.kind,
                "note": fact.note,
                "refs": [S._to_plain(r) for r in fact.refs],
            },
        )

    def _cond(self, cond: S.Condition) -> tuple[bool | None, str]:
        """Condición sobre un hecho del vehículo o sobre un atributo de destino ya
        resuelto. Un atributo solo cuenta si su valor está aprobado (conocido, inferido
        por regla o decisión de su ámbito, o requisito de formato); una propuesta sin
        aprobar o un atributo sin valor devuelven ``None`` (indeterminado)."""
        if cond.fact not in self.catalog.attributes:
            return eval_condition(cond, self.ctx)
        res = self.resolve(cond.fact)
        usable = (
            res.status in ("known", "inferred", "format_requirement")
            and not res.requires_approval
            and res.value is not None
        )
        if not usable:
            why = "propuesta sin aprobar" if res.requires_approval else res.status
            return None, f"{cond.fact} sin valor aprobado ({why})"
        fact = Fact(cond.fact, res.value, res.unit, "computed")
        ctx = VehicleContext(self.ctx.vehicle, self.ctx.variant, {cond.fact: fact})
        return eval_condition(cond, ctx)

    def _evaluate(self, rule: S.Rule) -> _Candidate:
        ok, why = in_scope(rule.scope, self.ctx)
        cand = _Candidate(rule, ok, why)
        for exc in rule.exceptions:
            ex_ok, ex_why = in_scope(exc.scope, self.ctx)
            if ex_ok:
                cand.error = f"excepción ({ex_why}): {exc.reason}"
                return cand
        for cond in rule.conditions:
            res, cwhy = self._cond(cond)
            if res is not True:
                cand.error = f"condición no cumplida: {cwhy}"
                return cand
        for cond in rule.applies_when:
            res, cwhy = self._cond(cond)
            if res is None and cond.fact in self.catalog.attributes:
                # el atributo del que depende no tiene valor aprobado: no se sabe
                cand.error = f"aplicabilidad indeterminada: {cwhy}"
                return cand
            if res is not True:
                cand.not_applicable = True
                cand.error = f"no aplica: {cwhy}"
                return cand
        if rule.kind == "applicability":
            # solo declara cuándo NO aplica el atributo; nunca asigna un valor
            cand.applicable_only = True
            return cand
        values: dict[str, tuple[Any, str]] = {}
        for name, ref in rule.inputs.items():
            got = self._input(ref)
            if isinstance(got, str):
                cand.error = f"entrada '{name}': {got}"
                return cand
            value, unit, desc = got
            expected = rule.transform.input_units.get(name)
            if expected and expected != unit:
                if (
                    expected in ("text", "enum")
                    or unit in ("text", "enum")
                    or not units.compatible(unit, expected)
                ):
                    cand.error = (
                        f"unidades incompatibles en '{name}': el hecho está en "
                        f"{unit!r} y la regla espera {expected!r}"
                    )
                    return cand
            values[name] = (value, unit)
            cand.inputs.append({"name": name, **desc})
        try:
            cand.value, value_assumptions = transforms.apply_detailed(
                rule.transform.id,
                values,
                rule.transform.params,
                rule.transform.output_unit,
            )
        except (transforms.TransformError, units.UnitError) as exc:
            cand.error = f"transformación {rule.transform.id}: {exc}"
            return cand
        # declarados + implícitos de la transformación + dependientes de los valores
        # (p. ej. el error del ajuste de ac_coast_ref; G2.7, D-G26a)
        cand.assumptions = (
            list(rule.assumptions)
            + transforms.implied_assumptions(rule.transform.id, rule.transform.params)
            + value_assumptions
        )
        return cand

    # --- validaciones y confianza ---------------------------------------------------
    def _vehicle_validations(self, covers: str) -> list[S.Validation]:
        vs = [
            v
            for v in self.ctx.validations
            if covers in v.covers
            and v.vehicle == self.ctx.vehicle
            and (not v.variant or v.variant == self.ctx.variant)
        ]
        # G2.29: lo MEDIDO en el juego manda sobre lo DECLARADO en la ficha. Si el mismo
        # hecho tiene una validación in_game_value superada, la info_reference fallida
        # (p. ej. el Weight de Automation, menor que la masa real en BeamNG en los
        # 40340) no rebaja: el objetivo es reproducir el vehículo del juego
        if any(v.method == "in_game_value" and v.result == "pass" for v in vs):
            vs = [
                v
                for v in vs
                if not (v.method == "info_reference" and v.result == "fail")
            ]
        return vs

    def _confidence(
        self, rule: S.Rule, cand: _Candidate, conflict: bool
    ) -> tuple[int, list[str], bool]:
        crit: list[str] = []
        level = 0
        e1 = bool(rule.evidence) and rule.transform.output_unit != "undocumented"
        crit.append(f"E1 evidencia y unidad documentada: {'sí' if e1 else 'no'}")
        e2 = e1 and bool(rule.source_doc) and bool(rule.target_doc)
        crit.append(f"E2 definición en origen y destino: {'sí' if e2 else 'no'}")
        semantics = (
            any(r.type == "code" for r in rule.source_doc) or rule.kind == "format"
        )
        rule_val = [
            self.catalog.validations[v]
            for v in rule.validations
            if v in self.catalog.validations
        ]
        own = self._vehicle_validations(rule.target)
        numeric = any(
            v.result == "pass" and v.method != "in_game_functional"
            for v in rule_val + own
        )
        clean = not cand.assumptions and rule.kind in ("direct", "measured", "format")
        e3 = e2 and semantics and (numeric or clean)
        crit.append(
            "E3 semántica en código y (validación numérica o regla directa "
            "sin supuestos): "
            f"{'sí' if e3 else 'no'}"
        )
        own_pass = any(
            v.result == "pass" for v in own if v.method != "in_game_functional"
        )
        e4 = e3 and own_pass
        crit.append(f"E4 validación superada en este vehículo: {'sí' if e4 else 'no'}")
        level = 4 if e4 else 3 if e3 else 2 if e2 else 1 if e1 else 0
        failed = [v.id for v in own if v.result == "fail"]
        via: set[str] = set()
        for inp in cand.inputs:
            ref = inp.get("fact") or inp.get("attribute")
            failed += [
                v.id for v in self._vehicle_validations(ref) if v.result == "fail"
            ]
            # G2.5a: la validación que cubre un hecho equivalente demostrado (p. ej.
            # bng.mass.total para ir.mass.total) alcanza también a esta entrada.
            for eq in self.ctx.fact_equivalents.get(ref, ()):
                hits = [
                    v.id
                    for v in self._vehicle_validations(eq)
                    if v.result == "fail" and ref not in v.covers
                ]
                failed += hits
                via |= {f"{h} cubre {eq} ≡ {ref}" for h in hits}
        if via:
            crit.append(
                f"equivalencia exacta de hechos para validaciones: {sorted(via)}"
            )
        if conflict and level > 0:
            level -= 1
            crit.append("rebaja: conflicto abierto (−1)")
        if failed:
            level = min(level, 1)
            crit.append(f"rebaja: validación fallida {sorted(set(failed))} (máx. E1)")
        return level, crit, bool(failed) or own_pass

    def _promotion(self, rule: S.Rule) -> dict[str, Any]:
        """¿Podría ampliarse el ámbito según el plan §6.1? Solo se informa; nunca se
        aplica."""
        vehicles = {
            self.catalog.validations[v].vehicle
            for v in rule.validations
            if v in self.catalog.validations
            and self.catalog.validations[v].result == "pass"
            and self.catalog.validations[v].method != "in_game_functional"
        }
        if any(v.result == "pass" for v in self._vehicle_validations(rule.target)):
            vehicles.add(self.ctx.vehicle)
        eligible = len(vehicles) >= 2 and not rule.open_issues
        return {
            "validated_vehicles": sorted(vehicles),
            "family_scope_eligible": eligible,
            "why": (
                "≥ 2 vehículos con validación numérica superada "
                "y sin problemas abiertos"
                if eligible
                else "faltan validaciones independientes en "
                "≥ 2 vehículos (plan §6.1) o hay problemas "
                "abiertos"
            ),
            "applied": False,
        }

    # --- resolución -------------------------------------------------------------------
    def resolve(self, target: str) -> Resolution:
        if target in self._memo:
            return self._memo[target]
        if target in self._stack:
            return Resolution(
                target,
                "manual_required",
                reasons=[f"dependencia circular: {self._stack}"],
            )
        self._stack.append(target)
        try:
            res = self._resolve(target)
        finally:
            self._stack.pop()
        self._memo[target] = res
        return res

    def _resolve(self, target: str) -> Resolution:
        attr = self.catalog.attributes.get(target)
        out = Resolution(target, "manual_required", unit=attr.unit if attr else "")
        cands = [self._evaluate(r) for r in self.catalog.rules_for(target)]
        for c in cands:
            if c.error and not c.not_applicable and not c.applicable_only:
                out.rejected.append(
                    {
                        "rule": c.rule.key,
                        "reason": c.error,
                        "scope": c.scope_why,
                        "in_scope": str(c.in_scope),
                    }
                )
        applied = [
            c for c in cands if c.in_scope and not c.error and not c.applicable_only
        ]
        na = [c for c in cands if c.in_scope and c.not_applicable]
        proposals = [
            c for c in cands if not c.in_scope and not c.error and not c.applicable_only
        ]

        # 1. decisiones manuales
        decisions = [
            d
            for d in self.catalog.active_decisions(self.ctx.vehicle, target)
            if d.scope.level != "variant" or d.variant == self.ctx.variant
        ]
        if decisions:
            decisions.sort(key=lambda d: LEVEL_RANK[d.scope.level])
            best = [d for d in decisions if d.scope.level == decisions[0].scope.level]
            return self._from_decision(out, best, applied, attr)

        # 2. reglas aplicadas. «No aplica» es un hecho estructural del vehículo (p. ej.
        # no hay 5.ª marcha): vale aunque la regla sea de otro ámbito, porque no asigna
        # ningún valor.
        na_any = na or [c for c in cands if not c.in_scope and c.not_applicable]
        if na_any and not applied:
            c = min(na_any, key=lambda c: LEVEL_RANK[c.rule.scope.level])
            out.status, out.source, out.rule = "not_applicable", "rule", c.rule.key
            out.reasons.append(c.error)
            return out
        if applied:
            applied.sort(key=lambda c: LEVEL_RANK[c.rule.scope.level])
            top = [
                c for c in applied if c.rule.scope.level == applied[0].rule.scope.level
            ]
            chosen = top[0]
            conflict = [c for c in top[1:] if not _same_value(c.value, chosen.value)]
            keys = {c.rule.key for c in applied} | {c.rule.id for c in applied}
            incompatible = [
                c.rule.key
                for c in applied
                for other in c.rule.incompatible_with
                if other in keys
            ]
            out.alternatives = [
                {"rule": c.rule.key, "value": c.value, "scope": c.scope_why}
                for c in applied[1:]
            ]
            self._fill_from_rule(out, chosen, "rule", attr)
            out.reasons.insert(
                0,
                f"regla {chosen.rule.key} aplicada ({chosen.scope_why}; "
                "nivel más específico)",
            )
            if conflict or incompatible:
                out.status = "ambiguous"
                if conflict:
                    out.open_issues.append(
                        "reglas del mismo nivel con valores distintos: "
                        + ", ".join(f"{c.rule.key}={_short(c.value)}" for c in conflict)
                    )
                if incompatible:
                    out.open_issues.append(
                        "reglas incompatibles aplicables a la vez: "
                        f"{sorted(set(incompatible))}"
                    )
                lvl, crit, _ = self._confidence(chosen.rule, chosen, True)
                out.confidence, out.confidence_criteria = f"E{lvl}", crit
            return out

        # 3. propuestas (reglas compatibles de otro ámbito)
        if proposals:
            proposals.sort(key=lambda c: LEVEL_RANK[c.rule.scope.level])
            chosen = proposals[0]
            self._fill_from_rule(out, chosen, "proposal", attr)
            out.requires_approval = True
            out.promotion = self._promotion(chosen.rule)
            if out.status not in (
                "format_requirement",
                "not_applicable",
                "manual_required",
            ):
                out.status = "ambiguous" if out.status == "ambiguous" else "inferred"
            out.reasons.insert(
                0,
                f"propuesta de {chosen.rule.key} (ámbito {chosen.scope_why}; "
                "este vehículo "
                "no está en el ámbito): calculada con los "
                "hechos de este vehículo, NO aplicada",
            )
            others = [
                c for c in proposals[1:] if not _same_value(c.value, chosen.value)
            ]
            if others:
                out.status = "ambiguous"
                out.open_issues.append(
                    "propuestas con valores distintos: "
                    + ", ".join(f"{c.rule.key}={_short(c.value)}" for c in others)
                )
            return out

        out.reasons.append("ninguna regla ni decisión aplicable")
        return out

    def _fill_from_rule(
        self, out: Resolution, c: _Candidate, source: str, attr: S.AttributeDef | None
    ) -> None:
        r = c.rule
        out.value, out.unit, out.source, out.rule = (
            c.value,
            r.transform.output_unit,
            source,
            r.key,
        )
        out.transform = S._to_plain(r.transform)
        out.inputs = c.inputs
        out.assumptions = c.assumptions
        out.open_issues = list(r.open_issues)
        if (
            attr is not None
            and attr.unit not in ("undocumented",)
            and not units.compatible(r.transform.output_unit, attr.unit)
            and r.transform.output_unit != attr.unit
        ):
            out.status = "ambiguous"
            out.open_issues.append(
                f"unidad de la regla {r.transform.output_unit!r} incompatible con la "
                f"del atributo {attr.unit!r}"
            )
            return
        lvl, crit, own_validated = self._confidence(r, c, False)
        out.confidence, out.confidence_criteria = f"E{lvl}", crit
        failed = any("validación fallida" in x for x in crit)
        if r.kind == "format":
            out.status = "format_requirement"
        elif failed:
            # hay valor, pero una validación de este vehículo lo contradice
            out.status = "ambiguous"
            out.open_issues.append(
                "una validación de este vehículo que cubre "
                "el atributo o una entrada ha fallado"
            )
        elif lvl < 2:
            out.status = "manual_required"
            out.reasons.append(
                f"regla insuficientemente justificada (E{lvl} < E2): no se aplica"
            )
        elif out.open_issues:
            out.status = "ambiguous"
        elif lvl >= 3 and (not c.assumptions or own_validated):
            out.status = "known"
        else:
            out.status = "inferred"

    def _from_decision(
        self,
        out: Resolution,
        best: list[S.Decision],
        applied: list[_Candidate],
        attr: S.AttributeDef | None,
    ) -> Resolution:
        d = best[0]
        out.source, out.decision = "decision", d.id
        values = {repr(x.value) for x in best}
        out.reasons.append(
            f"decisión manual {d.id} ({d.scope.level}, {d.status}): {d.justification}"
        )
        if len(values) > 1:
            out.status = "ambiguous"
            out.open_issues.append(
                f"decisiones vigentes con valores distintos: {[x.id for x in best]}"
            )
            return out
        if d.action in ("no_correspondence", "defer"):
            out.status = "manual_required"
            out.reasons.append(
                "declarado sin correspondencia conocida"
                if d.action == "no_correspondence"
                else "decisión aplazada"
            )
            return out
        value, unit = d.value, d.unit
        if d.action == "select_reference":
            value = d.reference.get("value", d.value)
            unit = d.reference.get("unit", d.unit)
        elif d.action == "transform" and d.transform is not None:
            vals: dict[str, tuple[Any, str]] = {}
            for name, ref in d.inputs.items():
                got = self._input(ref)
                if isinstance(got, str):
                    out.status = "manual_required"
                    out.reasons.append(f"entrada '{name}' de la decisión: {got}")
                    return out
                vals[name] = (got[0], got[1])
                out.inputs.append({"name": name, **got[2]})
            try:
                value, value_assumptions = transforms.apply_detailed(
                    d.transform.id, vals, d.transform.params, d.transform.output_unit
                )
                unit = d.transform.output_unit
                out.assumptions = list(out.assumptions) + value_assumptions
            except (transforms.TransformError, units.UnitError) as exc:
                out.status = "ambiguous"
                out.open_issues.append(f"transformación de la decisión: {exc}")
                return out
        out.value, out.unit = value, unit
        if (
            attr is not None
            and attr.unit != "undocumented"
            and unit != "undocumented"
            and unit != attr.unit
            and not units.compatible(unit, attr.unit)
        ):
            out.status = "ambiguous"
            out.open_issues.append(
                f"unidad de la decisión {unit!r} incompatible con {attr.unit!r}"
            )
            return out
        for c in applied:
            if _same_value(c.value, value):
                continue
            agrees = (
                same_as_written(str(value), c.value)
                if isinstance(value, (int, float))
                else False
            )
            out.alternatives.append(
                {
                    "rule": c.rule.key,
                    "value": c.value,
                    "note": (
                        "la regla coincide con la decisión con su precisión escrita"
                        if agrees
                        else "regla aplicable con otro valor; prevalece la decisión"
                    ),
                }
            )
        if d.status == "validated":
            out.status, out.confidence = "known", "E4"
            out.confidence_criteria = ["decisión manual validada"]
        elif d.status == "provisional":
            out.status, out.confidence = "inferred", "E1"
            out.confidence_criteria = ["decisión manual provisional (sin validar)"]
        else:
            out.status = "manual_required"
            out.reasons.append("decisión pendiente de aprobación")
        return out

    def resolve_all(self, targets: list[str]) -> list[Resolution]:
        self._prefit_tyres(targets)
        return [self.resolve(t) for t in targets]

    def _prefit_tyres(self, targets: list[str]) -> None:
        """Adelanta en paralelo los ajustes SCTM que van a pedir las reglas. No
        resuelve ni decide nada: si no acierta o falla, la resolución los calcula."""
        from src.ac import tyre_fit

        if not tyre_fit.PARALLEL:
            return
        try:
            wanted = set(targets)
            rules = self.catalog.rules.values()
            old = {r.supersedes for r in rules if r.supersedes}
            jobs: dict[tuple, tuple] = {}
            for rule in rules:
                if (
                    rule.target not in wanted
                    or rule.transform is None
                    or rule.transform.id != "ac_sctm_fit"
                    or rule.key in old
                    or rule.status in ("rejected", "superseded")
                    or not in_scope(rule.scope, self.ctx)[0]
                ):
                    continue
                conditions = [*rule.conditions, *rule.applies_when]
                if any(
                    c.fact in self.catalog.attributes
                    or eval_condition(c, self.ctx)[0] is not True
                    for c in conditions
                ):
                    continue
                samples = self.ctx.fact(rule.inputs["samples"])
                load = self.ctx.fact(rule.inputs["fz0"])
                if samples is None or load is None or "estimated_sctm" in samples.value:
                    continue
                fz0 = transforms._convert(load.value, load.unit, "N", "fz0")
                ref = rule.transform.params.get("kappa_reference")
                free = ref == "free_rolling_at_alpha"
                jobs[(id(samples.value), fz0, free)] = (samples.value, fz0, free)
            tyre_fit.prefit(list(jobs.values()))
        except Exception:  # noqa: BLE001 - un adelanto nunca cambia la resolución
            return


def _trace_value(value: Any, unit: str) -> Any:
    """Valor de una entrada tal como se guarda en la traza del resultado.

    G2.20: un ``dataset`` (p. ej. las ~6000 muestras fuerza–deslizamiento de un eje) se
    resume con su huella y su tamaño; el valor íntegro sigue en el hecho/IR y en el
    registro de medida. Sin esto cada clave resuelta copiaba el conjunto entero."""
    if unit == "dataset" and isinstance(value, dict):
        import hashlib
        import json

        blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return {
            "dataset_sha256": hashlib.sha256(blob).hexdigest(),
            "columns": [c[0] for c in value.get("columns", [])],
            "rows": len(value.get("rows", [])),
        }
    return value


def _same_value(a: Any, b: Any) -> bool:
    if (
        isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and not isinstance(a, bool)
    ):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b
