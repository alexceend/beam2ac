"""G2.44 (D-G212-LOCK): central ``locked`` del 200409 por regla (aproximación)."""

import unittest

from src.attrs import transforms
from tests.test_g211_transmissions import _real, _session, have

AWD = "ac:drivetrain.ini/AWD/"


class TestLockedCenter(unittest.TestCase):
    def test_cap_transform(self) -> None:
        # ΣI = 2·(3,6 + 3,6) = 14,4; chatter 1 rad/s; dt = 1/333 ⇒ 14,4·333
        v = transforms.apply(
            "ac_center_lock_cap",
            {"i_front": (3.6, "kg·m²"), "i_rear": (3.6, "kg·m²")},
            {"chatter": 1.0},
            "N·m",
        )
        self.assertAlmostEqual(v, 14.4 * 333.0)
        with self.assertRaises(transforms.TransformError):
            transforms.apply(
                "ac_center_lock_cap", {"i_front": (1, "kg·m²")}, {"chatter": 1.0}, "N·m"
            )

    def test_patagonia_and_atacama_get_awd_keys(self) -> None:
        for name in ("2020_rpm_patagonia_xt_v8", "2020_rpm_atacama_sport__4x4"):
            if not have(name):
                self.skipTest("muestras 200409 no disponibles")
            _, ir = _real(name)
            by = {r.target: r for r in _session(ir).results}
            with self.subTest(name):
                self.assertEqual(
                    ir.drivetrain.differentials["center"].initial_mode.value, "locked"
                )
                self.assertEqual(
                    ir.drivetrain.differentials["front"].initial_mode.value, "open"
                )
                self.assertEqual(by["ac:drivetrain.ini/TRACTION/TYPE"].value, "AWD")
                self.assertEqual(by[AWD + "FRONT_SHARE"].value, 50.0)
                pre = by[AWD + "CENTRE_DIFF_PRELOAD"]
                self.assertEqual(pre.rule, "dt.awd_center_diff_preload_lockc@1")
                self.assertGreater(pre.value, 1000.0)
                for k in (
                    "CENTRE_DIFF_POWER",
                    "CENTRE_DIFF_COAST",
                    "FRONT_DIFF_PRELOAD",
                    "REAR_DIFF_POWER",
                ):
                    self.assertEqual(by[AWD + k].value, 0)
                self.assertEqual(
                    by[AWD + "FRONT_DIFF_PRELOAD"].rule,
                    "dt.awd_front_diff_preload_openmode@1",
                )

    def test_open_and_lsd_centers_do_not_use_lock_rules(self) -> None:
        for name in ("2020_rpm_atlanta_gt_3_6t", "2020_rpm_legion_grand_touring"):
            if not have(name):
                self.skipTest("muestras 200409 no disponibles")
            _, ir = _real(name)
            by = {r.target: r for r in _session(ir).results}
            with self.subTest(name):
                self.assertNotIn("lockc", by[AWD + "CENTRE_DIFF_PRELOAD"].rule or "")
                self.assertNotIn("openmode", by[AWD + "FRONT_DIFF_PRELOAD"].rule or "")


if __name__ == "__main__":
    unittest.main()
