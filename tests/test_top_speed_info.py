"""«Top Speed» de info_<config>.json frente a la velocidad teórica (2026-10-30)."""

from __future__ import annotations

import pytest

import unittest
from pathlib import Path

from src.ac.drivetrain import ACCEL_NOT_REACHED, analyse
from src.extract.mass_geometry import compute_mass_geometry

from .jbeam_fixtures import resolve_parts
from .test_drivetrain import ENGINE, rwd_car

SAMPLES = Path(__file__).resolve().parents[1] / "samples_beamng"


def top_check(info: dict):
    r = resolve_parts(rwd_car())
    res = analyse(r, compute_mass_geometry(r), ENGINE, info)
    return next(c for c in res.checks if c.quantity == "Top Speed"), res


class TestSentinel(unittest.TestCase):
    def test_normal_value_is_compared_with_fastest_gear(self) -> None:
        c, res = top_check({"Top Speed": 50.0, "0-100 km/h": 6.1})
        fastest = max(s.speed_at_ceiling for s in res.speeds)
        self.assertEqual(c.computed, fastest)
        self.assertIn("unidad inferida m/s", c.explanation)

    def test_sentinel_is_not_compared(self) -> None:
        c, res = top_check({"Top Speed": 10.0, "0-100 km/h": ACCEL_NOT_REACHED})
        self.assertIsNone(c.computed)
        self.assertIn("999", c.explanation)
        first = next(s for s in res.speeds if s.gear == "1")
        self.assertIn(f"{10.0 / first.speed_at_ceiling:.3f}", c.explanation)


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestSamples(unittest.TestCase):
    @pytest.mark.ampliada
    def test_sentinel_vehicles_are_about_092_of_first_gear(self) -> None:
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        seen = {}
        # exportador 40340: los 7 del 200409 no tienen transmisión analizable
        # (dctGearbox, convertidor de par, geometría; informe G2.8 §6.2)
        for d in sorted(SAMPLES.glob("thershow_*/vehicles/*")):
            src = load_vehicle_dir(d, next(d.glob("*.pc")).name)
            res = preview_physics(src, None).drivetrain
            c = next(c for c in res.checks if c.quantity == "Top Speed")
            if src.info.get("0-100 km/h") == ACCEL_NOT_REACHED:
                first = next(s for s in res.speeds if s.gear == "1")
                seen[d.name] = src.info["Top Speed"] / first.speed_at_ceiling
                self.assertIsNone(c.computed)
            else:
                self.assertIsNotNone(c.computed)
        self.assertEqual(
            sorted(seen),
            ["autist_cuanto", "sucette_poubelle", "sucette_poubelle_turbo"],
        )
        for name, ratio in seen.items():
            self.assertAlmostEqual(ratio, 0.92, delta=0.01, msg=name)


if __name__ == "__main__":
    unittest.main()
