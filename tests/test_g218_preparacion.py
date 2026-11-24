"""G2.18: preparación de la validación en AC.

Comprueba:

* ``ac_body_cg`` y las reglas ``CG_LOCATION``/``BASEY`` del 200409 sin piloto
  (decisión D-G217-CG = A): solo con ``ir.mass.driver == 0`` y el exportador 200409,
  y con los valores «sin piloto» de ``output/g214_suspension_2026-11-03/cdg.md``;
* la gravedad de los bloqueos que AC leería como 0 (``ZERO_IS_WRONG_KEYS``);
* ``gears_problem`` (COUNT frente a GEAR_n);
* el recorte de nombres de Blender (63 bytes) y la caja de vértices con cara del DAE.
"""

from __future__ import annotations

import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from src.ac.car_validate import gears_problem
from src.attrs import transforms
from src.attrs.store import REPO_CATALOG, Catalog
from src.mesh.dae import read_dae
from src.mesh.plan import BLENDER_NAME_BYTES, blender_name
from src.project.export import ZERO_IS_WRONG_KEYS, _blocker_severity

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
LEGION = "2020_rpm_legion_grand_touring"
ATACAMA = "2020_rpm_atacama_sport__4x4"
CG = "ac:suspensions.ini/BASIC/CG_LOCATION"
BF = "ac:suspensions.ini/FRONT/BASEY"
BR = "ac:suspensions.ini/REAR/BASEY"
RULES = (
    "susp.cg_location_200409_nodriver@2",
    "susp.basey_front_200409_nodriver@2",
    "susp.basey_rear_200409_nodriver@2",
)


def available() -> bool:
    return (SAMPLES / LEGION).is_dir()


@lru_cache(maxsize=None)
def resolved(name: str, driver: float):
    from src.attrs.report import resolve_vehicle
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    vd = SAMPLES / name / "vehicles" / name
    pv = preview_physics(load_vehicle_dir(vd, f"{name}.pc"), driver)
    _, results = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), "ac_physics", driver)
    return {r.target: r for r in results}


class TestBodyCgTransform(unittest.TestCase):
    INPUTS = {
        "total": (1000.0, "kg"),
        "front_fraction": (0.6, "1"),
        "hub_front": (25.0, "kg"),
        "hub_rear": (25.0, "kg"),
    }

    def test_cg_location(self):
        v = transforms.apply("ac_body_cg", self.INPUTS, {"output": "cg_location"}, "1")
        self.assertAlmostEqual(v, (1000 * 0.6 - 50) / 900)

    def test_basey(self):
        inp = dict(self.INPUTS, height_front=(-0.2, "m"), height_rear=(-0.1, "m"))
        f = transforms.apply("ac_body_cg", inp, {"output": "basey_front"}, "m")
        r = transforms.apply("ac_body_cg", inp, {"output": "basey_rear"}, "m")
        shift = (50 * -0.2 + 50 * -0.1) / 900
        self.assertAlmostEqual(f, -0.2 + shift)
        self.assertAlmostEqual(r, -0.1 + shift)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(transforms.TransformError):
            transforms.apply("ac_body_cg", self.INPUTS, {"output": "basey_front"}, "m")
        with self.assertRaises(transforms.TransformError):
            transforms.apply("ac_body_cg", self.INPUTS, {"output": "x"}, "1")
        bad = dict(self.INPUTS, hub_front=(0.0, "kg"))
        with self.assertRaises(transforms.TransformError):
            transforms.apply("ac_body_cg", bad, {"output": "cg_location"}, "1")
        self.assertTrue(transforms.implied_assumptions("ac_body_cg", {}))


class TestRules(unittest.TestCase):
    def test_rules_limited_to_200409_and_no_driver(self):
        cat = Catalog.load(REPO_CATALOG)
        for key in RULES:
            rule = cat.rules[key]
            conds = {(c.fact, c.op, str(c.value)) for c in rule.conditions}
            self.assertIn(("ir.formats.exporter_version", "in", "['200409']"), conds)
            self.assertIn(("ir.mass.driver", "eq", "0"), conds)
            self.assertEqual(rule.status, "provisional")

    @unittest.skipUnless(available(), "sin samples_beamng")
    def test_legion_values_without_driver(self):
        r = resolved(LEGION, 0.0)
        # output/g214_suspension_2026-11-03/cdg.md (sin piloto)
        for target, rule, ref in (
            (CG, RULES[0], 0.5598),
            (BF, RULES[1], -0.1696),
            (BR, RULES[2], -0.1699),
        ):
            self.assertEqual(r[target].status, "inferred")
            self.assertEqual(r[target].rule, rule)
            self.assertAlmostEqual(r[target].value, ref, places=4)

    @unittest.skipUnless(available(), "sin samples_beamng")
    def test_driver_rules_apply_with_driver(self):
        # G2.45: con piloto manda la regla *_driver (posición estimada del piloto)
        r0, r = resolved(LEGION, 0.0), resolved(LEGION, 70.0)
        for target, rule in zip((CG, BF, BR), RULES):
            self.assertEqual(r[target].status, "inferred")
            self.assertEqual(r[target].rule, rule.replace("_nodriver@2", "_driver@1"))
            self.assertLess(abs(r[target].value - r0[target].value), 0.02)

    @unittest.skipUnless(available(), "sin samples_beamng")
    def test_rigid_axle_stays_unresolved(self):
        r = resolved(ATACAMA, 0.0)
        # G2.44: el eje rígido ya resuelve (variantes *_axle_nodriver)
        for target in (CG, BF, BR):
            self.assertEqual(r[target].status, "inferred")


