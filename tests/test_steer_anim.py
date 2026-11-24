"""Pruebas de src.ac.steer_anim: formato .ksanim, clip estático y clip de dirección."""

import math
import random
import unittest

from src.ac.driver_pose import FINGERS, Knh
from src.ac.steer_anim import (
    SteerAnimError,
    _axis_rotation,
    ac_clip_pos,
    ac_frame_angle,
    ac_frame_blend,
    ac_pose_at,
    saturating_follow,
    clip_angle,
    clip_bones,
    clip_worlds,
    frame_to_matrix,
    matrix_to_frame,
    max_error,
    read_ksanim,
    rim_angle_of,
    static_clip,
    steer_clip,
    write_ksanim,
)
from tests.test_assembler_integration import _knh_bytes

CENTRE, AXIS = (0.0, 1.05, 0.40), (0.0, 0.0, 1.0)


class TestFormat(unittest.TestCase):
    def test_matrix_frame_roundtrip(self):
        rnd = random.Random(7)
        for _ in range(50):
            axis = [rnd.uniform(-1, 1) for _ in range(3)]
            q = _axis_rotation(axis, rnd.uniform(-math.pi, math.pi))
            s = [rnd.uniform(0.5, 2) for _ in range(3)]
            m = [[q[i][j] * s[i] for j in range(3)] + [0.0] for i in range(3)]
            m.append([rnd.uniform(-1, 1) for _ in range(3)] + [1.0])
            self.assertLess(max_error(frame_to_matrix(matrix_to_frame(m)), m), 1e-9)

    def test_write_read_roundtrip_and_errors(self):
        clip = [
            ("A", [(0, 0, 0, 1, 1, 2, 3, 1, 1, 1)] * 3),
            ("B", [(0,) * 9 + (1,)] * 3),
        ]
        data = write_ksanim(clip)
        ver, bones = read_ksanim(data)
        self.assertEqual(ver, 2)
        self.assertEqual([b for b, _ in bones], ["A", "B"])
        self.assertEqual(
            bones[0][1][0], (0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 1.0, 1.0, 1.0)
        )
        with self.assertRaises(SteerAnimError):
            read_ksanim(data + b"x")
        with self.assertRaises(SteerAnimError):
            write_ksanim([("A", [(0,) * 10] * 2), ("B", [(0,) * 10] * 3)])

    def test_clip_angle_matches_ac_mapping(self):
        # pos = (1 − deg/LOCK)/2: primer fotograma +LOCK, último −LOCK
        self.assertEqual(clip_angle(0, 100, 75), 75)
        self.assertEqual(clip_angle(99, 100, 75), -75)
        self.assertAlmostEqual(clip_angle(49, 100, 75) + clip_angle(50, 100, 75), 0)


class TestClips(unittest.TestCase):
    def setUp(self):
        self.knh = Knh(_knh_bytes())

    def test_static_clip_reproduces_pose(self):
        clip = static_clip(self.knh)
        self.assertEqual([b for b, _ in clip], clip_bones(self.knh))
        _, back = read_ksanim(write_ksanim(clip))
        for name, frames in back:
            self.assertEqual(len(frames), 100)
            for f in (0, 50, 99):
                self.assertLess(
                    max_error(frame_to_matrix(frames[f]), self.knh.local[name]), 1e-6
                )

    def test_steer_clip_rotates_only_arms_by_signed_angle(self):
        w0 = self.knh.worlds()
        arm_bones = {
            f"DRIVER:RIG_{b}_{s}" for s in FINGERS for b in ("Arm", "ForeArm", "HAND")
        }
        for sign in (1, -1):
            clip, info = steer_clip(self.knh, CENTRE, AXIS, 20.0, sign, frames=11)
            self.assertGreater(info["reach_margin_m"], 0)
            for f in (0, 5, 10):
                w = clip_worlds(self.knh, clip, f)
                deg = clip_angle(f, 11, 20.0)
                for side, fingers in FINGERS.items():
                    hand = f"DRIVER:RIG_HAND_{side}"
                    got = rim_angle_of(w[hand][3][:3], w0[hand][3][:3], CENTRE, AXIS)
                    self.assertAlmostEqual(got, sign * deg, places=4)
                    arm = f"DRIVER:RIG_Arm_{side}"
                    self.assertLess(math.dist(w[arm][3][:3], w0[arm][3][:3]), 1e-6)
                    fore = f"DRIVER:RIG_ForeArm_{side}"
                    self.assertAlmostEqual(
                        math.dist(w[fore][3][:3], w[arm][3][:3]),
                        math.dist(w0[fore][3][:3], w0[arm][3][:3]),
                        places=5,
                    )
                for name, frames in clip:
                    if name not in arm_bones:
                        self.assertLess(
                            max_error(frame_to_matrix(frames[f]), self.knh.local[name]),
                            1e-6,
                        )

    def test_unreachable_angle_is_refused(self):
        with self.assertRaises(SteerAnimError):
            steer_clip(self.knh, CENTRE, AXIS, 179.0, 1, frames=11)


