"""Volante detectado por geometría (src/ac/steer_detect.py), contra el .car del Eagle Inca P395."""

import unittest
from pathlib import Path

from src.ac.steer_detect import detect_steering_wheel

STAGES = Path("output/projects/eagle_inca_p395_dale_earnhardt_edition/stages/model")
KN5 = next(iter(sorted(STAGES.glob("*/_stages/textured_*.kn5"))), None) if STAGES.is_dir() else None
EYES = (0.327, 1.062, -0.195)  # DRIVEREYES del export (marco del KN5)


@unittest.skipUnless(KN5, "KN5 del Eagle no generado (python -m src.project run … --with-model)")
class TestEagleWheel(unittest.TestCase):
    def test_matches_automation_car_file(self) -> None:
        w = detect_steering_wheel(KN5, EYES)
        d = w["detected"]
        # fixture EFT_INTERIOR 9E62DA del .car: x = 31,86 cm, inclinación −13,04°
        self.assertAlmostEqual(d["centre"][0], 0.3186, delta=0.01)
        tilt = -d["axis"][1]
        self.assertAlmostEqual(tilt, 0.2255, delta=0.02)  # sin(13,04°)
        self.assertEqual(w["move_materials"], ["material_93_m_alcantara_inst1_inst_c27b2"])
        self.assertEqual([s["material"] for s in w["splits"]], ["material_92_m_metal_c27b2"])

    def test_user_choice_overrides_and_is_validated(self) -> None:
        w = detect_steering_wheel(KN5, EYES, "material_93_m_alcantara_inst1_inst_c27b2")
        self.assertEqual(w["detected"]["method"], "elegido por el usuario")
        with self.assertRaises(ValueError):  # el salpicadero no es un aro
            detect_steering_wheel(KN5, EYES, "material_87_fixturemat_blackplastic_001_c27b2")
        with self.assertRaises(ValueError):
            detect_steering_wheel(KN5, EYES, "no_existe")

    def test_material_list_puts_likely_first(self) -> None:
        from src.ac.steer_detect import list_materials

        rows = list_materials(KN5, EYES)
        names = [r["material"] for r in rows if r["likely"]]
        self.assertIn("material_93_m_alcantara_inst1_inst_c27b2", names)
        self.assertTrue(rows[0]["likely"])

    def test_override_enters_stage_key(self) -> None:
        import json
        import tempfile
        from types import SimpleNamespace

        from src.project.model import WHEEL_FILE, tool_inputs

        with tempfile.TemporaryDirectory() as tmp:
            proj = SimpleNamespace(root=Path(tmp))
            a = tool_inputs(proj)
            (Path(tmp) / WHEEL_FILE).write_text(json.dumps({"rim_material": "x"}), encoding="utf-8")
            self.assertNotEqual(a, tool_inputs(proj))

    def test_no_wheel_far_from_eyes(self) -> None:
        with self.assertRaises(ValueError):
            detect_steering_wheel(KN5, (0.327, 1.062, -2.0))


if __name__ == "__main__":
    unittest.main()
