"""[GEARBOX] VALID_SHIFT_RPM_WINDOW: requisito de compatibilidad con AC
(docs/research/ensamblado.md §9.15). Generador de drivetrain.ini y validador.
"""

import tempfile
import unittest
from pathlib import Path

from src.ac.car_validate import read_ini, shift_window_problem, validate_structure
from src.ac.drivetrain import VALID_SHIFT_RPM_WINDOW, render_drivetrain_ini
from tests.test_drivetrain import TestAcDrivetrain


def _ini(text: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "drivetrain.ini"
        p.write_text(text, encoding="utf-8")
        return read_ini(p)


class TestGenerator(unittest.TestCase):
    def test_generated_ini_has_positive_window_once(self):
        text = render_drivetrain_ini(TestAcDrivetrain().result())
        ini = _ini(text)
        self.assertEqual(VALID_SHIFT_RPM_WINDOW, 800)
        self.assertEqual(ini["GEARBOX"], {"VALID_SHIFT_RPM_WINDOW": "800"})
        self.assertEqual(text.count("[GEARBOX]"), 1)
        line = next(x for x in text.splitlines() if x.startswith("VALID_SHIFT"))
        self.assertIn("compatibilidad con AC", line)
        self.assertIn("ks_toyota_celica_st185", line)
        self.assertIsNone(shift_window_problem(ini))

    def test_other_drivetrain_values_unchanged(self):
        ini = _ini(render_drivetrain_ini(TestAcDrivetrain().result()))
        self.assertEqual(
            ini["GEARS"],
            {
                "COUNT": "3",
                "GEAR_R": "-3",
                "GEAR_1": "3",
                "GEAR_2": "2",
                "GEAR_3": "1",
                "FINAL": "4",
            },
        )
        self.assertEqual(ini["DIFFERENTIAL"], {"PRELOAD": "50"})
        self.assertEqual(ini["TRACTION"], {"TYPE": "RWD"})


class TestValidator(unittest.TestCase):
    def test_cases(self):
        self.assertIn("falta", shift_window_problem({}))
        self.assertIn("falta", shift_window_problem({"GEARBOX": {}}))
        for bad in ("0", "-800", "0.0"):
            self.assertIn(
                "> 0",
                shift_window_problem({"GEARBOX": {"VALID_SHIFT_RPM_WINDOW": bad}}),
            )
        self.assertIn(
            "numérico",
            shift_window_problem({"GEARBOX": {"VALID_SHIFT_RPM_WINDOW": "abc"}}),
        )
        self.assertIsNone(
            shift_window_problem({"GEARBOX": {"VALID_SHIFT_RPM_WINDOW": "800"}})
        )
        self.assertIsNone(
            shift_window_problem({"GEARBOX": {"VALID_SHIFT_RPM_WINDOW": "1000"}})
        )

    def test_validate_structure_flags_missing_key(self):
        name = (
            "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW > 0 (ensamblado.md §9.15)"
        )
        with tempfile.TemporaryDirectory() as d:
            car = Path(d) / "car"
            (car / "data").mkdir(parents=True)
            dt = car / "data" / "drivetrain.ini"
            dt.write_text("[TRACTION]\nTYPE=FWD\n", encoding="utf-8")
            v = validate_structure(car, ())
            self.assertTrue(any(name in e for e in v.errors), v.errors)
            dt.write_text(
                "[TRACTION]\nTYPE=FWD\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=0\n",
                encoding="utf-8",
            )
            self.assertTrue(any(name in e for e in validate_structure(car, ()).errors))
            dt.write_text(
                "[TRACTION]\nTYPE=FWD\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n",
                encoding="utf-8",
            )
            checks = {c["check"]: c["ok"] for c in validate_structure(car, ()).checks}
            self.assertTrue(checks[name])


if __name__ == "__main__":
    unittest.main()