if __name__ == "__main__":
    unittest.main()


class TestContinuityV14(unittest.TestCase):
    """Salto de v14hp a ±LOCK (fmodf de AC) y clip continuo con saturación."""

    def setUp(self):
        self.knh = Knh(_knh_bytes())
        self.w0 = self.knh.worlds()

    def _hand_angle(self, clip, deg, lock, side="L"):
        hand = f"DRIVER:RIG_HAND_{side}"
        w = ac_pose_at(self.knh, clip, deg, lock)
        return rim_angle_of(w[hand][3][:3], self.w0[hand][3][:3], CENTRE, AXIS)

    def test_ac_mapping_edges(self):
        self.assertEqual(ac_clip_pos(0, 75), 0.5)
        self.assertEqual(ac_clip_pos(75, 75), 0.0)
        self.assertAlmostEqual(
            ac_clip_pos(75.75, 75), 0.495, places=9
        )  # fmodf: vuelve al centro
        self.assertAlmostEqual(ac_clip_pos(-75.75, 75), 0.505, places=9)
        self.assertEqual(ac_frame_blend(0.5, 600), (300, 301, 0.0))
        self.assertEqual(ac_frame_blend(1.0, 600), (599, 599, 0.0))
        self.assertEqual(ac_frame_blend(0.0, 600), (0, 1, 0.0))
        self.assertEqual(ac_frame_angle(300, 600, 450), 0.0)

    def test_old_clip_jumps_at_lock(self):
        lock = 20.0
        clip, _ = steer_clip(self.knh, CENTRE, AXIS, lock, 1, frames=100)
        before = self._hand_angle(clip, lock - 0.05, lock)
        after = self._hand_angle(clip, lock + 0.05, lock)
        self.assertGreater(
            abs(before - after), 0.9 * lock
        )  # la causa del salto de v14hp

    def test_saturating_follow_is_c1_and_bounded(self):
        fn = saturating_follow(10.0, 15.0)
        self.assertEqual(fn(7.0), 7.0)
        self.assertEqual(fn(-7.0), -7.0)
        eps = 1e-6
        self.assertAlmostEqual((fn(10 + eps) - fn(10 - eps)) / (2 * eps), 1.0, places=4)
        values = [fn(d / 10) for d in range(-5000, 5001)]
        self.assertTrue(all(b >= a for a, b in zip(values, values[1:])))
        self.assertLessEqual(max(abs(v) for v in values), 15.0)
        with self.assertRaises(SteerAnimError):
            saturating_follow(15.0, 10.0)

    def test_new_clip_continuous_and_consistent(self):
        lock, frames, fn = 100.0, 200, saturating_follow(10.0, 15.0)
        clip, info = steer_clip(
            self.knh,
            CENTRE,
            AXIS,
            lock,
            1,
            frames=frames,
            angle_fn=ac_frame_angle,
            hand_fn=fn,
        )
        self.assertGreater(info["reach_margin_m"], 0)
        prev = None
        d = -lock
        while d <= lock + 1e-9:
            for side in FINGERS:
                a = self._hand_angle(clip, d, lock, side)
                self.assertAlmostEqual(a, fn(d), delta=0.05)
                if abs(d) <= 10:
                    self.assertAlmostEqual(a, d, delta=0.05)  # sigue al aro
            if prev is not None:
                self.assertLessEqual(
                    abs(a - prev), 0.25 + 1e-3
                )  # nunca más que el volante
            prev = a
            d += 0.25
        mid = clip_worlds(self.knh, clip, frames // 2)  # fotograma central = 0° exacto
        for name in clip_bones(self.knh):
            self.assertLess(max_error(mid[name], self.w0[name]), 1e-6)
        arm_bones = {
            f"DRIVER:RIG_{b}_{s}" for s in FINGERS for b in ("Arm", "ForeArm", "HAND")
        }
        for name, fr in clip:
            if name not in arm_bones:
                for f in (0, 57, frames - 1):
                    self.assertLess(
                        max_error(frame_to_matrix(fr[f]), self.knh.local[name]), 1e-6
                    )
