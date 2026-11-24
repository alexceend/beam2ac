"""Turbo ajustable (curva atmosférica + [TURBO_0]) y reglajes de boxes (reparto de frenada, presiones)."""

from __future__ import annotations

import math
import unittest

from src.ac import adjustable_turbo as AT
from src.ac import setup_items as SI
from src.ac.tyre_compounds import parse_ini

POWER = "0|0\n2000|100\n4000|200\n6000|240\n"
ENGINE = "[HEADER]\nVERSION=1\n\n[ENGINE_DATA]\nLIMITER=7000\nMINIMUM=1000\n"
FACTS = {
    "ir.engine.boost_pressure_curve": {
        "value": [[0, 1.0], [2000, 1.0], [4000, 1.5], [6000, 2.0]]
    },
    "ir.engine.boost_max": {"value": 1.0},
    "ir.engine.boost_full_rpm": {"value": 6000.0},
    "ir.engine.boost_kind": {"value": "supercharger"},
}
TYRES = "[FRONT]\nPRESSURE_STATIC=26.5\n[REAR]\nPRESSURE_STATIC=23\n"
BRAKES = "[DATA]\nFRONT_SHARE=0.6\n"


class TestAdjustableTurbo(unittest.TestCase):
    def test_na_curve_and_turbo_section(self) -> None:
        lut, ini, notes = AT.apply(POWER, ENGINE, FACTS)
        na = dict(AT.parse_lut(lut))
        # par neto de BeamNG = curva atmosférica × presión absoluta del mapa (boost = presión − 1)
        self.assertAlmostEqual(na[4000] * 1.5, 200, delta=0.1)
        self.assertAlmostEqual(na[6000] * 2.0, 240, delta=0.1)
        self.assertEqual(na[2000], 100.0)
        s = parse_ini(ini)
        self.assertEqual(s["ENGINE_DATA"]["DEFAULT_TURBO_ADJUSTMENT"], "1")
        t = s["TURBO_0"]
        # compresor: sin lag (LAG=0 es instantáneo en motor de AC); ajustable: WASTEGATE = MAX_BOOST
        self.assertEqual(
            (t["LAG_UP"], t["LAG_DN"], t["COCKPIT_ADJUSTABLE"]), ("0", "0", "1")
        )
        self.assertEqual(t["WASTEGATE"], t["MAX_BOOST"])
        self.assertTrue(notes)

    def test_turbo_has_lag_supercharger_does_not(self) -> None:
        for kind, lag in (
            ("turbo", "0.9888"),
            ("both", "0.9888"),
            ("supercharger", "0"),
        ):
            facts = {**FACTS, "ir.engine.boost_kind": {"value": kind}}
            for adjustable in (True, False):  # el lag no depende de la casilla
                ini = AT.apply(POWER, ENGINE, facts, adjustable)[1]
                t = parse_ini(ini)["TURBO_0"]
                self.assertEqual(
                    (t["LAG_UP"], t["LAG_DN"]), (lag, lag), (kind, adjustable)
                )
                self.assertEqual(t["COCKPIT_ADJUSTABLE"], "1" if adjustable else "0")
                self.assertEqual(t["WASTEGATE"] == "0", not adjustable)
                self.assertEqual(
                    "DEFAULT_TURBO_ADJUSTMENT" in parse_ini(ini)["ENGINE_DATA"],
                    adjustable,
                )

    def test_lag_matches_acs_formula(self) -> None:
        """motor de AC: tasa = (1−LAG)·444,44; el turbo de AC: k = dt·tasa; τ = 0,2 s de BeamNG (rate 5)."""
        k = 0.003 * (1 - AT.TURBO_LAG) * 1.3333334 * 333.33334
        self.assertAlmostEqual(
            -0.003 / math.log(1 - k), 1 / AT.BNG_PRESSURE_RATE, places=6
        )

    def test_default_path_refuses_a_bad_fit(self) -> None:
        with self.assertRaises(AT.AdjustableTurboError):
            AT.apply(POWER, ENGINE, FACTS, False, max_err=1e-9)

    def test_fit_ignores_limiter_drop_at_top_rpm(self) -> None:
        pressure = [[r, min(1 + r / 2000, 3.0)] for r in range(0, 6001, 200)] + [
            [6200, 2.0]
        ]
        ramps, err = AT.fit_ramps(pressure)
        self.assertLess(err, 0.03)

    def test_fit_follows_s_shaped_boost(self) -> None:
        """Mapa del ranunculus (boost que entra a 2000-3000 rpm): el par de AC no se aparta más del 3 %."""
        pressure = [
            [0, 1.0],
            [700, 1.04],
            [1400, 1.09],
            [2100, 1.23],
            [2800, 1.79],
            [3500, 2.03],
            [6900, 2.03],
        ]
        ramps, err = AT.fit_ramps(pressure)
        self.assertLess(err, 0.03)
        self.assertLessEqual(len(ramps), 2)
        self.assertAlmostEqual(sum(m for m, _, _ in ramps), 1.03, delta=0.05)
        # el error informado es el real
        self.assertAlmostEqual(err, AT._max_err(pressure, ramps))

    def test_refuses_without_boost_or_with_existing_turbo(self) -> None:
        with self.assertRaises(AT.AdjustableTurboError):
            AT.apply(POWER, ENGINE, {})
        with self.assertRaises(AT.AdjustableTurboError):
            AT.apply(POWER, ENGINE + "\n[TURBO_0]\nMAX_BOOST=1\n", FACTS)


