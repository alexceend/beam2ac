"""Regresión de masa y geometría sobre la muestra real (solo lectura)."""

import hashlib
import unittest

from src.extract.mass_geometry import check_reference_mass, compute_mass_geometry
from src.extract.nodes import collect_nodes
from src.jbeam import PartIndex, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"


def _fingerprint():
    return {
        str(p.relative_to(VEHICLE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VEHICLE.rglob("*"))
        if p.suffix in (".jbeam", ".pc", ".json")
    }


class TestSampleMassGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _fingerprint()
        cls.resolved = resolve(
            PartIndex.from_directories(VEHICLE), load_pc(VEHICLE / "35b84.pc")
        )
        cls.g = compute_mass_geometry(cls.resolved)
        cls.ref = check_reference_mass(cls.g, VEHICLE / "info_35b84.json")
        cls.nodes = {n.id: n for n in collect_nodes(cls.resolved)}

    def test_mass_against_reference(self):
        self.assertEqual(self.g.node_count, 437)
        self.assertAlmostEqual(self.g.node_mass, 1149.0, places=1)
        self.assertAlmostEqual(self.g.wheel_generated_mass, 77.27, places=2)
        self.assertAlmostEqual(self.g.total_mass, 1226.27, places=2)
        self.assertEqual(self.ref.reference_kg, 1226)
        self.assertLessEqual(abs(self.ref.difference_kg), 0.5)

    def test_plan_criteria(self):
        g = self.g
        self.assertAlmostEqual(g.wheelbase, 2.500, delta=0.001)
        self.assertAlmostEqual(g.axles[0].track, 1.475, delta=0.001)
        self.assertAlmostEqual(g.axles[1].track, 1.430, delta=0.001)
        self.assertAlmostEqual(g.front_axle_fraction, 0.575, delta=0.005)
        self.assertEqual(g.axles[0].wheels, ("FR", "FL"))  # eje delantero
        self.assertEqual(g.issues, [])

    def test_wheels(self):
        w = {x.name: x for x in self.g.wheels}
        self.assertEqual(w["FL"].side, "left")
        self.assertEqual(w["FR"].side, "right")
        self.assertAlmostEqual(w["FL"].center_ac[0], 0.7375, places=4)
        self.assertAlmostEqual(w["FL"].center_ac[2], 1.2004, places=4)
        self.assertEqual(w["FL"].radius, 0.3125)
        self.assertAlmostEqual(w["FL"].generated_mass, 21.197, places=3)
        self.assertAlmostEqual(w["RL"].generated_mass, 17.439, places=3)
        self.assertAlmostEqual(w["FL"].spin_inertia, 1.465, places=3)

    def test_node_offset_rule_geometric_evidence(self):
        # Pinza fh5l (pieza con nodeOffset heredado) en el plano de la rueda FL
        # (nodos absolutos fw1ll/fw1l, cuyo nodeOffset fue reemplazado por 0).
        fl = {x.name: x for x in self.g.wheels}["FL"]
        self.assertAlmostEqual(
            self.nodes["fh5l"].pos[0], fl.center_beamng[0], delta=1e-6
        )
        # Parejas izquierda/derecha simétricas tras aplicar los offsets.
        pairs = [
            k for k in self.nodes if k.endswith("l") and k[:-1] + "r" in self.nodes
        ]
        self.assertGreaterEqual(len(pairs), 19)
        for left in pairs:
            lp, rp = self.nodes[left].pos, self.nodes[left[:-1] + "r"].pos
            self.assertAlmostEqual(lp[0], -rp[0], delta=1e-9, msg=left)
            self.assertAlmostEqual(lp[1], rp[1], delta=1e-9, msg=left)
            self.assertAlmostEqual(lp[2], rp[2], delta=1e-9, msg=left)

    def test_cog_and_hypotheses(self):
        g = self.g
        self.assertAlmostEqual(g.cog_beamng[0], 0.0, delta=0.002)
        self.assertAlmostEqual(g.cog_ac[2], 0.1328, places=3)  # delante del origen
        self.assertAlmostEqual(g.ground_z_beamng, 0.2988, places=3)
        self.assertAlmostEqual(g.cog_height, 0.4355, places=3)
        i = g.inertia_ac
        self.assertTrue(all(i[k][k] > 0 for k in range(3)))
        self.assertGreater(i[1][1], i[0][0])  # yaw > pitch > roll
        self.assertGreater(i[0][0], i[2][2])

    def test_samples_not_modified(self):
        self.assertEqual(_fingerprint(), self.before)


if __name__ == "__main__":
    unittest.main()
