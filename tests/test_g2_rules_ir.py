"""G2: reglas con entradas del IR (hechos ``ir.*``); primera: ``eng.power_lut@4``."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir import Param, VehicleIR
from src.ir.legacy_facts import ir_facts, ir_to_facts

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
OLD = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
NEW = "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"
TARGET = "ac:power.lut"


def resolve(facts: dict):
    cat = Catalog.load(REPO_CATALOG)
    ctx = context_from_facts("veh_test", "v", facts, cat)
    return ResolutionEngine(cat, ctx).resolve(TARGET)


def engine_ir(sha: str | None, curve=True) -> VehicleIR:
    ir = VehicleIR()
    ir.set("identity.source_tool", Param.of("Automation", "text", "extracted"))
    ir.set("engine.device_type", Param.of("camsoEngine", "enum", "extracted"))
    if curve:
        ir.set(
            "engine.wot_torque_curve",
            Param.of([[1000, 100.0], [5000, 200.0]], "table:rpm->N·m", "computed"),
        )
    if sha is not None:
        ir.formats.code_versions["camsoEngine"] = Param.of(sha, "text", "extracted")
    return ir


class TestRuleDefinition(unittest.TestCase):
    def test_v4_changes_only_references(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        v3, v4 = cat.rules["eng.power_lut@3"], cat.rules["eng.power_lut@4"]
        self.assertEqual(v4.supersedes, v3.key)
        # (G2.10: la regla paralela eng.power_lut_combustion es otra cadena)
        self.assertEqual(
            [r.key for r in cat.rules_for(TARGET) if r.id == "eng.power_lut"],
            ["eng.power_lut@4"],
        )
        for field in (
            "transform",
            "scope",
            "status",
            "assumptions",
            "validations",
            "kind",
            "target",
            "applies_when",
            "exceptions",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    S._to_plain(getattr(v4, field)), S._to_plain(getattr(v3, field))
                )
        self.assertEqual(v4.inputs, {"x": "ir.engine.wot_torque_curve"})
        conds = {c.fact: (c.op, c.value) for c in v4.conditions}
        self.assertEqual(
            conds,
            {
                "ir.engine.device_type": ("eq", "camsoEngine"),
                "ir.formats.code_versions.camsoEngine": ("in", [OLD, NEW]),
            },
        )
        self.assertFalse(any(ref.startswith("bng.") for ref in v4.inputs.values()))


class TestIrFacts(unittest.TestCase):
    def test_only_present_params_become_facts(self) -> None:
        ir = engine_ir(NEW)
        facts = ir_facts(ir)
        f = facts["ir.engine.wot_torque_curve"]
        p = ir.engine.wot_torque_curve
        self.assertEqual((f.value, f.unit, f.kind), (p.value, p.unit, p.kind))
        self.assertNotIn("ir.engine.idle_speed", facts)  # ausente: sin hecho
        self.assertNotIn("ir.steering.wheel_lock", facts)
        ir.engine.idle_speed = Param.not_applicable("rpm", "prueba")
        self.assertNotIn("ir.engine.idle_speed", ir_facts(ir))

    def test_incompatible_input_unit_is_rejected(self) -> None:
        """Una curva del IR en otra dimensión (N en vez de N·m) no se convierte."""
        facts = ir_to_facts(engine_ir(NEW))
        bad = facts["ir.engine.wot_torque_curve"]
        bad.unit = "table:rpm->N"
        r = resolve(facts)
        self.assertIsNone(r.value)
        self.assertTrue(
            any("unidades incompatibles" in x["reason"] for x in r.rejected)
        )

    def test_legacy_facts_unchanged_next_to_ir_facts(self) -> None:
        facts = ir_to_facts(engine_ir(NEW))
        legacy = facts["bng.engine.net_wot_curve"]
        native = facts["ir.engine.wot_torque_curve"]
        self.assertEqual(
            (legacy.value, legacy.unit, legacy.kind, legacy.refs),
            (native.value, native.unit, native.kind, native.refs),
        )
        self.assertEqual(facts["bng.engine.device_type"].value, "camsoEngine")

    def test_rule_applies_and_is_rejected(self) -> None:
        ok = resolve(ir_to_facts(engine_ir(NEW)))
        self.assertEqual(ok.rule, "eng.power_lut@4")
        self.assertEqual(ok.value, [[1000, 100.0], [5000, 200.0]])
        self.assertEqual(ok.inputs[0]["fact"], "ir.engine.wot_torque_curve")
        for name, ir in (
            ("versión del Lua desconocida", engine_ir("0" * 64)),
            ("sin versión del Lua", engine_ir(None)),
            ("sin curva", engine_ir(NEW, curve=False)),
        ):
            with self.subTest(case=name):
                r = resolve(ir_to_facts(ir))
                self.assertIsNone(r.value)
                self.assertEqual(r.status, "manual_required")
                self.assertTrue(r.rejected)


if __name__ == "__main__":
    unittest.main()
