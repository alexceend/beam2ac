"""Opciones de ABS/TC de ``electronics.ini`` (src/ac/driver_aids.py, 2026-11-21)."""

import unittest

from src.ac.driver_aids import (
    AidSpec,
    DriverAidsError,
    apply_driver_aids,
    from_options,
    level_values,
)
from src.project.resolved_ini import parse_lut, table_problems

SRC = "; cab\n\n[ABS]\nPRESENT=1\nACTIVE=1\nSLIP_RATIO_LIMIT=0.2029\nRATE_HZ=50\n"
NO_ABS = "; cab\n\n[ABS]\nPRESENT=0\nACTIVE=0\n"


def _kv(text: str, sec: str) -> dict[str, str]:
    from src.ac.driver_aids import _parse_ini

    return _parse_ini(text)[1][sec]


class TestDriverAids(unittest.TestCase):
    def test_default_changes_nothing(self) -> None:
        self.assertEqual(apply_driver_aids(SRC, AidSpec(), AidSpec()), (None, {}, []))

    def test_levels_curve_is_valid_and_limit_repeats_a_curve_value(self) -> None:
        text, luts, _ = apply_driver_aids(SRC, AidSpec("auto", 8), AidSpec())
        a = _kv(text, "ABS")
        rows = parse_lut(luts["abs_control.lut"])
        self.assertEqual(len(rows), 8)
        self.assertEqual(table_problems([list(r) for r in rows]), [])
        # AC busca el nivel inicial por igualdad exacta de flotantes
        self.assertIn(float(a["SLIP_RATIO_LIMIT"]), [r[1] for r in rows])
        self.assertEqual(a["CURVE"], "abs_control.lut")
        self.assertEqual(a["RATE_HZ"], "50")  # lo del origen no se pisa

    def test_off_and_on(self) -> None:
        text, luts, _ = apply_driver_aids(SRC, AidSpec("off"), AidSpec("off"))
        self.assertEqual(_kv(text, "ABS")["PRESENT"], "0")
        self.assertEqual(_kv(text, "TRACTION_CONTROL")["ACTIVE"], "0")
        self.assertEqual(luts, {})
        text, _, notes = apply_driver_aids(NO_ABS, AidSpec("on"), AidSpec("on", 5))
        self.assertEqual(_kv(text, "ABS")["PRESENT"], "1")
        tc = _kv(text, "TRACTION_CONTROL")
        self.assertEqual((tc["PRESENT"], tc["MIN_SPEED_KMH"]), ("1", "40"))
        self.assertTrue(any("plantilla" in n for n in notes))

    def test_levels_without_source_aid_are_not_invented(self) -> None:
        text, luts, notes = apply_driver_aids(NO_ABS, AidSpec("auto", 5), AidSpec())
        self.assertIsNone(text)
        self.assertEqual(luts, {})
        self.assertTrue(notes)

    def test_invalid(self) -> None:
        for spec in (AidSpec("x"), AidSpec("on", 1), AidSpec("on", 21)):
            with self.assertRaises(DriverAidsError):
                apply_driver_aids(SRC, spec, AidSpec())
        self.assertEqual(from_options(None), (AidSpec(), AidSpec()))

    def test_level_values_monotonic(self) -> None:
        v = [float(x) for x in level_values(0.08, 0.2, 10)]
        self.assertEqual(v, sorted(set(v)))


if __name__ == "__main__":
    unittest.main()


class TestCheckerRegressions(unittest.TestCase):
    def test_no_file_keeps_the_other_aid_present(self) -> None:
        text, _, notes = apply_driver_aids(None, AidSpec("off"), AidSpec())
        self.assertEqual(_kv(text, "ABS")["PRESENT"], "0")
        self.assertEqual(_kv(text, "TRACTION_CONTROL")["PRESENT"], "1")
        self.assertTrue(notes)

    def test_present_is_numeric_like_getint(self) -> None:
        src = "[ABS]\nSLIP_RATIO_LIMIT=0.2\nPRESENT=0.0\nACTIVE=1\n"
        self.assertEqual(apply_driver_aids(src, AidSpec("auto", 4), AidSpec())[0], None)

    def test_bad_slip_is_a_driver_aids_error(self) -> None:
        for bad in ("abc", "", "nan"):
            src = f"[ABS]\nPRESENT=1\nACTIVE=1\nSLIP_RATIO_LIMIT={bad}\n"
            with self.assertRaises(DriverAidsError):
                apply_driver_aids(src, AidSpec("auto", 4), AidSpec())
        with self.assertRaises(DriverAidsError):
            from_options({"abs_levels": "x"})
        # un comentario en línea no rompe el valor
        src = "[ABS]\nPRESENT=1\nACTIVE=1\nSLIP_RATIO_LIMIT=0.12 ; x\n"
        self.assertIsNotNone(apply_driver_aids(src, AidSpec("auto", 4), AidSpec())[0])
