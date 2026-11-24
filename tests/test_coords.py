import unittest

from src.coords import (
    BEAMNG_TO_AC,
    ac_to_beamng,
    beamng_tensor_to_ac,
    beamng_to_ac,
)


def _det(m):
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


class TestCoordinates(unittest.TestCase):
    def test_semantic_axes(self):
        # BeamNG: +X izquierda, -Y adelante, +Z arriba
        self.assertEqual(beamng_to_ac((1, 0, 0)), (1, 0, 0))  # izquierda -> +X
        self.assertEqual(beamng_to_ac((0, -1, 0)), (0, 0, 1))  # adelante -> +Z
        self.assertEqual(beamng_to_ac((0, 0, 1)), (0, 1, 0))  # arriba -> +Y

    def test_sample_evidence_front_left_wheel(self):
        # Rueda FL de la muestra BeamNG (0.7375, -1.2004, 0.6119): en AC debe
        # quedar a la izquierda (+X), delante (+Z) y arriba (+Y), como WHEEL_LF.
        x, y, z = beamng_to_ac((0.7375, -1.2004, 0.6119))
        self.assertGreater(x, 0)
        self.assertGreater(z, 0)
        self.assertGreater(y, 0)

    def test_round_trip(self):
        for v in [(0.1, -2.5, 3.0), (-1.0, 0.0, 0.25)]:
            self.assertEqual(ac_to_beamng(beamng_to_ac(v)), v)
            self.assertEqual(beamng_to_ac(ac_to_beamng(v)), v)

    def test_is_a_proper_rotation(self):
        self.assertEqual(_det(BEAMNG_TO_AC), 1)  # sin espejo
        for i in range(3):
            for j in range(3):
                dot = sum(BEAMNG_TO_AC[i][k] * BEAMNG_TO_AC[j][k] for k in range(3))
                self.assertEqual(dot, 1 if i == j else 0)

    def test_matrix_matches_function(self):
        v = (0.3, -0.7, 1.9)
        by_matrix = tuple(sum(r[k] * v[k] for k in range(3)) for r in BEAMNG_TO_AC)
        self.assertEqual(by_matrix, beamng_to_ac(v))

    def test_tensor_transform_permutes_diagonal(self):
        # Ixx (lateral) se queda; Iyy BeamNG (longitudinal, roll) pasa a Izz AC;
        # Izz BeamNG (vertical, yaw) pasa a Iyy AC.
        t = beamng_tensor_to_ac(((1, 0, 0), (0, 2, 0), (0, 0, 3)))
        self.assertEqual(t, ((1, 0, 0), (0, 3, 0), (0, 0, 2)))

    def test_tensor_off_diagonal_sign(self):
        # Producto de inercia xy en BeamNG -> -xz en AC (porque z_AC = -y_B).
        t = beamng_tensor_to_ac(((0, 5, 0), (5, 0, 0), (0, 0, 0)))
        self.assertEqual(t[0][2], -5)
        self.assertEqual(t[2][0], -5)


if __name__ == "__main__":
    unittest.main()
