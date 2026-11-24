"""G2.5a: las validaciones alcanzan a los hechos ``ir.*`` con equivalencia demostrada.

Riesgo corregido (informe G2 §7.5): la validación de masa cubre ``bng.mass.total`` y
``car.totalmass@2`` lee ``ir.mass.total``; con la validación fallida la @1 daba
``ambiguous``/E1 y la @2 ``inferred``/E2. Todo pasa por el mecanismo real:
``report.context_from_facts`` (validaciones automáticas + registradas en el catálogo +
equivalencias) y ``ResolutionEngine``.
"""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.facts import Fact
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import (
    STATIC,
    VALIDATION_FACT_EQUIVALENTS,
    validation_fact_equivalents,
)

from .test_attrs_engine import CODE, EV, attr, catalog, rule

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
PROJECT = ROOT / "output/projects/ibiza_35b84_b1"
TOTALMASS = "ac:car.ini/BASIC/TOTALMASS"
MASS_VALIDATION = "seat_ibiza_acfr.35b84.mass"
FAILED = "validación fallida"


def _ibiza_facts() -> tuple[object, dict[str, Fact]]:
    from src.importers.beamng import import_preview
    from src.ir.legacy_facts import ir_to_facts
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    src = load_vehicle_dir(IBIZA, "35b84.pc")
    return src, ir_to_facts(import_preview(preview_physics(src, 70.0), 70.0))


def _ibiza_catalog(version: int, extra: tuple[S.Validation, ...] = ()) -> Catalog:
    cat = Catalog.load(REPO_CATALOG, PROJECT if PROJECT.is_dir() else None)
    if version == 1:  # como antes de G2.4: la @1 vuelve a estar vigente
        cat.rules.pop("car.totalmass@2")
    for v in extra:
        cat.validations[v.id] = v
    return cat


def _resolve_totalmass(src, facts, version: int, extra=(), equivalents=True):
    cat = _ibiza_catalog(version, extra)
    ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
    if not equivalents:  # el comportamiento anterior a G2.5a, solo para contrastar
        ctx.fact_equivalents = {}
    return ctx, ResolutionEngine(cat, ctx).resolve(TOTALMASS)


def _with_declared_weight(facts: dict[str, Fact], delta: float) -> dict[str, Fact]:
    out = dict(facts)
    # G2.8: validations_bng lee ir.declared.weight (mismo parámetro que bng.info.weight)
    for fid in ("bng.info.weight", "ir.declared.weight"):
        w = out[fid]
        out[fid] = dataclasses.replace(w, value=w.value + delta)
    return out


def _failed_rebajas(res) -> list[str]:
    return [c for c in res.confidence_criteria if FAILED in c]


