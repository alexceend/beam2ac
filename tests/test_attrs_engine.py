"""Motor de resolución con catálogos sintéticos (sin datos de los juegos)."""

import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine, VehicleContext
from src.attrs.facts_bng import Fact
from src.attrs.store import Catalog

EV = [S.EvidenceRef(type="doc", path="d.md")]
CODE = [S.EvidenceRef(type="code", path="src/x.py", locator="f")]


def attr(aid, unit="m"):
    return S.AttributeDef(id=aid, game="assettocorsa", unit=unit)


def rule(
    rid,
    target="ac:x",
    level="vehicle",
    vehicle="car_a",
    family="",
    inputs=None,
    unit="m",
    in_unit="m",
    version=1,
    **kw,
):
    scope = S.Scope(
        level=level,
        vehicle=vehicle if level in ("vehicle", "variant") else "",
        family=family,
    )
    base = dict(
        id=rid,
        version=version,
        target=target,
        kind="direct",
        transform=S.Transform(
            id="identity", output_unit=unit, input_units={"x": in_unit}
        ),
        scope=scope,
        inputs=inputs if inputs is not None else {"x": "bng.len"},
        evidence=EV,
        source_doc=CODE,
        target_doc=EV,
        status="validated",
    )
    base.update(kw)
    return S.Rule(**base)


def catalog(rules=(), decisions=(), validations=(), families=(), attrs=None):
    cat = Catalog(Path("."))
    for a in attrs or [attr("ac:x"), attr("ac:y", "kg"), attr("ac:z", "kg")]:
        cat.attributes[a.id] = a
    for r in rules:
        cat.rules[r.key] = r
    cat.decisions = list(decisions)
    for v in validations:
        cat.validations[v.id] = v
    for f in families:
        cat.families[f.id] = f
    cat.check()
    return cat


def ctx(vehicle="car_a", facts=None, validations=()):
    facts = (
        facts
        if facts is not None
        else {"bng.len": Fact("bng.len", 2.5, "m", "measured")}
    )
    return VehicleContext(vehicle, "v1", facts, list(validations))


def resolve(cat, c, target="ac:x"):
    return ResolutionEngine(cat, c).resolve(target)


