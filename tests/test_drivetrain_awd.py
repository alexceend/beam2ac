"""Tracción total con diferencial central (extracción, conversión y validación)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.ac.car_validate import awd_problem
from src.ac.drivetrain import analyse, render_drivetrain_ini
from src.extract.drivetrain import DrivetrainDataError, build_drivetrain
from src.extract.mass_geometry import compute_mass_geometry

from .jbeam_fixtures import resolve_parts, simple_car
from .test_drivetrain import ENGINE, PT

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"


def awd_car(center_opts=None, front_final=4.0, rear_final=4.0, center_rows=None):
    """simple_car() con caja → central (salida 1 = trasero, 2 = delantero) → ejes."""
    parts = simple_car()
    center = {"diffType": "lsd", "lsdPreload": 10, "lsdLockCoef": 0.4}
    if center_opts is not None:
        center = center_opts
    parts["car"]["powertrain"] = [
        PT,
        ["camsoEngine", "mainEngine", "dummy", 0],
        ["frictionClutch", "clutch", "mainEngine", 1],
        ["manualGearbox", "gearbox", "clutch", 1],
        center_rows or ["differential", "centerDiff", "gearbox", 1, center],
        ["shaft", "rearShaft", "centerDiff", 1],
        ["shaft", "frontShaft", "centerDiff", 2],
        ["differential", "rearDiff", "rearShaft", 1, {"diffType": "open"}],
        ["differential", "frontDiff", "frontShaft", 1, {"diffType": "open"}],
        ["shaft", "axleRL", "rearDiff", 1, {"connectedWheel": "RL"}],
        ["shaft", "axleRR", "rearDiff", 2, {"connectedWheel": "RR"}],
        ["shaft", "axleFL", "frontDiff", 1, {"connectedWheel": "FL"}],
        ["shaft", "axleFR", "frontDiff", 2, {"connectedWheel": "FR"}],
    ]
    parts["car"]["gearbox"] = {"gearRatios": [-3, 0, 3, 2, 1]}
    parts["car"]["rearDiff"] = {"gearRatio": rear_final}
    parts["car"]["frontDiff"] = {"gearRatio": front_final}
    return parts


def result_for(parts):
    r = resolve_parts(parts)
    return analyse(r, compute_mass_geometry(r), ENGINE)


class TestAwdExtraction(unittest.TestCase):
    def test_center_and_axles_identified(self) -> None:
        dt = build_drivetrain(resolve_parts(awd_car({"diffTorqueSplit": 0.45})))
        self.assertIsNone(dt.differential)
        self.assertEqual(dt.awd.center.name, "centerDiff")
        self.assertEqual(
            [(a.output, a.differential.name, a.wheels) for a in dt.awd.axles],
            [(1, "rearDiff", ("RL", "RR")), (2, "frontDiff", ("FL", "FR"))],
        )
        self.assertEqual(dt.final_drive, 4.0)
        self.assertEqual(dt.forward, [3, 2, 1])

    def test_torque_split_by_output(self) -> None:
        # differential.lua l. 315-317: salida 1 = diffTorqueSplit, salida 2 = resto
        res = result_for(awd_car({"diffTorqueSplit": 0.45}))
        self.assertEqual(res.traction, "AWD")
        self.assertAlmostEqual(res.awd["rear"]["torque_share"], 0.45)
        self.assertAlmostEqual(res.awd["front"]["torque_share"], 0.55)
        self.assertAlmostEqual(res.awd["front_share_percent"], 55.0)

    def test_default_split_is_half_and_declared(self) -> None:
        res = result_for(awd_car({}))
        self.assertAlmostEqual(res.awd["front_share_percent"], 50.0)
        self.assertIn("por defecto", res.awd["split_source"])

    def test_incompatible_cases_rejected(self) -> None:
        cases = {
            "finales distintos": awd_car(front_final=4.0, rear_final=3.5),
            "central con relación": awd_car({"gearRatio": 1.2}),
            "reparto fuera de rango": awd_car({"diffTorqueSplit": 1.5}),
        }
        for name, parts in cases.items():
            with self.subTest(case=name), self.assertRaises(DrivetrainDataError):
                build_drivetrain(resolve_parts(parts))

    def test_two_diffs_still_rejected(self) -> None:
        parts = awd_car()
        rows = parts["car"]["powertrain"]
        parts["car"]["powertrain"] = [r for r in rows if r[1] != "frontDiff"]
        with self.assertRaises(DrivetrainDataError):
            build_drivetrain(resolve_parts(parts))


class TestAwdDraftAndValidator(unittest.TestCase):
    def test_draft_writes_only_verified_keys(self) -> None:
        text = render_drivetrain_ini(result_for(awd_car({"diffTorqueSplit": 0.45})))
        self.assertIn("TYPE=AWD", text)
        self.assertIn("FINAL=4", text)
        self.assertIn("delantero 55 %", text)
        self.assertNotIn("\n[AWD]", text)
        self.assertNotIn("FRONT_SHARE=", text)
        self.assertNotIn("[DIFFERENTIAL]", text)

    def test_validator_requires_front_share_with_awd(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / "drivetrain.ini"
            ini.write_text("[TRACTION]\nTYPE=AWD\n", encoding="utf-8")
            name, ok, detail = awd_problem(d)
            self.assertFalse(ok)
            self.assertIn("AC leería 0", detail)
            ini.write_text("[TRACTION]\nTYPE=AWD\n[AWD]\nFRONT_SHARE=55\n", "utf-8")
            self.assertTrue(awd_problem(d)[1])
            ini.write_text("[TRACTION]\nTYPE=AWD\n[AWD]\nFRONT_SHARE=0\n", "utf-8")
            self.assertFalse(awd_problem(d)[1])
            ini.write_text("[TRACTION]\nTYPE=FWD\n", encoding="utf-8")
            self.assertIsNone(awd_problem(d))


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestAutistCuanto(unittest.TestCase):
    def test_real_awd_layout(self) -> None:
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        d = next(SAMPLES.glob("*/vehicles/autist_cuanto"))
        pv = preview_physics(load_vehicle_dir(d, "b78dc.pc"), 70.0)
        self.assertTrue(pv.stages["drivetrain"]["ok"])
        res = pv.drivetrain
        self.assertEqual(res.traction, "AWD")
        self.assertEqual(res.awd["center"], "transferCase")
        self.assertEqual(res.awd["rear"]["center_output"], 1)
        self.assertAlmostEqual(res.awd["front_share_percent"], 55.0, places=4)
        self.assertEqual(res.drivetrain.final_drive, 4.72)
        self.assertEqual(res.awd["center_params"]["lsdPreload"], 10.0)
        info = {c.quantity: c for c in res.checks}
        self.assertEqual(info["Drivetrain"].reference, "AWD")
        self.assertEqual(info["Drivetrain"].explanation, "coincide")


if __name__ == "__main__":
    unittest.main()
