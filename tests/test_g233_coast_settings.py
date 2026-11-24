"""G2.33: ley de [COAST_SETTINGS] y ajuste (puro, sin juegos ni proyectos)."""

import unittest

from src.ac import coast_settings as CS

POWER = [(0, 0), (1000, 60), (3000, 150), (6000, 250), (12000, 200)]
MIN, CEIL = 1000, 12000


def concave_coast():
    # subida rápida y meseta: lo que no puede dar una recta desde MINIMUM
    return [
        (r, 90 * (1 - 2.718281828 ** (-(r - MIN) / 2500)))
        for r in range(1100, CEIL + 1, 100)
    ]


class CoastSettingsLaw(unittest.TestCase):
    def test_floor_zero_at_minimum_and_saturates(self):
        entry = MIN + (CEIL - 2 * MIN)
        self.assertEqual(CS.throttle_floor(MIN, 0.1, MIN, entry), 0.0)
        self.assertAlmostEqual(CS.throttle_floor(CEIL, 0.1, MIN, entry), 0.1)
        self.assertAlmostEqual(CS.throttle_floor(CEIL * 2, 0.1, MIN, entry), 0.1)
        self.assertEqual(CS.throttle_floor(5000, 0.0, MIN, entry), 0.0)

    def test_brake_without_offset_is_acs_linear_law(self):
        for r in (MIN - 50, MIN, 2000, 9000):
            want = 0.0 if r <= MIN else 0.01 * (r - MIN)
            self.assertAlmostEqual(
                CS.coast_brake(r, 0.01, 0.0, MIN, 11000, POWER), want
            )

    def test_offset_only_removes_brake(self):
        entry = MIN + 9000
        for r in (1500, 4000, 12000):
            self.assertLess(
                CS.coast_brake(r, 0.01, 0.1, MIN, entry, POWER),
                CS.coast_brake(r, 0.01, 0.0, MIN, entry, POWER),
            )


class Fit(unittest.TestCase):
    def test_improves_concave_curve(self):
        f = CS.fit_coast_settings(concave_coast(), POWER, MIN, CEIL, 0.1)
        self.assertIsNotNone(f)
        self.assertGreater(f.offset, 0)
        self.assertLessEqual(f.offset, 0.1 + 1e-9)
        self.assertLess(f.rms_error, f.rms_baseline)
        self.assertEqual(f.activation_rpm, CEIL - 2 * MIN)
        self.assertAlmostEqual(f.ref_torque, f.slope * (f.ref_rpm - MIN), places=2)

    def test_no_variant_when_line_from_minimum_is_exact(self):
        line = [(r, 0.01 * (r - MIN)) for r in range(1100, CEIL + 1, 100)]
        self.assertIsNone(CS.fit_coast_settings(line, POWER, MIN, CEIL, 0.1))

    def test_not_applicable_without_room_for_activation(self):
        self.assertIsNone(
            CS.fit_coast_settings(concave_coast(), POWER, 7000, 12000, 0.1)
        )

    def test_render(self):
        f = CS.fit_coast_settings(concave_coast(), POWER, MIN, CEIL, 0.1)
        self.assertEqual(CS.render_lut(0.1), "0|0.1\n")
        txt = CS.render_engine_ini_sections(f)
        self.assertIn("[COAST_SETTINGS]\nLUT=coast_settings.lut\nDEFAULT=0\n", txt)

    def test_decay_matches_closed_form_for_constant_brake(self):
        # I·Δω/B con B constante: Δω = (r0 − r1)·π/30
        t = CS.decay_time(lambda r: 50.0, 0.1, 6000, 1500)
        self.assertAlmostEqual(
            t, 0.1 * (4500 * 3.141592653589793 / 30) / 50.0, places=3
        )


if __name__ == "__main__":
    unittest.main()
