"""Resolución de configuraciones con fixtures mínimos en una carpeta temporal."""

import tempfile
import unittest
from pathlib import Path

from src.jbeam.config import PcFormatError, parse_pc
from src.jbeam.parts import PartIndex
from src.jbeam.resolver import ResolveError, resolve
from src.safe_paths import ensure_safe_output_path

CORE = """{
"car_core": {
    "slotType": "main",
    "variables": [
        ["name", "type", "unit", "category", "default", "min", "max",
         "title", "description"],
        ["$psi", "range", "PSI", "Tyres", 30, 5, 60, "Pressure", ""]
    ],
    "slots": [
        ["type", "default", "description"],
        ["body", "body_a", "Body"],
        ["engine", "engine_a", "Engine"],
        ["horn", "", "Horn"]
    ],
    "mainEngine": {"idleRPM": 900}
}
}"""

PARTS = """{
"body_a": {
    "slotType": "body",
    "slots": [
        ["type", "default", "description"],
        ["suspension_F", "susp_f", "Front",
         {"nodeOffset": {"x": 0.1, "y": -1.2, "z": 0.6}}]
    ]
},
"susp_f": {
    "slotType": "suspension_F",
    "slots": [
        ["type", "default", "description"],
        ["hubs_F", "hubs_f", "Hubs"],
        ["wheels_F", "wheels_f", "Wheels", {"nodeOffset": {"x": 0, "y": 0, "z": 0}}],
        ["brake_F", "does_not_exist", "Brake"],
        ["wrong_F", "engine_b", "Wrong slotType"]
    ]
},
"hubs_f": {"slotType": "hubs_F"},
"wheels_f": {
    "slotType": "wheels_F",
    "variables": [
        ["name", "type", "unit", "category", "default", "min", "max",
         "title", "description"],
        ["$psi", "range", "PSI", "Tyres", 99, 5, 60, "Duplicate", ""]
    ]
},
"engine_a": {"slotType": "engine", "mainEngine": {"idleRPM": 1000}},
"engine_b": {"slotType": "engine", "mainEngine": {"idleRPM": 1400}},
"loop": {
    "slotType": "loop",
    "slots": [["type", "default", "description"], ["loop", "loop", "Self"]]
},
"new_style": {
    "slotType": "new",
    "slots2": [["name", "allowTypes", "denyTypes", "default", "description"]]
}
}"""


