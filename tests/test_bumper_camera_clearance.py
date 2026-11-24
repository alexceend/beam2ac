"""Cámara Bumper en coches altos: sube lo justo en vez de fallar (2026-11-17, bisexual_aspiration)."""

from __future__ import annotations

import unittest

from src.ac import cameras as C


def slab(z0: float, z1: float, y: float) -> list[C.Tri]:
    a, b, c, d = (-1.0, y, z0), (1.0, y, z0), (1.0, y, z1), (-1.0, y, z1)
    return [(a, b, c), (a, c, d)]


class TestBumperClearance(unittest.TestCase):
    def test_high_hood_raises_camera(self) -> None:
        # capó plano a 1,2 m (más alto que BUMPER_Y_BODY = 0,575): antes CameraError
        tris = slab(-1.0, 0.8, 0.8) + slab(0.8, 2.5, 1.2)
        cams = C.compute_cameras(2.5, tris, (0.3, 1.4, 0.0), (0.0, 0.0, 0.0))
        self.assertGreaterEqual(cams.bumper_min_clearance, C.BUMPER_MIN_CLEARANCE_M)
        self.assertGreater(cams.bumper_body[1], C.BUMPER_Y_BODY)
        self.assertAlmostEqual(cams.bumper_kn5[2], 2.5 + C.BUMPER_Z_MINUS_FRONT_M, 3)

    def test_low_car_keeps_kunos_height(self) -> None:
        tris = slab(-1.0, 0.8, 0.3) + slab(0.8, 2.5, 0.4)
        cams = C.compute_cameras(2.5, tris, (0.3, 0.9, 0.0), (0.0, 0.0, 0.0))
        self.assertEqual(cams.bumper_body[1], C.BUMPER_Y_BODY)


if __name__ == "__main__":
    unittest.main()
