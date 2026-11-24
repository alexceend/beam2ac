"""Fila 100 del mapa de par (referencia de Automation) frente a la 99 que lee BeamNG.

camsoEngine.lua:725 limita ``throttleLookup`` a 99; Automation declara par y potencia con la fila 100.
La curva que se convierte sigue siendo la de la fila 99; la validación contra ``info_*.json`` usa la 100.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"


class TestReferenceRow(unittest.TestCase):
    @unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza ausente")
    def test_reference_curve_is_built_beside_the_converted_one(self) -> None:
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics
        from src.importers.beamng import import_preview

        ir = import_preview(
            preview_physics(load_vehicle_dir(IBIZA, "35b84.pc"), 70.0), 70.0
        )
        p = dict(ir.walk())
        self.assertEqual(p["engine.reference_wot_torque_curve"].status, "present")
        self.assertGreater(p["engine.reference_peak_torque"].value, 0)
        # la curva convertida no cambia: sigue siendo la fila 99
        self.assertTrue(
            any("fila 99" in r.locator for r in p["engine.wot_torque_curve"].refs)
        )

    def _engine_curve(self, extra: dict):
        from src.attrs import validations_bng as V
        from src.attrs.facts_bng import Fact

        fv = {
            "ir.declared.torque": 187,
            "ir.declared.implied_power": 169.437,
            "ir.declared.torque_peak_speed": 4600,
            "ir.declared.power_peak_speed": 7800,
            "ir.formats.exporter_version": "40340",
            "ir.engine.wot_torque_curve": [
                [0, 0],
                [4600, 186.92],
                [7800, 153.0],
                [7900, 150.0],
            ],
            "ir.engine.peak_torque": 186.92,
            "ir.engine.peak_power": 168.242 * V.HP_W,
            **extra,
        }
        facts = {k: Fact(k, v, "1", "extracted") for k, v in fv.items()}
        return {v.id.split(".")[-1]: v for v in V.info_validations("car", "x", facts)}[
            "engine_curve"
        ]

    def test_validation_uses_reference_row_when_present(self) -> None:
        from src.attrs import validations_bng as V

        ref = {
            "ir.engine.reference_wot_torque_curve": [
                [0, 0],
                [4600, 187.13],
                [7800, 154.74],
                [7900, 151.0],
            ],
            "ir.engine.reference_peak_torque": 187.13,
            "ir.engine.reference_peak_power": 169.497 * V.HP_W,
        }
        self.assertEqual(
            self._engine_curve({}).result, "fail"
        )  # sin referencia: 168,24 hp frente a 169,44
        v = self._engine_curve(ref)
        self.assertEqual(v.result, "pass")
        self.assertEqual(v.observed["basis"], "fila 100 (Automation)")
        self.assertAlmostEqual(
            v.observed["beamng_power_hp"], 168.242, places=3
        )  # la curva convertida no cambia


if __name__ == "__main__":
    unittest.main()