class TestExportSeverity(unittest.TestCase):
    def test_zero_is_wrong_keys_are_errors(self):
        for target in (
            CG,
            BF,
            BR,
            "ac:suspensions.ini/FRONT/TYPE",
            "ac:suspensions.ini/REAR/SPRING_RATE",
            "ac:suspensions.ini/FRONT/WBCAR_STEER",
            "ac:car.ini/BASIC/TOTALMASS",
            "ac:car.ini/CONTROLS/STEER_RATIO",
        ):
            self.assertIn(target, ZERO_IS_WRONG_KEYS)
            self.assertEqual(_blocker_severity({"target": target}, {}), "error")

    def test_optional_keys_stay_warnings(self):
        for target in (
            "ac:suspensions.ini/FRONT/PACKER_RANGE",
            # G2.24: COAST_REF/RPM ya no es opcional (ausente ⇒ freno motor 0 en
            # AC); su severidad se comprueba en test_g224_exports
            "ac:suspensions.ini/REAR/TOE_OUT",
        ):
            self.assertEqual(_blocker_severity({"target": target}, {}), "warning")


class TestGears(unittest.TestCase):
    def check(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "drivetrain.ini").write_text(text, encoding="utf-8")
            return gears_problem(d)

    def test_complete(self):
        res = self.check("[GEARS]\nCOUNT=2\nGEAR_R=-3\nGEAR_1=3\nGEAR_2=2\nFINAL=4\n")
        self.assertTrue(res[1])

    def test_missing_gear(self):
        res = self.check("[GEARS]\nCOUNT=3\nGEAR_R=-3\nGEAR_1=3\nGEAR_2=2\n")
        self.assertFalse(res[1])
        self.assertIn("GEAR_3 falta", res[2])

    def test_bad_count_and_zero(self):
        self.assertFalse(self.check("[GEARS]\nGEAR_1=3\n")[1])
        self.assertFalse(self.check("[GEARS]\nCOUNT=1\nGEAR_R=0\nGEAR_1=3\n")[1])

    def test_no_section(self):
        self.assertIsNone(self.check("[TRACTION]\nTYPE=FWD\n"))


DAE = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
 <asset><unit meter="0.01"/><up_axis>Z_UP</up_axis></asset>
 <library_geometries><geometry id="g-lib" name="g">
  <mesh>
   <source id="g-pos">
    <float_array id="a" count="12">0 0 0 1 0 0 0 1 0 9 9 9</float_array>
   </source>
   <vertices id="g-v"><input semantic="POSITION" source="#g-pos"/></vertices>
   <triangles count="1"><input semantic="VERTEX" source="#g-v" offset="0"/>
    <input semantic="NORMAL" source="#n" offset="1"/><p>0 0 1 0 2 0</p></triangles>
  </mesh></geometry></library_geometries>
 <library_visual_scenes><visual_scene id="s">
  <node name="obj"><instance_geometry url="#g-lib"/></node>
 </visual_scene></library_visual_scenes>
</COLLADA>
"""


class TestMesh(unittest.TestCase):
    def test_blender_name(self):
        long = "x" * 65
        self.assertEqual(len(blender_name(long)), BLENDER_NAME_BYTES)
        self.assertEqual(len(blender_name(long, 2)), BLENDER_NAME_BYTES - 2)
        self.assertEqual(blender_name("corto"), "corto")
        # no parte un carácter de varios bytes
        self.assertTrue(blender_name("é" * 40).encode("utf-8").__len__() <= 63)

    def test_face_vertices(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.dae"
            p.write_text(DAE, encoding="utf-8")
            geom = read_dae(p).geometry_for_node("obj")
        self.assertEqual(len(geom.positions), 4)
        self.assertEqual(geom.face_vertices, frozenset({0, 1, 2}))  # el 3 está suelto


if __name__ == "__main__":
    unittest.main()
