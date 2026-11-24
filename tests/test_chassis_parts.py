import math
import tempfile
import unittest
from pathlib import Path

from src.ac.chassis import (
    PENDING,
    ChassisWriteError,
    analyse,
    render_brakes_ini,
    render_suspensions_ini,
    render_tyres_ini,
    write_chassis_files,
)
from src.extract.aero import AeroDataError, build_aero
from src.extract.brakes_tyres import (
    BrakeTyreDataError,
    TyreNominal,
    brake_torque,
    build_brakes_tyres,
    front_share,
)
from src.extract.mass_geometry import compute_mass_geometry
from src.extract.suspension import (
    GRAVITY,
    SuspensionDataError,
    build_suspension,
    camber_toe,
)
from src.safe_paths import PROJECT_ROOT, ProtectedPathError

from .jbeam_fixtures import NODE_HEADER, resolve_parts, simple_car

BEAM_HEADER = ["id1:", "id2:"]
WHEELS = {"FL": (0.8, -1.0), "FR": (-0.8, -1.0), "RL": (0.8, 1.0), "RR": (-0.8, 1.0)}


def chassis_car(
    label="Test MacPherson Strut", lean=0.03, skip_spring=None, brakes=True
):
    """simple_car() + muelle/amortiguador/tope por rueda, frenos, presión y aero."""
    parts = simple_car(
        wheel_overrides={
            "pressurePSI": 30,
            **({"brakeTorque": 1000, "parkingTorque": 0} if brakes else {}),
        }
    )
    car = parts["car"]
    car["information"] = {"name": label}
    nodes, beams = [NODE_HEADER, {"nodeWeight": 1.0}], [BEAM_HEADER]
    for w, (x, y) in WHEELS.items():
        inward = -lean if x > 0 else lean  # muelle inclinado hacia dentro
        nodes += [[f"sl_{w}", x, y, 0.3], [f"su_{w}", x + inward, y, 0.7]]
        if w != skip_spring:
            beams += [
                {"beamSpring": 20000, "beamDamp": 0, "precompressionRange": 0.05},
                [f"sl_{w}", f"su_{w}", {"name": f"spring_{w}"}],
            ]
        beams += [
            {"beamSpring": 0, "beamDamp": 2000, "beamDampRebound": 4000},
            [f"sl_{w}", f"su_{w}", {"name": f"damper_{w}"}],
            {"beamDamp": 0, "beamDampRebound": False, "beamLimitSpring": 1e5},
            [f"sl_{w}", f"su_{w}", {"longBoundRange": 0.08, "shortBoundRange": 0.08}],
        ]
    parts["wheel_part"]["nodes"] += nodes[2:]
    parts["wheel_part"]["beams"] = beams
    car["controller"] = [["fileName"], ["cefaero"]]
    car["dragModel"] = {"vehicleArea": 0.6, "dragNodes:": ["body"]}
    car["downforceModel"] = {
        "downforceTargetFront": 100,
        "downforceTargetRear": 200,
        "targetSpeedKMH": 180,
        "targetAirDensity": 1.2,
    }
    car["downforceNodeGroups"] = [
        ["ref:", "up:", "isFront"],
        ["sl_FL", "su_FL", True],
        ["sl_RL", "su_RL", False],
    ]
    return parts


class TestFormulas(unittest.TestCase):
    def test_camber_toe_signs(self):
        # Rueda izquierda: exterior (x mayor) más alto -> caída negativa.
        camber, toe = camber_toe(
            (0.6, 0, 0.30), (0.8, 0, 0.30 + 0.2 * math.tan(math.radians(2)))
        )
        self.assertAlmostEqual(camber, -2.0)
        self.assertAlmostEqual(toe, 0.0)
        # Exterior más adelantado (−Y) -> convergencia positiva, en ambos lados.
        _, toe_l = camber_toe((0.6, 0, 0), (0.8, -0.01, 0))
        _, toe_r = camber_toe((-0.6, 0, 0), (-0.8, -0.01, 0))
        self.assertGreater(toe_l, 0)
        self.assertAlmostEqual(toe_l, toe_r)
        with self.assertRaises(SuspensionDataError):
            camber_toe((0, 0, 0), (0, 0, 1))

    def test_brake_torque_knee(self):
        self.assertEqual(brake_torque(1000, None, None, 1), 1000)
        self.assertEqual(brake_torque(1000, 1, 1, 0.5), 500)
        # codo a 0,5 con pendiente ×0,5 después
        self.assertEqual(brake_torque(1000, 0.5, 0.5, 1), 750)
        self.assertEqual(brake_torque(1000, 0.5, 0.5, 0.4), 400)
        for bad in ((1000, 0.5, 0.5, 1.2), (1000, 0, 1, 1), (1000, 1.5, 1, 1)):
            with self.assertRaises(BrakeTyreDataError):
                brake_torque(*bad)

    def test_tyre_nominal(self):
        t = TyreNominal(215, 45, 17)
        self.assertAlmostEqual(t.radius, 0.09675 + 0.2159)
        self.assertAlmostEqual(t.rim_radius + 0.0254, 0.2413)


