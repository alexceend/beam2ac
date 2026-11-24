"""G2.7, paso 1 (G2.6a): ``COAST_REF`` como transformación de destino sobre el IR.

* ``src.ac.coast_ref.coast_ref_linear`` = el algoritmo anterior de ``fit_coast_ref``.
* ``ac_coast_ref``: salidas, redondeo, errores y supuesto con el error del ajuste.
* ``eng.coast_*@2`` = ``@1`` en valor, unidad, estado y confianza (Ibiza real y casos
  sintéticos); solo cambian las entradas y se AÑADE el supuesto del error.
* Supuestos dependientes de los valores en el motor (reglas y decisiones).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from src.ac.coast_ref import CoastRefFit, coast_ref_linear
from src.ac.engine import fit_coast_ref
from src.attrs import schema as S
from src.attrs import transforms as T
from src.attrs.engine import ResolutionEngine
from src.attrs.facts import Fact
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.importers.beamng import import_preview
from src.ir.legacy_facts import ir_to_facts
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
CURVE_U = "table:rpm->N·m"
TARGETS = {
    "ac:engine.ini/COAST_REF/RPM": ("eng.coast_rpm", "rpm"),
    "ac:engine.ini/COAST_REF/TORQUE": ("eng.coast_torque", "torque"),
    "ac:engine.ini/COAST_REF/NON_LINEARITY": (
        "eng.coast_nonlinearity",
        "non_linearity",
    ),
}


def old_fit(coast, ceiling):
    """Copia literal del cuerpo de ``fit_coast_ref`` antes de G2.7."""
    points = [(r, t) for r, t in coast if r <= ceiling]
    if not points:
        return None
    ref_rpm, ref_torque = points[-1]
    errors = [(abs(ref_torque * r / ref_rpm - t), r) for r, t in points]
    worst, worst_rpm = max(errors)
    return CoastRefFit(ref_rpm, ref_torque, 0.0, worst, worst_rpm)


SYNTHETIC = [
    ([[1000, 10.0], [2000, 25.0], [3000, 30.0], [4000, 50.0]], 3500.0),
    ([[800.0, 5.5], [1600.0, 9.25], [2400.0, 17.125]], 2400.0),  # techo = punto
    ([[1000, 0.0], [5000, 80.04]], 9000),  # techo por encima de todo
    ([[1000, 12.0]], 1000.0),  # un solo punto
]


def apply(output, curve, ceiling, out_unit, **params):
    return T.apply_detailed(
        "ac_coast_ref",
        {"curve": (curve, CURVE_U), "ceiling": (ceiling, "rpm")},
        {"output": output, **params},
        out_unit,
    )


class TestCoastRefLinear(unittest.TestCase):
    def test_same_as_previous_algorithm(self) -> None:
        for curve, ceiling in SYNTHETIC:
            with self.subTest(curve=curve, ceiling=ceiling):
                self.assertEqual(
                    coast_ref_linear(curve, ceiling), old_fit(curve, ceiling)
                )
                model = SimpleNamespace(coast=curve, rpm_ceiling=ceiling)
                self.assertEqual(fit_coast_ref(model), old_fit(curve, ceiling))

    def test_no_point_below_ceiling(self) -> None:
        self.assertIsNone(coast_ref_linear([[5000, 10.0]], 4000))
        self.assertIsNone(coast_ref_linear([], 4000))


class TestTransform(unittest.TestCase):
    def test_outputs_match_fit(self) -> None:
        for curve, ceiling in SYNTHETIC:
            fit = old_fit(curve, ceiling)
            with self.subTest(curve=curve):
                self.assertEqual(apply("rpm", curve, ceiling, "rpm")[0], fit.rpm)
                self.assertEqual(
                    apply("torque", curve, ceiling, "N·m", round=1)[0],
                    round(fit.torque, 1),
                )
                self.assertEqual(apply("torque", curve, ceiling, "N·m")[0], fit.torque)
                self.assertEqual(
                    apply("non_linearity", curve, ceiling, "1")[0], fit.non_linearity
                )

    def test_value_assumption_carries_fit_error(self) -> None:
        curve, ceiling = SYNTHETIC[0]
        fit = old_fit(curve, ceiling)
        _v, notes = apply("rpm", curve, ceiling, "rpm")
        self.assertEqual(len(notes), 1)
        self.assertIn(f"error máx. {fit.max_abs_error:.1f} N·m", notes[0])
        self.assertIn("5.0 N·m a 2000 rpm", notes[0])
        self.assertIn("3 puntos ≤ 3500 rpm", notes[0])
        # apply() sigue devolviendo solo el valor
        self.assertEqual(
            T.apply(
                "ac_coast_ref",
                {"curve": (curve, CURVE_U), "ceiling": (ceiling, "rpm")},
                {"output": "rpm"},
                "rpm",
            ),
            fit.rpm,
        )

    def test_errors(self) -> None:
        curve = [[5000, 10.0]]
        with self.assertRaisesRegex(T.TransformError, "ningún punto"):
            apply("rpm", curve, 4000.0, "rpm")
        with self.assertRaisesRegex(T.TransformError, "params.output"):
            apply("torque_max", curve, 6000.0, "N·m")
        with self.assertRaisesRegex(T.TransformError, "params.round"):
            apply("torque", curve, 6000.0, "N·m", round=1.5)
        with self.assertRaises(T.TransformError):  # salida en una unidad incompatible
            apply("torque", curve, 6000.0, "rpm")
        with self.assertRaisesRegex(T.TransformError, "falta la entrada 'ceiling'"):
            T.apply_detailed(
                "ac_coast_ref", {"curve": (curve, CURVE_U)}, {"output": "rpm"}, "rpm"
            )
        with self.assertRaisesRegex(T.TransformError, "tabla"):
            T.apply_detailed(
                "ac_coast_ref",
                {"curve": (10.0, "N·m"), "ceiling": (6000.0, "rpm")},
                {"output": "rpm"},
                "rpm",
            )
        with self.assertRaisesRegex(T.TransformError, "ejes"):
            T.apply_detailed(
                "ac_coast_ref",
                {"curve": (curve, "table:km->%"), "ceiling": (6000.0, "rpm")},
                {"output": "rpm"},
                "rpm",
            )

    def test_other_transforms_have_no_value_assumptions(self) -> None:
        self.assertEqual(
            T.apply_detailed("scale", {"x": (2.0, "N·m")}, {"factor": 3}, "N·m"),
            (6.0, []),
        )


class TestRuleDefinitions(unittest.TestCase):
    def test_v2_changes_only_inputs_transform_conditions(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        for target, (base, output) in TARGETS.items():
            with self.subTest(rule=base):
                v1, v2 = cat.rules[f"{base}@1"], cat.rules[f"{base}@2"]
                self.assertEqual(v2.supersedes, v1.key)
                self.assertEqual(
                    [r.key for r in cat.rules_for(target) if r.id == base], [v2.key]
                )
                self.assertEqual(
                    v2.inputs,
                    {
                        "curve": "ir.engine.coast_curve",
                        "ceiling": "ir.engine.speed_ceiling",
                    },
                )
                self.assertEqual(v2.transform.id, "ac_coast_ref")
                self.assertEqual(v2.transform.params["output"], output)
                self.assertEqual(
                    v2.transform.params.get("round"), 1 if output == "torque" else None
                )
                self.assertEqual(v2.transform.output_unit, v1.transform.output_unit)
                self.assertEqual(
                    [c.fact for c in v2.conditions],
                    ["ir.engine.device_type", "ir.formats.code_versions.camsoEngine"],
                )
                self.assertEqual(
                    [(c.op, c.value) for c in v2.conditions],
                    [(c.op, c.value) for c in v1.conditions],
                )
                for field in (
                    "scope",
                    "status",
                    "assumptions",
                    "validations",
                    "kind",
                    "target",
                    "applies_when",
                    "exceptions",
                    "open_issues",
                ):
                    self.assertEqual(
                        S._to_plain(getattr(v2, field)), S._to_plain(getattr(v1, field))
                    )


def _catalog(drop_v2: bool) -> Catalog:
    cat = Catalog.load(REPO_CATALOG)
    if drop_v2:
        for base, _o in TARGETS.values():
            cat.rules.pop(f"{base}@2")
    return cat


class TestIbizaV1EqualsV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pv = preview_physics(load_vehicle_dir(IBIZA, "35b84.pc"), 70.0)
        cls.facts = ir_to_facts(import_preview(pv, 70.0))

    def _resolve(self, target: str, drop_v2: bool):
        cat = _catalog(drop_v2)
        ctx = context_from_facts("seat_ibiza_acfr", "35b84", self.facts, cat)
        return ResolutionEngine(cat, ctx).resolve(target)

    def test_same_value_status_confidence(self) -> None:
        for target in TARGETS:
            with self.subTest(target=target):
                r1, r2 = self._resolve(target, True), self._resolve(target, False)
                self.assertEqual(r1.rule.split("@")[1], "1")
                self.assertEqual(r2.rule.split("@")[1], "2")
                for f in (
                    "value",
                    "unit",
                    "status",
                    "confidence",
                    "source",
                    "confidence_criteria",
                    "open_issues",
                    "requires_approval",
                ):
                    self.assertEqual(getattr(r1, f), getattr(r2, f), f)
                self.assertEqual(type(r1.value), type(r2.value))
                self.assertEqual(
                    [i["fact"] for i in r2.inputs],
                    ["ir.engine.coast_curve", "ir.engine.speed_ceiling"],
                )
                # supuestos de @1 + el error del ajuste (antes en la nota del hecho)
                self.assertEqual(r2.assumptions[:-1], r1.assumptions)
                self.assertIn("error máx. 21.5 N·m", r2.assumptions[-1])
        self.assertEqual(
            self._resolve("ac:engine.ini/COAST_REF/TORQUE", False).value, 66.1
        )

    def test_old_note_matches_new_assumption(self) -> None:
        note = self.facts["bng.engine.coast_ref_torque"].note
        r2 = self._resolve("ac:engine.ini/COAST_REF/TORQUE", False)
        err = note.split(";")[0]  # «error máx. X N·m»
        self.assertIn(err, r2.assumptions[-1])
        # la base del cálculo sigue en la nota de la entrada curve
        self.assertEqual(note.split("; ", 1)[1], r2.inputs[0]["note"])


class TestSyntheticRule(unittest.TestCase):
    def test_no_point_below_ceiling_rejects_rule(self) -> None:
        cat = _catalog(False)
        sha = cat.rules["eng.coast_rpm@2"].conditions[1].value
        facts = {
            "ir.engine.device_type": Fact(
                "ir.engine.device_type", "camsoEngine", "enum", "extracted"
            ),
            "ir.formats.code_versions.camsoEngine": Fact(
                "ir.formats.code_versions.camsoEngine", sha, "text", "extracted"
            ),
            "ir.engine.coast_curve": Fact(
                "ir.engine.coast_curve", [[5000.0, 10.0]], CURVE_U, "computed"
            ),
            "ir.engine.speed_ceiling": Fact(
                "ir.engine.speed_ceiling", 4000.0, "rpm", "computed"
            ),
        }
        ctx = context_from_facts("seat_ibiza_acfr", "35b84", facts, cat)
        res = ResolutionEngine(cat, ctx).resolve("ac:engine.ini/COAST_REF/RPM")
        self.assertIsNone(res.value)
        self.assertTrue(
            any("ningún punto" in str(r) for r in res.rejected), res.rejected
        )


class TestDecisionValueAssumptions(unittest.TestCase):
    def test_decision_transform_keeps_value_assumptions(self) -> None:
        cat = _catalog(False)
        target = "ac:engine.ini/COAST_REF/RPM"
        cat.decisions.append(
            S.Decision(
                id="d.test.coast",
                target=target,
                vehicle="veh_test",
                action="transform",
                justification="prueba",
                author="test",
                date="2026-11-01",
                scope=S.Scope(level="vehicle", vehicle="veh_test"),
                status="provisional",
                transform=S.Transform(
                    id="ac_coast_ref",
                    params={"output": "rpm"},
                    input_units={"curve": CURVE_U, "ceiling": "rpm"},
                    output_unit="rpm",
                ),
                inputs={
                    "curve": "ir.engine.coast_curve",
                    "ceiling": "ir.engine.speed_ceiling",
                },
            )
        )
        facts = {
            "ir.engine.coast_curve": Fact(
                "ir.engine.coast_curve",
                [[1000.0, 10.0], [2000.0, 30.0]],
                CURVE_U,
                "computed",
            ),
            "ir.engine.speed_ceiling": Fact(
                "ir.engine.speed_ceiling", 2000.0, "rpm", "computed"
            ),
        }
        ctx = context_from_facts("veh_test", "v", facts, cat)
        res = ResolutionEngine(cat, ctx).resolve(target)
        self.assertEqual(res.value, 2000.0)
        self.assertEqual(res.source, "decision")
        self.assertTrue(any("error máx. 5.0 N·m" in a for a in res.assumptions))


if __name__ == "__main__":
    unittest.main()