class TestRuleSelection(unittest.TestCase):
    def test_known_direct_rule_in_scope(self):
        r = resolve(catalog([rule("a")]), ctx())
        self.assertEqual(
            (r.status, r.value, r.source, r.confidence), ("known", 2.5, "rule", "E3")
        )

    def test_most_specific_scope_wins(self):
        fam = S.Family(
            id="f",
            description="d",
            membership=[S.Condition(fact="bng.len", op="exists")],
        )
        cat = catalog(
            [
                rule(
                    "fam",
                    level="family",
                    family="f",
                    inputs={},
                    transform=S.Transform(
                        id="constant", output_unit="m", params={"value": 9.0}
                    ),
                ),
                rule("veh"),
            ],
            families=[fam],
        )
        r = resolve(cat, ctx())
        self.assertEqual((r.rule, r.value), ("veh@1", 2.5))
        self.assertEqual(r.alternatives[0]["rule"], "fam@1")

    def test_same_level_conflict_is_ambiguous(self):
        other = rule(
            "b",
            inputs={},
            transform=S.Transform(
                id="constant", output_unit="m", params={"value": 3.0}
            ),
        )
        r = resolve(catalog([rule("a"), other]), ctx())
        self.assertEqual(r.status, "ambiguous")
        self.assertTrue(any("valores distintos" in i for i in r.open_issues))

    def test_incompatible_rules_are_ambiguous_even_with_equal_values(self):
        r = resolve(catalog([rule("a", incompatible_with=["b"]), rule("b")]), ctx())
        self.assertEqual(r.status, "ambiguous")
        self.assertTrue(any("incompatibles" in i for i in r.open_issues))

    def test_exception_excludes_vehicle(self):
        exc = S.RuleException(
            scope=S.Scope(level="vehicle", vehicle="car_a"),
            reason="caso conocido distinto",
        )
        r = resolve(
            catalog([rule("a", level="generic", vehicle="", exceptions=[exc])]), ctx()
        )
        self.assertEqual(r.status, "manual_required")
        self.assertIn("excepción", r.rejected[0]["reason"])

    def test_code_condition_blocks_other_code_version(self):
        cond = [S.Condition(fact="bng.code.engine_sha256", op="eq", value="aaa")]
        facts = {
            "bng.len": Fact("bng.len", 2.5, "m", "measured"),
            "bng.code.engine_sha256": Fact(
                "bng.code.engine_sha256", "bbb", "text", "extracted"
            ),
        }
        r = resolve(
            catalog([rule("a", level="generic", vehicle="", conditions=cond)]),
            ctx(facts=facts),
        )
        self.assertEqual(r.status, "manual_required")
        self.assertIn("condición no cumplida", r.rejected[0]["reason"])

    def test_vehicle_rule_never_applied_to_another_vehicle(self):
        r = resolve(catalog([rule("a", vehicle="car_a")]), ctx(vehicle="car_b"))
        self.assertEqual(r.source, "proposal")
        self.assertTrue(r.requires_approval)
        self.assertEqual(r.status, "inferred")
        self.assertFalse(r.promotion["applied"])
        self.assertFalse(r.promotion["family_scope_eligible"])

    def test_promotion_is_reported_but_not_applied(self):
        vals = [
            S.Validation(
                id="va",
                vehicle="car_a",
                method="info_reference",
                covers=["ac:x"],
                result="pass",
            )
        ]
        own = [
            S.Validation(
                id="vb",
                vehicle="car_b",
                method="info_reference",
                covers=["ac:x"],
                result="pass",
            )
        ]
        cat = catalog([rule("a", validations=["va"])], validations=vals)
        r = resolve(cat, ctx(vehicle="car_b", validations=own))
        self.assertTrue(r.promotion["family_scope_eligible"])
        self.assertFalse(r.promotion["applied"])
        self.assertTrue(r.requires_approval)

    def test_functional_test_is_not_value_validation(self):
        vals = [
            S.Validation(
                id="va",
                vehicle="car_a",
                method="in_game_functional",
                covers=["ac:x"],
                result="pass",
            )
        ]
        r = resolve(
            catalog([rule("a", kind="computed", validations=["va"])], validations=vals),
            ctx(),
        )
        self.assertEqual((r.status, r.confidence), ("inferred", "E2"))


