import json
import tempfile
import unittest
from pathlib import Path

from src.extract.mass_geometry import (
    check_reference_mass,
    compute_mass_geometry,
    write_report,
)
from src.safe_paths import PROJECT_ROOT, ProtectedPathError, ensure_safe_output_path

from .jbeam_fixtures import resolve_parts, simple_car

# simple_car(): cuerpo 100 kg en (0, 0, 0.5); 8 nodos de eje de 1 kg y 4 ruedas
# de 4·2·(1 + 0.5) = 12 kg en (±0.8, ±1, 0.3).
TOTAL = 100 + 8 + 48
COG_Z = (100 * 0.5 + 56 * 0.3) / TOTAL


class TestSimpleCar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = compute_mass_geometry(resolve_parts(simple_car()))

    def test_mass(self):
        self.assertAlmostEqual(self.g.node_mass, 108)
        self.assertAlmostEqual(self.g.wheel_generated_mass, 48)
        self.assertAlmostEqual(self.g.total_mass, TOTAL)
        self.assertAlmostEqual(self.g.wheel_assembly_mass, 4 * (12 + 2))

    def test_cog_in_both_frames(self):
        for got, want in zip(self.g.cog_beamng, (0.0, 0.0, COG_Z)):
            self.assertAlmostEqual(got, want)
        for got, want in zip(self.g.cog_ac, (0.0, COG_Z, 0.0)):
            self.assertAlmostEqual(got, want)

    def test_axles_wheelbase_track_fraction(self):
        g = self.g
        self.assertEqual([a.wheels for a in g.axles], [("FL", "FR"), ("RL", "RR")])
        self.assertAlmostEqual(g.axles[0].longitudinal_ac, 1.0)  # delante = +Z AC
        self.assertAlmostEqual(g.wheelbase, 2.0)
        self.assertAlmostEqual(g.axles[0].track, 1.6)
        self.assertAlmostEqual(g.front_axle_fraction, 0.5)
        self.assertEqual({w.name: w.side for w in g.wheels}["FL"], "left")

    def test_ground_and_cog_height(self):
        self.assertAlmostEqual(self.g.ground_z_beamng, 0.0)
        self.assertAlmostEqual(self.g.cog_height, COG_Z)

    def test_inertia_matches_explicit_point_sum(self):
        # Puntos en ejes AC (x lateral, y vertical, z longitudinal) respecto al CdG.
        pts = [(100, 0.0, 0.5 - COG_Z, 0.0)]
        for x in (0.9, -0.9, 0.7, -0.7):
            for z in (1.0, -1.0):
                pts.append((1, x, 0.3 - COG_Z, z))
        for x in (0.8, -0.8):
            for z in (1.0, -1.0):
                pts.append((12, x, 0.3 - COG_Z, z))
        pitch = sum(m * (y * y + z * z) for m, x, y, z in pts)
        yaw = sum(m * (x * x + z * z) for m, x, y, z in pts)
        roll = sum(m * (x * x + y * y) for m, x, y, z in pts)
        i = self.g.inertia_ac
        self.assertAlmostEqual(i[0][0], pitch)
        self.assertAlmostEqual(i[1][1], yaw)
        self.assertAlmostEqual(i[2][2], roll)
        for a, b in ((0, 1), (0, 2), (1, 2)):
            self.assertAlmostEqual(i[a][b], 0.0)  # simétrico
            self.assertEqual(i[a][b], i[b][a])

    def test_mass_by_part(self):
        self.assertAlmostEqual(self.g.mass_by_part["car"], 100)
        self.assertAlmostEqual(self.g.mass_by_part["wheel_part (ruedas generadas)"], 48)


class TestAxleEdgeCases(unittest.TestCase):
    def test_three_axles_leave_wheelbase_undefined(self):
        parts = simple_car()
        nodes = parts["wheel_part"]["nodes"]
        nodes[4][2] = -0.5  # FR_o
        nodes[5][2] = -0.5  # FR_i
        g = compute_mass_geometry(resolve_parts(parts))
        self.assertEqual(len(g.axles), 3)
        self.assertIsNone(g.wheelbase)
        self.assertIsNone(g.front_axle_fraction)
        self.assertTrue(g.issues)

    def test_no_wheels(self):
        parts = {
            "car": {
                "slotType": "main",
                "nodes": [
                    ["id", "posX", "posY", "posZ"],
                    {"nodeWeight": 5},
                    ["a", 0, 0, 1],
                ],
            }
        }
        g = compute_mass_geometry(resolve_parts(parts))
        self.assertEqual(g.total_mass, 5)
        self.assertEqual(g.axles, [])
        self.assertIsNone(g.cog_height)


class TestReferenceAndReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.g = compute_mass_geometry(resolve_parts(simple_car()))

    def tearDown(self):
        self._tmp.cleanup()

    def _info(self, weight):
        path = ensure_safe_output_path(self.tmp / "info_test.json")
        path.write_text(json.dumps({"Weight": weight}), encoding="utf-8")
        return path

    def test_reference_within_rounding(self):
        check = check_reference_mass(self.g, self._info(156))
        self.assertAlmostEqual(check.difference_kg, 0.0)
        self.assertIn("redondeado", check.explanation)
        self.assertIs(self.g.reference, check)

    def test_reference_mismatch_is_reported_not_corrected(self):
        check = check_reference_mass(self.g, self._info(150))
        self.assertAlmostEqual(check.difference_kg, 6.0)
        self.assertIn("diferencia real", check.explanation)
        self.assertAlmostEqual(self.g.total_mass, TOTAL)

    def test_missing_reference(self):
        self.assertIsNone(check_reference_mass(self.g, self.tmp / "nope.json"))

    def test_report_written_outside_samples_only(self):
        out = write_report(self.g, self.tmp / "out" / "mass.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["total_mass"], TOTAL)
        self.assertIn("status", data)
        with self.assertRaises(ProtectedPathError):
            write_report(self.g, PROJECT_ROOT / "sample_beamng" / "mass.json")
        self.assertFalse((PROJECT_ROOT / "sample_beamng" / "mass.json").exists())


if __name__ == "__main__":
    unittest.main()
