"""Apartado de ``tests/test_g27_driven_axle.py``.

Contenido: clase ``TestRule``, métodos ['test_v2_equals_v3'].
Ver ``tests_not_required/README.md``.
"""

import unittest
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import ir_to_facts
from tests.test_drivetrain_awd import awd_car
from tests.test_g25c_traction import fwd_car, ir_for
from tests.test_g27_driven_axle import CASES, DAD, PRELOAD, both_axles_no_center


class TestRule(unittest.TestCase):
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

    def test_v2_equals_v3(self) -> None:
        cases = dict(CASES)
        cases["AWD lsd"] = (awd_car({"diffType": "lsd", "lsdPreload": 10}), None, None)
        cases["AWD sin central"] = (both_axles_no_center(), None, None)
        for name, (parts, _role, _kind) in cases.items():
            for vehicle in ("seat_ibiza_acfr", "otro"):
                r2 = self._resolve(parts, True, vehicle)
                r3 = self._resolve(parts, False, vehicle)
                with self.subTest(name, vehicle=vehicle):
                    for f in (
                        "value",
                        "unit",
                        "status",
                        "confidence",
                        "source",
                        "assumptions",
                        "open_issues",
                        "requires_approval",
                    ):
                        self.assertEqual(getattr(r2, f), getattr(r3, f), f)
        r = self._resolve(fwd_car(), False)
        self.assertEqual((r.value, r.rule, r.status), (50, "dt.preload@3", "inferred"))
        self.assertEqual(r.inputs[0]["fact"], f"ir.{DAD}.preload")
