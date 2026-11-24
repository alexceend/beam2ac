"""Laboratorio matemático (src/lab): ecuaciones reconstruidas, casos límite y ausencias.

Los valores esperados se calculan a mano o con ``math`` en la propia prueba; no son
mediciones de los juegos.
"""

from __future__ import annotations

import math
import unittest

from src.lab import drivetrain_lab as L

# Ibiza 35b84, JBeam resuelto
# (output/research_frenos_diferencial_transmision_2026-10-26/vehicles)
IBZ_F_RATE, IBZ_R_RATE = 2173.5457032, 483.5717776
IBZ_BASE = L.ACBrakeInputs(
    max_torque_nm=2536.2, front_share=0.857, handbrake_torque_nm=1208.9
)


def sig(z: float) -> float:
    return 1 / (1 + math.exp(-z))


class TestBeamNGBrakes(unittest.TestCase):
    def test_input_factor_elbow(self) -> None:
        # trasera del Ibiza: s = 0,5, k = 0,5
        self.assertAlmostEqual(L.bng_brake_input_factor(0.3, 0.5, 0.5), 0.3)
        self.assertAlmostEqual(L.bng_brake_input_factor(1.0, 0.5, 0.5), 0.5 + 0.5 * 0.5)
        # ausentes = 1 (wheels.lua l. 1064-1065): sin codo
        self.assertAlmostEqual(L.bng_brake_input_factor(0.7, None, None), 0.7)
        # recorte a [0, 1]
        self.assertAlmostEqual(L.bng_brake_input_factor(1.0, 2.0, -1.0), 1.0)
        with self.assertRaises(L.LabInputError):
            L.bng_brake_input_factor(1.2, 1, 1)

    def test_thermal_efficiency_formula(self) -> None:
        w1, w2, b1, b2, a = 0.008462, -0.0117, 1.5, 10.75, -0.982  # semi-race
        expected = sig(w1 * 20 + b1) + sig(w2 * 20 + b2) + a
        self.assertAlmostEqual(L.bng_thermal_efficiency(20, "semi-race"), expected)
        self.assertAlmostEqual(
            L.bng_thermal_efficiency(20, "semi-race"), 0.8594, places=4
        )
        # muy caliente: σ1 → 1, σ2 → 0 ⇒ f = 1 + 0 + a = 0,012 (la curva no llega a 0)
        self.assertAlmostEqual(L.bng_thermal_efficiency(5000, "basic"), 1 - 0.988)
        for temp in (-1000, 0, 200, 600, 5000):
            for pad in L.BNG_PAD_EFFICIENCY:
                self.assertTrue(0.0 <= L.bng_thermal_efficiency(temp, pad) <= 1.0)
        self.assertLessEqual(L.bng_thermal_efficiency(300, "godmode"), 1.0)
        with self.assertRaises(L.LabInputError):
            L.bng_thermal_efficiency(20, "unobtainium")

    def test_wheel_torque_max_of_service_and_parking(self) -> None:
        # trasera del Ibiza a fondo: 483,57·0,75 = 362,68 < parking 1208,93
        t = L.bng_wheel_brake_torque_nm(
            IBZ_R_RATE,
            1.0,
            0.5,
            0.5,
            parking_torque_nm=1208.929443,
            parkingbrake_input=1.0,
        )
        self.assertAlmostEqual(t, 1208.929443)
        t2 = L.bng_wheel_brake_torque_nm(IBZ_R_RATE, 1.0, 0.5, 0.5)
        self.assertAlmostEqual(t2, IBZ_R_RATE * 0.75)
        # la eficiencia térmica escala el resultado (l. 642)
        t3 = L.bng_wheel_brake_torque_nm(IBZ_F_RATE, 1.0, thermal_efficiency=0.5)
        self.assertAlmostEqual(t3, IBZ_F_RATE * 0.5)
        with self.assertRaises(L.LabInputError):
            L.bng_wheel_brake_torque_nm(-1.0, 1.0)


