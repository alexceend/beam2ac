"""Pruebas sin Blender: DAE, transformaciones de flexbody, plan de escena, KN5."""

import math
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import (
    Kn5File,
    Kn5Node,
    invert_rigid,
    mat_mul,
    read_kn5,
    transform_point,
)
from src.extract.nodes import UnsupportedGeometry
from src.mesh.build import verify_kn5
from src.mesh.dae import DaeError, bounds, read_dae
from src.mesh.flexbody import apply, collect_flexbodies, flexbody_matrix, rotation_xyz
from src.mesh.plan import MeshPlanError, build_plan, wheel_frame

from .jbeam_fixtures import SLOT_HEADER, resolve_parts, simple_car
from .mesh_fixtures import write_dae, write_kn5

FB_HEADER = ["mesh", "[group]:", "nonFlexMaterials"]


def _det(m):
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def fb(mesh, groups, pos=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    xyz = lambda v: dict(zip("xyz", v))  # noqa: E731
    return [mesh, groups, [], {"pos": xyz(pos), "rot": xyz(rot), "scale": xyz(scale)}]


def _unique_wheel_groups(parts):
    for row in parts["wheel_part"]["pressureWheels"][2:]:
        row[1:3] = [f"wheel_{row[0]}", f"tire_{row[0]}"]


class TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestDae(TmpDir):
    def test_read_units_positions_and_nan_normals(self):
        path = write_dae(
            self.tmp / "a.dae", {"box": [(0, 0, 0), (100, 50, 10)]}, nan_normals=3
        )
        dae = read_dae(path)
        self.assertEqual((dae.unit_meter, dae.up_axis), (0.01, "Z_UP"))
        geom = dae.geometry_for_node("box")
        self.assertEqual(geom.positions, [(0, 0, 0), (100, 50, 10)])
        self.assertEqual(geom.nonfinite, {"box-Normal0": 3})
        self.assertEqual(bounds(geom.positions), ((0, 0, 0), (100, 50, 10)))

    def test_errors(self):
        cases = {
            "transformación no soportada": dict(
                node_extra="<translate>1 2 3</translate>"
            ),
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(DaeError):
                read_dae(write_dae(self.tmp / "e.dae", {"b": [(0, 0, 0)]}, **kwargs))
        dae = read_dae(write_dae(self.tmp / "ok.dae", {"b": [(0, 0, 0)]}))
        with self.assertRaises(DaeError):
            dae.geometry_for_node("noExiste")


class TestFlexbodyTransform(unittest.TestCase):
    def test_rotation_is_proper_and_ordered_xyz(self):
        for rot in [(0.9, -2, 180), (45.9, -1.5, 179.97), (-75.9, 180, 0)]:
            r = rotation_xyz(rot)
            self.assertAlmostEqual(_det(r), 1.0)
        # Orden Rx·Ry·Rz: Z se aplica primero. Con z = 90° y x = 90°:
        # X local -> (Rz) Y -> (Rx) Z.
        r = rotation_xyz((90, 0, 90))
        self.assertAlmostEqual(r[2][0], 1.0)
        # Mano derecha: +90° en Z lleva X a Y
        self.assertAlmostEqual(rotation_xyz((0, 0, 90))[1][0], 1.0)

    def test_matrix_composition(self):
        from src.mesh.flexbody import Flexbody

        body = Flexbody("p", "m", (), (1, 2, 3), (0, 0, 180), (2, 1, 1))
        m = flexbody_matrix(body, 0.01)
        # vértice (100 cm, 0, 0) -> escala x2 -> 2 m -> giro 180° -> (−2, 0, 0) + pos
        p = apply(m, (100, 0, 0))
        for got, want in zip(p, (-1, 2, 3)):
            self.assertAlmostEqual(got, want)

    def test_collect_and_reject(self):
        parts = simple_car()
        parts["car"]["flexbodies"] = [FB_HEADER, fb("body", "CarBody", (0, 0, 0.5))]
        (f,) = collect_flexbodies(resolve_parts(parts))
        self.assertEqual((f.groups, f.pos), (("CarBody",), (0, 0, 0.5)))
        bad = {
            "sin rot": [FB_HEADER, ["m", [], [], {"pos": {"x": 0, "y": 0, "z": 0}}]],
            "espejo": [FB_HEADER, fb("m", [], scale=(-1, 1, 1))],
        }
        for name, section in bad.items():
            with self.subTest(name):
                p = simple_car()
                p["car"]["flexbodies"] = section
                with self.assertRaises(UnsupportedGeometry):
                    collect_flexbodies(resolve_parts(p))
        p = simple_car()
        p["car"]["slots"].append(
            ["x", "fbpart", "X", {"nodeOffset": {"x": 0.1, "y": 0, "z": 0}}]
        )
        p["fbpart"] = {"slotType": "x", "flexbodies": [FB_HEADER, fb("m", [])]}
        with self.assertRaises(UnsupportedGeometry):
            collect_flexbodies(resolve_parts(p))


class TestWheelFrame(unittest.TestCase):
    def test_left_and_right_frames(self):
        t = math.tan(math.radians(2))
        # izquierda: exterior más alto (caída negativa)
        fl = wheel_frame((0.6, 0, 0.5), (0.8, 0, 0.5 + 0.2 * t), (0.7, 0, 0.5), True)
        fr = wheel_frame(
            (-0.6, 0, 0.5), (-0.8, 0, 0.5 + 0.2 * t), (-0.7, 0, 0.5), False
        )
        for frame in (fl, fr):
            rot = [row[:3] for row in frame[:3]]
            self.assertAlmostEqual(_det(rot), 1.0)  # sin espejo
            self.assertGreater(frame[0][0], 0.99)  # X hacia +X en ambos lados
            self.assertGreater(frame[2][2], 0.99)  # Z ≈ arriba
        self.assertAlmostEqual(fl[2][0], math.sin(math.radians(2)), places=6)
        self.assertAlmostEqual(fr[2][0], -math.sin(math.radians(2)), places=6)
        self.assertEqual((fl[0][3], fl[1][3], fl[2][3]), (0.7, 0, 0.5))


class TestPlan(TmpDir):
    def _plan(self, extra_fb=(), groups_tyre=("tire_FL",)):
        parts = simple_car()
        rows = [FB_HEADER, fb("body", "CarBody", (0, 0, 0.5))]
        rows.append(fb("tyre", list(groups_tyre), (0.8, -1.0, 0.3)))
        rows.append(fb("caliper", ["hub"], (0.8, -1.05, 0.35)))
        rows.extend(extra_fb)
        parts["car"]["flexbodies"] = rows
        _unique_wheel_groups(parts)
        cube = [(x, y, z) for x in (-5, 5) for y in (-5, 5) for z in (-5, 5)]
        dae = write_dae(
            self.tmp / "m.dae",
            {"body": [(-80, -200, 0), (80, 200, 100)], "tyre": cube, "caliper": cube},
        )
        return build_plan(resolve_parts(parts), {str(dae): read_dae(dae)})

    def test_classification_and_names(self):
        plan = self._plan()
        parents = {o.name: o.parent for o in plan.objects}
        self.assertEqual(parents["body"], None)
        self.assertEqual(parents["tyre_LF"], "WHEEL_LF")
        self.assertEqual(parents["caliper_LF"], "SUSP_LF")
        self.assertEqual(
            sorted(e.name for e in plan.empties),
            sorted(
                f"{k}_{s}" for k in ("WHEEL", "SUSP") for s in ("LF", "RF", "LR", "RR")
            ),
        )
        lf = next(e for e in plan.empties if e.name == "WHEEL_LF")
        self.assertEqual(
            (lf.matrix[0][3], lf.matrix[1][3], lf.matrix[2][3]), (0.8, -1.0, 0.3)
        )
        body = next(o for o in plan.objects if o.name == "body")
        self.assertAlmostEqual(body.bbox_beamng[1][2], 0.5 + 1.0)  # 100 cm -> 1 m

    def test_missing_mesh(self):
        with self.assertRaises(MeshPlanError):
            self._plan(extra_fb=[fb("ghost", [], (0, 0, 0))])

    def test_mesh_bound_to_two_wheels(self):
        parts = simple_car()
        parts["car"]["flexbodies"] = [FB_HEADER, fb("tyre", ["tire_FL", "tire_FR"])]
        _unique_wheel_groups(parts)
        dae = write_dae(self.tmp / "t.dae", {"tyre": [(0, 0, 0), (1, 1, 1)]})
        with self.assertRaises(MeshPlanError):
            build_plan(resolve_parts(parts), {str(dae): read_dae(dae)})

    def test_slots_header_constant_is_shared(self):
        self.assertEqual(SLOT_HEADER, ["type", "default", "description"])


class TestKn5(TmpDir):
    def test_synthetic_kn5_and_world_matrices(self):
        k = read_kn5(write_kn5(self.tmp / "a.kn5"))
        self.assertEqual([n.name for n in k.nodes], ["root", "WHEEL_LF", "TIRE"])
        wheel, tire = k.nodes[1], k.nodes[2]
        self.assertEqual(wheel.translation, (1, 2, 3))
        self.assertEqual(tire.parent, 1)
        for got, want in zip(k.mesh_world_vertices(tire)[0], (1.1, 2, 3)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(tire.nonfinite_normals, 0)
        self.assertEqual(
            read_kn5(write_kn5(self.tmp / "b.kn5", True)).nodes[2].nonfinite_normals, 3
        )

    def test_matrix_helpers(self):
        rot = (
            (0.0, 1.0, 0.0, 0.0),
            (-1.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (1.0, 2.0, 3.0, 1.0),
        )
        ident = mat_mul(rot, invert_rigid(rot))
        for i in range(4):
            for j in range(4):
                self.assertAlmostEqual(ident[i][j], 1.0 if i == j else 0.0)
        self.assertEqual(transform_point((1, 0, 0), rot), (1.0, 3.0, 3.0))

    def test_verify_detects_problems(self):
        from src.mesh.plan import PlanEmpty, ScenePlan

        frame = ((1, 0, 0, 1), (0, 1, 0, -3), (0, 0, 1, 2), (0, 0, 0, 1))  # BeamNG
        plan = ScenePlan(1, {}, {}, [], [PlanEmpty("WHEEL_LF", "FL", frame)], {})
        good = Kn5File(5, [], [], [Kn5Node("root", 1, -1)])
        node = Kn5Node("WHEEL_LF", 1, 0)
        node.world = (
            (1, 0, 0, 0),
            (0, 1, 0, 0),
            (0, 0, 1, 0),
            (1, 2, 3, 1),
        )  # AC (1,2,3)
        good.nodes.append(node)
        self.assertTrue(verify_kn5(good, plan).ok)
        node.world = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (1, 2, 3.5, 1))
        self.assertFalse(verify_kn5(good, plan).ok)
        good.nodes.append(Kn5Node("bad", 2, 0, nonfinite_normals=1))
        self.assertTrue(any("NaN" in e for e in verify_kn5(good, plan).errors))


if __name__ == "__main__":
    unittest.main()