def _write(root: Path, name: str, text: str) -> None:
    target = ensure_safe_output_path(root / name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class ResolverFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write(self.root, "core.jbeam", CORE)
        _write(self.root, "sub/parts.jbeam", PARTS)
        self.index = PartIndex.from_directories(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def pc(self, parts=None, variables=None):
        return parse_pc({"format": 2, "parts": parts or {}, "vars": variables or {}})


class TestIndex(ResolverFixture):
    def test_parts_from_all_files(self):
        self.assertEqual(len(self.index.files), 2)
        self.assertEqual(len(self.index.parts), 9)
        self.assertEqual(self.index["susp_f"].file.name, "parts.jbeam")
        self.assertEqual(self.index.file_errors, {})

    def test_syntax_error_does_not_stop_loading(self):
        _write(self.root, "broken.jbeam", '{"x": {"slotType": "x"')
        index = PartIndex.from_directories(self.root)
        self.assertEqual(len(index.file_errors), 1)
        self.assertIn("car_core", index)

    def test_duplicate_part_keeps_first_and_records(self):
        _write(self.root, "zz_dup.jbeam", '{"hubs_f": {"slotType": "other"}}')
        index = PartIndex.from_directories(self.root)
        self.assertEqual(index["hubs_f"].slot_type, "hubs_F")
        self.assertEqual(len(index.duplicates), 1)


class TestResolve(ResolverFixture):
    def test_default_tree_in_depth_first_order(self):
        r = resolve(self.index, self.pc())
        self.assertEqual(
            r.active_names,
            ["car_core", "body_a", "susp_f", "hubs_f", "wheels_f", "engine_a"],
        )
        self.assertEqual(r.get("hubs_f").depth, 3)
        self.assertEqual(r.get("hubs_f").parent, "susp_f")
        self.assertEqual(r.get("car_core").chosen_by, "root")

    def test_pc_overrides_default_and_empty_string_empties_slot(self):
        r = resolve(self.index, self.pc({"engine": "engine_b", "hubs_F": ""}))
        self.assertIn("engine_b", r.active_names)
        self.assertNotIn("engine_a", r.active_names)
        self.assertNotIn("hubs_f", r.active_names)
        self.assertEqual(r.get("engine_b").chosen_by, "pc")

    def test_missing_and_mismatched_parts_are_reported(self):
        r = resolve(self.index, self.pc())
        reasons = {m.part_name: m.reason for m in r.missing}
        self.assertEqual(reasons["does_not_exist"], "no existe")
        self.assertIn("slotType", reasons["engine_b"])
        self.assertNotIn("engine_b", r.active_names)

    def test_unreachable_pc_entries(self):
        r = resolve(self.index, self.pc({"ElectricMotor": "x", "engine": "engine_a"}))
        self.assertEqual(r.unreachable_pc, ["ElectricMotor"])
        self.assertTrue(any("ElectricMotor" in w for w in r.warnings))

    def test_node_offset_is_inherited_and_replaced(self):
        r = resolve(self.index, self.pc())
        self.assertEqual(r.get("susp_f").node_offset, {"x": 0.1, "y": -1.2, "z": 0.6})
        self.assertEqual(r.get("hubs_f").node_offset, {"x": 0.1, "y": -1.2, "z": 0.6})
        self.assertEqual(r.get("hubs_f").node_offset_from, "body_a/suspension_F")
        self.assertEqual(r.get("wheels_f").node_offset, {"x": 0, "y": 0, "z": 0})
        self.assertEqual(r.get("wheels_f").node_offset_from, "susp_f/wheels_F")
        self.assertIsNone(r.get("engine_a").node_offset)

    def test_variables_defaults_pc_values_and_duplicates(self):
        r = resolve(self.index, self.pc(variables={"$psi": 35, "$onlyPc": 1}))
        self.assertEqual(r.variables["$psi"].default, 30)
        self.assertEqual(r.variables["$psi"].declared_in, "car_core")
        self.assertEqual(r.variable_values()["$psi"], 35)
        self.assertIsNone(r.variables["$onlyPc"].declared_in)
        self.assertTrue(any("declarada otra vez" in w for w in r.warnings))

    def test_section_contributions_are_listed_not_merged(self):
        r = resolve(self.index, self.pc())
        contributions = r.section_contributions("mainEngine")
        self.assertEqual([a.name for a, _ in contributions], ["car_core", "engine_a"])
        self.assertEqual([v["idleRPM"] for _, v in contributions], [900, 1000])

    def test_self_reference_does_not_loop(self):
        r = resolve(self.index, self.pc({"main": "loop"}))
        self.assertEqual(r.active_names, ["loop"])
        self.assertTrue(any("otra vez" in w for w in r.warnings))

    def test_slots2_is_reported_as_unsupported(self):
        r = resolve(self.index, self.pc({"main": "new_style"}))
        self.assertTrue(any("slots2" in w for w in r.warnings))

    def test_root_errors(self):
        with self.assertRaises(ResolveError):
            resolve(self.index, self.pc({"main": "nope"}))
        _write(self.root, "second_main.jbeam", '{"other_core": {"slotType": "main"}}')
        index = PartIndex.from_directories(self.root)
        with self.assertRaises(ResolveError):
            resolve(index, self.pc())


class TestParsePc(unittest.TestCase):
    def test_valid(self):
        pc = parse_pc({"format": 2, "parts": {"a": "b"}, "vars": {"$x": 1}})
        self.assertEqual(pc.parts, {"a": "b"})
        self.assertEqual(pc.issues, [])

    def test_unverified_format_is_reported(self):
        self.assertEqual(len(parse_pc({"parts": {}}).issues), 1)

    def test_invalid_structures(self):
        for data in ([], {"parts": []}, {"parts": {"a": 1}}, {"vars": []}):
            with self.subTest(data=data):
                with self.assertRaises(PcFormatError):
                    parse_pc(data)


if __name__ == "__main__":
    unittest.main()
