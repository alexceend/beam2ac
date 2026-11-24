"""Esquema versionado, unidades, transformaciones y almacén del modelo de atributos."""

import json
import tempfile
import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs import transforms, units
from src.attrs.store import (
    REPO_CATALOG,
    Catalog,
    StoreError,
    append_decision,
    write_rule,
)

ROOT = Path(__file__).resolve().parents[1]


def make_rule(rid="r", target="ac:x", version=1, **kw) -> S.Rule:
    base = dict(
        id=rid,
        version=version,
        target=target,
        kind="direct",
        transform=S.Transform(id="identity", output_unit="m", input_units={"x": "m"}),
        scope=S.Scope(level="vehicle", vehicle="car_a"),
        inputs={"x": "bng.len"},
        evidence=[S.EvidenceRef(type="doc", path="d.md")],
    )
    base.update(kw)
    return S.Rule(**base)


def make_decision(did="d1", **kw) -> S.Decision:
    base = dict(
        id=did,
        target="ac:x",
        vehicle="car_a",
        action="manual_value",
        justification="porque sí",
        author="test",
        date="2026-10-26",
        scope=S.Scope(level="vehicle", vehicle="car_a"),
        value=1.0,
        unit="m",
    )
    base.update(kw)
    return S.Decision(**base)


class TestSchema(unittest.TestCase):
    def test_round_trip_all_entities(self):
        objs = [
            make_rule(
                conditions=[S.Condition(fact="bng.x", op="eq", value=1)],
                exceptions=[
                    S.RuleException(
                        scope=S.Scope(level="vehicle", vehicle="b"), reason="r"
                    )
                ],
                history=[
                    S.HistoryEntry(
                        date="2026-10-26", author="a", action="create", reason="r"
                    )
                ],
            ),
            make_decision(
                transform=S.Transform(id="scale", params={"factor": 2}, output_unit="m")
            ),
            S.Validation(
                id="v",
                vehicle="car_a",
                method="info_reference",
                covers=["ac:x"],
                result="pass",
            ),
            S.AttributeDef(id="ac:x", game="assettocorsa", unit="m"),
            S.Family(
                id="f",
                description="d",
                membership=[S.Condition(fact="bng.t", op="exists")],
            ),
        ]
        for obj in objs:
            with self.subTest(type(obj).__name__):
                doc = json.loads(json.dumps(S.to_dict(obj)))
                self.assertEqual(S.from_dict(doc), obj)
                self.assertTrue(doc["schema"].startswith("beam2ac."))

    def test_strict_validation(self):
        good = S.to_dict(make_rule())
        bad_cases = {
            "campo desconocido": {**good, "extra": 1},
            "enumerado": {**good, "kind": "magia"},
            "estado": {**good, "status": "aprobadísima"},
            "versión futura": {**good, "schema": "beam2ac.rule/99"},
            "entidad desconocida": {**good, "schema": "beam2ac.cosa/1"},
            "sin etiqueta": {k: v for k, v in good.items() if k != "schema"},
            "falta obligatorio": {k: v for k, v in good.items() if k != "transform"},
            "unidad": {
                **good,
                "transform": {**good["transform"], "output_unit": "furlong"},
            },
            "tipo": {**good, "version": "1"},
            "lista": {**good, "assumptions": "no es lista"},
            "operador": {
                **good,
                "conditions": [{"fact": "a", "op": "parecido", "value": 1}],
            },
        }
        for name, doc in bad_cases.items():
            with self.subTest(name):
                with self.assertRaises(S.SchemaError):
                    S.from_dict(doc)

    def test_decision_requires_justification_and_date(self):
        doc = S.to_dict(make_decision())
        for field, value in (
            ("justification", "  "),
            ("date", "26/10/2026"),
            ("author", ""),
        ):
            with self.subTest(field):
                with self.assertRaises(S.SchemaError):
                    S.from_dict({**doc, field: value})

    def test_expected_entity(self):
        with self.assertRaises(S.SchemaError):
            S.from_dict(S.to_dict(make_decision()), expected="rule")

    def test_migration_path_is_applied(self):
        # Simula una versión 2 del esquema de decisiones que renombra 'justification'.
        old = S.to_dict(make_decision())
        S.CURRENT_VERSION["decision"] = 2
        S.MIGRATIONS[("decision", 1)] = lambda d: d  # 1 -> 2 sin cambios de campos
        try:
            migrated = S.from_dict(old)
            self.assertEqual(migrated.justification, "porque sí")
            self.assertEqual(S.to_dict(migrated)["schema"], "beam2ac.decision/2")
            del S.MIGRATIONS[("decision", 1)]
            with self.assertRaises(S.SchemaError):  # sin migración registrada
                S.from_dict(old)
        finally:
            S.MIGRATIONS.pop(("decision", 1), None)
            S.CURRENT_VERSION["decision"] = 1