class TestACBrakes(unittest.TestCase):
    def test_base_distribution(self) -> None:
        fl, fr, rl, rr = L.ac_wheel_brake_torques_nm(IBZ_BASE, 1.0)
        self.assertAlmostEqual(fl, 2536.2 * 0.857)
        self.assertAlmostEqual(rl, 2536.2 * (1 - 0.857))
        self.assertEqual((fl, rl), (fr, rr))

    def test_missing_keys_are_zero(self) -> None:
        none = L.ACBrakeInputs(None, None, None)
        self.assertEqual(
            L.ac_wheel_brake_torques_nm(none, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0)
        )
        no_share = L.ACBrakeInputs(1000.0, None, None)
        self.assertEqual(
            L.ac_wheel_brake_torques_nm(no_share, 1.0), (0.0, 0.0, 1000.0, 1000.0)
        )

    def test_handbrake_is_max_not_sum(self) -> None:
        cfg = L.ACBrakeInputs(1000.0, 0.5, 300.0)
        _, _, rl, _ = L.ac_wheel_brake_torques_nm(cfg, 1.0, handbrake=1.0)
        self.assertAlmostEqual(rl, 500.0)  # max(500, 300)
        _, _, rl2, _ = L.ac_wheel_brake_torques_nm(cfg, 0.2, handbrake=1.0)
        self.assertAlmostEqual(rl2, 300.0)  # max(100, 300)

    def test_abs_override_and_limits(self) -> None:
        cfg = L.ACBrakeInputs(1000.0, 0.9, 300.0, limit_down=0.5, limit_up=0.7)
        fl, _, rl, _ = L.ac_wheel_brake_torques_nm(cfg, 1.0, abs_override=(0, 1, 1, 1))
        self.assertEqual(fl, 0.0)
        self.assertAlmostEqual(rl, 1000.0 * (1 - 0.7))  # bias recortado a 0,7

    def test_rear_correction_floor(self) -> None:
        cfg = L.ACBrakeInputs(1000.0, 0.8, 0.0, rear_correction_nm=500.0)
        _, _, rl, _ = L.ac_wheel_brake_torques_nm(cfg, 1.0)
        self.assertEqual(rl, 0.0)


class TestBrakeConversion(unittest.TestCase):
    def test_reproduces_ibiza_base_at_full_pedal(self) -> None:
        mt, fs = L.ac_brakes_from_bng_at_pedal(
            IBZ_F_RATE, 1, 1, IBZ_R_RATE, 0.5, 0.5, 1.0
        )
        self.assertAlmostEqual(mt, IBZ_F_RATE + IBZ_R_RATE * 0.75)
        self.assertAlmostEqual(mt, 2536.2, delta=0.05)
        self.assertAlmostEqual(fs, 0.857, delta=0.0005)

    def test_single_regime_counterexample(self) -> None:
        # Contraejemplo: la conversión a pedal 1 no reproduce el par trasero a pedal 0,3
        mt, fs = L.ac_brakes_from_bng_at_pedal(
            IBZ_F_RATE, 1, 1, IBZ_R_RATE, 0.5, 0.5, 1.0
        )
        _, _, ac_rear, _ = L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(mt, fs, 0.0), 0.3
        )
        bng_rear = L.bng_wheel_brake_torque_nm(IBZ_R_RATE, 0.3, 0.5, 0.5)
        self.assertAlmostEqual(bng_rear, IBZ_R_RATE * 0.3)
        self.assertLess(ac_rear, bng_rear)  # AC frena menos atrás a pedal bajo
        self.assertAlmostEqual(ac_rear / bng_rear, 0.75, places=6)

    def test_half_pedal_conversion_tradeoff(self) -> None:
        # Conversión a pedal 0,5: exacta por debajo del codo, +1/3 atrás a pedal 1
        mt, fs = L.ac_brakes_from_bng_at_pedal(
            IBZ_F_RATE, 1, 1, IBZ_R_RATE, 0.5, 0.5, 0.5
        )
        cfg = L.ACBrakeInputs(mt, fs, 0.0)
        for p in (0.25, 0.5):
            _, _, ac_r, _ = L.ac_wheel_brake_torques_nm(cfg, p)
            self.assertAlmostEqual(
                ac_r, L.bng_wheel_brake_torque_nm(IBZ_R_RATE, p, 0.5, 0.5)
            )
        _, _, ac_r1, _ = L.ac_wheel_brake_torques_nm(cfg, 1.0)
        bng_r1 = L.bng_wheel_brake_torque_nm(IBZ_R_RATE, 1.0, 0.5, 0.5)
        self.assertAlmostEqual(ac_r1 / bng_r1, 4 / 3)

    def test_invalid_reference(self) -> None:
        with self.assertRaises(L.LabInputError):
            L.ac_brakes_from_bng_at_pedal(1, 1, 1, 1, 1, 1, 0.0)
        with self.assertRaises(L.LabInputError):
            L.ac_brakes_from_bng_at_pedal(0, 1, 1, 0, 1, 1, 1.0)


