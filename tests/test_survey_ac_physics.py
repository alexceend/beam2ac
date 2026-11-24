"""Fase C4: encuesta de solo lectura de la física de AC
(tools/analysis/survey_ac_physics.py)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools/analysis/survey_ac_physics.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("survey_ac_physics", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestSurvey(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        car = self.tmp / "ks_test"
        (car / "data").mkdir(parents=True)
        (car / "ui").mkdir()
        files = {
            "suspensions.ini": "[BASIC]\nWHEELBASE=2.5\nCG_LOCATION=0.5\n"
            "[FRONT]\nTYPE=STRUT\n[REAR]\nTYPE=DWB\n",
            "drivetrain.ini": "[TRACTION]\nTYPE=RWD\n",
            "brakes.ini": "[DATA]\nMAX_TORQUE=2000\nFRONT_SHARE=0.7\n",
            "car.ini": "[BASIC]\nTOTALMASS=1000\n",
            "tyres.ini": "[FRONT]\nRADIUS=0.3\n[REAR]\nRADIUS=0.3\n",
            "engine.ini": "[HEADER]\nPOWER_CURVE=power.lut\n",
            "aero.ini": "[WING_0]\nNAME=BODY\nLUT_AOA_CD=cd.lut\n",
        }
        for name, text in files.items():
            (car / "data" / name).write_text(text, encoding="utf-8")
        (car / "data" / "power.lut").write_text(
            "1000|100\n5000|200\n", encoding="utf-8"
        )
        (car / "data" / "cd.lut").write_text("0|0.35\n", encoding="utf-8")
        (car / "ui" / "ui_car.json").write_text(
            json.dumps({"specs": {"torque": "230Nm", "bhp": "150bhp"}}),
            encoding="utf-8",
        )
        self.car = car
        self.before = {p: p.read_bytes() for p in car.rglob("*") if p.is_file()}

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_values_and_brake_convention(self) -> None:
        r = load_tool().survey_car(self.car)
        self.assertEqual(
            (r["front_type"], r["rear_type"], r["traction"]), ("STRUT", "DWB", "RWD")
        )
        # 2·MAX_TORQUE/(r·m·g) con radios iguales (frenos de AC)
        self.assertAlmostEqual(
            r["brake_decel_g"], 2 * 2000 / (0.3 * 1000 * 9.80665), places=3
        )
        self.assertEqual(r["lut_peak_torque"], (5000.0, 200.0))
        self.assertEqual(r["ui_torque"], 230.0)
        self.assertEqual(r["aero"]["WING_0"]["LUT_AOA_CD_rows"], [(0.0, 0.35)])
        self.assertIn("suspensions.ini", r["sha"])
        # solo lectura
        self.assertEqual(
            {p: p.read_bytes() for p in self.car.rglob("*") if p.is_file()}, self.before
        )


if __name__ == "__main__":
    unittest.main()
