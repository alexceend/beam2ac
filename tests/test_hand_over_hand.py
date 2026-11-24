"""Pruebas del módulo experimental src.ac.hand_over_hand (ciclo de agarre y solver).

No validan el cruce de manos: el informe (§2.6) documenta que, con el esqueleto y la
pose disponibles, los antebrazos se atraviesan al cruzar.
"""

import math
import unittest

from src.ac.driver_pose import Knh
from src.ac.hand_over_hand import (
    AFFECTED,
    HandCycle,
    HohParams,
    hoh_clip,
    pose_locals,
    rim_angle,
    swivel_deg,
)
from src.ac.steer_anim import (
    ac_pose_at,
    clip_bones,
    clip_worlds,
    frame_to_matrix,
    max_error,
)
from tests.test_assembler_integration import _knh_bytes

CENTRE, AXIS = (0.0, 1.05, 0.40), (0.0, 0.0, 1.0)


class TestCycle(unittest.TestCase):
    def setUp(self):
        self.c = HandCycle(a_min=-40.0, grip=120.0, transfer=60.0, base=10.0)

    def test_base_pose_and_periodicity(self):
        a, lift, grip = self.c.state(0.0)
        self.assertEqual((a, lift, grip), (10.0, 0.0, True))
        for d in (-250.0, -33.3, 0.0, 47.0, 400.0):
            s1, s2 = self.c.state(d), self.c.state(d + self.c.period)
            self.assertAlmostEqual(s1[0], s2[0], places=9)
            self.assertAlmostEqual(s1[1], s2[1], places=9)

    def test_continuous_angle_lift_and_slope(self):
        step, prev = 0.01, None
        d = -200.0
        while d < 200.0:
            a, lift, _ = self.c.state(d)
            if prev is not None:
                self.assertLess(abs(a - prev[0]), 0.05)  # sin saltos
                self.assertLess(abs(lift - prev[1]), 0.01)
            prev = (a, lift)
            d += step
        # en los cambios agarre↔traslado: despegue 0 y pendiente = la del aro (C1)
        for edge in (70.0, 130.0):  # tau = grip (70 = 120 − 50) y fin del traslado
            e = 1e-6
            slope = (self.c.state(edge + e)[0] - self.c.state(edge - e)[0]) / (2 * e)
            self.assertAlmostEqual(slope, 1.0, places=3)
            self.assertLess(self.c.state(edge)[1], 1e-9)

    def test_swivel_bounds(self):
        spec = (60.0, 120.0, 30.0)
        self.assertEqual(swivel_deg(40.0, spec), 0.0)
        self.assertEqual(swivel_deg(130.0, spec), 30.0)
        self.assertAlmostEqual(swivel_deg(90.0, spec), 15.0)
        self.assertEqual(swivel_deg(90.0, (120.0, 60.0, -30.0)), -15.0)


class TestSolver(unittest.TestCase):
    def setUp(self):
        self.knh = Knh(_knh_bytes())
        w = self.knh.worlds()
        self.base = {
            s: rim_angle(w[f"DRIVER:RIG_HAND_{s}"][3][:3], CENTRE, AXIS) for s in "LR"
        }
        self.p = HohParams(
            # traslados: L en [30, 45) y R en [5, 20) (periodo 55): nunca a la vez
            HandCycle(self.base["L"] - 10, 40.0, 15.0, self.base["L"]),
            HandCycle(self.base["R"] - 35, 40.0, 15.0, self.base["R"]),
            lift_m=0.05,
        )

    def test_one_hand_always_grips(self):
        for i in range(0, 1000):
            d = i * self.p.period / 1000
            self.assertTrue(self.p.left.state(d)[2] or self.p.right.state(d)[2])

    def test_only_arm_and_clavicle_bones_change_and_grip_follows_rim(self):
        for d in (0.0, 4.0, -6.0, 13.0):
            local, info = pose_locals(self.knh, self.p, d, CENTRE, AXIS)
            for name in self.knh.nodes:
                if name not in AFFECTED:
                    self.assertLess(max_error(local[name], self.knh.local[name]), 1e-12)
            w = self.knh.worlds(local)
            for s, cyc in (("L", self.p.left), ("R", self.p.right)):
                if info[s]["grip"]:
                    got = rim_angle(w[f"DRIVER:RIG_HAND_{s}"][3][:3], CENTRE, AXIS)
                    diff = (got - cyc.state(d)[0] + 180.0) % 360.0 - 180.0  # módulo 360
                    self.assertAlmostEqual(diff, 0.0, places=4)
                self.assertGreater(info[s]["margin"], 0)
        local, _ = pose_locals(self.knh, self.p, 0.0, CENTRE, AXIS)
        w, w0 = self.knh.worlds(local), self.knh.worlds()
        for name in self.knh.nodes:  # a 0° la pose es la del .knh
            self.assertLess(max_error(w[name], w0[name]), 1e-6)


