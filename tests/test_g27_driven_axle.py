"""G2.7, paso 3 (G2.6b): diferencial del eje motriz en el IR y ``dt.preload@3``.

* ``drivetrain.driven_axle`` y ``drivetrain.driven_axle_differential``: presentes con
  un solo eje motriz y sin central (FWD, RWD, ``torqueVectoring``, ``open``); «no
  aplica» en AWD con central; ausentes en AWD sin central reconocido.
* El diferencial del eje motriz es EL MISMO objeto que ``differentials.<rol>``: los
  ``Param`` no pueden divergir. No se agrupan tipos (``kind`` literal).
* Ningún hecho ``bng.*`` nuevo; los ``bng.diff.*`` siguen iguales a sus ``ir.*``.
* ``dt.preload@3`` = ``@2`` en valor, estado, confianza y aplicabilidad.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import ir_facts, ir_to_facts
from src.ir.vehicle import VehicleIR

from .test_drivetrain import rwd_car
from .test_drivetrain_awd import awd_car
from .test_g25c_traction import fwd_car, ir_for, layout_for

ROOT = Path(__file__).resolve().parents[1]
PRELOAD = "ac:drivetrain.ini/DIFFERENTIAL/PRELOAD"
DAD = "drivetrain.driven_axle_differential"


def both_axles_no_center():
    parts = rwd_car()
    pt = parts["car"]["powertrain"]
    pt[7] = ["shaft", "axleFL2", "rearDiff", 2, {"connectedWheel": "FL"}]
    pt[8] = ["shaft", "axleRR", "halfshaftRR", 1, {"connectedWheel": "RR"}]
    return parts


CASES = {
    "FWD lsd": (fwd_car(), "front", "lsd"),
    "RWD lsd": (
        rwd_car(diff_row_opts={"diffType": "lsd", "lsdPreload": 70}),
        "rear",
        "lsd",
    ),
    "RWD open": (rwd_car(diff_row_opts={"diffType": "open"}), "rear", "open"),
    "RWD torqueVectoring": (
        rwd_car(diff_row_opts={"diffType": "torqueVectoring"}),
        "rear",
        "torqueVectoring",
    ),
    "RWD sin diffType": (rwd_car(diff_row_opts={"lsdPreload": 50}), "rear", None),
}


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_rule_to_ir", ROOT / "tools/catalog/migrate_rule_to_ir.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLayout(unittest.TestCase):
    def test_driven_axle_property(self) -> None:
        for name, (parts, role, _kind) in CASES.items():
            with self.subTest(name):
                self.assertEqual(layout_for(parts).driven_axle, role)
        self.assertIsNone(layout_for(awd_car({"diffType": "lsd"})).driven_axle)
        self.assertIsNone(layout_for(both_axles_no_center()).driven_axle)


class TestIR(unittest.TestCase):
    def test_single_driven_axle_is_the_same_object(self) -> None:
        for name, (parts, role, kind) in CASES.items():
            ir = ir_for(parts)
            dtr = ir.drivetrain
            with self.subTest(name):
                self.assertEqual(dtr.driven_axle.value, role)
                self.assertEqual(dtr.driven_axle.unit, "enum")
                self.assertIs(dtr.driven_axle_differential, dtr.differentials[role])
                if kind is None:
                    self.assertEqual(
                        dtr.driven_axle_differential.kind.status, "missing"
                    )
                else:
                    self.assertEqual(dtr.driven_axle_differential.kind.value, kind)
                facts = ir_facts(ir)
                for field in (
                    "kind",
                    "preload",
                    "lock_coef",
                    "rev_lock_coef",
                    "final_ratio",
                    "torque_share",
                ):
                    a = facts.get(f"ir.drivetrain.differentials.{role}.{field}")
                    b = facts.get(f"ir.{DAD}.{field}")
                    self.assertEqual(a is None, b is None, field)
                    if a is not None:
                        da, db = a.to_dict(), b.to_dict()
                        da.pop("id"), db.pop("id")
                        self.assertEqual(da, db, field)
                self.assertEqual(facts["ir.drivetrain.driven_axle"].value, role)

    def test_awd_with_center_is_not_applicable(self) -> None:
        ir = ir_for(awd_car({"diffType": "lsd", "lsdPreload": 10}))
        dtr = ir.drivetrain
        self.assertEqual(dtr.driven_axle.status, "not_applicable")
        self.assertIn("varios ejes motrices", dtr.driven_axle.reason)
        self.assertEqual(dtr.driven_axle_differential.kind.status, "not_applicable")
        # G2.8: diferencial que no existe → todos sus campos «no aplica»
        self.assertEqual(dtr.driven_axle_differential.preload.status, "not_applicable")
        for d in dtr.differentials.values():
            self.assertIsNot(dtr.driven_axle_differential, d)
        facts = ir_to_facts(ir)
        # bng.diff.final_ratio viene de drivetrain.final_ratio (STATIC), no del eje
        self.assertFalse(
            [
                k
                for k in facts
                if k.startswith(("bng.diff.", f"ir.{DAD}"))
                and k != "bng.diff.final_ratio"
            ]
        )
        self.assertNotIn("ir.drivetrain.driven_axle", facts)

    def test_awd_without_center_is_absent(self) -> None:
        dtr = ir_for(both_axles_no_center()).drivetrain
        self.assertEqual(dtr.driven_axle.status, "missing")
        self.assertEqual(dtr.driven_axle_differential.kind.status, "missing")

    def test_no_new_legacy_facts(self) -> None:
        for name, (parts, role, _k) in CASES.items():
            ir = ir_for(parts)
            facts = ir_to_facts(ir)
            with self.subTest(name):
                legacy = sorted(k for k in facts if k.startswith("bng."))
                ir.drivetrain.driven_axle_differential = type(
                    ir.drivetrain.driven_axle_differential
                )()
                from src.ir.param import Param

                ir.drivetrain.driven_axle = Param.missing("enum", "")
                self.assertEqual(
                    legacy, sorted(k for k in ir_to_facts(ir) if k.startswith("bng."))
                )
                for field, fid in (
                    ("kind", "bng.diff.type"),
                    ("preload", "bng.diff.lsd_preload"),
                ):
                    if fid in facts:
                        a = facts[fid].to_dict()
                        b = facts[f"ir.{DAD}.{field}"].to_dict()
                        a.pop("id"), b.pop("id")
                        self.assertEqual(a, b)


class TestRule(unittest.TestCase):
    def test_v3_definition(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        v2, v3 = cat.rules["dt.preload@2"], cat.rules["dt.preload@3"]
        self.assertEqual(v3.supersedes, v2.key)
        self.assertEqual(
            [r.key for r in cat.rules_for(PRELOAD) if r.id == "dt.preload"],
            ["dt.preload@4"],  # G2.39
        )
        self.assertEqual(v3.inputs, {"x": f"ir.{DAD}.preload"})
        self.assertEqual(
            [(c.fact, c.op, c.value) for c in v3.applies_when],
            [(f"ir.{DAD}.kind", "eq", "lsd")],
        )
        for field in (
            "transform",
            "scope",
            "status",
            "assumptions",
            "validations",
            "kind",
            "target",
            "conditions",
            "exceptions",
            "open_issues",
        ):
            self.assertEqual(
                S._to_plain(getattr(v3, field)), S._to_plain(getattr(v2, field))
            )

    def test_tool_pairs_are_closed_and_checked(self) -> None:
        mod = load_tool()
        self.assertEqual(
            mod.DRIVEN_AXLE_DIFF,
            {"bng.diff.type": f"{DAD}.kind", "bng.diff.lsd_preload": f"{DAD}.preload"},
        )
        mod.check_driven_axle_pairs()  # no lanza
        table = mod.legacy_to_ir()
        self.assertEqual(mod.ir_ref("bng.diff.type", table), f"ir.{DAD}.kind")
        with self.assertRaises(SystemExit):  # sin par: no se inventa
            mod.ir_ref("bng.diff.lock_coef", table)

    def _resolve(self, parts, drop_v3: bool, vehicle="seat_ibiza_acfr"):
        cat = Catalog.load(REPO_CATALOG)
        # G2.39: @4 y la regla de familia 38022 (×2) quedan fuera: aquí se compara @2 con @3
        cat.rules.pop("dt.preload@4")
        cat.rules.pop("dt.diff_preload_2wd_lsd_38022@1")
        if drop_v3:
            cat.rules.pop("dt.preload@3")
        facts = ir_to_facts(ir_for(parts))
        ctx = context_from_facts(vehicle, "35b84", facts, cat)
        return ResolutionEngine(cat, ctx).resolve(PRELOAD)


class TestEmptyIR(unittest.TestCase):
    def test_default_ir_has_absent_driven_axle(self) -> None:
        ir = VehicleIR()
        self.assertEqual(ir.drivetrain.driven_axle.status, "missing")
        self.assertEqual(ir.drivetrain.driven_axle_differential.kind.status, "missing")


if __name__ == "__main__":
    unittest.main()
