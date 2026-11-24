import unittest
from src.engine_mapping import EngineMapper


class TestEngineMapper(unittest.TestCase):
    def setUp(self):
        self.mock_data = {
            "torque_table": [(1000, 100), (2000, 200)],
            "idle_rpm": 950,
            "max_rpm": 7500,
        }
        self.mapper = EngineMapper(self.mock_data)

    def test_map_torque_curve(self):
        self.assertEqual(self.mapper.map_torque_curve(), [(1000, 100), (2000, 200)])

    def test_map_idle_rpm(self):
        self.assertEqual(self.mapper.map_idle_rpm(), 950)

    def test_map_max_rpm(self):
        self.assertEqual(self.mapper.map_max_rpm(), 7500)

    def test_missing_values(self):
        mapper = EngineMapper({})
        self.assertIsNone(mapper.map_idle_rpm())
        self.assertIsNone(mapper.map_max_rpm())
        self.assertIsNone(mapper.map_torque_curve())

    def test_unknown_mappings(self):
        self.assertIsNone(self.mapper.map_inertia())
        self.assertIsNone(self.mapper.map_limiter_hz())
        self.assertIsNone(self.mapper.map_friction())


if __name__ == "__main__":
    unittest.main()
