"""Variantes de transmisión encontradas en samples_beamng (2026-10-30)."""

from __future__ import annotations

import unittest

from src.ac.drivetrain import analyse, render_drivetrain_ini
from src.extract.drivetrain import DrivetrainDataError, build_drivetrain
from src.extract.mass_geometry import compute_mass_geometry

from .jbeam_fixtures import resolve_parts
from .test_drivetrain import ENGINE, rwd_car


def car_with(gearbox_type: str, diff_opts: dict | None = None):
    parts = rwd_car(diff_row_opts=diff_opts)
    rows = parts["car"]["powertrain"]
    for row in rows[1:]:
        if row[1] == "gearbox":
            row[0] = gearbox_type
    return resolve_parts(parts)


class TestSequentialGearbox(unittest.TestCase):
    def test_sequential_reads_like_manual_and_is_flagged(self) -> None:
        manual = build_drivetrain(car_with("manualGearbox"))
        seq = build_drivetrain(car_with("sequentialGearbox"))
        self.assertEqual(
            (seq.reverse, seq.forward, seq.final_drive),
            (manual.reverse, manual.forward, manual.final_drive),
        )
        self.assertEqual(seq.gearbox.type, "sequentialGearbox")
        self.assertTrue(any("sequentialGearbox" in i for i in seq.issues))
        self.assertFalse(any("sequentialGearbox" in i for i in manual.issues))

    def test_other_gearboxes_still_rejected(self) -> None:
        for kind in ("automaticGearbox", "dctGearbox"):
            with self.subTest(kind=kind), self.assertRaises(DrivetrainDataError):
                build_drivetrain(car_with(kind))


class TestNonLsdDiffComment(unittest.TestCase):
    def test_torque_vectoring_is_named_not_translated(self) -> None:
        resolved = car_with("manualGearbox", {"diffType": "torqueVectoring"})
        res = analyse(resolved, compute_mass_geometry(resolved), ENGINE)
        text = render_drivetrain_ini(res)
        self.assertIn("diffType='torqueVectoring'", text)
        self.assertNotIn("PRELOAD=", text)

    def test_lsd_has_no_extra_comment(self) -> None:
        resolved = car_with("manualGearbox")
        text = render_drivetrain_ini(
            analyse(resolved, compute_mass_geometry(resolved), ENGINE)
        )
        self.assertNotIn("diffType=", text)
        self.assertIn("PRELOAD=50", text)


if __name__ == "__main__":
    unittest.main()
