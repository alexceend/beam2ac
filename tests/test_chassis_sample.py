"""Regresión de suspensión, frenos, neumáticos y aero (muestra, solo lectura)."""

import hashlib
import unittest

from src.ac.chassis import (
    analyse,
    chassis_report,
    render_brakes_ini,
    render_suspensions_ini,
    render_tyres_ini,
)
from src.jbeam import PartIndex, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"


def _fingerprint():
    return {
        str(p.relative_to(VEHICLE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VEHICLE.rglob("*"))
        if p.suffix in (".jbeam", ".pc", ".json")
    }


class TestSampleChassis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _fingerprint()
        cls.res = analyse(
            resolve(PartIndex.from_directories(VEHICLE), load_pc(VEHICLE / "35b84.pc"))
        )
        cls.corners = {c.wheel: c for a in cls.res.suspension.axles for c in a.corners}

    def test_suspension_read_values(self):
        front, rear = self.res.suspension.axles
        self.assertEqual(front.label, "ACFR MacPherson Strut Front Suspension")
        self.assertEqual(front.ac_type, "STRUT")
        self.assertIsNone(rear.ac_type)  # eje torsional: pendiente
        fl, rl = self.corners["FL"], self.corners["RL"]
        self.assertEqual((fl.spring_rate, fl.spring_preload_range), (60000, 0.052383))
        self.assertEqual((rl.spring_rate, rl.spring_preload_range), (50000, 0.045701))
        self.assertEqual(
            (
                fl.damper.bump,
                fl.damper.rebound,
                fl.damper.bump_fast,
                fl.damper.velocity_split,
            ),
            (3060, 6732, 1989, 0.15),
        )
        self.assertEqual((fl.bumpstop_travel, rl.bumpstop_travel), (0.07429, 0.083887))
        self.assertAlmostEqual(front.torsionbars[0]["spring_Nm_per_rad"], 1520.990601)
        self.assertEqual(front.torsionbars[0]["name"], "torsionBarFront")
        self.assertAlmostEqual(rear.torsionbars[0]["spring_Nm_per_rad"], 1230.68396)

    def test_suspension_geometry(self):
        fl, rl = self.corners["FL"], self.corners["RL"]
        self.assertAlmostEqual(fl.spring_inclination_deg, 4.81, places=2)
        self.assertAlmostEqual(rl.spring_inclination_deg, 2.66, places=2)
        self.assertAlmostEqual(fl.camber_deg, -2.00, places=2)
        self.assertAlmostEqual(rl.camber_deg, -1.00, places=2)
        self.assertAlmostEqual(fl.toe_deg, 0.0, places=6)
        self.assertEqual(fl.camber_deg, self.corners["FR"].camber_deg)  # simetría

    def test_rest_vs_unloaded_consistency(self):
        # Hipótesis: precarga del muelle en rueda ≈ carga suspendida en reposo
        for wheel, ratio in (("FL", 0.993), ("RL", 0.995)):
            c = self.corners[wheel]
            self.assertAlmostEqual(
                c.preload_at_wheel_hyp / c.sprung_corner_load, ratio, places=3
            )

    def test_brakes(self):
        b = {x.wheel: x for x in self.res.brakes_tyres.brakes}
        self.assertAlmostEqual(b["FL"].max_torque, 2173.546, places=3)
        self.assertAlmostEqual(b["RL"].rate, 483.572, places=3)
        self.assertAlmostEqual(b["RL"].max_torque, 362.679, places=3)  # codo trasero
        self.assertAlmostEqual(b["RL"].parking_torque, 1208.929443)
        report = chassis_report(self.res)["brakes"]
        self.assertAlmostEqual(report["front_share_pedal_100"], 0.8570, places=4)
        self.assertAlmostEqual(report["front_share_below_knee"], 0.8180, places=4)

    def test_tyres(self):
        t = {x.wheel: x for x in self.res.brakes_tyres.tyres}
        self.assertEqual(
            (t["FL"].radius, t["FL"].hub_radius, t["FL"].width), (0.3125, 0.2413, 0.216)
        )
        self.assertEqual(
            (t["RL"].width, t["RL"].pressure_psi, t["FL"].pressure_psi),
            (0.2, 34.9, 37.0),
        )
        checks = {
            c["wheel"]: c for c in chassis_report(self.res)["tyres"]["label_checks"]
        }
        self.assertAlmostEqual(checks["FL"]["nominal_radius"], 0.31265, places=5)
        self.assertAlmostEqual(checks["RL"]["radius_difference"], -0.0034, places=4)
        for w in ("FL", "RL"):
            self.assertAlmostEqual(
                checks[w]["nominal_rim_plus_1in"], checks[w]["hub_radius"]
            )

    def test_aero(self):
        a = self.res.aero
        self.assertEqual(a.cda, 0.582)
        self.assertAlmostEqual(a.cla_front, 0.09055, places=5)
        self.assertAlmostEqual(a.cla_rear, 0.46896, places=5)
        self.assertEqual(a.target_air_density_unused, 1.2041)
        front, rear = a.points
        self.assertEqual((front.is_front, rear.is_front), (True, False))
        self.assertAlmostEqual(front.position_ac[2], 1.20041)
        self.assertAlmostEqual(rear.position_ac[2], -1.29992)

    def test_ac_drafts(self):
        susp = render_suspensions_ini(self.res)
        self.assertIn("WHEELBASE=2.50033", susp)
        self.assertIn("TYPE=STRUT", susp)
        self.assertIn("TRACK=1.475", susp)
        self.assertIn("TRACK=1.43", susp)
        self.assertNotIn("SPRING_RATE=", susp)
        self.assertNotIn("CG_LOCATION=", susp)
        self.assertIn("FRONT_SHARE=0.8570", render_brakes_ini(self.res))
        self.assertNotIn("MAX_TORQUE=", render_brakes_ini(self.res))
        tyres = render_tyres_ini(self.res)
        for key in (
            "RADIUS=0.3125",
            "RIM_RADIUS=0.2413",
            "WIDTH=0.216",
            "PRESSURE_STATIC=34.9",
        ):
            self.assertIn(key, tyres)

    def test_samples_not_modified(self):
        self.assertEqual(_fingerprint(), self.before)


if __name__ == "__main__":
    unittest.main()