class TestUnitsEvidenceAndStatus(unittest.TestCase):
    def test_incompatible_units_are_detected(self):
        facts = {"bng.len": Fact("bng.len", 70, "N/m", "extracted")}
        r = resolve(
            catalog(
                [rule("a", unit="N·m", in_unit="N·m")], attrs=[attr("ac:x", "N·m")]
            ),
            ctx(facts=facts),
        )
        self.assertEqual(r.status, "manual_required")
        self.assertIn("unidades incompatibles", r.rejected[0]["reason"])

    def test_explicit_unit_assumption_is_inferred(self):
        facts = {"bng.len": Fact("bng.len", 70, "N/m", "extracted")}
        t = S.Transform(
            id="assume_unit",
            output_unit="N·m",
            input_units={"x": "N/m"},
            params={"declared": "N/m", "as_unit": "N·m"},
        )
        r = resolve(
            catalog([rule("a", transform=t)], attrs=[attr("ac:x", "N·m")]),
            ctx(facts=facts),
        )
        self.assertEqual((r.status, r.value), ("inferred", 70))
        self.assertTrue(any("se interpreta como" in a for a in r.assumptions))

    def test_rule_without_evidence_is_not_applied(self):
        r = resolve(
            catalog([rule("a", evidence=[], source_doc=[], target_doc=[])]), ctx()
        )
        self.assertEqual(r.status, "manual_required")
        self.assertTrue(any("insuficientemente justificada" in x for x in r.reasons))

    def test_missing_fact_is_reported(self):
        r = resolve(catalog([rule("a")]), ctx(facts={}))
        self.assertEqual(r.status, "manual_required")
        self.assertIn("falta el hecho bng.len", r.rejected[0]["reason"])

    def test_open_issue_makes_ambiguous(self):
        r = resolve(catalog([rule("a", open_issues=["AC no modela X"])]), ctx())
        self.assertEqual(r.status, "ambiguous")

    def test_failed_validation_on_input_caps_confidence(self):
        val = S.Validation(
            id="m",
            vehicle="car_a",
            method="info_reference",
            covers=["bng.len"],
            result="fail",
        )
        r = resolve(catalog([rule("a")]), ctx(validations=[val]))
        self.assertEqual((r.status, r.confidence), ("ambiguous", "E1"))

    def test_not_applicable(self):
        cond = [S.Condition(fact="bng.len", op="gt", value=10)]
        r = resolve(catalog([rule("a", applies_when=cond)]), ctx())
        self.assertEqual(r.status, "not_applicable")
        r2 = resolve(
            catalog([rule("a", applies_when=cond)]), ctx(vehicle="car_b")
        )  # también fuera de ámbito
        self.assertEqual(r2.status, "not_applicable")

    def test_format_requirement(self):
        t = S.Transform(id="constant", output_unit="1", params={"value": 0})
        r = resolve(
            catalog(
                [
                    rule(
                        "a",
                        kind="format",
                        inputs={},
                        transform=t,
                        level="generic",
                        vehicle="",
                    )
                ],
                attrs=[attr("ac:x", "1")],
            ),
            ctx(),
        )
        self.assertEqual((r.status, r.value), ("format_requirement", 0))

    def test_no_rule(self):
        r = resolve(catalog([]), ctx())
        self.assertEqual((r.status, r.confidence), ("manual_required", "E0"))


class TestDecisionsAndDependencies(unittest.TestCase):
    def dec(self, did, value, status="validated", **kw):
        base = dict(
            id=did,
            target="ac:x",
            vehicle="car_a",
            action="manual_value",
            justification="j",
            author="t",
            date="2026-10-26",
            scope=S.Scope(level="vehicle", vehicle="car_a"),
            value=value,
            unit="m",
            status=status,
        )
        base.update(kw)
        return S.Decision(**base)

    def test_decision_overrides_rule_and_reports_alternative(self):
        r = resolve(catalog([rule("a")], decisions=[self.dec("d1", 3.0)]), ctx())
        self.assertEqual((r.status, r.value, r.source), ("known", 3.0, "decision"))
        self.assertEqual(r.alternatives[0]["rule"], "a@1")

    def test_superseded_and_rejected_decisions_are_ignored(self):
        decs = [
            self.dec("d1", 3.0),
            self.dec("d2", 4.0, supersedes="d1"),
            self.dec("d3", 5.0, status="rejected"),
        ]
        r = resolve(catalog([], decisions=decs), ctx())
        self.assertEqual(r.value, 4.0)

    def test_provisional_and_no_correspondence(self):
        r = resolve(
            catalog([], decisions=[self.dec("d1", 3.0, status="provisional")]), ctx()
        )
        self.assertEqual((r.status, r.confidence), ("inferred", "E1"))
        nc = self.dec("d2", None, action="no_correspondence")
        r = resolve(catalog([], decisions=[nc]), ctx())
        self.assertEqual(r.status, "manual_required")

    def test_decision_for_other_vehicle_is_not_used(self):
        r = resolve(catalog([], decisions=[self.dec("d1", 3.0)]), ctx(vehicle="car_b"))
        self.assertEqual(r.status, "manual_required")

    def test_decision_with_incompatible_unit(self):
        r = resolve(catalog([], decisions=[self.dec("d1", 3.0, unit="kg")]), ctx())
        self.assertEqual(r.status, "ambiguous")

    def test_dependency_on_other_attribute(self):
        t = S.Transform(
            id="linear_sum",
            output_unit="kg",
            input_units={"m": "kg", "h": "kg"},
            params={"coefficients": {"m": 1, "h": -2}},
        )
        total = rule(
            "tot",
            target="ac:y",
            unit="kg",
            inputs={"m": "bng.mass", "h": "ac:z"},
            transform=t,
            kind="computed",
        )
        facts = {"bng.mass": Fact("bng.mass", 100.0, "kg", "measured")}
        hub = S.Decision(
            id="hz",
            target="ac:z",
            vehicle="car_a",
            action="manual_value",
            justification="j",
            author="t",
            date="2026-10-26",
            scope=S.Scope(level="vehicle", vehicle="car_a"),
            value=10.0,
            unit="kg",
            status="provisional",
        )
        cat = catalog([total], decisions=[hub])
        r = resolve(cat, ctx(facts=facts), "ac:y")
        self.assertEqual(r.value, 80.0)
        r2 = resolve(catalog([total]), ctx(facts=facts), "ac:y")
        self.assertEqual(r2.status, "manual_required")
        self.assertIn("depende de ac:z", r2.rejected[0]["reason"])

    def test_circular_dependency(self):
        a = rule("a", target="ac:y", unit="kg", in_unit="kg", inputs={"x": "ac:z"})
        b = rule("b", target="ac:z", unit="kg", in_unit="kg", inputs={"x": "ac:y"})
        r = resolve(catalog([a, b]), ctx(), "ac:y")
        self.assertEqual(r.status, "manual_required")


