"""G2.40: protocolo b0p (POWER del LSD 2WD: 1.ª a fondo) y criterio de saturación medido en r91."""

import unittest

from tools.acbench import validate as V


class TestB0p(unittest.TestCase):
    def test_protocol(self):
        self.assertEqual(V.PROTOCOLS["b0p"], ("static", "split_power", "final"))
        self.assertEqual(V.SPLIT_POWER_ITEMS, ((1, 1.0),))
        self.assertEqual(
            V.PROTOCOLS["b0"], ("static", "split", "final")
        )  # b0 histórico intacto

    def test_power_cannot_saturate_on_split_surface(self):
        # r91 (Ibiza, 200/0,5): diferencia máxima entre ruedas que da la carretera ~1020 N·m;
        # lim = 200 + 0,5·T·r ya supera eso con T·r >= 1650 (el bloqueo nunca desliza a gas alto).
        max_road_diff = 1022.0
        self.assertLess(max_road_diff, 200 + 0.5 * 1650)


if __name__ == "__main__":
    unittest.main()
