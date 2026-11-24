"""Regresión de la transmisión sobre la muestra real (solo lectura)."""

import hashlib
import unittest

from src.ac.drivetrain import analyse, render_drivetrain_ini
from src.jbeam import PartIndex, load, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"


def _fingerprint():
    return {
        str(p.relative_to(VEHICLE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VEHICLE.rglob("*"))
        if p.suffix in (".jbeam", ".pc", ".json")
    }


class TestSampleDrivetrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _fingerprint()
        resolved = resolve(
            PartIndex.from_directories(VEHICLE), load_pc(VEHICLE / "35b84.pc")
        )
        cls.res = analyse(resolved, info=load(VEHICLE / "info_35b84.json").value)
        cls.dt = cls.res.drivetrain

    def test_graph(self):
        self.assertEqual(
            self.dt.path,
            [
                "mainEngine",
                "clutch",
                "gearbox",
                "frontDriveShaft",
                "frontDiff",
                "halfshaftFL",
                "wheelaxleFL",
            ],
        )
        self.assertEqual(self.dt.driven_wheels, ["FL", "FR"])
        self.assertEqual(self.dt.undriven_axles, ["wheelaxleRL", "wheelaxleRR"])
        self.assertEqual(self.res.traction, "FWD")

    def test_ratios(self):
        self.assertEqual(self.dt.reverse, -3.3)
        self.assertEqual(self.dt.forward, [3.14, 1.87, 1.3, 0.99, 0.8, 0.66])
        self.assertEqual(self.dt.final_drive, 5.98)
        self.assertEqual(self.dt.differential.part, "Camso_differential_front_35b84")
        p = self.dt.differential.params
        self.assertEqual(
            (p["diffType"], p["lsdPreload"], p["lsdLockCoef"], p["lsdRevLockCoef"]),
            ("lsd", 100.0, 0.25, 0.025),
        )
        self.assertEqual(self.dt.clutch.params, {})  # embrague sin parámetros

    def test_speeds_and_info(self):
        top = self.res.speeds[-1]
        self.assertEqual(top.gear, "6")
        self.assertAlmostEqual(top.total_ratio, 3.9468, places=4)
        self.assertAlmostEqual(top.speed_at_ceiling, 82.915, places=3)
        self.assertAlmostEqual(self.res.speeds[1].speed_at_ceiling, 17.428, places=3)
        checks = {c.quantity: c for c in self.res.checks}
        self.assertEqual(checks["Drivetrain"].explanation, "coincide")
        self.assertEqual(checks["Transmission"].explanation, "coincide")
        self.assertEqual(checks["Top Speed"].reference, 82.3)
        # Diferencia documentada, no corregida: -0,74 %
        self.assertAlmostEqual(
            (82.3 - top.speed_at_ceiling) / top.speed_at_ceiling, -0.0074, places=4
        )

    def test_shift_controller_is_read_not_mapped(self):
        self.assertEqual(self.res.shift_controller_raw["transmissionShiftDelay"], 0.185)
        ini = render_drivetrain_ini(self.res)
        self.assertNotIn("CHANGE_UP_TIME", ini)
        self.assertNotIn("POWER=", ini)
        self.assertNotIn("COAST=", ini)
        self.assertIn("FINAL=5.98", ini)
        self.assertIn("PRELOAD=100", ini)
        self.assertIn("TYPE=FWD", ini)

    def test_samples_not_modified(self):
        self.assertEqual(_fingerprint(), self.before)


if __name__ == "__main__":
    unittest.main()
