"""Apartado de ``tests/test_g2_rules_ir.py``.

Contenido: clase ``TestRealVehicles``.
Ver ``tests_not_required/README.md``.
"""

import unittest
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import ir_to_facts
from tests.test_g2_rules_ir import IBIZA, SAMPLES, TARGET


@unittest.skipUnless(IBIZA.is_dir() and SAMPLES.is_dir(), "sin muestras")
class TestRealVehicles(unittest.TestCase):
    def test_same_value_as_v3_input_on_both_lua_versions(self) -> None:
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        cases = {
            "seat_ibiza_acfr": (IBIZA, "35b84.pc", "known"),
            "sucette_poubelle": (
                next(SAMPLES.glob("*/vehicles/sucette_poubelle")),
                "f5893.pc",
                "known",
            ),
            "hyacinth_sage": (
                next(SAMPLES.glob("*/vehicles/hyacinth_sage")),
                "70548.pc",
                "known",  # G2.31: la validación in_game_value de la curva resolvió la ambigüedad
            ),
        }
        for name, (d, pc, status) in cases.items():
            with self.subTest(vehicle=name):
                facts = ir_to_facts(
                    import_preview(preview_physics(load_vehicle_dir(d, pc), 70.0), 70.0)
                )
                cat = Catalog.load(REPO_CATALOG)
                ctx = context_from_facts(name, pc[:-3], facts, cat)
                r = ResolutionEngine(cat, ctx).resolve(TARGET)
                self.assertEqual(r.rule, "eng.power_lut@4")
                self.assertEqual(r.status, status)
                self.assertEqual(r.value, facts["bng.engine.net_wot_curve"].value)
