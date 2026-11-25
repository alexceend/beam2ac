"""G2.43: automática con convertidor del 200409 (D-G211-TC b): marchas y FINAL por regla, aproximación declarada."""

import unittest

from tests.test_g211_transmissions import GEARS, _real, _session, have

CARS = {
    "2021_rpm_portland_touring": 4.44,
    "2020_rpm_patagonia_xt_v8": None,
    "2020_rpm_atacama_sport__4x4": None,
}


class TestAtcRules(unittest.TestCase):
    def test_gears_resolved_by_atc_rules(self) -> None:
        for name, final in CARS.items():
            if not have(name):
                self.skipTest("muestras 200409 no disponibles")
            _, ir = _real(name)
            by = {r.target: r for r in _session(ir).results}
            with self.subTest(name):
                f = by[GEARS + "/FINAL"]
                self.assertEqual(f.rule, "dt.final_atc_200409@1")
                self.assertEqual(f.source, "rule")
                self.assertEqual(f.value, ir.drivetrain.final_ratio.value)
                if final:
                    self.assertEqual(f.value, final)
                self.assertEqual(
                    by[GEARS + "/COUNT"].value,
                    len(ir.drivetrain.gearbox.forward_ratios),
                )


if __name__ == "__main__":
    unittest.main()