@unittest.skipUnless(IBIZA.is_dir() and PROJECT.is_dir(), "sin Ibiza o su proyecto")
class TestTotalMassValidation(unittest.TestCase):
    """``car.totalmass@1`` (``bng.mass.total``) frente a ``@2`` (``ir.mass.total``)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src, cls.facts = _ibiza_facts()

    def _pair(self, facts, extra=()):
        _, r1 = _resolve_totalmass(self.src, facts, 1, extra)
        ctx, r2 = _resolve_totalmass(self.src, facts, 2, extra)
        self.assertEqual((r1.rule, r2.rule), ("car.totalmass@1", "car.totalmass@2"))
        return ctx, r1, r2

    def test_failed_automatic_validation_same_result(self) -> None:
        # El peso declarado se aleja 10 kg: la validación automática real falla.
        facts = _with_declared_weight(self.facts, 10.0)
        ctx, r1, r2 = self._pair(facts)
        mass = [v for v in ctx.validations if v.id == MASS_VALIDATION]
        self.assertEqual([v.result for v in mass], ["fail"])
        self.assertEqual(mass[0].covers, ["bng.mass.total"])  # registro intacto
        self.assertEqual((r1.status, r1.confidence), ("ambiguous", "E1"))
        self.assertEqual(
            (r2.value, r2.unit, r2.status, r2.confidence),
            (r1.value, r1.unit, r1.status, r1.confidence),
        )
        self.assertEqual(r2.open_issues, r1.open_issues)
        for r in (r1, r2):
            self.assertEqual(
                _failed_rebajas(r),
                [f"rebaja: {FAILED} ['{MASS_VALIDATION}'] (máx. E1)"],
            )

    def test_without_equivalence_the_bug_reappears(self) -> None:
        # Contraste: sin la propagación, la @2 no ve el fallo (riesgo de §7.5).
        facts = _with_declared_weight(self.facts, 10.0)
        _, r2 = _resolve_totalmass(self.src, facts, 2, equivalents=False)
        self.assertEqual((r2.status, r2.confidence), ("inferred", "E2"))
        self.assertEqual(_failed_rebajas(r2), [])

    def test_failed_registered_validation_same_result(self) -> None:
        # Validación registrada en el catálogo (como la de spicy_mandala).
        reg = S.Validation(
            id="seat_ibiza_acfr.35b84.mass_registro_prueba",
            vehicle="seat_ibiza_acfr",
            variant="35b84",
            method="info_reference",
            covers=["bng.mass.total"],
            result="fail",
            expected=1000,
            observed=1226.27,
            tolerance=0.5,
            evidence=EV,
        )
        _, r1, r2 = self._pair(self.facts, (reg,))
        self.assertEqual((r1.status, r1.confidence), ("ambiguous", "E1"))
        self.assertEqual((r2.status, r2.confidence), (r1.status, r1.confidence))
        self.assertEqual(_failed_rebajas(r2), _failed_rebajas(r1))

    def test_passing_validation_same_result(self) -> None:
        ctx, r1, r2 = self._pair(self.facts)
        mass = [v for v in ctx.validations if v.id == MASS_VALIDATION]
        self.assertEqual([v.result for v in mass], ["pass"])
        self.assertNotEqual(r1.status, "ambiguous")
        self.assertEqual(
            (r2.value, r2.status, r2.confidence, r2.confidence_criteria),
            (r1.value, r1.status, r1.confidence, r1.confidence_criteria),
        )

    def test_only_the_closed_list_is_active(self) -> None:
        # Muchos pares bng.*/ir.* son idénticos en el Ibiza; solo la masa se activa.
        same = [
            b
            for p, b in STATIC.items()
            if b in self.facts
            and f"ir.{p}" in self.facts
            and self.facts[b].value == self.facts[f"ir.{p}"].value
        ]
        self.assertGreater(len(same), 10)
        self.assertEqual(
            validation_fact_equivalents(self.facts),
            {
                "bng.mass.total": ("ir.mass.total",),
                "ir.mass.total": ("bng.mass.total",),
            },
        )


# --- catálogo sintético (mismo motor y mismo context_from_facts) ---------------------

MASS = 1226.27


def _facts(ir_mass: float = MASS, weight: float = 1226.0) -> dict[str, Fact]:
    ref = [S.EvidenceRef(type="file", path="cfg.pc")]
    return {
        "bng.mass.total": Fact("bng.mass.total", MASS, "kg", "measured", list(ref)),
        "ir.mass.total": Fact("ir.mass.total", ir_mass, "kg", "measured", list(ref)),
        "bng.info.weight": Fact("bng.info.weight", weight, "kg", "declared"),
        "ir.declared.weight": Fact("ir.declared.weight", weight, "kg", "declared"),
        "bng.engine.peak_torque": Fact("bng.engine.peak_torque", 300.0, "N·m", "c"),
        "ir.engine.peak_torque": Fact("ir.engine.peak_torque", 300.0, "N·m", "c"),
    }


def _mass_rule(rid: str, fact: str) -> S.Rule:
    return rule(rid, target="ac:y", unit="kg", in_unit="kg", inputs={"x": fact})


def _torque_rule(rid: str, fact: str) -> S.Rule:
    return rule(rid, target="ac:t", unit="N·m", in_unit="N·m", inputs={"x": fact})


def _torque_failure() -> S.Validation:
    return S.Validation(
        id="car_a.v1.torque_prueba",
        vehicle="car_a",
        variant="v1",
        method="info_reference",
        covers=["bng.engine.peak_torque"],
        result="fail",
        evidence=EV,
    )


def _run(rules, facts, target, validations=()):
    attrs = [attr("ac:y", "kg"), attr("ac:t", "N·m")]
    cat = catalog(rules, validations=validations, attrs=attrs)
    ctx = context_from_facts("car_a", "v1", facts, cat)
    return ResolutionEngine(cat, ctx).resolve(target)


class TestSyntheticPropagation(unittest.TestCase):
    def test_closed_list_is_demonstrated_by_construction(self) -> None:
        for bng, ir in VALIDATION_FACT_EQUIVALENTS:
            self.assertEqual(STATIC[ir.removeprefix("ir.")], bng)
        self.assertEqual(
            VALIDATION_FACT_EQUIVALENTS, (("bng.mass.total", "ir.mass.total"),)
        )

    def test_mass_failure_reaches_ir_input(self) -> None:
        facts = _facts(weight=1200.0)  # Δ 26 kg: la validación automática falla
        r_bng = _run([_mass_rule("m", "bng.mass.total")], facts, "ac:y")
        r_ir = _run([_mass_rule("m", "ir.mass.total")], facts, "ac:y")
        self.assertEqual((r_bng.status, r_bng.confidence), ("ambiguous", "E1"))
        self.assertEqual((r_ir.status, r_ir.confidence), ("ambiguous", "E1"))
        self.assertTrue(
            any("equivalencia exacta" in c for c in r_ir.confidence_criteria)
        )
        self.assertFalse(
            any("equivalencia exacta" in c for c in r_bng.confidence_criteria)
        )

    def test_mass_pass_changes_nothing(self) -> None:
        facts = _facts()
        r_bng = _run([_mass_rule("m", "bng.mass.total")], facts, "ac:y")
        r_ir = _run([_mass_rule("m", "ir.mass.total")], facts, "ac:y")
        self.assertEqual((r_bng.status, r_bng.confidence), ("known", "E3"))
        self.assertEqual(
            (r_ir.status, r_ir.confidence, r_ir.confidence_criteria),
            (r_bng.status, r_bng.confidence, r_bng.confidence_criteria),
        )

    def test_ir_fact_without_listed_equivalence_is_not_affected(self) -> None:
        # bng.engine.peak_torque e ir.engine.peak_torque son el mismo parámetro del IR
        # (STATIC) e idénticos, pero no están en la lista cerrada: no se propaga.
        self.assertIn("engine.peak_torque", STATIC)
        facts = _facts()
        val = (_torque_failure(),)
        r_bng = _run([_torque_rule("t", "bng.engine.peak_torque")], facts, "ac:t", val)
        r_ir = _run([_torque_rule("t", "ir.engine.peak_torque")], facts, "ac:t", val)
        self.assertEqual((r_bng.status, r_bng.confidence), ("ambiguous", "E1"))
        self.assertEqual((r_ir.status, r_ir.confidence), ("known", "E3"))

    def test_not_identical_facts_are_not_equivalent(self) -> None:
        facts = _facts(ir_mass=MASS + 1.0, weight=1200.0)
        self.assertEqual(validation_fact_equivalents(facts), {})
        r_ir = _run([_mass_rule("m", "ir.mass.total")], facts, "ac:y")
        self.assertEqual((r_ir.status, r_ir.confidence), ("known", "E3"))
        for missing in ("bng.mass.total", "ir.mass.total"):
            f = _facts()
            del f[missing]
            self.assertEqual(validation_fact_equivalents(f), {})

    def test_bng_rules_keep_their_behaviour(self) -> None:
        # Mismo resultado con y sin equivalencias para reglas que leen bng.*.
        for weight in (1226.0, 1200.0):
            facts = _facts(weight=weight)
            attrs = [attr("ac:y", "kg"), attr("ac:t", "N·m")]
            cat = catalog(
                [
                    _mass_rule("m", "bng.mass.total"),
                    _torque_rule("t", "bng.engine.peak_torque"),
                ],
                validations=(_torque_failure(),),
                attrs=attrs,
            )
            ctx = context_from_facts("car_a", "v1", facts, cat)
            with_eq = [ResolutionEngine(cat, ctx).resolve(t) for t in ("ac:y", "ac:t")]
            ctx.fact_equivalents = {}
            without = [ResolutionEngine(cat, ctx).resolve(t) for t in ("ac:y", "ac:t")]
            for a, b in zip(with_eq, without):
                self.assertEqual(
                    (a.status, a.confidence, a.confidence_criteria, a.open_issues),
                    (b.status, b.confidence, b.confidence_criteria, b.open_issues),
                )

    def test_no_duplicate_or_double_downgrade(self) -> None:
        both = rule(
            "m",
            target="ac:y",
            unit="kg",
            kind="computed",
            inputs={"a": "bng.mass.total", "b": "ir.mass.total"},
            transform=S.Transform(
                id="linear_sum",
                output_unit="kg",
                input_units={"a": "kg", "b": "kg"},
                params={"coefficients": {"a": 0.5, "b": 0.5}},
            ),
            source_doc=CODE,
        )
        facts = _facts(weight=1200.0)
        explicit = S.Validation(
            id="car_a.v1.mass_ambos",
            vehicle="car_a",
            variant="v1",
            method="info_reference",
            covers=["bng.mass.total", "ir.mass.total"],
            result="fail",
            evidence=EV,
        )
        for extra in ((), (explicit,)):
            with self.subTest(explicit=bool(extra)):
                r = _run([both], facts, "ac:y", extra)
                self.assertEqual((r.status, r.confidence), ("ambiguous", "E1"))
                rebajas = _failed_rebajas(r)
                self.assertEqual(len(rebajas), 1)
                ids = sorted(["car_a.v1.mass", *(v.id for v in extra)])
                self.assertEqual(rebajas[0], f"rebaja: {FAILED} {ids} (máx. E1)")
                self.assertEqual(
                    sum("ha fallado" in i for i in r.open_issues), 1, r.open_issues
                )
        # Si la validación ya cubre ir.mass.total, no se cuenta por equivalencia.
        r = _run([_mass_rule("m", "ir.mass.total")], _facts(), "ac:y", (explicit,))
        self.assertFalse(any("equivalencia exacta" in c for c in r.confidence_criteria))
        self.assertEqual((r.status, r.confidence), ("ambiguous", "E1"))


if __name__ == "__main__":
    unittest.main()
