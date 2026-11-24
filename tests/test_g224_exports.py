"""G2.24: defectos de los primeros exports 200409 probados en AC.

* A: GRAPHICS_OFFSET ausente ⇒ carrocería desplazada de las ruedas
  (``src.ac.graphics_offset``, ``export._graphics_offset``).
* B: ``materials.cs`` sin leer ⇒ KN5 sin texturas (``src.extract.materials_cs``,
  ``src.ac.kn5_materials``).
* C: ``[COAST_REF]`` ausente ⇒ freno motor nulo (reglas ``eng.coast_*_combustion@1``,
  ``ac_coast_ref`` con ``law = acs_from_minimum``, ``export.ZERO_IS_WRONG_KEYS``).
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from src.ac import cockpit_config
from src.ac import graphics_offset as gfx
from src.ac.car_validate import parse_ini_ac, read_ini_ac
from src.ac.coast_ref import acs_coast_brake, coast_ref_from_minimum, coast_ref_linear
from src.ac.kn5_materials import (
    Kn5FormatError,
    diffuse_sources,
    material_key,
    paint_dependent_materials,
    upper_layer_paint,
)
from src.attrs import transforms
from src.extract import materials_cs

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
IBIZA_004 = ROOT / "output/projects/ibiza_35b84_b1/exports/004/beam2ac_seat_ibiza_acfr"
IBIZA_V18BU = (
    ROOT
    / "output/seat_ibiza_acfr/ingame_cycle/pkg_v18bu/beam2ac_test16sn_seat_ibiza_acfr"
)
G223 = ROOT / "output/g223_2026-11-09/proyectos"
CARS_200409 = [
    "2020_rpm_atacama_sport__4x4",
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_patagonia_xt_v8",
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
]

CS = """// comentario
singleton Material(mat_a)
{
    mapTo = "mat_a";
    diffuseMap[0] = "vehicles/common/null.dds";
    diffuseColor[0] = "0.5 0.25 1 1";
    /* bloque
       de comentario */
    instanceDiffuse[2] = true;
    doubleSided = "1";
    translucent = "0";
    materialTag0 = "beamng"; materialTag2 = "vehicle";
};
new GFXStateBlockData(algo) { foo = "bar"; };
singleton Material(mat_b)
{
    mapTo = "mat_b";
    diffuseMap[0] = "tex.dds";
    roto[0] = ;
    diffuseColor[0] = "1 1 1 1";
};
"""


def _dds() -> bytes:
    return b"DDS " + b"\0" * 124


class TestMaterialsCs(unittest.TestCase):
    def test_parse_shape_like_materials_json(self):
        res = materials_cs.parse(CS)
        self.assertEqual(sorted(res.materials), ["mat_a", "mat_b"])
        a = res.materials["mat_a"]
        self.assertEqual(a["mapTo"], "mat_a")
        self.assertEqual(len(a["Stages"]), 4)
        self.assertEqual(a["Stages"][0]["diffuseColor"], [0.5, 0.25, 1.0, 1.0])
        self.assertIs(a["Stages"][2]["instanceDiffuse"], True)
        self.assertIs(a["doubleSided"], True)  # "1" ⇒ True (campo bool)
        self.assertIs(a["translucent"], False)
        self.assertEqual(a["materialTag2"], "vehicle")

    def test_malformed_statement_is_reported_and_skipped(self):
        res = materials_cs.parse(CS)
        self.assertTrue(any("roto" in i for i in res.issues))
        self.assertEqual(res.materials["mat_b"]["Stages"][0]["diffuseMap"], "tex.dds")
        self.assertNotIn("GFXStateBlockData", str(res.materials))

    def test_corpus_200409_complete(self):
        for car in CARS_200409:
            p = SAMPLES / car / "vehicles" / car / "materials.cs"
            if not p.is_file():
                self.skipTest(f"falta {p}")
            res = materials_cs.load(p)
            text = p.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(len(res.materials), text.count("singleton Material("))
            self.assertEqual(res.issues, [], car)
            for name, m in res.materials.items():
                self.assertEqual(m["mapTo"], name)


class TestDiffuseSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "materials.cs").write_text(CS, encoding="utf-8")
        (self.dir / "tex.dds").write_bytes(_dds())

    def tearDown(self):
        self.tmp.cleanup()

    def test_null_dds_is_neutral_white(self):
        src = diffuse_sources(
            ["mat_a", "mat_b"], self.dir / "materials.cs", self.dir, None
        )
        by = {s.material: s for s in src}
        self.assertEqual(by["mat_a"].kind, "diffuseColor")  # null.dds × color = color
        self.assertEqual(by["mat_a"].texture_name, "beam2ac_col_8040ff.dds")
        self.assertEqual(by["mat_b"].kind, "diffuseMap")

    def test_upper_layer_paint_is_not_layer_zero(self):
        mats = self.dir / "materials.cs"
        self.assertEqual(paint_dependent_materials(["mat_a"], mats), [])
        self.assertEqual(upper_layer_paint(["mat_a", "mat_b"], mats), ["mat_a"])

    def test_truncated_names_use_dae_materials(self):
        long = "material_29_" + "x" * 60
        bm = {long: {}, long + "_on": {}, long + "_off": {}}
        cut = long[:63]
        self.assertIsNone(material_key(cut, bm))  # ambiguo sin el DAE
        self.assertEqual(material_key(cut, bm, {long}), long)
        self.assertEqual(material_key(cut + ".001", bm, {long}), long)

    def test_unknown_common_texture_still_fails(self):
        (self.dir / "materials.cs").write_text(
            CS.replace("vehicles/common/null.dds", "vehicles/common/otra.dds"),
            encoding="utf-8",
        )
        with self.assertRaises(Kn5FormatError):
            diffuse_sources(["mat_a"], self.dir / "materials.cs", self.dir, None)


class TestGraphicsOffset(unittest.TestCase):
    SUSP = (
        "[BASIC]\nWHEELBASE=2.5\nCG_LOCATION=0.6\n"
        "[FRONT]\nTRACK=1.5\nBASEY=-0.15\n[REAR]\nTRACK=1.4\nBASEY=-0.14\n"
    )

    def nulls(self, shift):
        # KN5 con el suelo en y = 0 y la caja centrada: ruedas a 0,31/0,32 m
        base = {
            "WHEEL_LF": (0.75, 0.31, 1.2),
            "WHEEL_RF": (-0.75, 0.31, 1.2),
            "WHEEL_LR": (0.7, 0.32, -1.3),
            "WHEEL_RR": (-0.7, 0.32, -1.3),
        }
        return {k: tuple(v[i] + shift[i] for i in range(3)) for k, v in base.items()}

    def test_rigid_translation(self):
        bases = gfx.physical_wheel_bases(parse_ini_ac(self.SUSP))
        self.assertEqual(bases["WHEEL_LF"], (0.75, -0.15, 0.4 * 2.5))
        self.assertEqual(bases["WHEEL_RR"], (-0.7, -0.14, -0.6 * 2.5))
        fit = gfx.fit_offset(self.nulls((0, 0, 0)), bases)
        self.assertLess(fit.residual, 1e-9)
        for got, want in zip(fit.offset, (0.0, -0.46, -0.2)):
            self.assertAlmostEqual(got, want, places=9)

    def test_incoherent_wheels_detected(self):
        bases = gfx.physical_wheel_bases(parse_ini_ac(self.SUSP))
        n = self.nulls((0, 0, 0))
        n["WHEEL_LR"] = (0.7, 0.36, -1.3)  # rueda trasera 4 cm más alta en el KN5
        self.assertGreater(gfx.fit_offset(n, bases).residual, gfx.RIGID_TOLERANCE_M)

    def test_missing_physics_key_is_error(self):
        with self.assertRaises(gfx.GraphicsOffsetError):
            gfx.physical_wheel_bases(parse_ini_ac(self.SUSP.replace("BASEY=-0.14", "")))

    def test_ibiza_reference_reproduced(self):
        """El método reproduce el GRAPHICS_OFFSET del Ibiza que funcionó en AC."""
        for car in (IBIZA_V18BU, IBIZA_004):
            kn5s = [p for p in car.glob("*.kn5") if p.name != "collider.kn5"]
            if not kn5s:
                self.skipTest(f"falta {car}")
            susp = read_ini_ac(car / "data/suspensions.ini")
            fit = gfx.derive(kn5s[0], susp)
            for got, want in zip(fit.offset, (0.0, -0.4619, -0.0914)):
                self.assertAlmostEqual(got, want, delta=2e-4)
            self.assertLess(gfx.check(kn5s[0], susp, (0, -0.4619, -0.0914)), 2e-4)

    def test_g223_exports_were_displaced(self):
        """Reproduce el defecto: sin GRAPHICS_OFFSET (AC lee 0) las ruedas del KN5
        quedan a ~0,5 m de su posición física en los cuatro exports G2.23."""
        cars = sorted(G223.glob("*/exports/001/beam2ac_*")) if G223.is_dir() else []
        if len(cars) != 4:
            self.skipTest("faltan los exports G2.23")
        for car in cars:
            kn5 = [p for p in car.glob("*.kn5") if p.name != "collider.kn5"][0]
            susp = read_ini_ac(car / "data/suspensions.ini")
            self.assertIsNone(
                read_ini_ac(car / "data/car.ini")
                .sections["BASIC"]
                .get("GRAPHICS_OFFSET")
            )
            self.assertGreater(gfx.check(kn5, susp, (0, 0, 0)), 0.4, car.name)
            fit = gfx.derive(kn5, susp)
            self.assertLess(gfx.check(kn5, susp, fit.offset), 1e-3)

    def test_add_basic_line(self):
        txt = "[HEADER]\nVERSION=1\n\n[BASIC]\nTOTALMASS=1000\n\n[CONTROLS]\nX=1\n"
        out = cockpit_config.add_basic_line(
            txt, "GRAPHICS_OFFSET", "GRAPHICS_OFFSET=0, -0.4, 0"
        )
        ini = parse_ini_ac(out)
        self.assertEqual(ini.sections["BASIC"]["GRAPHICS_OFFSET"], "0, -0.4, 0")
        self.assertEqual(ini.sections["CONTROLS"]["X"], "1")
        with self.assertRaises(cockpit_config.CockpitConfigError):
            cockpit_config.add_basic_line(out, "GRAPHICS_OFFSET", "GRAPHICS_OFFSET=1")


class TestExportGraphicsOffset(unittest.TestCase):
    def setUp(self):
        self.kn5s = [p for p in IBIZA_004.glob("*.kn5") if p.name != "collider.kn5"]
        if not self.kn5s:
            self.skipTest("falta el export 004 del Ibiza")
        self.susp = (IBIZA_004 / "data/suspensions.ini").read_text(encoding="utf-8")

    def test_resolved_value_is_verified_not_changed(self):
        from src.project.export import _graphics_offset

        car = (IBIZA_004 / "data/car.ini").read_text(encoding="utf-8")
        issues, added, ctx = [], [], {}
        out = _graphics_offset(car, self.susp, self.kn5s[0], issues, added, ctx)
        self.assertEqual(out, car)
        self.assertEqual(issues, [])
        self.assertEqual(ctx["graphics_offset"]["source"], "atributo")

    def test_absent_value_is_derived(self):
        from src.project.export import _graphics_offset

        car = "[HEADER]\nVERSION=1\n\n[BASIC]\nTOTALMASS=1119.57\n"
        issues, added, ctx = [], [], {}
        out = _graphics_offset(car, self.susp, self.kn5s[0], issues, added, ctx)
        self.assertEqual(issues, [])
        self.assertEqual(
            parse_ini_ac(out).sections["BASIC"]["GRAPHICS_OFFSET"],
            "0, -0.4618, -0.0914",
        )
        self.assertEqual(added[0]["key"], "[BASIC] GRAPHICS_OFFSET")

    def test_wrong_resolved_value_is_error(self):
        from src.project.export import _graphics_offset

        car = "[BASIC]\nTOTALMASS=1\nGRAPHICS_OFFSET=0, 0, 0\n"
        issues: list = []
        _graphics_offset(car, self.susp, self.kn5s[0], issues, [], {})
        self.assertEqual([i.severity for i in issues], ["error"])


class TestCoastRef(unittest.TestCase):
    CURVE = [(671.0, 57.6), (1500.0, 60.6), (4000.0, 69.4), (6500.0, 78.3), (7000, 90)]

    def test_acs_law(self):
        self.assertEqual(acs_coast_brake(600, 6500, 78.3, 645), 0.0)
        self.assertEqual(acs_coast_brake(645, 6500, 78.3, 645), 0.0)
        self.assertAlmostEqual(acs_coast_brake(6500, 6500, 78.3, 645), 78.3)

    def test_same_anchor_error_at_idle(self):
        lin = coast_ref_linear(self.CURVE, 6500)
        acs = coast_ref_from_minimum(self.CURVE, 6500, 645)
        self.assertEqual((lin.rpm, lin.torque), (acs.rpm, acs.torque))
        self.assertEqual((acs.rpm, acs.torque, acs.non_linearity), (6500.0, 78.3, 0.0))
        self.assertAlmostEqual(acs.max_abs_error, 57.6 - 78.3 * 26 / 5855, places=6)
        self.assertIsNone(coast_ref_from_minimum(self.CURVE, 6500, 7000))

    def test_transform_law_param(self):
        inputs = {
            "curve": (self.CURVE, "table:rpm->N·m"),
            "ceiling": (6500, "rpm"),
            "minimum": (645.5273, "rpm"),
        }
        for out, want in (("rpm", 6500), ("torque", 78.3), ("non_linearity", 0.0)):
            unit = {"rpm": "rpm", "torque": "N·m", "non_linearity": "1"}[out]
            d = transforms.t_ac_coast_ref(
                inputs, {"output": out, "law": "acs_from_minimum"}, unit
            )
            self.assertEqual(d.value, want)
            self.assertIn("desde MINIMUM = 645 rpm", d.assumptions[0])
        legacy = transforms.t_ac_coast_ref(inputs, {"output": "torque"}, "N·m")
        self.assertIn("lineal desde 0", legacy.assumptions[0])  # Ibiza sin cambios
        with self.assertRaises(transforms.TransformError):
            transforms.t_ac_coast_ref(inputs, {"output": "rpm", "law": "otra"}, "rpm")

    def test_rules_scope(self):
        from src.attrs.store import REPO_CATALOG, Catalog

        cat = Catalog.load(REPO_CATALOG)
        for rid in ("rpm", "torque", "nonlinearity"):
            r = cat.rules[f"eng.coast_{rid}_combustion@2"]
            self.assertEqual(r.open_issues, [])  # @1 quedaba ambigua
            conds = {(c.fact, c.op, str(c.value)) for c in r.conditions}
            self.assertIn(("ir.engine.device_type", "eq", "combustionEngine"), conds)
            self.assertIn(("ir.formats.exporter_version", "in", "['200409']"), conds)
            self.assertEqual(r.transform.params["law"], "acs_from_minimum")
            self.assertEqual(r.status, "provisional")

    def test_absence_blocks_export(self):
        from src.project.export import ZERO_IS_WRONG_KEYS, _blocker_severity

        for k in ("RPM", "TORQUE"):
            t = f"ac:engine.ini/COAST_REF/{k}"
            self.assertIn(t, ZERO_IS_WRONG_KEYS)
            self.assertEqual(_blocker_severity({"target": t}, {}), "error")
        self.assertNotIn("ac:engine.ini/COAST_REF/NON_LINEARITY", ZERO_IS_WRONG_KEYS)


class TestKn5Texture(unittest.TestCase):
    def test_solid_dds_from_cs_colour(self):
        from src.ac.kn5_materials import solid_dds

        d = solid_dds((128, 64, 255))
        self.assertEqual(d[:4], b"DDS ")
        self.assertEqual(struct.unpack_from("<I", d, 12)[0], 4)


if __name__ == "__main__":
    unittest.main()


class TestColliderBody(unittest.TestCase):
    def test_largest_box_not_most_vertices(self):
        from types import SimpleNamespace as NS

        from src.ac.assemble import write_collider

        engine = NS(  # muchos vértices, caja pequeña (como la malla del motor)
            name="flat_block_mid_actor_sectionmesh",
            parent=None,
            vertex_count=130000,
            bbox_beamng=((-0.8, -1.3, 0.2), (0.8, 1.9, 0.7)),
        )
        body = NS(
            name="car_body0",
            parent=None,
            vertex_count=66000,
            bbox_beamng=((-1.0, -2.3, 0.1), (1.0, 2.3, 1.2)),
        )
        wheel = NS(
            name="tire",
            parent="WHEEL_LF",
            vertex_count=999999,
            bbox_beamng=((-9, -9, -9), (9, 9, 9)),
        )
        with tempfile.TemporaryDirectory() as tmp:
            rep = write_collider(
                NS(objects=[engine, body, wheel]), Path(tmp) / "collider.kn5"
            )
        self.assertEqual(rep["source_object"], "car_body0")