if __name__ == "__main__":
    unittest.main()


class TestAttributeConditionsAndApplicability(unittest.TestCase):
    """Tarea B4: condiciones sobre atributos de destino y reglas de aplicabilidad."""

    def setUp(self):
        self.attrs = [attr("ac:type", "enum"), attr("ac:point", "m")]

    def type_rule(self, value="STRUT", **kw):
        t = S.Transform(id="constant", output_unit="enum", params={"value": value})
        return rule("t", target="ac:type", inputs={}, transform=t, unit="enum", **kw)

    def app_rule(self):
        return rule(
            "app",
            target="ac:point",
            level="generic",
            vehicle="",
            kind="applicability",
            inputs={},
            transform=S.Transform(
                id="constant", output_unit="m", params={"value": None}
            ),
            applies_when=[S.Condition(fact="ac:type", op="ne", value="STRUT")],
        )

    def test_not_applicable_when_type_excludes_key(self):
        cat = catalog([self.type_rule("STRUT"), self.app_rule()], attrs=self.attrs)
        r = resolve(cat, ctx(), "ac:point")
        self.assertEqual(r.status, "not_applicable")

    def test_applicability_rule_never_assigns_a_value(self):
        cat = catalog([self.type_rule("DWB"), self.app_rule()], attrs=self.attrs)
        r = resolve(cat, ctx(), "ac:point")
        self.assertEqual((r.status, r.value), ("manual_required", None))
        self.assertEqual(r.rejected, [])

    def test_unapproved_type_is_indeterminate(self):
        # la regla del tipo es de otro vehículo: solo es una propuesta, no decide
        cat = catalog(
            [self.type_rule("STRUT", vehicle="otro"), self.app_rule()], attrs=self.attrs
        )
        r = resolve(cat, ctx(), "ac:point")
        self.assertEqual(r.status, "manual_required")
        self.assertIn("aplicabilidad indeterminada", r.rejected[0]["reason"])

    def test_old_rule_documents_migrate(self):
        doc = S.to_dict(self.type_rule())
        self.assertEqual(doc["schema"], "beam2ac.rule/2")
        doc["schema"] = "beam2ac.rule/1"
        self.assertEqual(S.from_dict(doc), self.type_rule())
