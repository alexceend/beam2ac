"""Apartado de ``tests/test_g2_engine_rules_ir.py``.

Contenido: clase ``TestReferenceVehicles``.
Ver ``tests_not_required/README.md``.
"""

import unittest
from src.ir.legacy_facts import ir_to_facts
from tests.test_g2_engine_rules_ir import (
    COMMON,
    IBIZA,
    MODS,
    NEW,
    RULES,
    SAMPLES,
    resolve,
)


class TestReferenceVehicles(unittest.TestCase):
    def vehicles(self):
        from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip

        out = {}
        if IBIZA.is_dir():
            out["seat_ibiza_acfr"] = lambda: load_vehicle_dir(IBIZA, "35b84.pc")
        if (MODS / "spicy_mandala.zip").is_file() and COMMON.is_file():
            out["spicy_mandala"] = lambda: load_vehicle_zip(
                MODS / "spicy_mandala.zip", COMMON
            )
        for d in sorted(SAMPLES.glob("*/vehicles/*")) if SAMPLES.is_dir() else []:
            out[d.name] = lambda d=d: load_vehicle_dir(d, next(d.glob("*.pc")).name)
        return out

    def test_v1_and_v2_equivalent(self) -> None:
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import preview_physics

        vehicles = self.vehicles()
        if not vehicles:
            self.skipTest("sin vehículos de referencia")
        for name, load in vehicles.items():
            src = load()
            facts = ir_to_facts(import_preview(preview_physics(src, 70.0), 70.0))
            lua = facts.get("ir.formats.code_versions.camsoEngine")
            for target, base in RULES.items():
                with self.subTest(vehicle=name, target=target):
                    new = resolve(facts, target, vehicle=src.vehicle_id)
                    old = resolve(facts, target, drop_v2=True, vehicle=src.vehicle_id)
                    self.assertEqual(
                        (new.value, new.unit, new.status, new.confidence, new.source),
                        (old.value, old.unit, old.status, old.confidence, old.source),
                    )
                    if lua is not None and lua.value == NEW:
                        self.assertEqual(new.rule, f"{base}@2")
                        self.assertEqual(old.rule, f"{base}@1")
                    else:  # Ibiza y spicy (47437d5c): no les afecta
                        self.assertEqual(new.rule, old.rule)
                        self.assertNotIn("_camso91392@", new.rule or "")
