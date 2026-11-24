"""G2.27 (B0): circuito de agarre partido y prueba ``split`` del banco de validación.

Sin AC: el KN5 se lee con el lector del proyecto y la prueba corre contra el doble sintético
de G2.25 (que no modela el agarre: aquí solo se comprueba el flujo y las ventanas).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from tests.test_g225_validate import run_sim
from tools.acbench import track_g225, track_split
from tools.acbench.validate import PROTOCOLS, SPLIT_ITEMS


class TestSplitTrack(unittest.TestCase):
    def test_strip_replaces_ground_cells_on_one_side_of_the_lane(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.kn5"
            p.write_bytes(track_split.build_kn5())
            k = read_kn5(p)
        meshes = {n.name: k.mesh_world_vertices(n) for n in k.nodes if n.type == 2}
        self.assertEqual(set(meshes), {"1ROAD", "1LOWMU"})
        low = meshes["1LOWMU"]
        # un borde de la franja es la línea central de la recta: un lado del coche sobre cada superficie
        self.assertAlmostEqual(min(v[0] for v in low), track_g225.LANE_X)
        self.assertAlmostEqual(max(v[0] for v in low), track_g225.LANE_X + track_g225.CELL)
        self.assertAlmostEqual(min(v[2] for v in low), track_split.STRIP_Z[0])
        self.assertAlmostEqual(max(v[2] for v in low), track_split.STRIP_Z[1])
        self.assertTrue(all(abs(v[1]) < 1e-6 for v in low))  # misma cota que el suelo
        # la salida de la recta queda fuera de la franja (aproximación sobre agarre uniforme)
        self.assertLess(track_g225.LANE_Z0, track_split.STRIP_Z[0])

    def test_surfaces_add_low_friction_and_keep_permissions(self):
        s = track_split.SURFACES_INI
        self.assertIn("KEY=LOWMU\nFRICTION=0.1", s)
        self.assertIn("KEY=ROAD\nFRICTION=1", s)
        self.assertIn("[_SCRIPTING_PHYSICS]", s)
        self.assertEqual(s.count("[_SCRIPTING_PHYSICS]"), 1)

    def test_g225_track_build_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            r = track_g225.build(Path(d))
            self.assertEqual(r.name, track_g225.TRACK_ID)
            self.assertEqual(
                (r / f"{track_g225.TRACK_ID}.kn5").read_bytes(), track_g225.build_kn5()
            )
            r2 = track_split.build(Path(d))
            self.assertTrue((r2 / f"{track_split.TRACK_ID}.kn5").is_file())


class TestSplitProtocolSynthetic(unittest.TestCase):
    def test_split_runs_and_marks_windows(self):
        v, _ = run_sim()
        trials = v.run_tests(PROTOCOLS["b0"])
        split = [t for t in trials if t.test == "split"]
        self.assertEqual(len(split), len(SPLIT_ITEMS))
        for t in split:
            self.assertEqual(t.reasons, [], t.item)
            for w in ("aproximacion", "split_gas", "split_retencion"):
                self.assertIn(w, t.seg_windows, t.item)


if __name__ == "__main__":
    unittest.main()