class TestExportTurboDefault(unittest.TestCase):
    """El lag del turbo no depende de la casilla «turbo ajustable»; el compresor solo la sigue."""

    def _run(self, kind: str, adjustable: bool):
        import json
        import tempfile
        from pathlib import Path

        from src.project.export import _apply_adjustable_turbo

        with tempfile.TemporaryDirectory() as d:
            facts = {**FACTS, "ir.engine.boost_kind": {"value": kind}}
            (Path(d) / "report.json").write_text(
                json.dumps({"facts": facts}), encoding="utf-8"
            )
            files: list = []
            texts = {"power.lut": POWER, "engine.ini": ENGINE}
            issues: list = []
            _apply_adjustable_turbo(
                {"adjustable_turbo": adjustable}, files, texts, issues, Path(d), {}
            )
        return texts, issues

    def test_turbo_gets_lag_without_the_checkbox(self) -> None:
        texts, issues = self._run("turbo", False)
        t = parse_ini(texts["engine.ini"])["TURBO_0"]
        self.assertEqual((t["LAG_UP"], t["COCKPIT_ADJUSTABLE"]), ("0.9888", "0"))
        self.assertNotEqual(texts["power.lut"], POWER)  # sin boost
        self.assertFalse([i for i in issues if i.severity == "error"])

    def test_supercharger_unchanged_without_the_checkbox(self) -> None:
        texts, _ = self._run("supercharger", False)
        self.assertEqual((texts["power.lut"], texts["engine.ini"]), (POWER, ENGINE))
        texts, _ = self._run("supercharger", True)
        self.assertEqual(parse_ini(texts["engine.ini"])["TURBO_0"]["LAG_UP"], "0")


class TestSetupItems(unittest.TestCase):
    def test_pressure_and_bias_sections(self) -> None:
        text, notes = SI.apply(
            "[DISPLAY_METHOD]\nSHOW_CLICKS=0\n",
            TYRES,
            BRAKES,
            brake_bias=True,
            tyre_pressure=True,
        )
        s = parse_ini(text)
        self.assertEqual(
            (s["PRESSURE_LF"]["MIN"], s["PRESSURE_LF"]["MAX"]), ("18", "42")
        )  # 26,5 → ⌊⌋−8, ⌈⌉+15
        self.assertEqual(
            (s["PRESSURE_RR"]["MIN"], s["PRESSURE_RR"]["MAX"]), ("15", "38")
        )
        self.assertEqual(
            (s["PRESSURE_RF"]["POS_X"], s["PRESSURE_LR"]["POS_Y"]), ("1", "4")
        )
        self.assertEqual(
            (s["FRONT_BIAS"]["MIN"], s["FRONT_BIAS"]["MAX"], s["FRONT_BIAS"]["TAB"]),
            ("45", "70", "GENERIC"),
        )
        self.assertEqual(len(notes), 2)
        self.assertIn("DISPLAY_METHOD", s)  # lo que ya había se conserva

    def test_pressure_range_contains_every_compound(self) -> None:
        """Con compuestos añadidos, el rango cubre la presión estática de cada uno (la de Exported no basta)."""
        tyres = TYRES + "[FRONT_1]\nPRESSURE_STATIC=23\n[REAR_1]\nPRESSURE_STATIC=33\n"
        s = parse_ini(SI.apply("", tyres, BRAKES, tyre_pressure=True)[0])
        self.assertEqual(
            (s["PRESSURE_RF"]["MIN"], s["PRESSURE_RF"]["MAX"]), ("15", "42")
        )  # 23 y 26,5
        self.assertEqual(
            (s["PRESSURE_RR"]["MIN"], s["PRESSURE_RR"]["MAX"]), ("15", "48")
        )  # 23 y 33

    def test_reexport_repeats_last_extras(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from src.app.workspace import Workspace

        with tempfile.TemporaryDirectory() as t:
            m = Path(t) / "exports" / "001"
            m.mkdir(parents=True)
            opts = {
                "driver_aids": {"abs": "on", "abs_levels": 6},
                "tyre_compounds": ["street"],
                "adjustable_turbo": True,
                "setup_tyre_pressure": True,
            }
            (m / "manifest.json").write_text(json.dumps({"options": opts}), "utf-8")
            ws = Workspace.__new__(Workspace)
            e = ws._last_extras(SimpleNamespace(root=Path(t)))
            self.assertEqual(
                (
                    e["abs"],
                    e["abs_levels"],
                    e["tc"],
                    e["turbo"],
                    e["setup_tyre_pressure"],
                    e["setup_brake_bias"],
                    e["tyre_compounds"],
                ),
                (True, 6, False, True, True, False, ["street"]),
            )
            self.assertIsNone(ws._last_extras(SimpleNamespace(root=Path(t) / "nada")))

    def test_nothing_marked_adds_nothing_and_missing_data_errors(self) -> None:
        text, notes = SI.apply("[DISPLAY_METHOD]\nSHOW_CLICKS=0\n", TYRES, BRAKES)
        self.assertEqual((list(parse_ini(text)), notes), (["DISPLAY_METHOD"], []))
        with self.assertRaises(SI.SetupItemsError):
            SI.apply("", "[FRONT]\n", BRAKES, tyre_pressure=True)
        with self.assertRaises(SI.SetupItemsError):
            SI.apply("", TYRES, "[DATA]\n", brake_bias=True)

    def test_flags_validation(self) -> None:
        from src.app.workspace import WorkspaceError, _parse_flags

        self.assertEqual(
            _parse_flags({"turbo": True, "setup_brake_bias": False}),
            {"adjustable_turbo": True},
        )
        self.assertEqual(_parse_flags(None), {})
        with self.assertRaises(WorkspaceError):
            _parse_flags({"turbo": "yes"})


if __name__ == "__main__":
    unittest.main()
