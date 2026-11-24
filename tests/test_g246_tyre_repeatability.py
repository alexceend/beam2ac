"""G2.46: repetibilidad del banco de neumáticos.

Con las sesiones reales de G2.45 (composed_fox inédito, s1/s2) y la s3 de G2.46: la
diferencia de μ entre sesiones es ≈ 0,002 en las condiciones estables y mucho mayor SOLO
en las que el neumático oscila (ciclo límite). Una métrica que mezcle ambas (0,04) no es
la precisión del banco."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from src.bench import tyre_runner as tr

ROOT = Path(__file__).resolve().parents[1]
S12 = ROOT / "output/g245_2026-11-14/banco_inedito"
S3 = ROOT / "output/g246_2026-11-14/banco_repe"
HAVE = all(
    (p / f"userpath_{s}/current/beam2ac_tyre/composed_fox_{s}.csv").is_file()
    for p, s in ((S12, "s1"), (S12, "s2"), (S3, "s3"))
)


def _sess():
    return {
        s: tr.reduce_session(w / f"userpath_{s}" / "current", "composed_fox", s)
        for s, w in (("s1", S12), ("s2", S12), ("s3", S3))
    }


class TestOscillationIndex(unittest.TestCase):
    def _samples(self, amp: float, n: int = 400):
        import math

        return [
            SimpleNamespace(
                seg_name="lat_x",
                wheel="FL",
                fy=1000 + amp * math.sin(i * 1.7),
                fx=0.0,
                fz=2000.0,
            )
            for i in range(n)
        ]

    def test_quiet_vs_oscillating(self) -> None:
        quiet = tr.oscillation_index(self._samples(10.0))["lat_x"]["FL"]
        loud = tr.oscillation_index(self._samples(600.0))["lat_x"]["FL"]
        self.assertLess(quiet, tr.OSC_LIMIT)
        self.assertGreater(loud, tr.OSC_LIMIT)
        # ≈ amp/√2/Fz
        self.assertAlmostEqual(loud, 600 / 2**0.5 / 2000, delta=0.03)

    def test_short_segments_are_skipped(self) -> None:
        self.assertEqual(tr.oscillation_index(self._samples(10.0, n=50)), {})


@unittest.skipUnless(HAVE, "faltan las sesiones guardadas de G2.45/G2.46")
class TestRepeatability(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sess = _sess()

    def test_unstable_conditions_are_the_ones_found_by_experiment(self) -> None:
        u = tr.unstable_conditions(*self.sess.values())
        self.assertIn("lat_km", u["front"])
        self.assertIn("lat_v30", u["rear"])
        for axle, conds in u.items():  # las condiciones de referencia nunca
            self.assertNotIn("lat_base", conds)
            self.assertNotIn("lat_rep", conds)

    def test_stable_precision_is_a_few_thousandths_in_every_pair(self) -> None:
        for a, b in (("s1", "s2"), ("s1", "s3"), ("s2", "s3")):
            for axle, d in tr.repeatability(self.sess[a], self.sess[b]).items():
                with self.subTest(pair=(a, b), axle=axle):
                    self.assertLess(d["mu_rms_diff_stable"], 0.004)
                    self.assertLess(d["mu_median_abs_diff_stable"], 0.002)
                    # el escalar de siempre queda MUY por encima: es la mezcla
                    self.assertGreater(d["mu_rms_diff"], 2 * d["mu_rms_diff_stable"])
                    # base y repetición de la misma sesión: misma precisión
                    c = d["mu_rms_diff_by_condition"]
                    self.assertLess(c["lat_base"], 0.004)
                    self.assertLess(c["lon_base"], 0.005)

    def test_scalars_are_repeatable(self) -> None:
        for axle, d in tr.repeatability(self.sess["s1"], self.sess["s3"]).items():
            self.assertAlmostEqual(d["reference_load"], 0.0, delta=1e-3)
            self.assertAlmostEqual(d["free_rolling_radius"], 0.0, delta=1e-4)
            # s3 corrió con la CPU cargada: 0,7 % (s1-s2) y 2,4 % (s1-s3)
            self.assertAlmostEqual(d["vertical_stiffness_static"], 0.0, delta=0.03)

    def test_fit_level_function_shape(self) -> None:
        # solo la forma (el ajuste completo tarda ≈ 45 s por eje y sesión)
        self.assertEqual(set(tr.FIT_WEAK_KEYS), {"brake_dx_mod", "falloff_level"})


if __name__ == "__main__":
    unittest.main()
