"""Pérdidas de la cadena completa por marcha y rpm (laboratorio, fase C6)."""

from __future__ import annotations

import unittest

from src.lab.powerlut_losses import (
    chain_affine_loss,
    chain_engine_side_loss,
    chain_wheel_error,
    gear_rpm_matrix,
    minimax_constant_loss,
)

FINAL = 5.21
GEARS = [2.77, 1.72, 1.25, 0.98, 0.81, 0.69, 0.6]
# Cadena del Porsche GT3 RS (JBeam: dynamicFriction = torqueLossCoef = 0).
CHAIN = [
    {"name": "gearbox", "friction": 10.620067690897, "ratio_before": []},
    {"name": "transferCase", "friction": 0.0, "ratio_before": ["gear"]},
    {"name": "rearDriveShaft", "friction": 1.0, "ratio_before": ["gear"]},
    {"name": "rearDiff", "friction": 6.7453120232821, "ratio_before": ["gear"]},
    {"name": "halfshafts", "friction": 2.0, "ratio_before": ["gear", FINAL]},
]


class TestChainAffine(unittest.TestCase):
    def test_affine_matches_chain(self) -> None:
        a, b = chain_affine_loss(CHAIN)
        self.assertAlmostEqual(a, 10.620067690897)
        self.assertAlmostEqual(b, 7.7453120232821 + 2.0 / FINAL)
        for g in GEARS:
            self.assertAlmostEqual(a + b / g, chain_engine_side_loss(CHAIN, g))
        # Parte posterior a la caja = par constante en la rueda, sea cual sea la marcha.
        self.assertAlmostEqual(b * FINAL, 42.3531, places=3)

    def test_gear_twice_rejected(self) -> None:
        with self.assertRaises(ValueError):
            chain_affine_loss([{"friction": 1.0, "ratio_before": ["gear", "gear"]}])

    def test_minimax_is_midpoint(self) -> None:
        lo = chain_engine_side_loss(CHAIN, 2.77)
        hi = chain_engine_side_loss(CHAIN, 0.6)
        self.assertAlmostEqual(minimax_constant_loss(CHAIN, GEARS), (lo + hi) / 2)


class TestGearRpmMatrix(unittest.TestCase):
    CURVE = [[1000.0, 250.0], [5000.0, 420.0], [7000.0, 463.62], [9000.0, 10.0]]

    def test_error_constant_in_rpm_per_gear(self) -> None:
        rows = gear_rpm_matrix(self.CURVE, CHAIN, GEARS, FINAL, 18.0, 1000, 8500)
        self.assertNotIn(9000.0, {r["rpm"] for r in rows})  # fuera de rango
        for g in GEARS:
            errs = {round(r["err_nm"], 9) for r in rows if r["gear"] == g}
            self.assertEqual(len(errs), 1)
            expected = (chain_engine_side_loss(CHAIN, g) - 18.0) * g * FINAL
            self.assertAlmostEqual(errs.pop(), round(expected, 9))
        # El error relativo es mayor donde el par es menor (misma marcha).
        first = {r["rpm"]: abs(r["err_pct"]) for r in rows if r["gear"] == 2.77}
        self.assertGreater(first[1000.0], first[7000.0])

    def test_exact_when_lut_loss_equals_gear_loss(self) -> None:
        g = 1.25
        rows = gear_rpm_matrix(
            self.CURVE, CHAIN, [g], FINAL, chain_engine_side_loss(CHAIN, g), 0, 8500
        )
        for r in rows:
            self.assertAlmostEqual(r["err_nm"], 0.0, places=9)

    def test_consistent_with_peak_table(self) -> None:
        loss = chain_engine_side_loss(CHAIN, 1.1006807742753437)
        rows = gear_rpm_matrix(self.CURVE, CHAIN, GEARS, FINAL, loss, 7000, 7000)
        peak = chain_wheel_error(463.62, CHAIN, GEARS, FINAL, 1.1006807742753437)
        for r, p in zip(rows, peak):
            self.assertAlmostEqual(r["err_pct"], p["err_corrected_pct"], places=9)


if __name__ == "__main__":
    unittest.main()
