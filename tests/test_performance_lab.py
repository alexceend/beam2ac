"""Modelo de prestaciones (src/lab/performance.py): caso analítico, clasificación y controles de plausibilidad."""

import unittest
from pathlib import Path

from src.lab.performance import (
    PerformanceInputError,
    Spec,
    compare_with_reference,
    read_spec,
    simulate,
)

ROOT = Path(__file__).resolve().parents[1]


def flat_spec(**kw) -> Spec:
    base = dict(
        mass_kg=1000.0,
        torque_curve=[(0, 200.0), (10000, 200.0)],
        gears=[2.0],
        final=4.0,
        limiter_rpm=9000,
        idle_rpm=800,
        wheel_radius_m=0.3,
        engine_inertia=0.0,
        gearbox_inertia=0.0,
        wheel_inertia_sum=0.0,
    )
    base.update(kw)
    return Spec(**base)


class TestSimulate(unittest.TestCase):
    def test_analytic_constant_force(self):
        # una marcha, par constante, sin inercias ni resistencias: a = K·T·G/(r·M) ⇒ t = v/a
        s = flat_spec()
        a = 0.9797 * 200 * 8 / 0.3 / 1000
        t = simulate(s, targets_kmh=(60,)).marks[60]
        self.assertAlmostEqual(
            t, (60 / 3.6 - 0.5) / a, delta=0.01
        )  # arranque a 0,5 m/s

    def test_inertia_and_drag_only_slow_it_down(self):
        base = simulate(flat_spec(), targets_kmh=(60,)).marks[60]
        self.assertGreater(
            simulate(flat_spec(wheel_inertia_sum=10.0), targets_kmh=(60,)).marks[60],
            base,
        )
        self.assertGreater(
            simulate(flat_spec(), targets_kmh=(60,), cda=0.7, crr=0.01).marks[60], base
        )

    def test_grip_cap_is_never_faster_than_power_limit(self):
        s = flat_spec(driven_load_fraction=0.5)
        p, g = (
            simulate(s, targets_kmh=(60,)).marks[60],
            simulate(s, targets_kmh=(60,), mu=0.5).marks[60],
        )
        self.assertGreaterEqual(g, p)
        self.assertAlmostEqual(g, (60 / 3.6 - 0.5) / (0.5 * 9.81 * 0.5), delta=0.01)

    def test_unreachable_target_is_missing_not_invented(self):
        # límite 3000 rpm: v máx = 3000·π/30·0,3/8 = 11,8 m/s (42 km/h) < 60 km/h
        self.assertNotIn(
            60, simulate(flat_spec(limiter_rpm=3000), targets_kmh=(60,)).marks
        )


class TestCompare(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(compare_with_reference(6.0, 4.0)["veredicto"], "inconsistente")
        self.assertEqual(compare_with_reference(6.0, 6.3)["veredicto"], "coherente")
        self.assertEqual(
            compare_with_reference(6.0, 9.0)["veredicto"], "mas_lento_que_potencia"
        )
        self.assertTrue(
            compare_with_reference(6.0, 9.0, grip_s=9.2)["coincide_con_agarre"]
        )


class TestRealPackage(unittest.TestCase):
    def test_exported_cars_are_plausible(self):
        found = 0
        for d in sorted((ROOT / "builds").glob("*/*/data")):
            try:
                r = simulate(read_spec(d))
            except (
                PerformanceInputError
            ):  # build antiguo sin alguna clave (p. ej. el Ibiza sin [GEARBOX] INERTIA)
                continue
            found += 1
            if 100 in r.marks:
                # cota 30 s: coches pequeños y lentos (p. ej. turbot_aster, 626 kg, 3 marchas, ~80 N·m) son legítimos
                self.assertTrue(1.5 < r.marks[100] < 30, (d, r.marks))
        if not found:
            self.skipTest("sin builds")


if __name__ == "__main__":
    unittest.main()


class TestExportIntegration(unittest.TestCase):
    def test_declared_accel_sentinel_and_issues(self):
        import json
        import tempfile

        from src.project.export import _declared_accel, performance_issues

        with tempfile.TemporaryDirectory() as t:

            def rep(v):
                (Path(t) / "report.json").write_text(
                    json.dumps({"facts": {"ir.declared.accel_0_100": {"value": v}}})
                )
                return _declared_accel(Path(t))

            self.assertEqual(rep(5.5), 5.5)
            self.assertIsNone(rep(999.0))  # centinela de Automation
            self.assertIsNone(_declared_accel(None))
            self.assertEqual(
                performance_issues(Path(t), None)[0].code, "prestaciones.no_evaluable"
            )


class TestIbizaReference(unittest.TestCase):
    DATA = (
        ROOT
        / "output/g239_2026-11-13/ac/prep_split/content/cars/beam2ac_g239val_seat_ibiza/data"
    )

    def test_mass_matches_ac_measurement_and_ficha(self):
        if not self.DATA.is_dir():
            self.skipTest("sin la evidencia de G2.39")
        s = read_spec(self.DATA)
        self.assertAlmostEqual(
            s.mass_kg, 1336.9, delta=0.5
        )  # masa medida en AC (G2.29 ideal.json)
        t = simulate(s).marks[100]
        self.assertEqual(
            compare_with_reference(t, 5.5)["veredicto"], "coherente"
        )  # ficha de Automation 5,5 s


class TestRobustness(unittest.TestCase):
    def test_broken_packages_only_warn(self):
        import tempfile

        from src.project.export import performance_issues

        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "data").mkdir()
            self.assertEqual(
                performance_issues(Path(t), 5.5)[0].severity, "warning"
            )  # sin INI
            (Path(t) / "data" / "power.lut").write_text("rpm|torque\n")
            self.assertEqual(
                performance_issues(Path(t), None)[0].code, "prestaciones.no_evaluable"
            )


class TestLaunch(unittest.TestCase):
    """Quail (G2.41): 7,27 s a ralentí vs 5,7 s de ficha vs 6,37 s en AC; el 0-100 depende del régimen de salida."""

    TURBO = [(0, 40.0), (5000, 100.0), (5400, 600.0), (12000, 600.0)]

    def test_launch_slip_is_faster_and_idle_unchanged(self):
        s = flat_spec(
            torque_curve=self.TURBO,
            idle_rpm=1200,
            limiter_rpm=12000,
            gears=[1.8],
            final=5.0,
        )
        idle, best = simulate(s, targets_kmh=(60,)), simulate(
            s, targets_kmh=(60,), launch_slip=True
        )
        self.assertLess(best.marks[60], idle.marks[60] * 0.5)
        self.assertEqual(
            simulate(s, targets_kmh=(60,), launch_slip=False).marks, idle.marks
        )

    def test_sheet_between_idle_and_best_launch_is_plausible(self):
        self.assertEqual(
            compare_with_reference(7.3, 5.7, best_launch_s=2.2)["veredicto"],
            "coherente",
        )
        self.assertEqual(
            compare_with_reference(7.3, 1.5, best_launch_s=2.2)["veredicto"],
            "inconsistente",
        )