if __name__ == "__main__":
    unittest.main()


class TestAlternatingClip(unittest.TestCase):
    """Clip completo de agarre alterno (como v14ha) en el esqueleto sintético, leído con
    la correspondencia exacta de AC (fmodf + interpolación de fotogramas)."""

    @classmethod
    def setUpClass(cls):
        t = TestSolver()
        t.setUp()
        cls.knh, cls.p = t.knh, t.p
        cls.frames = 110
        cls.clip, cls.info = hoh_clip(cls.knh, cls.p, CENTRE, AXIS, frames=cls.frames)
        cls.lock = cls.p.period

    def _hands(self, deg):
        w = ac_pose_at(self.knh, self.clip, deg, self.lock)
        return {s: w[f"DRIVER:RIG_HAND_{s}"][3][:3] for s in "LR"}, w

    def test_continuous_through_wraps(self):
        """Ningún paso (tampoco al pasar de ±LOCK·k) supera claramente al mayor paso de
        un traslado normal; un salto de pose real sería de decenas de cm."""
        step, prev = 0.25, None
        at_wrap, interior = 0.0, 0.0
        d = -3 * self.lock
        while d <= 3 * self.lock:
            h, _ = self._hands(d)
            if prev is not None:
                j = max(math.dist(h[s], prev[s]) for s in "LR")
                near = min(abs(d - k * self.lock) for k in range(-3, 4)) <= 1.0
                if near:
                    at_wrap = max(at_wrap, j)
                else:
                    interior = max(interior, j)
            prev, d = h, d + step
        self.assertLess(max(at_wrap, interior), 0.02)
        self.assertLess(at_wrap, 1.3 * interior)

    def test_cycle_repeats(self):
        for d in (-50.0, -12.5, 3.0, 20.0, 41.0):
            a, _ = self._hands(d)
            b, _ = self._hands(d + self.lock)
            for s in "LR":
                self.assertLess(math.dist(a[s], b[s]), 0.003)

    def test_gripping_hand_follows_rim(self):
        d = -self.lock + 2.0
        while d < self.lock - 2.0:
            h, _ = self._hands(d)
            for s, cyc in (("L", self.p.left), ("R", self.p.right)):
                ang, _, grip = cyc.state(d)
                if grip:
                    got = rim_angle(h[s], CENTRE, AXIS)
                    self.assertLess(abs((got - ang + 180.0) % 360.0 - 180.0), 0.5)
            d += 1.0

    def test_centre_pose_and_untouched_bones(self):
        w0 = self.knh.worlds()
        mid = clip_worlds(self.knh, self.clip, self.frames // 2)
        for name in clip_bones(self.knh):
            self.assertLess(max_error(mid[name], w0[name]), 1e-6)
        for name, frames in self.clip:
            if name not in AFFECTED:
                for fr in frames[:: self.frames // 10]:
                    self.assertLess(
                        max_error(frame_to_matrix(fr), self.knh.local[name]), 1e-6
                    )
        self.assertGreater(self.info["reach_margin_m"], 0)
