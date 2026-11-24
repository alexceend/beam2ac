import math
import unittest

from src.extract.engine_camso import (
    HP_W,
    EngineDataError,
    build_engine_model,
    compare_with_info,
    interp_curve,
)

from .jbeam_fixtures import SLOT_HEADER, resolve_parts

TORQUE_HEADER = ["throttle", "rpm", "torque", "FuelUsed", "Pressure"]
PT_HEADER = ["type", "name", "inputName", "inputIndex"]


def engine_parts(
    main_engine=None, management=None, rows=None, friction=None, device="camsoEngine"
):
    """Motor mínimo: throttle 1, 99 y 100; rpm 0..3000; fricción 10 Nm a 1000,
    30 Nm a 3000. Gestión opcional como pieza hija."""
    if rows is None:
        rows = []
        for rpm, t99, t100, t1, p in (
            (0, 0, 0, 0, 1.0),
            (1000, 110, 120, 5, 1.0),
            (2000, 220, 240, -2, 1.5),
            (3000, 200, 230, -10, 2.0),
        ):
            rows += [[1, rpm, t1, 0, 1.0], [99, rpm, t99, 0, p], [100, rpm, t100, 0, p]]
    engine = {
        "torque": [TORQUE_HEADER, *rows],
        "frictionTorque": [["rpm", "torque"], *(friction or [[1000, 10], [3000, 30]])],
        "idleRPM": 1000,
        "maxRPM": 3500,
        "inertia": 0.12,
        "hasRevLimiter": False,
    }
    engine.update(main_engine or {})
    slots = [SLOT_HEADER]
    parts = {
        "car": {"slotType": "main", "slots": [SLOT_HEADER, ["eng", "engine", "E"]]},
        "engine": {
            "slotType": "eng",
            "powertrain": [PT_HEADER, [device, "mainEngine", "dummy", 0]],
            "mainEngine": engine,
            "slots": slots,
        },
    }
    if management is not None:
        slots.append(["mgmt", "management", "M"])
        parts["management"] = {"slotType": "mgmt", "mainEngine": management}
    return parts


def model_for(**kwargs):
    return build_engine_model(resolve_parts(engine_parts(**kwargs)))


class TestCurve(unittest.TestCase):
    def test_interp_curve(self):
        pts = [(0.0, 0.0), (1000.0, 10.0), (3000.0, 30.0)]
        self.assertEqual(interp_curve(pts, 500), 5)
        self.assertEqual(interp_curve(pts, 2000), 20)
        self.assertEqual(interp_curve(pts, 4000), 30)  # último valor (Lua:731)
        self.assertEqual(interp_curve(pts, -1), 0)

    def test_uses_throttle_99_and_subtracts_friction(self):
        m = model_for()
        by_rpm = {p.rpm: p for p in m.curve}
        self.assertEqual(m.wot_throttle, 99)
        self.assertEqual(by_rpm[2000].gross, 220)  # no 240 (throttle 100)
        self.assertEqual(by_rpm[2000].friction, 20)
        self.assertEqual(by_rpm[2000].net, 200)
        self.assertEqual(by_rpm[1000].net, 100)

    def test_power_and_peaks(self):
        m = model_for()
        self.assertEqual((m.peak_torque.rpm, m.peak_torque.net), (2000, 200))
        # 3000 rpm: neto 200 - 30 = 170 Nm; P = 170·3000·2π/60
        self.assertAlmostEqual(m.peak_power.power_w, 170 * 3000 * 2 * math.pi / 60)
        self.assertEqual(m.peak_power.rpm, 3000)

    def test_na_equivalent_divides_by_pressure(self):
        p = {p.rpm: p for p in model_for().curve}[3000]
        self.assertAlmostEqual(p.na_equivalent, 170 / 2.0)

    def test_duplicate_rpm_uses_last_row_and_is_reported(self):
        rows = [
            [99, 0, 0, 0, 1],
            [99, 200, 0, 0, 1],
            [99, 200, 30, 0, 1],
            [99, 1000, 100, 0, 1],
            [1, 1000, -5, 0, 1],
        ]
        m = model_for(rows=rows)
        self.assertEqual(m.duplicate_rpms, [(200, [0, 30])])
        self.assertEqual({p.rpm: p.gross for p in m.curve}[200], 30)
        self.assertTrue(m.issues)

    def test_missing_wot_rows(self):
        with self.assertRaises(EngineDataError):
            model_for(rows=[[1, 0, 0, 0, 1], [1, 1000, 5, 0, 1]])


