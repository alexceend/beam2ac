"""Soporte de la CVT (``cvtGearbox``) del exportador 40340 (2026-11-19; agus_1_nrt_1).

Extracción de la caja y de su lógica de rpm objetivo, estructura del camino motriz, reglas
de catálogo ``dt.*_cvt@1`` y la muestra real (se omite si no está en ``samples_beamng/``).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract import automatic_transmission as AT
from src.extract.drivetrain import (
    Device,
    Drivetrain,
    DrivetrainDataError,
    _cvt_range,
    check_transmission_structure,
)
from src.ir.legacy_facts import ir_to_facts
from src.ir.param import Param
from src.ir.vehicle import VehicleIR

ROOT = Path(__file__).resolve().parents[1]
AGUS = ROOT / "samples_beamng" / "agus_1_nrt_1" / "vehicles" / "agus_1_nrt_1"
CATALOG = Catalog.load(REPO_CATALOG)
GEARS = "ac:drivetrain.ini/GEARS"
CVT_RULES = [
    "dt.gear_count_cvt@1",
    "dt.gear_1_cvt@1",
    "dt.gear_r_cvt@1",
    "dt.final_cvt@1",
]
TARGETS = [f"{GEARS}/{k}" for k in ("COUNT", "GEAR_1", "GEAR_R", "FINAL", "GEAR_2")]


def dev(dtype, name, inp, idx=1, **params) -> Device:
    return Device(dtype, name, inp, idx, "sintética", params)


ENGINE = dev("camsoEngine", "mainEngine", "dummy", 0)
TC = dev("torqueConverter", "clutch", "mainEngine")
CVT = dev("cvtGearbox", "gearbox", "clutch", minGearRatio=0.69, maxGearRatio=1.97)
CONTROLLER = {"cvtLowRPM": 2200, "cvtHighRPM": 11500, "cvtAggression": 0.1}


class TestCvtRange(unittest.TestCase):
    def test_reads_both_ratios(self) -> None:
        self.assertEqual(_cvt_range(CVT), (0.69, 1.97))

    def test_missing_or_inverted_is_rejected_not_defaulted(self) -> None:
        bad = (
            {},
            {"minGearRatio": 1.0},
            {"maxGearRatio": 2.0},
            {"minGearRatio": 2.0, "maxGearRatio": 1.0},
            {"minGearRatio": 0.0, "maxGearRatio": 1.0},
            {"minGearRatio": "x", "maxGearRatio": 1.0},
        )
        for params in bad:
            with self.subTest(params=params), self.assertRaises(DrivetrainDataError):
                _cvt_range(dev("cvtGearbox", "gearbox", "clutch", **params))


class TestStructure(unittest.TestCase):
    def test_converter_in_front_is_required(self) -> None:
        conv, _rb, issues = check_transmission_structure(ENGINE, CVT, [], [TC], [])
        self.assertIs(conv, TC)
        self.assertTrue(any("cvtGearbox" in i for i in issues))
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, CVT, [], [], [])

    def test_wrong_order_and_friction_clutch_rejected(self) -> None:
        swapped = dev(
            "cvtGearbox", "gearbox", "mainEngine", minGearRatio=0.5, maxGearRatio=2
        )
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, swapped, [], [TC], [])
        clutch = dev("frictionClutch", "fc", "mainEngine")
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, CVT, [clutch], [TC], [])


class TestReadCvt(unittest.TestCase):
    def _dt(self, cvt_range=(0.69, 1.97)) -> Drivetrain:
        return Drivetrain(
            devices={},
            engine="mainEngine",
            gearbox=CVT,
            differential=None,
            clutch=None,
            path=[],
            driven_wheels=[],
            undriven_axles=[],
            gear_ratios_raw=[],
            reverse=-cvt_range[1],
            forward=[cvt_range[1], cvt_range[0]],
            final_drive=7.0,
            cvt_range=cvt_range,
        )

    def test_values_and_defaults(self) -> None:
        s = AT.read_cvt(self._dt(), CONTROLLER)
        self.assertEqual((s.min_ratio, s.max_ratio), (0.69, 1.97))
        self.assertEqual((s.low_rpm.value, s.high_rpm.value), (2200, 11500))
        self.assertEqual(s.aggression.value, 0.1)
        self.assertFalse(s.aggression.is_default)
        self.assertEqual(s.automatic_modes.value, "PRND")  # por defecto del Lua
        self.assertTrue(s.automatic_modes.is_default)

    def test_rpm_window_is_mandatory_and_ordered(self) -> None:
        for ctrl in (
            {"cvtHighRPM": 11500},
            {"cvtLowRPM": 2200},
            {"cvtLowRPM": 9000, "cvtHighRPM": 5000},
        ):
            with self.subTest(ctrl=ctrl), self.assertRaises(DrivetrainDataError):
                AT.read_cvt(self._dt(), ctrl)

    def test_converter_lockup_defaults_cite_the_cvt_controller(self) -> None:
        s = AT.read_torque_converter(TC, CVT, {"torqueConverterLockupRPM": 1800})
        self.assertAlmostEqual(s.lockup_range.value, 360.0)
        self.assertIn("shiftLogic/cvtGearbox.lua", s.lockup_range.locator)
        self.assertEqual(s.lockup_min_gear.value, 0.0)


def _cvt_ir(kind="cvtGearbox", exporter="40340", device="camsoEngine") -> VehicleIR:
    ir = VehicleIR()
    ir.set("identity.source_tool", Param.of("Automation", "text", "extracted", []))
    ir.set(
        "identity.author", Param.of("Camshaft Software LTD", "text", "extracted", [])
    )
    ir.set("formats.exporter_version", Param.of(exporter, "text", "extracted", []))
    ir.set("engine.device_type", Param.of(device, "enum", "extracted", []))
    ir.set("drivetrain.layout", Param.of("AWD", "enum", "computed", []))
    ir.set("drivetrain.gearbox.kind", Param.of(kind, "enum", "extracted", []))
    why = "cvtGearbox: relación continua"
    ir.drivetrain.gearbox.reverse_ratio = Param.not_applicable("1", why)
    ir.drivetrain.gearbox.forward_count = Param.not_applicable("1", why)
    ir.set("drivetrain.gearbox.cvt_ratio_min", Param.of(0.69, "1", "extracted"))
    ir.set("drivetrain.gearbox.cvt_ratio_max", Param.of(1.97, "1", "extracted"))
    ir.set("drivetrain.final_ratio", Param.of(7.01, "1", "extracted"))
    ir.set(
        "drivetrain.torque_converter.stall_torque_ratio",
        Param.of(1.8, "1", "extracted"),
    )
    return ir


def _resolve(ir: VehicleIR) -> dict:
    ctx = context_from_facts("synthetic", "v", ir_to_facts(ir), CATALOG)
    eng = ResolutionEngine(CATALOG, ctx)
    return {t: eng.resolve(t) for t in TARGETS}


class TestRules(unittest.TestCase):
    def test_rules_exist_with_closed_conditions(self) -> None:
        for key in CVT_RULES:
            with self.subTest(rule=key):
                r = CATALOG.rules[key]
                cond = {(c.fact, c.op): c.value for c in r.conditions}
                self.assertEqual(
                    cond[("ir.drivetrain.gearbox.kind", "eq")], "cvtGearbox"
                )
                self.assertEqual(cond[("ir.formats.exporter_version", "in")], ["40340"])
                self.assertEqual(cond[("ir.engine.device_type", "eq")], "camsoEngine")
                self.assertEqual(r.status, "provisional")
                self.assertFalse(r.supersedes)

    def test_apply_to_cvt(self) -> None:
        res = _resolve(_cvt_ir())
        expect = {"COUNT": 1, "GEAR_1": 1.0, "GEAR_R": -1.97, "FINAL": 7.01}
        for key, value in expect.items():
            r = res[f"{GEARS}/{key}"]
            with self.subTest(key=key):
                self.assertEqual(r.source, "rule")
                self.assertTrue(r.rule.endswith("_cvt@1"), r.rule)
                self.assertAlmostEqual(r.value, value)
        self.assertEqual(res[f"{GEARS}/GEAR_2"].status, "not_applicable")

    def test_not_applied_outside_scope(self) -> None:
        cases = {
            "200409": dict(exporter="200409"),
            "38022": dict(exporter="38022"),
            "combustionEngine": dict(device="combustionEngine"),
            "manualGearbox": dict(kind="manualGearbox"),
            "automaticGearbox": dict(kind="automaticGearbox"),
        }
        for label, kw in cases.items():
            for t, r in _resolve(_cvt_ir(**kw)).items():
                with self.subTest(case=label, target=t):
                    self.assertFalse(
                        r.source == "rule" and r.rule and r.rule.endswith("_cvt@1"),
                        f"{t}: {r.rule}",
                    )

    def test_missing_ratio_never_zero(self) -> None:
        ir = _cvt_ir()
        ir.set("drivetrain.gearbox.cvt_ratio_max", Param.missing("1", "ausente"))
        r = _resolve(ir)[f"{GEARS}/GEAR_R"]
        self.assertNotEqual(r.source, "rule")
        self.assertNotEqual(r.value, 0)


FACTS = {
    "ir.drivetrain.gearbox.kind": "cvtGearbox",
    "ir.drivetrain.gearbox.cvt_ratio_min": 0.69,
    "ir.drivetrain.gearbox.cvt_ratio_max": 1.97,
    "ir.drivetrain.final_ratio": 7.01,
    "ir.drivetrain.gearbox.cvt_rpm_low": 2200.0,
    "ir.drivetrain.gearbox.cvt_rpm_high": 11500.0,
    "ir.drivetrain.gearbox.cvt_aggression": 0.1,
    "ir.drivetrain.gearbox.cvt_aggression_down": 0.5,
}


class TestScript(unittest.TestCase):
    def test_not_a_cvt_gives_no_script(self) -> None:
        from src.ac.cvt_script import params_from_facts

        self.assertIsNone(
            params_from_facts({"ir.drivetrain.gearbox.kind": "manualGearbox"})
        )

    def test_missing_or_incoherent_data_is_an_error(self) -> None:
        from src.ac.cvt_script import CvtScriptError, params_from_facts

        for drop in [k for k in FACTS if k != "ir.drivetrain.gearbox.kind"]:
            facts = {k: v for k, v in FACTS.items() if k != drop}
            with self.subTest(drop=drop), self.assertRaises(CvtScriptError):
                params_from_facts(facts)
        for key, val in (
            ("ir.drivetrain.gearbox.cvt_rpm_low", 12000.0),
            ("ir.drivetrain.final_ratio", float("nan")),
            ("ir.drivetrain.final_ratio", float("inf")),
        ):
            with self.subTest(key=key, val=val), self.assertRaises(CvtScriptError):
                params_from_facts(dict(FACTS, **{key: val}))

    def test_render_carries_the_extracted_constants(self) -> None:
        from src.ac.cvt_script import RPM_TO_AV, params_from_facts, render_cvt_script

        text = render_cvt_script(params_from_facts(FACTS))
        for needle in (
            "RATIO_MIN, RATIO_MAX = 0.69, 1.97",
            "BASE_FINAL = 7.01",
            f"{2200.0 * RPM_TO_AV!r}, {11500.0 * RPM_TO_AV!r}",
            "AGGRESSION = 0.1",
            "AGG_UP, AGG_DOWN = 1.5, 0.5",
            "ac.setGearsFinalRatio",
            "function script.update(dt)",
        ):
            self.assertIn(needle, text)
        # la agresividad entra sin recortar al suavizador (vehicleController.lua)
        self.assertIn("(gas * 1.333 * dGas)", text)
        self.assertNotIn("{", text)  # sin marcadores de plantilla sin sustituir
        self.assertNotIn("}", text)


class TestDrivenRadius(unittest.TestCase):
    def _radius(self, radii):
        from types import SimpleNamespace as NS

        from src.extract.traction import driven_wheel_radius

        wheels = [NS(name=f"w{i}", radius=r) for i, r in enumerate(radii)]
        dt = NS(driven_wheels=[w.name for w in wheels])
        return driven_wheel_radius(dt, NS(wheels=wheels))

    def test_equal_and_slightly_staggered(self) -> None:
        self.assertEqual(self._radius([0.3, 0.3, 0.3, 0.3]), 0.3)
        self.assertAlmostEqual(self._radius([0.32, 0.32, 0.325, 0.325]), 0.3225)

    def test_large_or_invalid_spread_still_rejected(self) -> None:
        for radii in ([0.3, 0.3, 0.4, 0.4], [float("nan"), 0.3], [0.0, 0.3]):
            with self.subTest(radii=radii), self.assertRaises(DrivetrainDataError):
                self._radius(radii)


@unittest.skipUnless(AGUS.is_dir(), "muestra agus_1_nrt_1 ausente")
class TestAgusSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        src = load_vehicle_dir(AGUS, "7dfd7.pc")
        cls.pv = preview_physics(src, 70.0)
        cls.ir = import_preview(cls.pv, 70.0)

    def test_extracted_cvt(self) -> None:
        p = dict(self.ir.walk())
        self.assertEqual(p["drivetrain.gearbox.kind"].value, "cvtGearbox")
        self.assertAlmostEqual(
            p["drivetrain.gearbox.cvt_ratio_min"].value, 0.68775006, 6
        )
        self.assertAlmostEqual(
            p["drivetrain.gearbox.cvt_ratio_max"].value, 1.97173491, 6
        )
        self.assertEqual(p["drivetrain.gearbox.cvt_rpm_low"].value, 2200)
        self.assertEqual(p["drivetrain.gearbox.cvt_rpm_high"].value, 11500)
        self.assertEqual(p["drivetrain.gearbox.cvt_aggression"].value, 0.1)
        self.assertEqual(p["drivetrain.gearbox.cvt_aggression_down"].value, 0.5)
        self.assertEqual(p["drivetrain.gearbox.automatic_modes"].value, "PRNDS")
        self.assertEqual(p["drivetrain.final_ratio"].value, 7.01)
        self.assertEqual(p["drivetrain.gearbox.forward_count"].status, "not_applicable")
        self.assertEqual(p["drivetrain.gearbox.reverse_ratio"].status, "not_applicable")
        self.assertEqual(p["drivetrain.torque_converter.stall_torque_ratio"].value, 1.8)
        # 2200 = ralentí 1700 + 500 y 11500 = min(11800, 12000 − 500) (ProcessGearbox.lua)
        self.assertEqual(p["engine.idle_speed"].value + 500, 2200)

    def test_staggered_tyres_do_not_block_the_drivetrain(self) -> None:
        lay = self.pv.drivetrain.layout
        self.assertEqual(lay.layout, "AWD")
        self.assertAlmostEqual(
            lay.driven_radius, 0.3225, 4
        )  # 0,320 delante, 0,325 detrás

    def test_gear_targets_resolve_with_cvt_rules(self) -> None:
        res = _resolve(self.ir)
        self.assertEqual(res[f"{GEARS}/COUNT"].value, 1)
        self.assertEqual(res[f"{GEARS}/GEAR_1"].value, 1.0)
        self.assertAlmostEqual(res[f"{GEARS}/GEAR_R"].value, -1.97173491, 6)
        self.assertEqual(res[f"{GEARS}/FINAL"].value, 7.01)


if __name__ == "__main__":
    unittest.main()
