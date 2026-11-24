"""G2.9: motor ``combustionEngine`` conectado al flujo (adaptador → IR → reglas).

Casos sintéticos (sin el juego) y, si están, las 7 exportaciones de Automation del
exportador 200409 de ``samples_beamng/`` (solo lectura) y el Ibiza (``camsoEngine``,
regresión). Informe: ``docs/research/g29_combustion_engine_2026-11-02.md``.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.extract.engine_combustion import (
    BOOST_PER_PASCAL,
    PSI_TO_PASCAL,
    TURBO_AV_TO_RPM,
    TURBO_RPM_TO_AV,
    CombustionEngineError,
    create_curve,
    find_combustion_engine,
    read_combustion_engine,
)
from src.importers.beamng import _import_combustion_engine
from src.ir.param import Param
from src.ir.vehicle import VehicleIR
from src.jbeam.resolver import ResolvedConfig

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
V200409 = (
    "2020_rpm_atacama_sport__4x4",
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_patagonia_xt_v8",
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
)
TORQUE = [
    ["rpm", "torque"],
    [0, 0],
    [1000, 100],
    [2000, 150],
    [3000, 180],
    [4000, 170],
    [5000, 150],
    [6000, 120],
]


def _active(name: str, parent: str | None, data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, parent=parent, part=SimpleNamespace(name=name, data=data)
    )


def _engine(extra: dict | None = None, rows=None, turbo: dict | None = None):
    """Configuración resuelta mínima con un ``combustionEngine`` (y turbo opcional)."""
    eng = {
        "torque": TORQUE,
        "idleRPM": 800,
        "maxRPM": 6000,
        "inertia": 0.15,
        "friction": 10,
        "dynamicFriction": 0.02,
    }
    eng.update(extra or {})
    eng = {k: v for k, v in eng.items() if v is not None}
    data = {
        "powertrain": rows
        or [
            ["type", "name", "inputName", "inputIndex"],
            ["combustionEngine", "mainEngine", "dummy", 0],
        ],
        "mainEngine": eng,
    }
    active = [_active("root", None, data)]
    if turbo is not None:
        active.append(
            _active(
                "turbo",
                "root",
                {"turbocharger": turbo, "mainEngine": {"turbocharger": "turbocharger"}},
            )
        )
    return ResolvedConfig(root="root", active=active)


TURBO = {
    "wastegateStart": 10,
    "wastegateLimit": 12,
    "maxExhaustPower": 40000,
    "backPressureCoef": 0.0001,
    "frictionCoef": 16,
    "pressurePSI": [[0, -3.5], [50000, 6], [100000, 16], [150000, 25], [200000, 25]],
    "engineDef": [
        [0, 0.0, 1.0],
        [1000, 0.0, 1.0],
        [2000, 0.5, 1.0],
        [3000, 1.0, 1.0],
        [6000, 1.0, 1.0],
    ],
}


class TestAdapterSynthetic(unittest.TestCase):
    def test_complete_na_engine(self) -> None:
        e = read_combustion_engine(_engine())
        self.assertEqual(e.wot.kind, "na")
        self.assertEqual(e.wot_blockers, [])
        self.assertTrue(e.supported)
        # sin revLimiterRPM: maxRPM = min(maxRPM, tabla) = 6000 (l. 1957, 1968)
        self.assertEqual(e.max_rpm, 6000)
        self.assertEqual(
            sorted(e.defaults_used),
            [
                # G2.10 (D-G29c): + engineBrakeTorque e idleRPMRoughness (freno motor)
                "engineBrakeTorque",
                "idleRPMRoughness",
                "redlineTorqueDropOffRange",
                "revLimiterRPM",
                "revLimiterType",
            ],
        )
        # en un nudo de la tabla la spline pasa por el punto: 180 − 10 − 0,02·ω
        w = 3000 * math.pi / 30
        self.assertAlmostEqual(e.wot.curve[3000], 180 - 10 - 0.02 * w, places=9)
        # la curva NA del adaptador anterior (net_curve) no cambia
        self.assertEqual(dict(e.net_curve)[3000], e.wot.curve[3000])
        self.assertEqual(e.table_rpms, [0, 1000, 2000, 3000, 4000, 5000, 6000])

    def test_defaults_are_recorded_not_invented(self) -> None:
        e = read_combustion_engine(_engine({"inertia": None, "friction": None}))
        self.assertEqual(e.inertia, 0.1)
        self.assertIn("inertia", e.defaults_used)
        self.assertIn("1717", e.defaults_used["inertia"][1])
        self.assertEqual(e.defaults_used["friction"][0], 0.0)

    def test_missing_required_data_fails(self) -> None:
        for key in ("idleRPM", "maxRPM", "torque"):
            with self.subTest(key=key), self.assertRaises(CombustionEngineError):
                read_combustion_engine(_engine({key: None}))

    def test_invalid_data_fails(self) -> None:
        cases = {
            "fila no numérica": {"torque": [["rpm", "torque"], [0, 0], [1000, "x"]]},
            "un solo punto": {"torque": [["rpm", "torque"], [1000, 100]]},
            "sin columna torque": {"torque": [["rpm", "par"], [0, 0], [1000, 1]]},
            "idle no numérico": {"idleRPM": "mucho"},
            "rpm repetida": {
                "torque": [["rpm", "torque"], [0, 0], [1000, 1], [1000, 2], [2000, 3]]
            },
        }
        for name, extra in cases.items():
            with self.subTest(name), self.assertRaises(CombustionEngineError):
                read_combustion_engine(_engine(extra))

    def test_duplicate_points_are_an_explicit_error(self) -> None:
        with self.assertRaises(CombustionEngineError):
            create_curve([(0, 0), (1000, 1), (1000, 2)])

    def test_duplicate_or_missing_engine(self) -> None:
        two = [
            ["type", "name", "inputName", "inputIndex"],
            ["combustionEngine", "a", "dummy", 0],
            ["combustionEngine", "b", "dummy", 0],
        ]
        with self.assertRaises(CombustionEngineError):
            find_combustion_engine(_engine(rows=two))
        none = [["type", "name", "inputName", "inputIndex"], ["shaft", "s", "x", 1]]
        with self.assertRaises(CombustionEngineError):
            find_combustion_engine(_engine(rows=none))

    def test_unknown_limiter_type_keeps_rev_limiter_rpm(self) -> None:
        # combustionEngine.lua l. 1968-1988: maxRPM ya vale revLimiterRPM cuando el
        # tipo desconocido desactiva el limitador (antes el adaptador lo deshacía)
        e = read_combustion_engine(
            _engine({"revLimiterType": "magia", "revLimiterRPM": 5500})
        )
        self.assertFalse(e.has_rev_limiter)
        self.assertEqual(e.max_rpm, 5500)

    def test_has_rev_limiter_uses_lua_truthiness(self) -> None:
        on = read_combustion_engine(
            _engine({"hasRevLimiter": 0, "revLimiterRPM": 5000})
        )
        self.assertTrue(on.has_rev_limiter)  # en Lua 0 es verdadero
        self.assertEqual(on.max_rpm, 5000)
        off = read_combustion_engine(
            _engine({"hasRevLimiter": False, "revLimiterRPM": 5000})
        )
        self.assertFalse(off.has_rev_limiter)
        self.assertEqual(off.max_rpm, 6000)

    def test_turbo_estimate_matches_lua_formula(self) -> None:
        e = read_combustion_engine(_engine(turbo=dict(TURBO)))
        w = e.wot
        self.assertEqual(w.kind, "turbo_estimate")
        self.assertFalse(e.supported)  # la curva NA ya no es la de plena carga
        t = w.turbo
        # coeficiente a 4000 rpm recalculado a mano (getTorqueCoefs l. 536-545)
        av = math.sqrt(
            (0.9 * 4000 * TURBO_RPM_TO_AV / (6000 * TURBO_RPM_TO_AV) * 1.0 * 40000 - 16)
            / 0.0001
        )
        av = min(av, 200000 * TURBO_RPM_TO_AV)
        p = min(t.pressure_curve[math.floor(av * TURBO_AV_TO_RPM)], 11.0)
        self.assertAlmostEqual(t.estimated_wastegate_limit_psi, 11.0, places=6)
        # eficiencia: la spline Catmull-Rom de engineDef (no 1,0 exacto a 4000 rpm)
        eff = create_curve(
            [(0, 0.0), (1000, 0.0), (2000, 0.5), (3000, 1.0), (6000, 1.0)]
        )
        coef = 1 + BOOST_PER_PASCAL * p * PSI_TO_PASCAL * eff[4000]
        self.assertAlmostEqual(t.coef(4000), coef, places=12)
        na = dict(e.net_curve)[4000]
        self.assertAlmostEqual(
            w.curve[4000],
            (na + 10 + 0.02 * 4000 * math.pi / 30) * coef
            - 10
            - 0.02 * 4000 * math.pi / 30,
            places=9,
        )
        # en un nudo con eficiencia 0 el turbo no añade nada; entre nudos la spline
        # puede sobrepasar (Catmull-Rom, igual que el juego) y el coeficiente no es 1
        self.assertEqual(t.coef(1000), 1.0)
        self.assertNotEqual(t.coef(500), 1.0)
        self.assertIsNotNone(w.display_peak_torque)
        self.assertLess(w.display_max_abs_delta, 5.0)

    def test_turbo_out_of_range_or_invalid_is_blocked(self) -> None:
        cases = {
            "presión fuera de tabla": dict(
                TURBO, pressurePSI=[[0, 0], [5, 1]], maxExhaustPower=1e9
            ),
            "sin engineDef": {k: v for k, v in TURBO.items() if k != "engineDef"},
            "válvula por marcha": dict(TURBO, wastegateStart={"1": 5, "2": 8}),
            "fila no numérica": dict(TURBO, pressurePSI=[[0, 0], [100, "x"]]),
            "contrapresión 0": dict(TURBO, backPressureCoef=0),
        }
        for name, turbo in cases.items():
            with self.subTest(name):
                e = read_combustion_engine(_engine(turbo=turbo))
                self.assertIsNone(e.wot)
                self.assertTrue(e.wot_blockers)
                self.assertTrue(e.wot_blockers[0].startswith("turbocharger:"))

    def test_supercharger_blocks_the_wot_curve(self) -> None:
        r = _engine({"supercharger": "sc"})
        r.active.append(_active("sc", "root", {"sc": {"x": 1}}))
        e = read_combustion_engine(r)
        self.assertIsNone(e.wot)
        self.assertIn("supercharger", e.wot_blockers[0])


def _ir(engine) -> VehicleIR:
    ir = VehicleIR()
    pv = SimpleNamespace(combustion_engine=engine, resolved=None)

    def put(path, value, unit, kind, *refs, note="", key=""):
        ir.set(path, Param.of(value, unit, kind, list(refs), note, key))

    _import_combustion_engine(ir, pv, put)
    return ir


class TestImporterSynthetic(unittest.TestCase):
    def test_na_is_computed(self) -> None:
        ir = _ir(read_combustion_engine(_engine()))
        e = ir.engine
        self.assertEqual(e.device_type.value, "combustionEngine")
        self.assertEqual(e.idle_speed.kind, "extracted")
        self.assertEqual(e.wot_torque_curve.kind, "computed")
        self.assertEqual(e.peak_torque.kind, "computed")
        self.assertIn("−2,7", e.peak_torque.note)  # discrepancia abierta, declarada
        self.assertFalse(e.forced_induction.value)
        rpms = [r for r, _ in e.wot_torque_curve.value]
        # G2.10 (D-G29b): nudos de la tabla + rpm añadidas (interpolación ≤ 0,1 N·m)
        self.assertTrue({0, 1000, 2000, 3000, 4000, 5000, 6000} <= set(rpms))
        # G2.10 (D-G29c): el freno motor ya se calcula (tests/test_g210_…); sin
        # transmisión sintética, la inercia de los hijos sigue ausente con motivo
        for p in (e.clutch_additional_inertia,):
            self.assertEqual(p.status, "missing")
            self.assertIsNone(p.value)
            self.assertTrue(p.reason)

    def test_turbo_is_estimated(self) -> None:
        ir = _ir(read_combustion_engine(_engine(turbo=dict(TURBO))))
        e = ir.engine
        self.assertTrue(e.forced_induction.value)
        for p in (e.wot_torque_curve, e.peak_torque, e.peak_power):
            self.assertEqual((p.status, p.kind), ("present", "estimated"))
        self.assertIn("ESTIMACIÓN", e.wot_torque_curve.note)

    def test_lua_default_is_computed_with_line(self) -> None:
        ir = _ir(read_combustion_engine(_engine({"inertia": None})))
        p = ir.engine.inertia
        self.assertEqual((p.value, p.kind), (0.1, "computed"))
        self.assertIn("combustionEngine.lua:1717", p.note)

    def test_blocked_curve_is_missing_not_zero(self) -> None:
        r = _engine({"supercharger": "sc"})
        r.active.append(_active("sc", "root", {"sc": {"x": 1}}))
        ir = _ir(read_combustion_engine(r))
        for path in (
            "engine.wot_torque_curve",
            "engine.peak_torque",
            "engine.peak_torque_speed",
            "engine.peak_power",
            "engine.peak_power_speed",
        ):
            p = ir.get(path)
            self.assertEqual(p.status, "missing")
            self.assertIsNone(p.value)
            self.assertIn("supercharger", p.reason)
        self.assertTrue(any("supercharger" in u.reason for u in ir.unknown))
        # lo que sí se conoce sigue presente
        self.assertTrue(ir.engine.idle_speed.present)


class TestInfoValidationNonNumeric(unittest.TestCase):
    """validations_bng: rpm de pico declarada como texto (info oficial de BeamNG)."""

    def _facts(self, trpm, prpm):
        from src.attrs.facts_bng import Fact

        vals = {
            "ir.declared.torque": (170, "N·m"),
            "ir.declared.power": (123, "hp"),
            "ir.declared.torque_peak_speed": (trpm, "rpm"),
            "ir.declared.power_peak_speed": (prpm, "rpm"),
            "ir.engine.peak_torque": (170.2, "N·m"),
            # potencia en 6000 rpm de la curva: 145,8 · 6000 · π/30 W (122,85 hp)
            "ir.engine.peak_power": (145.8 * 6000 * math.pi / 30, "W"),
            "ir.engine.wot_torque_curve": (
                [[0, 0], [4000, 170.2], [6000, 145.8]],
                "table:rpm->N·m",
            ),
        }
        return {k: Fact(k, v, u, "computed", [], "") for k, (v, u) in vals.items()}

    def test_text_rpm_is_inconclusive_not_crash(self) -> None:
        from src.attrs.validations_bng import info_validations

        (v,) = info_validations("x", "y", self._facts("3850 - 5150", "5600 - 6300"))
        self.assertEqual(v.result, "inconclusive")
        self.assertIn("no comparada", v.note)
        self.assertEqual(v.expected["torque_rpm"], "3850 - 5150")

    def test_numeric_rpm_unchanged(self) -> None:
        from src.attrs.validations_bng import info_validations

        (v,) = info_validations("x", "y", self._facts(4000, 6000))
        self.assertEqual(v.result, "pass")
        self.assertNotIn("no comparada", v.note)

    def test_text_torque_is_not_compared(self) -> None:
        from src.attrs.validations_bng import info_validations

        f = self._facts(4000, 6000)
        f["ir.declared.torque"].value = "170 Nm"
        self.assertEqual(info_validations("x", "y", f), [])


class TestPreviewDispatch(unittest.TestCase):
    def test_dir_reader_stays_inside_root(self) -> None:
        from src.pipeline.physics_preview import dir_resource_reader

        with tempfile.TemporaryDirectory() as tmp:
            vd = Path(tmp) / "mod" / "vehicles" / "car"
            vd.mkdir(parents=True)
            (vd / "t.csv").write_bytes(b"rpm,torque\n0,0\n")
            (Path(tmp) / "secreto.txt").write_text("x", "utf-8")
            read = dir_resource_reader(vd)
            self.assertEqual(read("vehicles/car/t.csv"), b"rpm,torque\n0,0\n")
            for bad in ("vehicles/car/no.csv", "../secreto.txt"):
                with self.subTest(bad), self.assertRaises(KeyError):
                    read(bad)
            self.assertIsNone(dir_resource_reader(Path(tmp) / "suelto"))


@unittest.skipUnless(
    all((SAMPLES / n / "vehicles" / n).is_dir() for n in V200409),
    "samples_beamng (200409) no disponible",
)
class TestSamples200409(unittest.TestCase):
    """Los 7 del exportador 200409: el adaptador se elige por el dispositivo."""

    @classmethod
    def setUpClass(cls) -> None:
        from src.attrs.report import resolve_vehicle
        from src.attrs.store import REPO_CATALOG, Catalog
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        cat = Catalog.load(REPO_CATALOG)
        cls.data = {}
        for n in V200409:
            vd = SAMPLES / n / "vehicles" / n
            pv = preview_physics(load_vehicle_dir(vd, f"{n}.pc"), 70.0)
            ir = import_preview(pv, 70.0)
            _, results = resolve_vehicle(pv, cat, "ac_physics", 70.0)
            cls.data[n] = (pv, ir, {r.target: r.to_dict() for r in results})

    def test_engine_step_uses_combustion_adapter(self) -> None:
        for n, (pv, ir, _) in self.data.items():
            with self.subTest(n):
                self.assertEqual(
                    pv.stages["engine_model"],
                    {"ok": True, "adapter": "combustionEngine"},
                )
                self.assertIsNone(pv.engine)  # sin borrador AC de camsoEngine
                self.assertEqual(ir.engine.device_type.value, "combustionEngine")
                self.assertEqual(ir.formats.exporter_version.value, "200409")
                self.assertEqual(ir.engine.wot_torque_curve.kind, "estimated")
                self.assertNotIn("engine.ini", pv.rendered)
                self.assertNotIn("power.lut", pv.rendered)

    def test_pinned_values(self) -> None:
        # Tres motores distintos, compartidos por parejas (G2.9 §8)
        expected = {
            "2020_rpm_atlanta_gt_3_6t": (645.5273, 6500, 3424, 542.163, 6499, 394.49),
            "2020_rpm_legion_grand_touring": (603.3, 6500, 3629, 772.88, 6499, 566.96),
            "2020_rpm_prothean_s": (707.5, 7000, 3429, 504.89, 6999, 375.63),
        }
        for n, (idle, ceil, tr, t, pr, hp) in expected.items():
            e = self.data[n][1].engine
            with self.subTest(n):
                self.assertAlmostEqual(e.idle_speed.value, idle, delta=0.05)
                self.assertEqual(e.speed_ceiling.value, ceil)
                self.assertEqual(e.peak_torque_speed.value, tr)
                self.assertAlmostEqual(e.peak_torque.value, t, delta=0.01)
                self.assertEqual(e.peak_power_speed.value, pr)
                self.assertAlmostEqual(e.peak_power.value / 745.69987158227, hp, 2)

    def test_no_camso_rule_applies(self) -> None:
        targets = (
            "ac:power.lut",
            "ac:engine.ini/ENGINE_DATA/INERTIA",
            "ac:engine.ini/ENGINE_DATA/LIMITER",
            "ac:engine.ini/ENGINE_DATA/MINIMUM",
        )
        # G2.10: estos destinos los resuelven ahora las reglas combustion (lista
        # cerrada, tests/test_g210_combustion_decisions.py); ninguna camso aplica
        g210 = {
            "eng.power_lut_combustion@1",
            "eng.inertia_combustion@1",
            "eng.limiter_combustion@2",
            "eng.minimum_combustion@2",
        }
        for n, (_, _, res) in self.data.items():
            for t in targets:
                with self.subTest(n=n, t=t):
                    self.assertIn(res[t].get("rule"), g210)
                    self.assertNotIn("camso", res[t].get("rule"))
                    self.assertTrue(
                        any(
                            "combustionEngine" in str(x.get("reason"))
                            for x in res[t]["rejected"]
                        )
                    )

    def test_exporter_version_does_not_select_the_adapter(self) -> None:
        from src.ir.legacy_facts import ir_facts

        pv, ir, _ = self.data["2020_rpm_prothean_s"]
        before = {k: v.to_dict() for k, v in ir_facts(ir).items() if ".engine." in k}
        ir.formats.exporter_version = Param.of("999999", "text", "extracted")
        after = {k: v.to_dict() for k, v in ir_facts(ir).items() if ".engine." in k}
        self.assertEqual(before, after)


@unittest.skipUnless(IBIZA.is_dir(), "Ibiza no disponible")
class TestCamsoUnchanged(unittest.TestCase):
    def test_ibiza_still_uses_camso(self) -> None:
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        pv = preview_physics(load_vehicle_dir(IBIZA, "35b84.pc"), 70.0)
        self.assertEqual(pv.stages["engine_model"], {"ok": True})
        self.assertIsNone(pv.combustion_engine)
        self.assertIn("power.lut", pv.rendered)
        ir = import_preview(pv, 70.0)
        self.assertEqual(ir.engine.device_type.value, "camsoEngine")
        self.assertEqual(ir.engine.wot_torque_curve.kind, "computed")


if __name__ == "__main__":
    unittest.main()
