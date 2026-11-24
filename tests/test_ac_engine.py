import tempfile
import unittest
from pathlib import Path

from src.ac.engine import (
    fit_coast_ref,
    has_turbo_section,
    render_engine_ini,
    render_power_lut,
    write_engine_files,
)
from src.safe_paths import PROJECT_ROOT, ProtectedPathError

from .test_engine_camso import model_for


class TestPowerLut(unittest.TestCase):
    def test_format_and_values(self):
        lines = render_power_lut(model_for()).splitlines()
        self.assertEqual(lines, ["0|0.00", "1000|100.00", "2000|200.00", "3000|170.00"])

    def test_points_above_ceiling_are_dropped(self):
        m = model_for(main_engine={"hasRevLimiter": True, "revLimiterRPM": 2000})
        self.assertEqual(render_power_lut(m).splitlines()[-1], "2000|200.00")


class TestEngineIni(unittest.TestCase):
    def test_no_turbo_section_even_with_boost(self):
        m = model_for()  # el mapa tiene boost (Pressure 2.0)
        self.assertIsNotNone(m.max_boost_bar)
        ini = render_engine_ini(m, fit_coast_ref(m))
        self.assertFalse(has_turbo_section(ini))
        self.assertIn("LIMITER=3000", ini)
        self.assertIn("MINIMUM=1000", ini)
        self.assertIn("INERTIA=0.12", ini)
        self.assertIn("POWER_CURVE=power.lut", ini)

    def test_turbo_section_detection(self):
        self.assertTrue(has_turbo_section("[HEADER]\n[TURBO_0]\nMAX_BOOST=1"))
        self.assertTrue(has_turbo_section("  [turbo_1]"))
        self.assertFalse(has_turbo_section("; sin [TURBO_n]\nX=1"))

    def test_coast_ref_fit(self):
        m = model_for()
        fit = fit_coast_ref(m)
        self.assertEqual(fit.rpm, 3000)
        self.assertAlmostEqual(fit.torque, 54.0)  # (30 + 10)·1.35
        # Lineal desde 0: a 1000 rpm predice 18, BeamNG da 6.75
        self.assertAlmostEqual(fit.max_abs_error, 18 - 6.75)
        ini = render_engine_ini(m, fit)
        self.assertIn("RPM=3000", ini)
        self.assertIn("NON_LINEARITY=0", ini)


class TestWriteFiles(unittest.TestCase):
    def test_writes_three_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = write_engine_files(model_for(), Path(tmp) / "engine")
            self.assertEqual(
                set(out), {"power.lut", "engine.ini", "engine_report.json"}
            )
            self.assertTrue(all(p.is_file() for p in out.values()))

    def test_refuses_samples(self):
        target = PROJECT_ROOT / "sample_assetto" / "engine_test"
        with self.assertRaises(ProtectedPathError):
            write_engine_files(model_for(), target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
