import unittest

from src.jbeam.merge import merge_section

from .jbeam_fixtures import SLOT_HEADER, resolve_parts


def _parts(engine, management, extra=None):
    parts = {
        "car": {
            "slotType": "main",
            "slots": [SLOT_HEADER, ["eng", "engine", "E"]],
        },
        "engine": {
            "slotType": "eng",
            "mainEngine": engine,
            "slots": [SLOT_HEADER, ["mgmt", "management", "M"]],
        },
        "management": {"slotType": "mgmt", "mainEngine": management},
    }
    if extra:
        parts["engine"]["slots"].append(["x", "extra", "X"])
        parts["extra"] = {"slotType": "x", "mainEngine": extra}
    return parts


class TestMergeSection(unittest.TestCase):
    def test_disjoint_keys_are_combined(self):
        r = resolve_parts(
            _parts({"idleRPM": 900, "torque": [1]}, {"revLimiterRPM": 7000})
        )
        m = merge_section(r, "mainEngine")
        self.assertEqual(
            m.values, {"idleRPM": 900, "torque": [1], "revLimiterRPM": 7000}
        )
        self.assertEqual(m.sources["revLimiterRPM"], ["management"])
        self.assertEqual(m.parts, ["engine", "management"])
        self.assertEqual(m.conflicts, {})

    def test_equal_values_are_not_a_conflict(self):
        r = resolve_parts(_parts({"idleRPM": 900}, {"idleRPM": 900}))
        m = merge_section(r, "mainEngine")
        self.assertEqual(m.get("idleRPM"), 900)
        self.assertEqual(m.sources["idleRPM"], ["engine", "management"])

    def test_conflicts_are_reported_not_resolved(self):
        r = resolve_parts(_parts({"hasRevLimiter": False}, {"hasRevLimiter": True}))
        m = merge_section(r, "mainEngine")
        self.assertNotIn("hasRevLimiter", m)
        self.assertEqual(
            [(c.part, c.depth, c.value) for c in m.conflicts["hasRevLimiter"]],
            [("engine", 1, False), ("management", 2, True)],
        )

    def test_operators_are_collected_not_applied(self):
        r = resolve_parts(
            _parts({"inertia": 0.1}, {"$*inertia": 1.2}, extra={"$+maxRPM": 300})
        )
        m = merge_section(r, "mainEngine")
        self.assertEqual(m.get("inertia"), 0.1)
        self.assertEqual(
            m.operators, [("management", "$*inertia", 1.2), ("extra", "$+maxRPM", 300)]
        )

    def test_variable_valued_keys_are_not_operators(self):
        # "$var" como VALOR es normal; como CLAVE con letra tampoco es operador.
        r = resolve_parts(_parts({"idleRPM": "$idle"}, {"$idle": 1}))
        m = merge_section(r, "mainEngine")
        self.assertEqual(m.get("idleRPM"), "$idle")
        self.assertEqual(m.operators, [])
        self.assertEqual(m.get("$idle"), 1)

    def test_non_dict_contribution_is_reported(self):
        r = resolve_parts(_parts({"idleRPM": 900}, "texto"))
        m = merge_section(r, "mainEngine")
        self.assertEqual([c.part for c in m.non_dict], ["management"])

    def test_missing_section(self):
        r = resolve_parts(_parts({"idleRPM": 900}, {}))
        m = merge_section(r, "noExiste")
        self.assertEqual((m.values, m.parts), ({}, []))


if __name__ == "__main__":
    unittest.main()