class TestDifferential(unittest.TestCase):
    def test_bng_side_cap(self) -> None:
        self.assertAlmostEqual(L.bng_lsd_side_cap_nm(1000, 100, 0.25, 0.025), 100 + 250)
        self.assertAlmostEqual(L.bng_lsd_side_cap_nm(-1000, 100, 0.25, 0.025), 100 + 25)
        self.assertAlmostEqual(L.bng_lsd_side_cap_nm(0, 100, 0.25, 0.025), 100)
        # valores por defecto (l. 333-335)
        self.assertAlmostEqual(L.bng_lsd_side_cap_nm(1000, None, None, None), 50 + 200)
        self.assertAlmostEqual(L.bng_lsd_side_cap_nm(-1000, None, 0.3, None), 50 + 300)
        # coef recortado a 1 y segundo tope del dispositivo
        self.assertAlmostEqual(L.bng_lsd_side_cap_nm(1000, 0, 5.0, None), 1000)
        self.assertAlmostEqual(
            L.bng_lsd_side_cap_nm(1000, 100, 0.25, 0.025, 200.0), 200
        )
        self.assertAlmostEqual(L.bng_lsd_difference_cap_nm(1000, 100, 0.25, 0.025), 700)

    def test_ac_cap(self) -> None:
        # base del Ibiza: PRELOAD 100, POWER = COAST = 0 (ausentes)
        for t in (-300.0, 0.0, 300.0):
            self.assertAlmostEqual(
                L.ac_lsd_difference_cap_nm(t, 18.8, 100, None, None), 100
            )
        self.assertAlmostEqual(
            L.ac_lsd_difference_cap_nm(300, 10, 20, 0.5, 0.1), 10 * 0.5 * 300 + 20
        )
        self.assertAlmostEqual(
            L.ac_lsd_difference_cap_nm(-300, 10, 20, 0.5, 0.1), 10 * 0.1 * 300 + 20
        )
        with self.assertRaises(L.LabInputError):
            L.ac_lsd_difference_cap_nm(300, 10, 0, 1.0, 1.0)

    def test_low_grip_axle(self) -> None:
        # abierto (tope 0): el eje transmite 2·low
        self.assertAlmostEqual(
            L.max_axle_torque_with_low_grip_nm(2000, 100, 2000, 0), 200
        )
        self.assertAlmostEqual(
            L.max_axle_torque_with_low_grip_nm(2000, 100, 2000, 300), 500
        )
        self.assertAlmostEqual(
            L.max_axle_torque_with_low_grip_nm(400, 100, 2000, 3000), 400
        )
        self.assertAlmostEqual(
            L.max_axle_torque_with_low_grip_nm(5000, 100, 600, 3000), 700
        )
        with self.assertRaises(L.LabInputError):
            L.max_axle_torque_with_low_grip_nm(1, -1, 1, 1)

    def test_symmetric_grip_does_not_discriminate(self) -> None:
        # Con la misma adherencia en las dos ruedas el tope del diferencial no influye:
        # un experimento simétrico no distingue factor 1 de factor 2.
        caps = (0.0, 100.0, 1544.9, 3089.8)
        totals = {
            L.max_axle_torque_with_low_grip_nm(5779.6, 4000, 4000, c) for c in caps
        }
        self.assertEqual(totals, {5779.6})
        asym = [L.max_axle_torque_with_low_grip_nm(5779.6, 300, 4000, c) for c in caps]
        self.assertEqual(len(set(asym)), len(caps))


class TestClutchAndGearing(unittest.TestCase):
    def test_bng_clutch_default(self) -> None:
        expected = 1.25 * 307.803 + 10000 * 0.104578 * math.pi / 30
        self.assertAlmostEqual(
            L.bng_clutch_default_lock_torque_nm(307.803, 10000, 0.104578), expected
        )
        self.assertAlmostEqual(expected, 494.3, delta=0.05)  # decisión base del Ibiza

    def test_ac_clutch(self) -> None:
        self.assertAlmostEqual(L.ac_clutch_torque_nm(4.0, 1.0, 500.0), 250.0)  # 50 %
        self.assertAlmostEqual(L.ac_clutch_torque_nm(-4.0, 1.0, 500.0), -250.0)
        self.assertAlmostEqual(
            L.ac_clutch_torque_nm(4.0, 1.0, None), 225.0
        )  # 450 por defecto
        self.assertAlmostEqual(L.ac_clutch_torque_nm(0.0, 1.0, 500.0), 0.0)
        self.assertTrue(L.ac_clutch_is_rigid(1.0, 300.0, 290.0))
        self.assertFalse(L.ac_clutch_is_rigid(1.0, 300.0, 250.0))
        self.assertFalse(L.ac_clutch_is_rigid(0.99, 300.0, 300.0))
        self.assertTrue(L.ac_clutch_is_rigid(1.0, 0.0, 0.0))

    def test_wheel_torque(self) -> None:
        self.assertAlmostEqual(L.wheel_torque_nm(300, 3.14, 5.98), 300 * 3.14 * 5.98)
        bng = L.wheel_torque_nm(
            300, 3.14, 5.98, gearbox_friction_nm=3.15, input_rad_s=100
        )
        self.assertAlmostEqual(bng, (300 - 3.15) * 3.14 * 5.98)
        for bad in (None, 0.0, float("nan")):
            with self.assertRaises(L.LabInputError):
                L.wheel_torque_nm(300, bad, 5.98)
            with self.assertRaises(L.LabInputError):
                L.wheel_torque_nm(300, 3.14, bad)


if __name__ == "__main__":
    unittest.main()
