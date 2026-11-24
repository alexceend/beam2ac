import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.ac.drivetrain import (
    analyse,
    compare_with_info,
    render_drivetrain_ini,
    write_drivetrain_files,
)
from src.extract.drivetrain import (
    DrivetrainDataError,
    build_drivetrain,
    gear_speeds,
    traction_type,
)
from src.extract.mass_geometry import compute_mass_geometry
from src.safe_paths import PROJECT_ROOT, ProtectedPathError

from .jbeam_fixtures import resolve_parts, simple_car

PT = ["type", "name", "inputName", "inputIndex"]


def rwd_car(diff_row_opts=None, gearbox=None, diff_section=None, extra_rows=()):
    """simple_car() con tracción trasera; FL cuelga de un semieje inexistente."""
    parts = simple_car()
    parts["car"]["powertrain"] = [
        PT,
        ["camsoEngine", "mainEngine", "dummy", 0],
        ["frictionClutch", "clutch", "mainEngine", 1],
        ["manualGearbox", "gearbox", "clutch", 1],
        ["shaft", "driveshaft", "gearbox", 1],
        [
            "differential",
            "rearDiff",
            "driveshaft",
            1,
            diff_row_opts or {"diffType": "lsd", "lsdPreload": 50, "lsdLockCoef": 0.2},
        ],
        ["shaft", "axleRL", "rearDiff", 1, {"connectedWheel": "RL"}],
        ["shaft", "axleRR", "rearDiff", 2, {"connectedWheel": "RR"}],
        ["shaft", "axleFL", "halfshaftFL", 1, {"connectedWheel": "FL"}],
        *extra_rows,
    ]
    parts["car"]["gearbox"] = gearbox or {"gearRatios": [-3, 0, 3, 2, 1]}
    parts["car"]["rearDiff"] = (
        diff_section if diff_section is not None else {"gearRatio": 4.0}
    )
    return parts


def dt_for(**kwargs):
    return build_drivetrain(resolve_parts(rwd_car(**kwargs)))