class TestLimiterAndMerge(unittest.TestCase):
    def test_conflict_with_same_outcome_is_accepted(self):
        # motor: hasRevLimiter false; gestión: true con corte en el fin de tabla.
        m = model_for(management={"hasRevLimiter": True, "revLimiterRPM": 3000})
        self.assertEqual(m.rpm_ceiling, 3000)
        self.assertIsNone(m.has_cut_limiter)
        self.assertIn("independiente", m.rpm_ceiling_basis)

    def test_conflict_with_different_outcome_is_rejected(self):
        with self.assertRaises(EngineDataError):
            model_for(management={"hasRevLimiter": True, "revLimiterRPM": 2000})

    def test_without_conflict(self):
        m = model_for(main_engine={"hasRevLimiter": True, "revLimiterRPM": 2000})
        self.assertEqual((m.rpm_ceiling, m.has_cut_limiter), (2000, True))
        self.assertEqual(m.peak_power.rpm, 2000)  # picos dentro del techo
        m = model_for()
        self.assertEqual((m.rpm_ceiling, m.has_cut_limiter), (3000, False))

    def test_lua_default_has_limiter_true(self):
        # Sin hasRevLimiter: el Lua lo toma como true (línea 2003).
        parts = engine_parts()
        del parts["engine"]["mainEngine"]["hasRevLimiter"]
        m = build_engine_model(resolve_parts(parts))
        self.assertTrue(m.has_cut_limiter)
        self.assertEqual(m.rpm_ceiling, 3000)  # min(tabla, maxRPM 3500)

    def test_conflict_on_used_key_is_rejected(self):
        with self.assertRaises(EngineDataError):
            model_for(management={"idleRPM": 800})

    def test_operator_on_used_key_is_rejected(self):
        with self.assertRaises(EngineDataError):
            model_for(management={"$*inertia": 1.2})

    def test_operator_on_unused_key_is_ignored(self):
        m = model_for(management={"$*instantAfterFireCoef": 1.25})
        self.assertEqual(m.raw.inertia, 0.12)

    def test_values_come_from_child_parts_when_unique(self):
        m = model_for(management={"revLimiterRPMDrop": 100})
        self.assertEqual(m.raw.rev_limiter_rpm_drop, 100)
        self.assertEqual(m.raw.source_parts, ["engine", "management"])

    def test_other_engine_types_not_supported(self):
        with self.assertRaises(EngineDataError):
            model_for(device="combustionEngine")


class TestDerivedQuantities(unittest.TestCase):
    def test_coast_from_closed_throttle(self):
        coast = dict(model_for().coast)
        # 1000: 5 - 10 = -5 -> ×1.35 -> 6.75 Nm de freno
        self.assertAlmostEqual(coast[1000], 6.75)
        self.assertAlmostEqual(coast[3000], (30 + 10) * 1.35)
        self.assertNotIn(0, coast)  # por debajo del ralentí

    def test_positive_closed_throttle_torque_is_not_scaled(self):
        rows = [[99, 1000, 100, 0, 1], [1, 1000, 15, 0, 1]]
        self.assertAlmostEqual(dict(model_for(rows=rows).coast)[1000], -5)

    def test_boost_and_overrev(self):
        m = model_for()
        self.assertAlmostEqual(m.max_boost_bar, 1.0)
        self.assertEqual(m.full_boost_rpm, 3000)
        self.assertAlmostEqual(m.overrev_damage_rpm, 3500 * 1.05)
        m = model_for(main_engine={"maxPhysicalRPM": 4000})
        self.assertEqual(m.overrev_damage_rpm, 4000)

    def test_no_boost_when_pressure_is_atmospheric(self):
        rows = [[99, 1000, 100, 0, 1.0], [1, 1000, 0, 0, 1.0]]
        self.assertIsNone(model_for(rows=rows).max_boost_bar)

    def test_compare_with_info(self):
        m = model_for()
        hp = m.peak_power.power_w / HP_W
        checks = compare_with_info(
            m, {"Torque": 200, "TorquePeakRPM": 2000, "Power": round(hp) + 3}
        )
        by = {c.quantity: c for c in checks}
        self.assertIn("compatible", by["Torque (Nm)"].explanation)
        self.assertIn("DISCREPANCIA", by["Power (hp)"].explanation)
        self.assertNotIn("PowerPeakRPM", by)  # sin referencia no se compara


if __name__ == "__main__":
    unittest.main()
