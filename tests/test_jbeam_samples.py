"""Lectura de la muestra real (solo lectura). Criterios de la fase 1 de docs/PLAN.md."""

import hashlib
import unittest
from collections import Counter

from src.jbeam import (
    PartIndex,
    evaluate,
    is_dynamic,
    is_header_table,
    load_pc,
    parse_table,
    resolve,
)
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"


def _fingerprint() -> dict[str, str]:
    return {
        str(p.relative_to(VEHICLE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VEHICLE.rglob("*"))
        if p.suffix in (".jbeam", ".pc")
    }


class TestSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _fingerprint()
        cls.index = PartIndex.from_directories(VEHICLE)
        cls.pc = load_pc(PC)
        cls.resolved = resolve(cls.index, cls.pc)

    def test_all_files_parse(self):
        self.assertEqual(len(self.index.files), 38)
        self.assertEqual(self.index.file_errors, {})
        self.assertEqual(self.index.issues, [])
        self.assertEqual(self.index.duplicates, [])
        self.assertEqual(len(self.index.parts), 107)

    def test_leniencies_present_in_sample(self):
        kinds = Counter(
            n.kind for notes in self.index.leniencies.values() for n in notes
        )
        self.assertGreater(kinds["missing_comma"], 0)
        self.assertGreater(kinds["trailing_comma"], 0)
        self.assertGreater(kinds["comment"], 0)
        self.assertEqual(kinds["extra_comma"], 3)
        self.assertEqual(kinds["duplicate_key"], 0)

    def test_pc(self):
        self.assertEqual(self.pc.format, 2)
        self.assertEqual(self.pc.vars, {"$finaldrive_f": 5.98, "$finaldrive_r": 5.98})
        self.assertEqual(self.pc.parts["Camso_Engine"], "Camso_Engine_83f4c")

    def test_active_parts(self):
        r = self.resolved
        self.assertEqual(r.root, "Camso_seat_ibiza_acfr_core")
        self.assertEqual(len(r.active), 47)
        for name in (
            "Camso_Trim_35b84",
            "Camso_Engine_83f4c",
            "Camso_EngineManagement_83f4c",
            "Camso_Turbo_83f4c",
            "Camso_Transmission_35b84",
            "Camso_wheels_front_35b84",
            "Camso_finaldrive_front_5.98",
        ):
            self.assertIn(name, r.active_names)
        self.assertNotIn("Camso_ElectricMotor_F_35b84", r.active_names)

    def test_expected_warnings(self):
        r = self.resolved
        self.assertEqual(r.unreachable_pc, ["Camso_ElectricMotor_F"])
        self.assertEqual([m.part_name for m in r.missing], ["soundscape_horn_7"])

    def test_node_offsets_follow_inherit_replace_rule(self):
        strut = self.resolved.get("Camso_strut_F_35b84")
        hubs = self.resolved.get("camso_hubs_front_35b84")
        wheels = self.resolved.get("Camso_wheels_front_35b84")
        self.assertEqual(strut.node_offset_from, "Camso_Trim_35b84/Camso_suspension_F")
        self.assertEqual(hubs.node_offset_from, strut.node_offset_from)
        self.assertEqual(
            wheels.node_offset_from, "camso_hubs_front_35b84/Camso_wheels_front"
        )
        values = self.resolved.variable_values()
        self.assertAlmostEqual(evaluate(strut.node_offset["y"], values), -1.20041)

    def test_every_header_table_in_active_parts_is_regular(self):
        tables = 0
        for active in self.resolved.active:
            for section in active.part.data.values():
                if is_header_table(section):
                    tables += 1
                    self.assertEqual(parse_table(section).issues, [], active.name)
        self.assertGreater(tables, 40)

    def test_engine_table_structure_is_read_not_interpreted(self):
        torque = self.index["Camso_Engine_83f4c"].data["mainEngine"]["torque"]
        table = parse_table(torque)
        self.assertEqual(
            table.header, ("throttle", "rpm", "torque", "FuelUsed", "Pressure")
        )
        self.assertEqual(len(table.rows), 10302)
        self.assertEqual({r.get("throttle") for r in table.rows}, set(range(101)))

    def test_all_dynamic_values_of_active_parts_evaluate(self):
        values = self.resolved.variable_values()
        count = 0

        def walk(value):
            nonlocal count
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif is_dynamic(value):
                evaluate(value, values)
                count += 1

        for active in self.resolved.active:
            walk(active.part.data)
        self.assertEqual(count, 188)

    def test_samples_are_not_modified(self):
        self.assertEqual(_fingerprint(), self.before)


if __name__ == "__main__":
    unittest.main()
