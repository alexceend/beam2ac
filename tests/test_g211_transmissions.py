"""G2.11: cajas de doble embrague (``dctGearbox``) y automáticas con convertidor de par
(``automaticGearbox`` + ``torqueConverter``) del exportador 200409 de Automation.

Casos sintéticos (dispositivos y hechos construidos a mano) y, si están, las 7
exportaciones 200409 de ``samples_beamng/`` y un ``camsoEngine`` 40340 (solo lectura).
Informe: ``docs/research/g211_transmissions_2026-11-02.md``.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract import automatic_transmission as AT
from src.extract.drivetrain import (
    Device,
    DrivetrainDataError,
    check_transmission_structure,
)
from src.ir.legacy_facts import ir_to_facts
from src.ir.param import Param
from src.ir.vehicle import VehicleIR

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
CATALOG = Catalog.load(REPO_CATALOG)
DCT_CARS = {
    "2020_rpm_atlanta_gt_3_6t": (
        [-7.09, 6.75, 4.08, 2.75, 1.95, 1.42, 1.04, 0.75],
        3.38,
    ),
    "2020_rpm_legion_grand_touring": (
        [-7.09, 6.75, 4.08, 2.75, 1.95, 1.42, 1.04, 0.75],
        3.2,
    ),
    "2020_rpm_prothean_s": ([-4.51, 4.3, 2.72, 1.93, 1.46, 1.14, 0.92, 0.75], 3.33),
    "rpm_omega_one_concept": ([-7.09, 6.75, 4.08, 2.75, 1.95, 1.42, 1.04, 0.75], 3.55),
}
TC_CARS = {
    "2020_rpm_atacama_sport__4x4": 5.4,
    "2020_rpm_patagonia_xt_v8": 4.52,
    "2021_rpm_portland_touring": 4.44,
}
TC_RATIOS = [-7.09, 6.75, 4.57, 3.32, 2.51, 1.95, 1.53, 1.21, 0.96, 0.75]
GEARS = "ac:drivetrain.ini/GEARS"
DCT_RULES = ["dt.gear_count_dct@1", "dt.gear_r_dct@1", "dt.final_dct@1"] + [
    f"dt.gear_{n}_dct@1" for n in range(1, 9)
]


def dev(dtype, name, inp, idx=1, **params) -> Device:
    return Device(dtype, name, inp, idx, "sintética", params)


ENGINE = dev("combustionEngine", "mainEngine", "dummy", 0)


def vehicle_dir(name: str) -> Path:
    return SAMPLES / name / "vehicles" / name


def have(name: str) -> bool:
    return vehicle_dir(name).is_dir()


# --- estructura del camino motriz -------------------------------------------------


class TestStructure(unittest.TestCase):
    def test_dct_fed_by_engine(self) -> None:
        gb = dev("dctGearbox", "gearbox", "mainEngine", gearRatios=[-3, 0, 3, 2])
        conv, rbox, issues = check_transmission_structure(ENGINE, gb, [], [], [])
        self.assertIsNone(conv)
        self.assertIsNone(rbox)
        self.assertTrue(any("dctGearbox" in i for i in issues))

    def test_dct_not_fed_by_engine(self) -> None:
        gb = dev("dctGearbox", "gearbox", "clutch")
        with self.assertRaisesRegex(DrivetrainDataError, "motor"):
            check_transmission_structure(ENGINE, gb, [], [], [])

    def test_dct_with_friction_clutch(self) -> None:
        gb = dev("dctGearbox", "gearbox", "mainEngine")
        clutch = dev("frictionClutch", "clutch", "mainEngine")
        with self.assertRaisesRegex(DrivetrainDataError, "frictionClutch"):
            check_transmission_structure(ENGINE, gb, [clutch], [], [])

    def test_dct_with_converter(self) -> None:
        gb = dev("dctGearbox", "gearbox", "mainEngine")
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        with self.assertRaisesRegex(DrivetrainDataError, "torqueConverter"):
            check_transmission_structure(ENGINE, gb, [], [tc], [])

    def test_automatic_needs_converter(self) -> None:
        gb = dev("automaticGearbox", "gearbox", "mainEngine")
        with self.assertRaisesRegex(DrivetrainDataError, "torqueConverter"):
            check_transmission_structure(ENGINE, gb, [], [], [])

    def test_automatic_with_converter(self) -> None:
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        gb = dev("automaticGearbox", "gearbox", "torqueConverter")
        conv, rbox, issues = check_transmission_structure(ENGINE, gb, [], [tc], [])
        self.assertIs(conv, tc)
        self.assertTrue(any("convertidor" in i for i in issues))

    def test_converter_order(self) -> None:
        tc = dev("torqueConverter", "torqueConverter", "gearbox")
        gb = dev("automaticGearbox", "gearbox", "mainEngine")
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, gb, [], [tc], [])

    def test_duplicate_converters(self) -> None:
        tc1 = dev("torqueConverter", "tc1", "mainEngine")
        tc2 = dev("torqueConverter", "tc2", "mainEngine")
        gb = dev("automaticGearbox", "gearbox", "tc1")
        with self.assertRaisesRegex(DrivetrainDataError, "varios|ambig"):
            check_transmission_structure(ENGINE, gb, [], [tc1, tc2], [])

    def test_manual_with_converter_rejected(self) -> None:
        gb = dev("manualGearbox", "gearbox", "clutch")
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, gb, [], [tc], [])

    def test_manual_unchanged(self) -> None:
        gb = dev("manualGearbox", "gearbox", "clutch")
        clutch = dev("frictionClutch", "clutch", "mainEngine")
        self.assertEqual(
            check_transmission_structure(ENGINE, gb, [clutch], [], []),
            (None, None, []),
        )

    def test_range_box_high_is_one(self) -> None:
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        gb = dev("automaticGearbox", "gearbox", "torqueConverter")
        rb = dev("rangeBox", "rangebox", "gearbox", gearRatios=[1, 2.5])
        _, rbox, issues = check_transmission_structure(ENGINE, gb, [], [tc], [rb])
        self.assertIs(rbox, rb)
        self.assertTrue(any("2.5" in i for i in issues))

    def test_range_box_initial_not_one(self) -> None:
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        gb = dev("automaticGearbox", "gearbox", "torqueConverter")
        rb = dev("rangeBox", "rangebox", "gearbox", gearRatios=[2.5, 1])
        with self.assertRaisesRegex(DrivetrainDataError, "1"):
            check_transmission_structure(ENGINE, gb, [], [tc], [rb])

    def test_range_box_invalid(self) -> None:
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        gb = dev("automaticGearbox", "gearbox", "torqueConverter")
        for bad in ([], [-1, 1], ["x"], None):
            rb = dev("rangeBox", "rangebox", "gearbox", gearRatios=bad)
            with self.subTest(bad=bad), self.assertRaises(DrivetrainDataError):
                check_transmission_structure(ENGINE, gb, [], [tc], [rb])


# --- lectura de parámetros ---------------------------------------------------------


class TestReadDct(unittest.TestCase):
    def test_defaults_are_recorded(self) -> None:
        gb = dev("dctGearbox", "gearbox", "mainEngine", gearRatios=[-3, 0, 3])
        s = AT.read_dct(gb, {})
        self.assertIsNone(s.lock_torque)
        self.assertEqual(s.clutch_time.value, 0.05)
        self.assertTrue(s.clutch_time.is_default)
        self.assertIn("vehicleController.dctClutchTime", s.defaults_used)
        self.assertIn(
            "dctGearbox.lua:840", s.defaults_used["vehicleController.dctClutchTime"]
        )
        self.assertEqual(s.crossover_time_range, (0.05, 0.5))
        self.assertEqual(s.shift_delay.value, 0.2)

    def test_values_from_jbeam(self) -> None:
        gb = dev("dctGearbox", "gearbox", "mainEngine", lockTorque=900, friction=0)
        s = AT.read_dct(gb, {"dctClutchTime": 0.08, "transmissionShiftDelay": 0.15})
        self.assertEqual(s.lock_torque.value, 900)
        self.assertEqual(s.clutch_time.source, "jbeam")
        self.assertEqual(s.crossover_time_range, (0.08, 0.8))
        self.assertNotIn("gearbox.friction", s.defaults_used)

    def test_invalid_values(self) -> None:
        for params, ctrl in (
            ({"lockTorque": "x"}, {}),
            ({"lockTorque": -1}, {}),
            ({"lockTorque": True}, {}),
            ({}, {"dctClutchTime": 0}),
            ({}, {"dctClutchTime": float("nan")}),
            ({"friction": -2}, {}),
        ):
            gb = dev("dctGearbox", "gearbox", "mainEngine", **params)
            with self.subTest(p=params, c=ctrl), self.assertRaises(DrivetrainDataError):
                AT.read_dct(gb, ctrl)

    def test_wrong_device(self) -> None:
        with self.assertRaises(DrivetrainDataError):
            AT.read_dct(dev("manualGearbox", "gearbox", "clutch"), {})


class TestReadConverter(unittest.TestCase):
    def _tc(self, **p):
        return dev("torqueConverter", "torqueConverter", "mainEngine", **p)

    def _gb(self, **p):
        return dev("automaticGearbox", "gearbox", "torqueConverter", **p)

    def test_converter_without_coupling_curve_uses_recorded_defaults(self) -> None:
        s = AT.read_torque_converter(self._tc(), self._gb(), {})
        for key, value in (
            ("torqueConverter.couplingAVRatio", 0.85),
            ("torqueConverter.stallTorqueRatio", 2.0),
            ("torqueConverter.converterStiffness", 10.0),
            ("torqueConverter.converterDiameter", 0.30),
        ):
            self.assertIn(key, s.defaults_used)
        self.assertEqual(s.coupling_av_ratio.value, 0.85)
        self.assertTrue(s.coupling_av_ratio.is_default)
        self.assertIsNone(s.converter_torque)
        self.assertFalse(s.has_lockup)  # torqueConverterLockupRPM ausente → 0
        self.assertEqual(s.lockup_range.value, 0.0)

    def test_lockup_range_default_is_fraction_of_rpm(self) -> None:
        s = AT.read_torque_converter(
            self._tc(), self._gb(), {"torqueConverterLockupRPM": 1000}
        )
        self.assertTrue(s.has_lockup)
        self.assertAlmostEqual(s.lockup_range.value, 200.0)
        self.assertTrue(s.lockup_range.is_default)

    def test_invalid(self) -> None:
        for p in (
            {"stallTorqueRatio": 0.9},
            {"couplingAVRatio": 1.2},
            {"couplingAVRatio": 0},
            {"converterDiameter": -0.3},
            {"lockupClutchTorque": "a"},
        ):
            with self.subTest(p=p), self.assertRaises(DrivetrainDataError):
                AT.read_torque_converter(self._tc(**p), self._gb(), {})

    def test_wrong_devices(self) -> None:
        with self.assertRaises(DrivetrainDataError):
            AT.read_torque_converter(self._gb(), self._gb(), {})
        with self.assertRaises(DrivetrainDataError):
            AT.read_torque_converter(self._tc(), dev("dctGearbox", "g", "x"), {})

    def test_converter_math(self) -> None:
        s = AT.read_torque_converter(
            self._tc(
                converterStiffness=12,
                stallTorqueRatio=1.8,
                couplingAVRatio=0.92,
                converterDiameter=0.31376157887133,
            ),
            self._gb(),
            {},
        )
        # k(0) = 0,004·s/(1 + s); multiplicación máxima en calado, 1 desde el
        # acoplamiento
        self.assertAlmostEqual(AT.converter_k_factor(s, 0.0), 0.004 * 12 / 13)
        self.assertAlmostEqual(AT.converter_k_factor(s, 1.0), 0.0)
        self.assertEqual(AT.converter_torque_ratio(s, 0.0), 1.8)
        self.assertEqual(AT.converter_torque_ratio(s, 0.92), 1.0)
        self.assertEqual(AT.converter_torque_ratio(s, 0.99), 1.0)
        self.assertAlmostEqual(s.k_factor_coef, 844 * 0.31376157887133**5)
        av = 3000 * math.pi / 30
        r, mult = AT.converter_steady_state(s, av, 400.0)
        self.assertAlmostEqual(AT.converter_absorbed_torque(s, av, r), 400.0, places=6)
        self.assertGreaterEqual(mult, 1.0)
        # por debajo del calado: la bomba no absorbe el par con la salida parada
        self.assertIsNone(AT.converter_steady_state(s, 100 * math.pi / 30, 400.0))
        self.assertIsNone(AT.converter_steady_state(s, av, 0.0))

    def test_default_coupling_torque(self) -> None:
        self.assertAlmostEqual(
            AT.default_engine_coupling_torque(500.0, 7000.0, 0.2),
            625.0 + 7000 * 0.2 * math.pi / 30,
        )


class TestReadRangeBox(unittest.TestCase):
    def test_initial_ratio(self) -> None:
        rb = AT.read_range_box(
            dev("rangeBox", "rangebox", "gearbox", gearRatios=[1, 2.5])
        )
        self.assertEqual(rb.initial_ratio, 1.0)
        self.assertEqual(rb.ratios, (1.0, 2.5))


# --- reglas (hechos sintéticos) ----------------------------------------------------


def _synthetic_ir(
    kind="dctGearbox",
    exporter: str | None = "200409",
    device: str | None = "combustionEngine",
    ratios=(-7.09, 6.75, 4.08, 2.75, 1.95, 1.42, 1.04, 0.75),
    final=3.38,
) -> VehicleIR:
    ir = VehicleIR()
    ir.set("identity.source_tool", Param.of("Automation", "text", "extracted", []))
    ir.set(
        "identity.author", Param.of("Camshaft Software LTD", "text", "extracted", [])
    )
    if exporter is not None:
        ir.set("formats.exporter_version", Param.of(exporter, "text", "extracted", []))
    if device is not None:
        ir.set("engine.device_type", Param.of(device, "enum", "extracted", []))
    ir.set("drivetrain.layout", Param.of("AWD", "enum", "computed", []))
    ir.set("drivetrain.gearbox.kind", Param.of(kind, "enum", "extracted", []))
    ir.set("drivetrain.gearbox.reverse_ratio", Param.of(ratios[0], "1", "extracted"))
    fwd = ratios[1:]
    ir.set("drivetrain.gearbox.forward_count", Param.of(len(fwd), "1", "extracted"))
    for k, r in enumerate(fwd, 1):
        ir.set(f"drivetrain.gearbox.forward_ratios.{k}", Param.of(r, "1", "extracted"))
    ir.set("drivetrain.final_ratio", Param.of(final, "1", "extracted"))
    return ir


def _resolve(ir: VehicleIR, targets) -> dict:
    ctx = context_from_facts("synthetic", "v", ir_to_facts(ir), CATALOG)
    eng = ResolutionEngine(CATALOG, ctx)
    return {t: eng.resolve(t) for t in targets}


TARGETS = [f"{GEARS}/COUNT", f"{GEARS}/GEAR_R", f"{GEARS}/FINAL"] + [
    f"{GEARS}/GEAR_{n}" for n in range(1, 9)
]


class TestDctRules(unittest.TestCase):
    def test_rules_exist_with_closed_conditions(self) -> None:
        for key in DCT_RULES:
            with self.subTest(rule=key):
                r = CATALOG.rules[key]
                cond = {(c.fact, c.op): c.value for c in r.conditions}
                self.assertEqual(
                    cond[("ir.drivetrain.gearbox.kind", "eq")], "dctGearbox"
                )
                self.assertEqual(
                    cond[("ir.formats.exporter_version", "in")], ["200409"]
                )
                self.assertEqual(
                    cond[("ir.engine.device_type", "eq")], "combustionEngine"
                )
                self.assertEqual(r.scope.family, "automation_export")
                self.assertEqual(r.status, "provisional")
                self.assertFalse(r.supersedes)

    def test_apply_to_200409_dct(self) -> None:
        res = _resolve(_synthetic_ir(), TARGETS)
        expect = {
            "COUNT": 7,
            "GEAR_R": -7.09,
            "FINAL": 3.38,
            "GEAR_1": 6.75,
            "GEAR_7": 0.75,
        }
        for key, value in expect.items():
            r = res[f"{GEARS}/{key}"]
            with self.subTest(key=key):
                self.assertEqual(r.source, "rule")
                self.assertTrue(r.rule.endswith("_dct@1"), r.rule)
                self.assertAlmostEqual(r.value, value)
        self.assertEqual(res[f"{GEARS}/GEAR_8"].status, "not_applicable")

    def test_reverse_and_count_bounds(self) -> None:
        nine = (-7.09, 6.75, 4.57, 3.32, 2.51, 1.95, 1.53, 1.21, 0.96, 0.75)
        res = _resolve(_synthetic_ir(ratios=nine), TARGETS)
        self.assertNotEqual(res[f"{GEARS}/COUNT"].source, "rule")  # > 8: sin GEAR_9
        self.assertEqual(res[f"{GEARS}/GEAR_8"].rule, "dt.gear_8_dct@1")

    def test_not_applied_outside_scope(self) -> None:
        cases = {
            "40340": dict(exporter="40340"),
            "38022": dict(exporter="38022"),
            "200410 (desconocida)": dict(exporter="200410"),
            "sin versión": dict(exporter=None),
            "camsoEngine": dict(device="camsoEngine"),
            "electricMotor": dict(device="electricMotor"),
            "automaticGearbox": dict(kind="automaticGearbox"),
            "manualGearbox": dict(kind="manualGearbox"),
            "cvtGearbox (no compatible)": dict(kind="cvtGearbox"),
        }
        for label, kw in cases.items():
            res = _resolve(_synthetic_ir(**kw), TARGETS)
            for t, r in res.items():
                with self.subTest(case=label, target=t):
                    self.assertFalse(
                        r.source == "rule" and r.rule and r.rule.endswith("_dct@1"),
                        f"{t}: {r.rule}",
                    )

    def test_missing_ratio_never_zero(self) -> None:
        ir = _synthetic_ir()
        ir.set("drivetrain.final_ratio", Param.missing("1", "sin dato"))
        r = _resolve(ir, [f"{GEARS}/FINAL"])[f"{GEARS}/FINAL"]
        self.assertNotEqual(r.source, "rule")
        self.assertIsNone(r.value)

    def test_automatic_has_no_applied_gear_rule(self) -> None:
        # D-G211-TC pendiente: las relaciones del convertidor no se aplican
        ir = _synthetic_ir(kind="automaticGearbox", ratios=tuple(TC_RATIOS), final=4.44)
        for t, r in _resolve(ir, TARGETS).items():
            with self.subTest(target=t):
                self.assertNotEqual(r.source, "rule", r.rule)
                self.assertNotIn(r.status, ("known",))

    def test_camso_rules_untouched(self) -> None:
        # las reglas 2WD siguen limitadas a caja manual o secuencial
        for n in ("gear_count", "gear_r", "final", *(f"gear_{k}" for k in range(1, 9))):
            r = CATALOG.rules[f"dt.{n}_2wd@3"]
            kinds = next(
                c.value
                for c in r.scope.conditions
                if c.fact == "ir.drivetrain.gearbox.kind"
            )
            self.assertEqual(kinds, ["manualGearbox", "sequentialGearbox"])


# --- vehículos reales --------------------------------------------------------------


_CACHE: dict = {}


def _real(name: str):
    if name not in _CACHE:
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        vd = vehicle_dir(name)
        src = load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])
        pv = preview_physics(src, 70.0)
        _CACHE[name] = (pv, import_preview(pv, 70.0))
    return _CACHE[name]


def _session(ir):
    from src.convert.service import ConversionSession

    s = ConversionSession.from_ir(ir)
    s.resolve()
    return s


class TestRealDct(unittest.TestCase):
    def test_four_dct_cars(self) -> None:
        for name, (ratios, final) in DCT_CARS.items():
            if not have(name):
                self.skipTest("muestras 200409 no disponibles")
            pv, ir = _real(name)
            with self.subTest(car=name):
                self.assertTrue(pv.stages["drivetrain"]["ok"], pv.stages["drivetrain"])
                g = ir.drivetrain.gearbox
                self.assertEqual(g.kind.value, "dctGearbox")
                self.assertEqual(g.reverse_ratio.value, ratios[0])
                self.assertEqual(g.forward_count.value, len(ratios) - 1)
                self.assertEqual([p.value for p in g.forward_ratios], ratios[1:])
                self.assertEqual(ir.drivetrain.final_ratio.value, final)
                self.assertEqual(ir.drivetrain.layout.value, "AWD")
                self.assertEqual(g.shift_crossover_time.value, 0.05)
                self.assertEqual(g.shift_delay.value, 0.15)
                # capacidad por defecto de los embragues (dctGearbox.lua l. 180)
                e = ir.engine
                expect = AT.default_engine_coupling_torque(
                    e.peak_torque.value, e.speed_ceiling.value, e.inertia.value
                )
                self.assertAlmostEqual(
                    ir.drivetrain.clutch.lock_torque_default.value, expect
                )
                self.assertEqual(ir.drivetrain.clutch.lock_torque.status, "missing")
                self.assertTrue(ir.drivetrain.clutch.lock_torque.reason)
                self.assertEqual(
                    ir.drivetrain.torque_converter.stall_torque_ratio.status,
                    "not_applicable",
                )
                res = {r.target: r for r in _session(ir).results}
                for n, ratio in enumerate(ratios[1:], 1):
                    r = res[f"{GEARS}/GEAR_{n}"]
                    self.assertEqual(r.rule, f"dt.gear_{n}_dct@1")
                    self.assertEqual(r.value, ratio)
                self.assertEqual(res[f"{GEARS}/GEAR_R"].value, ratios[0])
                self.assertEqual(res[f"{GEARS}/COUNT"].value, len(ratios) - 1)
                self.assertEqual(res[f"{GEARS}/FINAL"].value, final)
                self.assertEqual(res[f"{GEARS}/GEAR_8"].status, "not_applicable")
                # G2.12: TYPE=AWD por dt.traction_awd@1 (central lsd, reparto fijo)
                self.assertEqual(
                    (
                        res["ac:drivetrain.ini/TRACTION/TYPE"].value,
                        res["ac:drivetrain.ini/TRACTION/TYPE"].rule,
                    ),
                    ("AWD", "dt.traction_awd@1"),
                )


class TestRealConverter(unittest.TestCase):
    def test_portland(self) -> None:
        name = "2021_rpm_portland_touring"
        if not have(name):
            self.skipTest("muestras 200409 no disponibles")
        pv, ir = _real(name)
        self.assertTrue(pv.stages["drivetrain"]["ok"], pv.stages["drivetrain"])
        g = ir.drivetrain.gearbox
        self.assertEqual(g.kind.value, "automaticGearbox")
        self.assertEqual(g.reverse_ratio.value, TC_RATIOS[0])
        self.assertEqual([p.value for p in g.forward_ratios], TC_RATIOS[1:])
        self.assertEqual(ir.drivetrain.final_ratio.value, TC_CARS[name])
        tc = ir.drivetrain.torque_converter
        self.assertEqual(tc.stall_torque_ratio.value, 1.8)
        self.assertEqual(tc.coupling_speed_ratio.value, 0.92)
        self.assertEqual(tc.lockup_min_gear.value, 6)
        self.assertAlmostEqual(tc.diameter.value, 0.31376157887133)
        self.assertEqual(tc.torque_limit.kind, "computed")
        self.assertEqual(ir.drivetrain.clutch.lock_torque.status, "not_applicable")
        self.assertEqual(g.shift_crossover_time.status, "not_applicable")
        # sin borrador drivetrain.ini: el convertidor no tiene equivalente
        self.assertFalse(pv.stages["render_drivetrain_ini"]["ok"])
        self.assertNotIn("drivetrain.ini", pv.rendered)
        s = _session(ir)
        for r in s.results:
            if r.target.startswith(GEARS):
                with self.subTest(target=r.target):
                    # G2.43 (D-G211-TC b): solo las reglas _atc_200409, con la aproximación declarada
                    if r.source == "rule":
                        self.assertIn("_atc_200409", r.rule)
                        self.assertTrue(
                            any("APROXIMACIÓN" in a for a in r.assumptions), r.rule
                        )
        by = {r.target: r for r in s.results}
        self.assertEqual(by[GEARS + "/FINAL"].value, TC_CARS[name])
        self.assertEqual(by[GEARS + "/GEAR_9"].value, TC_RATIOS[-1])
        caps = {f.feature: f.level for f in s.capabilities()}
        self.assertEqual(caps["drivetrain.torque_converter"], "not_representable")

    def test_geometry_blocked_cars_still_extract(self) -> None:
        from src.extract.drivetrain import build_drivetrain
        from src.jbeam import resolve
        from src.pipeline.physics_preview import load_vehicle_dir

        for name in ("2020_rpm_atacama_sport__4x4", "2020_rpm_patagonia_xt_v8"):
            if not have(name):
                self.skipTest("muestras 200409 no disponibles")
            vd = vehicle_dir(name)
            src = load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])
            dt = build_drivetrain(resolve(src.index, src.pc))
            with self.subTest(car=name):
                self.assertEqual(dt.gearbox.type, "automaticGearbox")
                self.assertEqual(dt.converter.type, "torqueConverter")
                self.assertEqual(dt.range_box.name, "rangebox")
                self.assertEqual(dt.final_drive, TC_CARS[name])
                self.assertEqual([dt.reverse] + dt.forward, TC_RATIOS)
                self.assertIsNotNone(dt.awd)


class TestCamsoUnchanged(unittest.TestCase):
    def test_sucette_drivetrain(self) -> None:
        name = "sucette_poubelle"
        vd = SAMPLES / f"thershow_{name}" / "vehicles"
        if not vd.is_dir():
            self.skipTest("samples 40340 no disponibles")
        vd = next(vd.iterdir())
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        src = load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])
        pv = preview_physics(src, 70.0)
        ir = import_preview(pv, 70.0)
        self.assertIsNone(pv.drivetrain.transmission)
        self.assertEqual(ir.drivetrain.gearbox.kind.value, "manualGearbox")
        self.assertEqual(
            ir.drivetrain.torque_converter.stall_torque_ratio.status, "not_applicable"
        )
        self.assertEqual(
            ir.drivetrain.gearbox.shift_crossover_time.status, "not_applicable"
        )
        facts = ir_to_facts(ir)
        self.assertFalse(any("torque_converter" in k for k in facts))
        self.assertNotIn("ir.drivetrain.gearbox.shift_crossover_time", facts)
        res = {r.target: r for r in _session(ir).results}
        for t, r in res.items():
            if r.rule:
                self.assertFalse(r.rule.endswith("_dct@1"), (t, r.rule))


if __name__ == "__main__":
    unittest.main()