class TestUnitsAndTransforms(unittest.TestCase):
    def test_units(self):
        self.assertAlmostEqual(units.convert(1, "psi", "kPa"), 6.894757293, places=6)
        self.assertAlmostEqual(
            units.convert(6000, "rpm", "rad/s"), 628.3185307, places=6
        )
        self.assertTrue(units.compatible("table:rpm->N·m", "table:rad/s->N·m"))
        for a, b in (
            ("N/m", "N·m"),
            ("m", "kg"),
            ("undocumented", "undocumented"),
            ("text", "enum"),
            ("table:rpm->N·m", "N·m"),
        ):
            with self.subTest((a, b)):
                self.assertFalse(units.compatible(a, b))
        with self.assertRaises(units.UnitError):
            units.validate_unit("furlong")

    def test_transforms(self):
        self.assertEqual(
            transforms.apply("identity", {"x": (1000, "mm")}, {}, "m"), 1.0
        )
        self.assertEqual(transforms.apply("constant", {}, {"value": 800}, "rpm"), 800)
        self.assertEqual(
            transforms.apply(
                "linear_sum",
                {"a": (10, "kg"), "b": (2, "kg")},
                {"coefficients": {"a": 1, "b": -2}},
                "kg",
            ),
            6,
        )
        self.assertEqual(
            transforms.apply(
                "table", {"x": ([[1000, 10]], "table:rpm->N·m")}, {}, "table:rpm->N·m"
            ),
            [[1000, 10]],
        )
        self.assertEqual(
            transforms.apply(
                "assume_unit",
                {"x": (70, "N/m")},
                {"declared": "N/m", "as_unit": "N·m"},
                "N·m",
            ),
            70,
        )
        self.assertTrue(
            transforms.implied_assumptions(
                "assume_unit", {"declared": "N/m", "as_unit": "N·m"}
            )
        )
        bad = [
            ("identity", {"x": (70, "N/m")}, {}, "N·m"),
            ("linear_sum", {"a": (1, "kg")}, {"coefficients": {"a": 1, "b": 1}}, "kg"),
            (
                "assume_unit",
                {"x": (70, "N·m")},
                {"declared": "N/m", "as_unit": "N·m"},
                "N·m",
            ),
            ("constant", {"x": (1, "m")}, {"value": 1}, "m"),
            ("desconocida", {}, {}, "m"),
        ]
        for case in bad:
            with self.subTest(case[0]):
                with self.assertRaises(transforms.TransformError):
                    transforms.apply(*case)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "attributes").mkdir()
        (self.root / "attributes" / "a.json").write_text(
            json.dumps(
                [S.to_dict(S.AttributeDef(id="ac:x", game="assettocorsa", unit="m"))]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_repo_catalog_is_consistent(self):
        cat = Catalog.load(REPO_CATALOG)
        self.assertGreater(len(cat.rules), 40)
        self.assertEqual(len(cat.decisions), 158)
        self.assertIn("automation_export", cat.families)

    def test_rules_are_immutable(self):
        write_rule(self.root, make_rule())
        with self.assertRaises(StoreError):
            write_rule(self.root, make_rule())
        v2 = make_rule(version=2, supersedes="r@1")
        write_rule(self.root, v2)
        cat = Catalog.load(self.root)
        self.assertEqual(
            [r.key for r in cat.rules_for("ac:x")], ["r@2"]
        )  # la sustituida no cuenta

    def test_rule_filename_and_references_are_checked(self):
        rules = self.root / "rules"
        rules.mkdir()
        (rules / "otro@1.json").write_text(
            json.dumps(S.to_dict(make_rule())), encoding="utf-8"
        )
        with self.assertRaises(StoreError):
            Catalog.load(self.root)
        (rules / "otro@1.json").unlink()
        write_rule(self.root, make_rule(target="ac:inexistente"))
        with self.assertRaises(StoreError):
            Catalog.load(self.root)

    def test_decisions_append_only_with_history(self):
        cat = Catalog.load(self.root)
        append_decision(self.root, make_decision("d1", value=1.0), cat)
        append_decision(self.root, make_decision("d2", value=2.0, supersedes="d1"), cat)
        with self.assertRaises(StoreError):
            append_decision(self.root, make_decision("d1"), cat)  # id repetido
        with self.assertRaises(StoreError):
            append_decision(self.root, make_decision("d3", supersedes="nada"), cat)
        cat = Catalog.load(self.root)
        self.assertEqual([d.id for d in cat.active_decisions("car_a", "ac:x")], ["d2"])
        self.assertEqual([d.id for d in cat.decision_history("ac:x")], ["d1", "d2"])
        # revertir = nueva decisión que restaura el valor anterior; la historia se
        # conserva
        append_decision(self.root, make_decision("d3", value=1.0, supersedes="d2"), cat)
        cat = Catalog.load(self.root)
        self.assertEqual(
            [d.value for d in cat.active_decisions("car_a", "ac:x")], [1.0]
        )
        self.assertEqual(len(cat.decision_history("ac:x")), 3)
        lines = (
            (self.root / "decisions" / "car_a.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(len(lines), 3)

    def test_writes_outside_allowed_roots_are_refused(self):
        from src.safe_paths import ProtectedPathError

        with self.assertRaises(ProtectedPathError):
            write_rule(ROOT / "sample_beamng" / "nope", make_rule())


if __name__ == "__main__":
    unittest.main()