class TestSuspension(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = resolve_parts(chassis_car())
        cls.g = compute_mass_geometry(cls.r)
        cls.s = build_suspension(cls.r, cls.g)

    def test_corner_values(self):
        fl = {c.wheel: c for a in self.s.axles for c in a.corners}["FL"]
        length = math.hypot(0.03, 0.4)
        cos = 0.4 / length
        self.assertEqual(fl.spring_rate, 20000)
        self.assertAlmostEqual(fl.spring_length, length)
        self.assertAlmostEqual(fl.spring_inclination_deg, math.degrees(math.acos(cos)))
        self.assertAlmostEqual(fl.motion_ratio_hyp, cos)
        self.assertAlmostEqual(fl.wheel_rate_hyp, 20000 * cos**2)
        self.assertAlmostEqual(fl.preload_at_wheel_hyp, 20000 * 0.05 * cos)
        self.assertEqual((fl.damper.bump, fl.damper.rebound), (2000, 4000))
        self.assertEqual((fl.bumpstop_travel, fl.bumpstop_rate), (0.08, 1e5))
        self.assertAlmostEqual(fl.camber_deg, 0.0)
        # Eje del muelle en ejes AC: arriba (+Y) y hacia dentro (−X a la izquierda)
        self.assertGreater(fl.spring_axis_ac[1], 0)
        self.assertLess(fl.spring_axis_ac[0], 0)

    def test_sprung_corner_load(self):
        fl = self.s.axles[0].corners[0]
        assembly = {w.name: w.assembly_mass for w in self.g.wheels}[fl.wheel]
        expected = self.g.total_mass * GRAVITY * self.g.front_axle_fraction / 2
        self.assertAlmostEqual(fl.sprung_corner_load, expected - assembly * GRAVITY)

    def test_type_from_label(self):
        self.assertEqual([a.ac_type for a in self.s.axles], ["STRUT", "STRUT"])
        s = build_suspension(resolve_parts(chassis_car(label="Torsion Beam")))
        self.assertEqual([a.ac_type for a in s.axles], [None, None])
        self.assertEqual(len(s.issues), 2)

    def test_missing_spring_is_rejected(self):
        with self.assertRaises(SuspensionDataError):
            build_suspension(resolve_parts(chassis_car(skip_spring="RR")))


class TestBrakesTyresAero(unittest.TestCase):
    def test_brakes_and_tyres(self):
        bt = build_brakes_tyres(resolve_parts(chassis_car()))
        self.assertEqual([b.max_torque for b in bt.brakes], [1000] * 4)
        self.assertEqual({t.pressure_psi for t in bt.tyres}, {30})
        self.assertEqual({t.hub_radius for t in bt.tyres}, {0.2})
        fr = [b for b in bt.brakes if b.wheel.startswith("F")]
        rr = [b for b in bt.brakes if b.wheel.startswith("R")]
        self.assertAlmostEqual(front_share(fr, rr), 0.5)

    def test_missing_brakes_are_reported(self):
        bt = build_brakes_tyres(resolve_parts(chassis_car(brakes=False)))
        self.assertEqual(bt.brakes, [])
        self.assertEqual(len(bt.issues), 4)

    def test_aero(self):
        a = build_aero(resolve_parts(chassis_car()))
        q = 0.5 * 1.225 * (180 / 3.6) ** 2
        self.assertEqual(a.cda, 0.6)
        self.assertAlmostEqual(a.cla_front, 100 / q)
        self.assertAlmostEqual(a.cla_rear, 200 / q)
        self.assertEqual(a.target_air_density_unused, 1.2)
        self.assertEqual([p.is_front for p in a.points], [True, False])
        self.assertEqual(a.points[0].position_ac, (0.8, 0.3, 1.0))  # FL delante, +X

    def test_aero_without_cefaero_is_rejected(self):
        parts = chassis_car()
        del parts["car"]["controller"]
        with self.assertRaises(AeroDataError):
            build_aero(resolve_parts(parts))


class TestAcChassis(unittest.TestCase):
    def test_ini_keys(self):
        res = analyse(resolve_parts(chassis_car()))

        def keys(text):
            return [
                line.split("=")[0]
                for line in text.splitlines()
                if "=" in line and not line.startswith(";")
            ]

        self.assertEqual(
            keys(render_suspensions_ini(res)),
            ["WHEELBASE", "TYPE", "TRACK", "TYPE", "TRACK"],
        )
        self.assertEqual(keys(render_brakes_ini(res)), ["FRONT_SHARE"])
        self.assertEqual(
            keys(render_tyres_ini(res)),
            ["RADIUS", "RIM_RADIUS", "WIDTH", "PRESSURE_STATIC"] * 2,
        )
        self.assertIn("FRONT_SHARE=0.5000", render_brakes_ini(res))

    def test_inconsistent_axle_is_rejected(self):
        parts = chassis_car()
        parts["wheel_part"]["pressureWheels"][-1] += [{"pressurePSI": 25}]
        res = analyse(resolve_parts(parts))
        with self.assertRaises(ChassisWriteError):
            render_tyres_ini(res)

    def test_write_and_protection(self):
        res = analyse(resolve_parts(chassis_car()))
        with tempfile.TemporaryDirectory() as tmp:
            out = write_chassis_files(res, Path(tmp) / "c")
            self.assertEqual(
                set(out),
                {"suspensions.ini", "brakes.ini", "tyres.ini", "chassis_report.json"},
            )
            self.assertNotIn("aero.ini", out)
        target = PROJECT_ROOT / "sample_assetto" / "chassis_test"
        with self.assertRaises(ProtectedPathError):
            write_chassis_files(res, target)
        self.assertFalse(target.exists())
        self.assertIn("aero.ini", PENDING)


if __name__ == "__main__":
    unittest.main()