class TestGraph(unittest.TestCase):
    def test_path_driven_and_undriven(self):
        dt = dt_for()
        self.assertEqual(
            dt.path,
            ["mainEngine", "clutch", "gearbox", "driveshaft", "rearDiff", "axleRL"],
        )
        self.assertEqual(dt.driven_wheels, ["RL", "RR"])
        self.assertEqual(dt.undriven_axles, ["axleFL"])
        self.assertTrue(any("halfshaftFL" in i for i in dt.issues))

    def test_gears_and_final(self):
        dt = dt_for()
        self.assertEqual((dt.reverse, dt.forward, dt.final_drive), (-3, [3, 2, 1], 4.0))
        self.assertEqual(dt.differential.params["lsdPreload"], 50)

    def test_row_and_section_are_combined(self):
        dt = dt_for(diff_row_opts={"diffType": "open", "gearRatio": 4.0})
        self.assertEqual(dt.differential.params["gearRatio"], 4.0)

    def test_row_section_conflict_is_rejected(self):
        with self.assertRaises(DrivetrainDataError):
            dt_for(diff_row_opts={"diffType": "open", "gearRatio": 4.1})

    def test_expressions_are_evaluated(self):
        dt = dt_for(gearbox={"gearRatios": ["$=-3", 0, "$=1+2", 2, 1]})
        self.assertEqual(dt.reverse, -3)
        self.assertEqual(dt.forward[0], 3)

    def test_unsupported_structures_are_rejected(self):
        cases = {
            "sin punto muerto": dict(gearbox={"gearRatios": [-3, 3, 2, 1]}),
            "punto muerto mal colocado": dict(gearbox={"gearRatios": [0, -3, 3, 2]}),
            "marcha atrás positiva": dict(gearbox={"gearRatios": [3, 0, 3, 2]}),
            "dos marchas atrás": dict(gearbox={"gearRatios": [-3, -2, 0, 3]}),
            "sin gearRatios": dict(gearbox={"friction": 1}),
            "diferencial sin final": dict(diff_section={}),
            "dispositivo desconocido": dict(
                extra_rows=[["rangeBox", "rb", "gearbox", 1]],
            ),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                parts = rwd_car(**kwargs)
                if name == "dispositivo desconocido":
                    parts["car"]["powertrain"][4][2] = "rb"  # driveshaft <- rb
                with self.assertRaises(DrivetrainDataError):
                    build_drivetrain(resolve_parts(parts))

    def test_shaft_with_own_ratio_is_rejected(self):
        parts = rwd_car()
        parts["car"]["powertrain"][4].append({"gearRatio": 0.75})
        with self.assertRaises(DrivetrainDataError):
            build_drivetrain(resolve_parts(parts))

    def test_duplicate_device_and_cycle_are_rejected(self):
        dup = rwd_car(extra_rows=[["shaft", "driveshaft", "gearbox", 1]])
        with self.assertRaises(DrivetrainDataError):
            build_drivetrain(resolve_parts(dup))
        cyc = rwd_car()
        cyc["car"]["powertrain"][4][2] = "rearDiff"  # driveshaft <- rearDiff
        with self.assertRaises(DrivetrainDataError):
            build_drivetrain(resolve_parts(cyc))

    def test_no_driven_wheels(self):
        parts = rwd_car()
        parts["car"]["powertrain"] = parts["car"]["powertrain"][:6]
        with self.assertRaises(DrivetrainDataError):
            build_drivetrain(resolve_parts(parts))

    def test_non_decreasing_gears_are_reported(self):
        dt = dt_for(gearbox={"gearRatios": [-3, 0, 3, 3, 1]})
        self.assertTrue(any("decrecientes" in i for i in dt.issues))


class TestDerived(unittest.TestCase):
    def test_traction_type(self):
        front, rear = {"FL", "FR"}, {"RL", "RR"}
        self.assertEqual(traction_type(["FL", "FR"], front, rear), "FWD")
        self.assertEqual(traction_type(["RL"], front, rear), "RWD")
        self.assertEqual(traction_type(["FL", "RR"], front, rear), "AWD")
        with self.assertRaises(DrivetrainDataError):
            traction_type(["X"], front, rear)

    def test_gear_speeds_formula(self):
        speeds = gear_speeds(
            dt_for(), radius=0.3, rpm_ceiling=6000, peak_power_rpm=5000
        )
        top = speeds[-1]  # 3.ª: 1 · 4.0
        self.assertEqual(top.gear, "3")
        self.assertAlmostEqual(top.total_ratio, 4.0)
        self.assertAlmostEqual(
            top.speed_at_ceiling, 6000 * 2 * math.pi / 60 / 4.0 * 0.3
        )
        self.assertAlmostEqual(speeds[0].total_ratio, 12.0)  # R: |−3| · 4


ENGINE = SimpleNamespace(rpm_ceiling=6000.0, peak_power=SimpleNamespace(rpm=5000.0))


class TestAcDrivetrain(unittest.TestCase):
    def result(self, parts=None, info=None):
        r = resolve_parts(parts or rwd_car())
        return analyse(r, compute_mass_geometry(r), ENGINE, info)

    def test_ini_contains_only_justified_keys(self):
        ini = render_drivetrain_ini(self.result())
        for key in (
            "TYPE=RWD",
            "COUNT=3",
            "GEAR_R=-3",
            "GEAR_3=1",
            "FINAL=4",
            "PRELOAD=50",
        ):
            self.assertIn(key, ini)
        keys = {
            line.split("=")[0]
            for line in ini.splitlines()
            if "=" in line and not line.startswith(";")
        }
        self.assertEqual(
            keys,
            {
                "TYPE",
                "COUNT",
                "GEAR_R",
                "GEAR_1",
                "GEAR_2",
                "GEAR_3",
                "FINAL",
                "PRELOAD",
                "VALID_SHIFT_RPM_WINDOW",  # requisito de compatibilidad con AC (§9.15)
            },
        )

    def test_open_diff_writes_no_preload(self):
        ini = render_drivetrain_ini(
            self.result(rwd_car(diff_row_opts={"diffType": "open"}))
        )
        self.assertNotIn("PRELOAD=", ini)

    def test_awd_is_not_written(self):
        res = self.result()
        res.traction = "AWD"
        with self.assertRaises(DrivetrainDataError):
            render_drivetrain_ini(res)

    def test_info_checks(self):
        res = self.result(
            info={"Drivetrain": "FWD", "Transmission": "Manual", "Top Speed": 40}
        )
        by = {c.quantity: c for c in res.checks}
        self.assertEqual(by["Drivetrain"].explanation, "DISCREPANCIA")
        self.assertEqual(by["Transmission"].explanation, "coincide")
        self.assertIn("No se ajusta", by["Top Speed"].explanation)
        self.assertEqual(compare_with_info(res.drivetrain, "RWD", res.speeds, {}), [])

    def test_write_files_and_protection(self):
        res = self.result()
        with tempfile.TemporaryDirectory() as tmp:
            out = write_drivetrain_files(res, Path(tmp) / "dt")
            self.assertEqual(set(out), {"drivetrain.ini", "drivetrain_report.json"})
        target = PROJECT_ROOT / "sample_assetto" / "dt_test"
        with self.assertRaises(ProtectedPathError):
            write_drivetrain_files(res, target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
