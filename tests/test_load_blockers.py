"""Correcciones para los bloqueos de carga: collider, nulos de cabina, clasificación."""

import tempfile
import unittest
from pathlib import Path

from src.ac.car_validate import CLASSIFICATION, classify, validate_structure
from src.ac.kn5_inspect import Kn5File, Kn5Node, read_kn5
from src.ac.kn5_write import SimpleMesh, box_mesh, signed_volume, write_simple_kn5
from src.mesh.build import verify_kn5
from src.mesh.plan import (
    IDENTITY4,
    MeshPlanError,
    PlanEmpty,
    ScenePlan,
    add_cockpit_lr_nulls,
    add_steer_hr_diagnostic_nulls,
    translate_plan,
)
from src.safe_paths import PROJECT_ROOT, ProtectedPathError, ensure_safe_output_path


class TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestKn5Writer(TmpDir):
    def test_box_is_closed_outward_and_round_trips(self):
        box = box_mesh("GEO_collider", (-1, 0.1, -2), (1, 1.3, 2))
        self.assertEqual((len(box.vertices), len(box.indices) // 3), (8, 12))
        self.assertAlmostEqual(signed_volume(box.vertices, box.indices), 2 * 1.2 * 4)
        path = write_simple_kn5(
            self.tmp / "c.kn5", [box], [("GL", "GL")], root="collider"
        )
        kn5 = read_kn5(path)
        self.assertEqual(kn5.materials, [("GL", "GL")])
        self.assertEqual(kn5.textures, [])
        self.assertEqual(
            [(n.name, n.type) for n in kn5.nodes],
            [("collider", 1), ("GEO_collider", 1), ("GEO_collider", 2)],
        )
        mesh = kn5.nodes[2]
        self.assertEqual(
            {
                k: mesh.flags[k]
                for k in ("cast_shadows", "visible", "transparent", "layer")
            },
            {"cast_shadows": 1, "visible": 1, "transparent": 0, "layer": 0},
        )
        self.assertTrue(mesh.flags["renderable"])
        self.assertAlmostEqual(
            signed_volume(kn5.mesh_world_vertices(mesh), mesh.indices), 9.6, places=5
        )

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            box_mesh("b", (0, 0, 0), (0, 1, 1))
        bad = SimpleMesh("m", [(0, 0, 0)], [(0, 1, 0)], [0, 1, 2])
        with self.assertRaises(ValueError):
            write_simple_kn5(self.tmp / "x.kn5", [bad], [("GL", "GL")])

    def test_refuses_samples(self):
        with self.assertRaises(ProtectedPathError):
            write_simple_kn5(
                PROJECT_ROOT / "sample_assetto" / "x.kn5",
                [box_mesh("b", (0, 0, 0), (1, 1, 1))],
                [],
            )


class TestCockpitNulls(unittest.TestCase):
    def _plan(self):
        frame = ((1, 0, 0, 0.7), (0, 1, 0, -1.0), (0, 0, 1, 0.3), (0, 0, 0, 1))
        return ScenePlan(1, {}, {}, [], [PlanEmpty("WHEEL_LF", "FL", frame)], {})

    def test_added_empty_with_hierarchy(self):
        plan = add_cockpit_lr_nulls(self._plan())
        by = {e.name: e for e in plan.empties}
        self.assertEqual(by["COCKPIT_LR"].parent, None)
        self.assertEqual(by["STEER_LR"].parent, "COCKPIT_LR")
        self.assertEqual(by["COCKPIT_LR"].matrix, IDENTITY4)
        self.assertEqual(len(self._plan().empties), 1)  # original intacto
        with self.assertRaises(MeshPlanError):
            add_cockpit_lr_nulls(plan)

    def test_translation_keeps_parent(self):
        moved = translate_plan(add_cockpit_lr_nulls(self._plan()), (0, 0, 1))
        steer = next(e for e in moved.empties if e.name == "STEER_LR")
        self.assertEqual(steer.parent, "COCKPIT_LR")
        self.assertEqual(steer.matrix[2][3], 1)

    def test_verify_checks_null_parent(self):
        plan = add_cockpit_lr_nulls(ScenePlan(1, {}, {}, [], [], {}))
        kn5 = Kn5File(5, [], [], [Kn5Node("root", 1, -1)])
        kn5.nodes += [Kn5Node("COCKPIT_LR", 1, 0), Kn5Node("STEER_LR", 1, 1)]
        self.assertTrue(verify_kn5(kn5, plan).ok)
        kn5.nodes[2].parent = 0  # STEER_LR colgado de la raíz
        self.assertFalse(verify_kn5(kn5, plan).ok)


class TestSteerHrDiagnostic(unittest.TestCase):
    def test_empty_steer_hr_under_cockpit_hr(self):
        base = add_cockpit_lr_nulls(ScenePlan(1, {}, {}, [], [], {}))
        plan = add_steer_hr_diagnostic_nulls(base)
        by = {e.name: e for e in plan.empties}
        self.assertIsNone(by["COCKPIT_HR"].parent)
        self.assertEqual(by["STEER_HR"].parent, "COCKPIT_HR")
        self.assertEqual(by["STEER_HR"].matrix, IDENTITY4)
        self.assertEqual(plan.objects, [])  # sin geometría
        self.assertTrue(any("diagnóstico" in i for i in plan.issues))
        self.assertNotIn("STEER_HR", {e.name for e in base.empties})
        with self.assertRaises(MeshPlanError):
            add_steer_hr_diagnostic_nulls(plan)

    def test_verify_checks_parent(self):
        plan = add_steer_hr_diagnostic_nulls(ScenePlan(1, {}, {}, [], [], {}))
        kn5 = Kn5File(5, [], [], [Kn5Node("root", 1, -1)])
        kn5.nodes += [Kn5Node("COCKPIT_HR", 1, 0), Kn5Node("STEER_HR", 1, 1)]
        self.assertTrue(verify_kn5(kn5, plan).ok)
        kn5.nodes[2].parent = 0
        self.assertFalse(verify_kn5(kn5, plan).ok)


class TestCommentSections(TmpDir):
    def test_bracket_in_comment_is_error(self):
        data = self.tmp / "car" / "data"
        data.mkdir(parents=True)
        (data / "car.ini").write_text(
            "[BASIC]\nTOTALMASS=1000 ; ok\n; - [BASIC] INERTIA: pendiente\n",
            encoding="utf-8",
        )
        (data / "lods.ini").write_text("[LOD_0]\nFILE=x.kn5\n", encoding="utf-8")
        v = validate_structure(self.tmp / "car", ())
        self.assertTrue(any("car.ini sin [SECCIÓN]" in e for e in v.errors))
        self.assertFalse(any("lods.ini sin [SECCIÓN]" in e for e in v.errors))


class TestClassification(TmpDir):
    def test_classify_files_and_nulls(self):
        car = self.tmp / "car"
        for rel in ("car.kn5", "collider.kn5", "data/lods.ini", "ui/ui_car.json"):
            target = ensure_safe_output_path(car / rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
        (car / "skins" / "s").mkdir(parents=True)
        nulls = {
            f"{k}_{s}" for k in ("WHEEL", "SUSP") for s in ("LF", "LR", "RF", "RR")
        }
        rows = {
            r["item"]: r
            for r in classify(car, "car", nulls | {"COCKPIT_LR", "STEER_LR"})
        }
        self.assertTrue(rows["<id>.kn5"]["present"])
        self.assertTrue(rows["collider.kn5"]["present"])
        self.assertTrue(rows["WHEEL_xx/SUSP_xx"]["present"])
        self.assertTrue(rows["COCKPIT_LR/STEER_LR"]["present"])
        self.assertEqual(rows["COCKPIT_LR/STEER_LR"]["group"], "opcional")
        self.assertFalse(rows["STEER_HR"]["present"])
        self.assertEqual(rows["STEER_HR"]["group"], "cargar")
        self.assertTrue(rows["skins/*"]["present"])
        self.assertFalse(rows["DISC_xx"]["present"])
        self.assertFalse(rows["sfx/<id>.bank"]["present"])
        self.assertEqual(rows["DISC_xx"]["group"], "opcional")

    def test_every_entry_has_group_level_and_evidence(self):
        for group, item, kind, level, evidence in CLASSIFICATION:
            self.assertIn(group, {"cargar", "conducir", "opcional"}, item)
            self.assertIn(
                level, {"documentado", "binario", "observado", "hipótesis"}, item
            )
            self.assertTrue(evidence, item)
            self.assertIn(kind, {"file", "nulls", "skin"}, item)


if __name__ == "__main__":
    unittest.main()
