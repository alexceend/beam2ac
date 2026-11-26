"""Luces (src.ac.lights, experimental) y la comprobación de nombres de luces de
car_validate.validate_references."""

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.ac.car_validate import read_ini, validate_references
from src.ac.kn5_materials import split_kn5
from src.ac.kn5_tree import load, walk
from src.ac.kn5_write import box_mesh, write_simple_kn5
from src.ac.lights import (
    LightsError,
    light_functions,
    render_csp_emissive,
    render_lights_ini,
    rename_light_meshes,
)
from src.jbeam import PartIndex, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
MATS = [
    "material_1_m_carpaint",
    "material_2_fixtureglass_brakelight_001",
    "material_3_fixturebulb_light_003",
    "material_4_fixtureglass_reversinglight_001",
]
FUNCS = {MATS[1]: "BRAKE", MATS[2]: "SIGNAL_L", MATS[3]: "REVERSE"}
BM = {
    MATS[1] + "_on": {"Stages": [{"emissiveFactor": [1, 0.02, 0.01]}]},
    MATS[2] + "_on": {"Stages": [{"emissiveFactor": [1, 0.13, 0.0]}]},
    MATS[3] + "_on": {"Stages": [{"emissiveFactor": [1, 1, 1]}]},
}


def _ini(text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ini"
        p.write_text(text, encoding="utf-8")
        return read_ini(p)


class _Fake:
    def __init__(self, glow):
        self.glow = glow

    def section_contributions(self, name):
        return [(None, self.glow)] if name == "glowMap" else []


class TestFunctions(unittest.TestCase):
    def test_sample_glowmap(self):
        r = resolve(PartIndex.from_directories(VEHICLE), load_pc(VEHICLE / "35b84.pc"))
        f = light_functions(r)
        self.assertEqual(len(f), 19)
        self.assertEqual(
            Counter(f.values()),
            Counter(HEADLIGHT=3, TAIL=2, BRAKE=5, REVERSE=1, SIGNAL_L=4, SIGNAL_R=4),
        )
        self.assertEqual(
            f["material_43_fixtureglass_indicatorlight_005_35b84"], "SIGNAL_L"
        )
        self.assertEqual(
            f["material_48_fixtureglass_indicatorlight_005_35b84"], "SIGNAL_R"
        )

    def test_unknown_or_mixed_signals_refused(self):
        with self.assertRaises(LightsError):
            light_functions(_Fake({"m": {"simpleFunction": {"fog": 1}}}))
        with self.assertRaises(LightsError):
            light_functions(
                _Fake({"m": {"simpleFunction": {"brake": 1, "reverse": 1}}})
            )
        self.assertEqual(
            light_functions(
                _Fake({"m": {"simpleFunction": {"lowbeam": 0.49, "highbeam": 1}}})
            ),
            {"m": "HEADLIGHT"},
        )


class TestUserAndEdgeCases(unittest.TestCase):
    def test_running_is_a_tail_light(self):
        self.assertEqual(
            light_functions(_Fake({"m": {"simpleFunction": {"running": 0.5}}})),
            {"m": "TAIL"},
        )

    def test_unknown_signal_skips_only_that_material(self):
        glow = {
            "bad": {"simpleFunction": {"fog": 1}},
            "ok": {"simpleFunction": {"brake": 1}},
        }
        skipped: list[str] = []
        self.assertEqual(light_functions(_Fake(glow), skipped), {"ok": "BRAKE"})
        self.assertEqual(len(skipped), 1)
        with self.assertRaises(
            LightsError
        ):  # sin ``skipped`` se sigue negando (comportamiento antiguo)
            light_functions(_Fake(glow))

    def test_user_overrides_add_replace_and_remove(self):
        glow = {
            "a": {"simpleFunction": {"brake": 1}},
            "b": {"simpleFunction": {"lowbeam": 1}},
        }
        out = light_functions(
            _Fake(glow), [], {"a": None, "b": "SIGNAL_L", "c": "REVERSE"}
        )
        self.assertEqual(out, {"b": "SIGNAL_L", "c": "REVERSE"})
        with self.assertRaises(LightsError):
            light_functions(_Fake(glow), [], {"a": "NOPE"})


class TestRename(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        meshes = [
            box_mesh("body", (0, 0, 0), (1, 1, 1), material=0),
            box_mesh("body", (0, 0.5, -2), (0.4, 0.6, -1.9), material=1),
            box_mesh("body", (0.5, 0.5, -2), (0.7, 0.6, -1.9), material=2),
            box_mesh("body", (-0.3, 0.5, -2), (0.3, 0.6, -1.9), material=3),
        ]
        write_simple_kn5(
            self.dir / "car_x.kn5", meshes, [(m, "ksPerPixel") for m in MATS]
        )
        self.data = (self.dir / "car_x.kn5").read_bytes()

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_light_meshes_renamed_geometry_intact(self):
        new, lights = rename_light_meshes(self.data, FUNCS, BM, (-1.0, 1.0))
        (_, ra), (_, rb) = load(self.data), load(new)
        pairs = list(zip(walk(ra), walk(rb)))
        self.assertTrue(all(x.payload == y.payload for (x, _), (y, _) in pairs))
        renamed = sorted(y.name for (x, _), (y, _) in pairs if x.name != y.name)
        self.assertEqual(renamed, ["B2AC_BRAKE_2", "B2AC_REVERSE_4", "B2AC_SIGNAL_L_3"])
        mats = {m.name: [p for p, _ in m.props] for m in split_kn5(new).materials}
        self.assertNotIn("ksEmissive", mats[MATS[0]])
        for m in MATS[1:]:
            self.assertEqual(mats[m].count("ksEmissive"), 1)
        self.assertEqual(
            {li.function for li in lights}, {"BRAKE", "SIGNAL_L", "REVERSE"}
        )

    def test_material_on_two_meshes_gets_indexed_names(self):
        meshes = [
            box_mesh("body", (0, 0, 0), (1, 1, 1), material=0),
            box_mesh("body", (0, 0.5, -2), (0.4, 0.6, -1.9), material=1),
            box_mesh("body", (1, 0.5, -2), (1.4, 0.6, -1.9), material=1),
        ]
        write_simple_kn5(
            self.dir / "two.kn5", meshes, [(m, "ksPerPixel") for m in MATS[:2]]
        )
        new, lights = rename_light_meshes(
            (self.dir / "two.kn5").read_bytes(), {MATS[1]: "BRAKE"}, BM, (-1.0, 1.0)
        )
        self.assertEqual(
            sorted(li.mesh_name for li in lights),
            ["B2AC_BRAKE_2_0", "B2AC_BRAKE_2_1"],
        )
        li = _ini(render_lights_ini(lights))
        self.assertEqual(
            {li["BRAKE_0"]["NAME"], li["BRAKE_1"]["NAME"]},
            {"B2AC_BRAKE_2_0", "B2AC_BRAKE_2_1"},
        )

    def test_hand_assigned_light_without_on_variant_gets_default_colour(self):
        _, lights = rename_light_meshes(self.data, {MATS[1]: "BRAKE"}, {}, (-1.0, 1.0))
        self.assertEqual(lights[0].colour, (1.0, 0.05, 0.03))

    def test_missing_mesh_refused(self):
        with self.assertRaises(LightsError):
            rename_light_meshes(self.data, {"material_9_x": "BRAKE"}, BM, (-1.0, 1.0))

    def test_renderers(self):
        _, lights = rename_light_meshes(self.data, FUNCS, BM, (-1.0, 1.0))
        li = _ini(render_lights_ini(lights))
        self.assertEqual(li["BRAKE_0"]["NAME"], "B2AC_BRAKE_2")
        self.assertNotEqual(li["BRAKE_0"]["OFF_COLOR"], "0, 0, 0")  # vidrio: posición
        self.assertFalse(any(s.startswith("LIGHT_") for s in li))
        ext = _ini(render_csp_emissive(lights))
        self.assertEqual(ext["EMISSIVE_REVERSE_0"]["NAME"], "B2AC_REVERSE_4")
        self.assertEqual(ext["EMISSIVE_TURNSIGNAL_LEFT_0"]["LOCATION"], "REAR")


class TestValidator(unittest.TestCase):
    def test_light_names_must_be_unique_meshes(self):
        with tempfile.TemporaryDirectory() as d:
            car = Path(d) / "car_x"
            (car / "data").mkdir(parents=True)
            (car / "data" / "lods.ini").write_text(
                "[LOD_0]\nFILE=car_x.kn5\n", encoding="utf-8"
            )
            write_simple_kn5(
                car / "car_x.kn5",
                [
                    box_mesh("body", (0, 0, 0), (1, 1, 1)),
                    box_mesh("body", (2, 0, 0), (3, 1, 1)),
                    box_mesh("lamp", (0, 2, 0), (1, 3, 1)),
                ],
                [("m", "ksPerPixel")],
            )

            def errors(text, ext=None):
                (car / "data" / "lights.ini").write_text(text, encoding="utf-8")
                if ext is not None:
                    (car / "extension").mkdir(exist_ok=True)
                    (car / "extension" / "ext_config.ini").write_text(
                        ext, encoding="utf-8"
                    )
                return [e for e in validate_references(car).errors if "NAME" in e]

            self.assertEqual(errors("[LIGHT_0]\nNAME=lamp\n"), [])
            self.assertTrue(errors("[LIGHT_0]\nNAME=body\n"))  # 2 mallas
            self.assertTrue(errors("[BRAKE_0]\nNAME=nope\n"))  # no existe
            self.assertTrue(
                errors("[LIGHT_0]\nNAME=lamp\n", "[EMISSIVE_REVERSE_0]\nNAME=body\n")
            )
            self.assertEqual(
                errors("[LIGHT_0]\nNAME=lamp\n", "[EMISSIVE_REVERSE_0]\nNAME=lamp\n"),
                [],
            )


if __name__ == "__main__":
    unittest.main()
