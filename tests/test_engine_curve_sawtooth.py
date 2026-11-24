"""Tabla de par de origen dentada: ``engine_curve`` no bloquea (agus_1_nrt_1, 2026-11-20)."""

import unittest

from src.attrs.facts_bng import Fact
from src.attrs.validations_bng import info_validations, source_sawtooth

SMOOTH = [[r, 500.0 - abs(r - 5000) / 20] for r in range(0, 10100, 100)]
# zigzag de ±100 N·m entre pasos, como la fila 99 de agus por encima de 10 800 rpm
SAW = SMOOTH[:90] + [
    [9000 + 100 * i, 480 + (100 if i % 2 else -100)] for i in range(12)
]
SPOOL_STEP = [[r, 400.0 + (80 if r >= 5200 else 0)] for r in range(0, 10100, 100)]


def _result(curve: list[list[float]], power_hp: float) -> str:
    peak = max(t for _, t in curve)
    f = {
        "ir.engine.wot_torque_curve": curve,
        "ir.engine.peak_torque": peak,
        "ir.engine.peak_power": 700_000.0,  # ≈ 939 hp: no coincide con la ficha
        "ir.declared.torque": peak,
        "ir.declared.power": power_hp,
    }
    vs = info_validations(
        "v", "c", {k: Fact(k, x, "", "measured") for k, x in f.items()}
    )
    return next(v for v in vs if v.id.endswith("engine_curve")).result


class TestSawtooth(unittest.TestCase):
    def test_detector(self) -> None:
        self.assertGreaterEqual(source_sawtooth(SAW), 3)
        self.assertEqual(source_sawtooth(SMOOTH), 0)
        self.assertEqual(source_sawtooth(SPOOL_STEP), 0)

    def test_saw_is_inconclusive_but_real_failure_still_fails(self) -> None:
        self.assertEqual(_result(SAW, 500.0), "inconclusive")
        self.assertEqual(_result(SMOOTH, 500.0), "fail")


if __name__ == "__main__":
    unittest.main()
