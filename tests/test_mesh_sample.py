"""Regresión de mallas sobre la muestra real (solo lectura).

La construcción completa con Blender es opcional: se activa con la variable de
entorno ``BEAM2AC_BLENDER_TESTS=1`` y requiere ``blender`` en el PATH. Escribe
solo en una carpeta temporal.
"""

import hashlib
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from src.extract.nodes import collect_nodes
from src.jbeam import PartIndex, load_pc, resolve
from src.mesh.build import plan_for_vehicle, run_blender, verify_kn5
from src.mesh.dae import bounds
from src.mesh.flexbody import collect_flexbodies, rotation_xyz
from src.mesh.plan import write_plan
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"
AC_SAMPLE = PROJECT_ROOT / "sample_assetto" / "bw_ps13.kn5"


def _fingerprint():
    return {
        str(p.relative_to(VEHICLE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VEHICLE.rglob("*"))
        if p.suffix in (".jbeam", ".pc", ".dae")
    }


class TestSampleMeshPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _fingerprint()
        cls.plan = plan_for_vehicle(VEHICLE, PC)
        cls.resolved = resolve(PartIndex.from_directories(VEHICLE), load_pc(PC))
        cls.nodes = {n.id: n for n in collect_nodes(cls.resolved)}

    def test_objects_and_parents(self):
        objs = {o.name: o for o in self.plan.objects}
        self.assertEqual(len(objs), 31)
        self.assertEqual(sum(o.vertex_count for o in objs.values()), 361789)
        by_parent = {}
        for o in objs.values():
            by_parent.setdefault(o.parent, []).append(o.name)
        self.assertEqual(len(by_parent[None]), 15)
        for suffix, axle in (("LF", "f"), ("RF", "f"), ("LR", "r"), ("RR", "r")):
            children = sorted(by_parent[f"WHEEL_{suffix}"])
            self.assertEqual(len(children), 3, suffix)  # neumático, llanta, disco
            self.assertTrue(all(f"_{axle}" in c for c in children))
            (caliper,) = by_parent[f"SUSP_{suffix}"]
            self.assertIn("brakecaliper", caliper)

    def test_wheel_empties_at_wheel_centres(self):
        lf = next(e for e in self.plan.empties if e.name == "WHEEL_LF")
        self.assertAlmostEqual(lf.matrix[0][3], 0.7375, places=4)
        self.assertAlmostEqual(lf.matrix[1][3], -1.2004, places=4)
        self.assertAlmostEqual(lf.matrix[0][0], math.cos(math.radians(2.0)), places=4)
        self.assertAlmostEqual(lf.matrix[2][0], math.sin(math.radians(2.0)), places=4)

    def test_euler_convention_evidence(self):
        # Eje del neumático (X local rotado) frente al eje de rueda de los nodos:
        # solo R = Rx·Ry·Rz (mano derecha) lo alinea en las 4 ruedas.
        axles = {
            "FL": ("fw1l", "fw1ll"),
            "FR": ("fw1r", "fw1rr"),
            "RL": ("rw1l", "rw1ll"),
            "RR": ("rw1r", "rw1rr"),
        }
        tyres = [f for f in collect_flexbodies(self.resolved) if "tyre" in f.mesh]
        self.assertEqual(len(tyres), 4)
        for f in tyres:
            wheel = next(w for w in axles if w in "".join(f.groups))
            a, b = (self.nodes[n].pos for n in axles[wheel])
            axle = [b[i] - a[i] for i in range(3)]
            norm = math.sqrt(sum(c * c for c in axle))
            r = rotation_xyz(f.rot_deg)
            local_x = [r[i][0] for i in range(3)]
            cosang = abs(sum(local_x[i] * axle[i] for i in range(3))) / norm
            self.assertLess(math.degrees(math.acos(min(1.0, cosang))), 0.1, wheel)

    def test_body_matches_carbody_nodes(self):
        body = next(o for o in self.plan.objects if o.name == "35b84_body0")
        nodes = [
            n.pos
            for n in self.nodes.values()
            if n.group == "CarBody"
            or (isinstance(n.group, list) and "CarBody" in n.group)
        ]
        lo, hi = bounds(nodes)
        for i in range(3):
            self.assertLess(abs(body.bbox_beamng[0][i] - lo[i]), 0.03)
            self.assertLess(abs(body.bbox_beamng[1][i] - hi[i]), 0.03)

    def test_tyre_size_matches_pressure_wheels(self):
        tyre = next(o for o in self.plan.objects if o.name == "35b84_tyre_f2_LF")
        lo, hi = tyre.bbox_dae
        self.assertAlmostEqual((hi[1] - lo[1]) / 2 * 0.01, 0.3125, places=4)

    def test_nan_normals_reported(self):
        self.assertTrue(
            any("35b84_chassis1" in i and "NaN" in i for i in self.plan.issues)
        )

    def test_ac_sample_reference_layout(self):
        kn5 = read_kn5(AC_SAMPLE, keep_vertices=False)
        self.assertFalse(any(n.type == 3 for n in kn5.nodes))
        wheel = kn5.find("WHEEL_LF")[0]
        self.assertEqual(
            tuple(round(v, 3) for v in wheel.translation), (0.84, 0.28, 1.276)
        )

    def test_samples_not_modified(self):
        self.assertEqual(_fingerprint(), self.before)


@unittest.skipUnless(
    os.environ.get("BEAM2AC_BLENDER_TESTS") == "1" and shutil.which("blender"),
    "construcción con Blender desactivada (BEAM2AC_BLENDER_TESTS=1 para activarla)",
)
class TestBlenderBuild(unittest.TestCase):
    def test_full_build(self):
        plan = plan_for_vehicle(VEHICLE, PC)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            plan_path = write_plan(plan, out / "scene_plan.json")
            proc = run_blender(
                shutil.which("blender"), plan_path, out, "car.kn5", False
            )
            self.assertEqual(
                proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:]
            )
            result = verify_kn5(read_kn5(out / "car.kn5"), plan)
            self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
