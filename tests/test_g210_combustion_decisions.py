"""G2.10: decisiones D-G29a (MINIMUM/LIMITER/INERTIA), D-G29b (power.lut) y D-G29c
(freno motor) para ``combustionEngine`` del exportador 200409.

Casos sintéticos (IR construido a mano, sin el juego) y, si están, las 7 exportaciones
200409 de ``samples_beamng/`` y un ``camsoEngine`` 40340 (solo lectura). Informe:
``docs/research/g210_d_g29_decisions_2026-11-02.md``.
"""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.bench.service import default_beamng_root
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract.engine_combustion import (
    CombustionEngineError,
    read_combustion_engine,
)
from src.importers.beamng import (
    CLUTCHLIKE_ADDITIONAL_INERTIA,
    WOT_KNOT_TOLERANCE_NM,
    _combustion_clutch_additional_inertia,
    _import_combustion_engine,
    refine_knots,
)
from src.ir.legacy_facts import ir_to_facts
from src.ir.param import Param
from src.ir.vehicle import VehicleIR

from tests.test_g29_combustion_engine import TURBO, V200409, _engine

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
EVID = ROOT / "output/g210_d_g29_decisions_2026-11-02"
MINIMUM = "ac:engine.ini/ENGINE_DATA/MINIMUM"
LIMITER = "ac:engine.ini/ENGINE_DATA/LIMITER"
INERTIA = "ac:engine.ini/ENGINE_DATA/INERTIA"
POWER_LUT = "ac:power.lut"
COAST = (
    "ac:engine.ini/COAST_REF/RPM",
    "ac:engine.ini/COAST_REF/TORQUE",
    "ac:engine.ini/COAST_REF/NON_LINEARITY",
)
NEW_RULES = {
    MINIMUM: "eng.minimum_combustion@2",
    LIMITER: "eng.limiter_combustion@2",
    INERTIA: "eng.inertia_combustion@1",
    POWER_LUT: "eng.power_lut_combustion@1",
}
CATALOG = Catalog.load(REPO_CATALOG)


def _synthetic_ir(
    turbo: bool = True,
    exporter: str | None = "200409",
    device: str | None = None,
    extra: dict | None = None,
    clutch_extra: float | None = 0.0,
) -> VehicleIR:
    """IR de un ``combustionEngine`` sintético con la procedencia de Automation."""
    engine = read_combustion_engine(
        _engine(extra, turbo=dict(TURBO) if turbo else None)
    )
    ir = VehicleIR()
    pv = SimpleNamespace(combustion_engine=engine, resolved=None)

    def put(path, value, unit, kind, *refs, note="", key=""):
        ir.set(path, Param.of(value, unit, kind, list(refs), note, key))

    _import_combustion_engine(ir, pv, put)
    ir.set("identity.source_tool", Param.of("Automation", "text", "extracted", []))
    ir.set(
        "identity.author", Param.of("Camshaft Software LTD", "text", "extracted", [])
    )
    if exporter is not None:
        ir.set("formats.exporter_version", Param.of(exporter, "text", "extracted", []))
    if device is not None:
        ir.set("engine.device_type", Param.of(device, "enum", "extracted", []))
    if clutch_extra is not None:
        # sin transmisión sintética: se fija la suma de los hijos (la calcula el
        # importador con la configuración real; ver TestClutchInertia)
        ir.set(
            "engine.clutch_additional_inertia",
            Param.of(clutch_extra, "kg·m²", "computed", []),
        )
    return ir


def _resolve(ir: VehicleIR, targets=(MINIMUM, LIMITER, INERTIA, POWER_LUT)) -> dict:
    ctx = context_from_facts("synthetic", "v", ir_to_facts(ir), CATALOG)
    eng = ResolutionEngine(CATALOG, ctx)
    return {t: eng.resolve(t) for t in targets}


def _rejected_reason(res, rule: str) -> str:
    for x in res.to_dict().get("rejected", []):
        if x.get("rule") == rule:
            return str(x.get("reasons") or x.get("reason"))
    return ""


# --- D-G29a ------------------------------------------------------------------------


class TestRulesShape(unittest.TestCase):
    def test_conditions_are_closed_lists(self) -> None:
        for key in [
            "eng.minimum_combustion@1",
            "eng.limiter_combustion@1",
            *NEW_RULES.values(),
        ]:
            with self.subTest(rule=key):
                r = CATALOG.rules[key]
                cond = {(c.fact, c.op): c.value for c in r.conditions}
                self.assertEqual(
                    cond[("ir.engine.device_type", "eq")], "combustionEngine"
                )
                self.assertEqual(
                    cond[("ir.formats.exporter_version", "in")], ["200409"]
                )
                self.assertEqual(
                    (r.scope.level, r.scope.family), ("family", "automation_export")
                )
                # no se reutilizan reglas camso
                self.assertNotIn("camso", r.supersedes)
                self.assertFalse(any("camso" in x for x in r.incompatible_with))

    def test_versions_and_inputs(self) -> None:
        r = CATALOG.rules
        self.assertEqual(
            r["eng.minimum_combustion@2"].supersedes, "eng.minimum_combustion@1"
        )
        self.assertEqual(
            r["eng.limiter_combustion@2"].supersedes, "eng.limiter_combustion@1"
        )
        self.assertEqual(r["eng.minimum_combustion@2"].open_issues, [])
        self.assertTrue(r["eng.minimum_combustion@1"].open_issues)  # @1 no se reescribe
        self.assertEqual(
            r["eng.minimum_combustion@2"].inputs, {"x": "ir.engine.idle_speed"}
        )
        self.assertEqual(
            r["eng.limiter_combustion@2"].inputs, {"x": "ir.engine.speed_ceiling"}
        )
        self.assertEqual(
            r["eng.inertia_combustion@1"].inputs, {"x": "ir.engine.inertia"}
        )
        for key in (
            "eng.minimum_combustion@2",
            "eng.limiter_combustion@2",
            "eng.inertia_combustion@1",
        ):
            self.assertEqual(r[key].status, "provisional")
            self.assertEqual(r[key].transform.id, "identity")


class TestPhaseASynthetic(unittest.TestCase):
    def test_applies_to_200409_combustion(self) -> None:
        ir = _synthetic_ir()
        res = _resolve(ir)
        self.assertEqual(res[MINIMUM].value, 800)
        self.assertEqual(res[LIMITER].value, 6000)
        self.assertEqual(res[INERTIA].value, 0.15)
        for t in (MINIMUM, LIMITER, INERTIA):
            with self.subTest(target=t):
                self.assertEqual(res[t].status, "inferred")
                self.assertEqual(res[t].rule, NEW_RULES[t])

    def test_unit_is_kept(self) -> None:
        res = _resolve(_synthetic_ir())
        self.assertEqual(res[MINIMUM].unit, "rpm")
        self.assertEqual(res[LIMITER].unit, "rpm")
        self.assertEqual(res[INERTIA].unit, "kg·m²")

    def test_unsupported_exporter_versions(self) -> None:
        for exporter in ("40340", "38022", "200410", None):
            with self.subTest(exporter=exporter):
                res = _resolve(_synthetic_ir(exporter=exporter))
                for t in (MINIMUM, LIMITER, INERTIA, POWER_LUT):
                    self.assertEqual(res[t].status, "manual_required")
                    self.assertIsNone(res[t].value)

    def test_wrong_device_type(self) -> None:
        for device in ("camsoEngine", "electricMotor"):
            with self.subTest(device=device):
                res = _resolve(_synthetic_ir(device=device))
                for t in (MINIMUM, LIMITER, INERTIA, POWER_LUT):
                    self.assertNotIn("combustion", res[t].rule or "")
                    self.assertIn("device_type", _rejected_reason(res[t], NEW_RULES[t]))

    def test_limiter_requires_active_limiter(self) -> None:
        for extra in ({"hasRevLimiter": False}, {"revLimiterType": "desconocido"}):
            with self.subTest(extra=extra):
                ir = _synthetic_ir(extra=extra)
                self.assertEqual(ir.engine.rev_limiter_mode.value, "sin_limitador")
                res = _resolve(ir)
                self.assertEqual(res[LIMITER].status, "manual_required")
                self.assertIsNone(res[LIMITER].value)
                self.assertEqual(res[MINIMUM].status, "inferred")

    def test_inertia_requires_known_zero_child_inertia(self) -> None:
        for extra in (None, 0.02):
            with self.subTest(clutch_extra=extra):
                ir = _synthetic_ir(clutch_extra=extra)
                if extra is None:
                    self.assertEqual(
                        ir.engine.clutch_additional_inertia.status, "missing"
                    )
                res = _resolve(ir)
                self.assertEqual(res[INERTIA].status, "manual_required")
                self.assertIsNone(res[INERTIA].value)

    def test_missing_idle_is_not_zero(self) -> None:
        ir = _synthetic_ir()
        ir.set("engine.idle_speed", Param.missing("rpm", "prueba: sin idleRPM"))
        res = _resolve(ir)
        self.assertEqual(res[MINIMUM].status, "manual_required")
        self.assertIsNone(res[MINIMUM].value)


def _pv_with_children(children: list[tuple[str, str, dict]]):
    rows = [["type", "name", "inputName", "inputIndex"]]
    rows.append(["combustionEngine", "mainEngine", "dummy", 0])
    data: dict = {}
    for typ, name, params in children:
        rows.append([typ, name, "mainEngine", 1])
        if params:
            data[name] = params
    resolved = _engine(rows=rows)
    for depth, active in enumerate(resolved.active):
        active.depth = depth  # lo pide src.jbeam.merge (ActivePart.depth)
    for k, v in data.items():
        resolved.active[0].part.data[k] = v
    return SimpleNamespace(resolved=resolved)


class TestClutchInertia(unittest.TestCase):
    def test_known_clutchlike_children(self) -> None:
        for typ in ("dctGearbox", "torqueConverter", "frictionClutch"):
            with self.subTest(child=typ):
                v, why, kids = _combustion_clutch_additional_inertia(
                    _pv_with_children([(typ, "c", {})]), "mainEngine"
                )
                self.assertEqual((v, why), (0.0, ""))
                self.assertIn(typ, kids[0])
        v, _, _ = _combustion_clutch_additional_inertia(
            _pv_with_children([("dctGearbox", "c", {"additionalEngineInertia": 0.02})]),
            "mainEngine",
        )
        self.assertEqual(v, 0.02)

    def test_unknown_or_invalid_child(self) -> None:
        v, why, _ = _combustion_clutch_additional_inertia(
            _pv_with_children([("cvtGearbox", "c", {})]), "mainEngine"
        )
        self.assertIsNone(v)
        self.assertIn("cvtGearbox", why)
        v, why, _ = _combustion_clutch_additional_inertia(
            _pv_with_children([("dctGearbox", "c", {"additionalEngineInertia": "$x"})]),
            "mainEngine",
        )
        self.assertIsNone(v)
        self.assertIn("no numérico", why)

    def test_table_matches_installed_lua(self) -> None:
        lua = Path(
            (default_beamng_root() / "lua/vehicle/powertrain").as_posix()
        )
        if not lua.exists():
            self.skipTest("BeamNG no instalado")
        for typ, where in CLUTCHLIKE_ADDITIONAL_INERTIA.items():
            text = (lua / f"{typ}.lua").read_text("utf-8")
            self.assertIn("clutchlike = true", text)
            if where is None:
                self.assertNotIn("additionalEngineInertia", text)
            else:
                line = int(where.split("l. ")[1].split(":")[0])
                self.assertIn(
                    "additionalEngineInertia = jbeamData.additionalEngineInertia or 0",
                    text.splitlines()[line - 1],
                )


# --- D-G29b ------------------------------------------------------------------------


class TestRefineKnots(unittest.TestCase):
    def test_bound_and_kept_knots(self) -> None:
        curve = {k: 100 + 50 * (k / 1000) ** 2 for k in range(0, 6001)}
        knots = [0, 3000, 6000]
        out = refine_knots(curve, knots, 0.1)
        self.assertTrue(set(knots) <= set(out))
        for a, b in zip(out, out[1:]):
            for k in range(a, b + 1):
                y = curve[a] + (curve[b] - curve[a]) * (k - a) / (b - a)
                self.assertLessEqual(abs(y - curve[k]), 0.1 + 1e-12)

    def test_linear_curve_adds_nothing(self) -> None:
        curve = {k: 2.0 * k for k in range(0, 1001)}
        self.assertEqual(refine_knots(curve, [0, 1000], 0.1), [0, 1000])

    def test_knot_outside_curve(self) -> None:
        with self.assertRaises(ValueError):
            refine_knots({0: 1.0}, [0, 5], 0.1)

    def test_ir_curve_keeps_table_knots(self) -> None:
        ir = _synthetic_ir()
        rpms = [r for r, _ in ir.engine.wot_torque_curve.value]
        self.assertTrue({0, 1000, 2000, 3000, 4000, 5000, 6000} <= set(rpms))
        self.assertEqual(rpms, sorted(set(rpms)))
        self.assertIn(
            f"≤ {WOT_KNOT_TOLERANCE_NM:g} N·m", ir.engine.wot_torque_curve.note
        )


class TestPowerLut(unittest.TestCase):
    def test_rule(self) -> None:
        r = CATALOG.rules["eng.power_lut_combustion@1"]
        self.assertEqual(r.status, "validated")
        self.assertEqual(r.inputs, {"x": "ir.engine.wot_torque_curve"})
        cond = {(c.fact, c.op): c.value for c in r.conditions}
        self.assertIs(cond[("ir.engine.forced_induction", "eq")], True)
        self.assertEqual(len(r.validations), 7)
        for vid in r.validations:
            v = CATALOG.validations[vid]
            self.assertEqual((v.method, v.result), ("in_game_value", "pass"))
            self.assertEqual(v.covers, [POWER_LUT])
            self.assertIn(v.vehicle, V200409)

    def test_turbo_estimate_applies(self) -> None:
        ir = _synthetic_ir()
        self.assertEqual(ir.engine.wot_torque_curve.kind, "estimated")  # no cambia
        res = _resolve(ir)[POWER_LUT]
        self.assertEqual(res.rule, "eng.power_lut_combustion@1")
        self.assertEqual(res.value, ir.engine.wot_torque_curve.value)

    def test_na_is_not_covered(self) -> None:
        res = _resolve(_synthetic_ir(turbo=False))[POWER_LUT]
        self.assertEqual(res.status, "manual_required")
        self.assertIn("forced_induction", _rejected_reason(res, NEW_RULES[POWER_LUT]))

    def test_blocked_curve_stays_missing(self) -> None:
        # válvula por marcha: la curva queda ausente (G2.9) y la regla no aplica
        turbo = dict(TURBO, wastegateStart=[10, 11])
        engine = read_combustion_engine(_engine(turbo=turbo))
        self.assertIsNone(engine.wot)
        ir = _synthetic_ir()
        ir.set(
            "engine.wot_torque_curve",
            Param.missing("table:rpm->N·m", "; ".join(engine.wot_blockers)),
        )
        res = _resolve(ir)[POWER_LUT]
        self.assertEqual(res.status, "manual_required")
        self.assertIsNone(res.value)

    def test_measurement_evidence(self) -> None:
        f = EVID / "medicion_beamng/medicion.json"
        if not f.exists():
            self.skipTest("sin la evidencia de la medición")
        data = json.loads(f.read_text("utf-8"))
        self.assertEqual(sorted(data), sorted(V200409))
        for name, d in data.items():
            with self.subTest(vehicle=name):
                s = d["summary"]
                self.assertLess(s["max_abs_delta_norm_Nm"], 1e-3)
                self.assertLess(d["port_vs_game"]["max_abs_delta_Nm"], 1e-6)
                self.assertEqual(s["min_throttle"], 1)
                self.assertLessEqual(
                    s["max_abs_delta_lut_Nm"], WOT_KNOT_TOLERANCE_NM + 0.01
                )


# --- D-G29c ------------------------------------------------------------------------


class TestCoastSynthetic(unittest.TestCase):
    def test_default_engine_brake(self) -> None:
        e = read_combustion_engine(_engine())
        # engineBrakeTorque ausente: friction·2 (l. 1858);
        # idleRPMRoughness ausente: 50 (l. 1705)
        self.assertEqual(e.defaults_used["engineBrakeTorque"][0], 20.0)
        self.assertIn("1858", e.defaults_used["engineBrakeTorque"][1])
        self.assertEqual(e.defaults_used["idleRPMRoughness"][0], 50.0)
        self.assertEqual(e.coast_blockers, [])
        # desde idleRPM + 25 = 825 rpm: ese límite, nudos de la tabla y techo
        self.assertEqual(
            [r for r, _ in e.coast], [825, 1000, 2000, 3000, 4000, 5000, 6000]
        )
        for r, t in e.coast:
            self.assertAlmostEqual(t, 10 + 20 + 0.02 * r * math.pi / 30)

    def test_declared_engine_brake(self) -> None:
        e = read_combustion_engine(_engine({"engineBrakeTorque": 5}))
        self.assertNotIn("engineBrakeTorque", e.defaults_used)
        self.assertEqual(e.coast[0][0], 825)
        self.assertAlmostEqual(e.coast[0][1], 10 + 5 + 0.02 * 825 * math.pi / 30)

    def test_invalid_engine_brake(self) -> None:
        with self.assertRaises(CombustionEngineError):
            read_combustion_engine(_engine({"engineBrakeTorque": "mucho"}))

    def test_controllers_block(self) -> None:
        for ctrl in ("powertrainControl/antiLag", "braking/compressionBrake"):
            with self.subTest(controller=ctrl):
                resolved = _engine()
                resolved.active[0].part.data["controller"] = [
                    ["fileName"],
                    [ctrl, {}],
                ]
                e = read_combustion_engine(resolved)
                self.assertEqual(e.coast, [])
                self.assertIn(ctrl, e.coast_blockers[0])
                ir = VehicleIR()
                pv = SimpleNamespace(combustion_engine=e, resolved=None)
                _import_combustion_engine(
                    ir,
                    pv,
                    lambda path, v, u, k, *refs, note="", key="": ir.set(
                        path, Param.of(v, u, k, list(refs), note, key)
                    ),
                )
                self.assertEqual(ir.engine.coast_curve.status, "missing")
                self.assertIsNone(ir.engine.coast_curve.value)
                self.assertIn(ctrl, ir.engine.coast_curve.reason)

    def test_importer(self) -> None:
        ir = _synthetic_ir()
        p = ir.engine.coast_curve
        self.assertEqual(
            (p.status, p.kind, p.unit), ("present", "computed", "table:rpm->N·m")
        )
        self.assertTrue(all(t > 0 for _, t in p.value))  # par > 0 = freno
        self.assertIn("engineBrakeTorque", ir.engine.coast_basis.value)

    def test_coast_ref_stays_manual(self) -> None:
        # G2.24: el estado de G2.10 (sin regla) se evalúa sobre el catálogo SIN las
        # reglas eng.coast_*_combustion que G2.24 añadió con la decisión del usuario
        # (precedente G2.5d/G2.23); el estado nuevo, en test_coast_ref_g224
        cat = Catalog.load(REPO_CATALOG)
        for key in [
            k for k in cat.rules if k.startswith("eng.coast_") and "combustion" in k
        ]:
            cat.rules.pop(key)
        ctx = context_from_facts("synthetic", "v", ir_to_facts(_synthetic_ir()), cat)
        eng = ResolutionEngine(cat, ctx)
        res = {t: eng.resolve(t) for t in COAST}
        for t in COAST:
            with self.subTest(target=t):
                self.assertEqual(res[t].status, "manual_required")
                self.assertIsNone(res[t].value)
        for rule in (
            "eng.coast_rpm@2",
            "eng.coast_torque@2",
            "eng.coast_nonlinearity@2",
        ):
            self.assertEqual(
                [
                    c.value
                    for c in CATALOG.rules[rule].conditions
                    if c.fact == "ir.engine.device_type"
                ],
                ["camsoEngine"],
            )
        self.assertFalse(
            [k for k, r in cat.rules.items() if r.target in COAST and "combustion" in k]
        )

    def test_coast_ref_g224(self) -> None:
        """G2.24 (decisión del usuario): anclado en el techo con la regla combustion."""
        res = _resolve(_synthetic_ir(), COAST)
        for t in COAST:
            with self.subTest(target=t):
                self.assertEqual(res[t].status, "inferred")
                self.assertIn("_combustion@2", res[t].rule)
        self.assertEqual(res["ac:engine.ini/COAST_REF/NON_LINEARITY"].value, 0.0)

    def test_measurement_evidence(self) -> None:
        f = EVID / "medicion_beamng/freno.json"
        if not f.exists():
            self.skipTest("sin la evidencia de la medición")
        data = json.loads(f.read_text("utf-8"))
        self.assertEqual(sorted(data), sorted(V200409))
        for name, d in data.items():
            with self.subTest(vehicle=name):
                self.assertLess(d["max_abs_delta_port_Nm"], 1e-3)
                self.assertEqual(d["max_throttle"], 0)
                self.assertEqual(d["max_idleThrottle"], 0)
                # AC (AC) da 0 en MINIMUM; BeamNG frena > 50 N·m en el ralentí
                self.assertGreater(d["bng_brake_at_minimum_Nm"], 50)


# --- vehículos reales ---------------------------------------------------------------


def _real(name: str):
    from src.attrs.report import resolve_vehicle
    from src.importers.beamng import import_preview
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    top = SAMPLES / name / "vehicles" / name
    if not top.exists():
        alt = list(SAMPLES.glob(f"*/vehicles/{name}"))
        if not alt:
            return None
        top = alt[0]
    pv = preview_physics(
        load_vehicle_dir(top, sorted(p.name for p in top.glob("*.pc"))[0]), 70.0
    )
    ctx, results = resolve_vehicle(pv, CATALOG, "ac_physics", 70.0)
    return pv, import_preview(pv, 70.0), {r.target: r for r in results}


# idle, techo, inercia y nudos de la tabla (comprobados en G2.9 §9)
EXPECTED = {
    "2020_rpm_atlanta_gt_3_6t": (645.52730529858, 6500, 0.18525399113767),
    "2020_rpm_legion_grand_touring": (603.0, 6500, None),
    "2020_rpm_prothean_s": (707.48193097251, 7000, 0.17787748050336),
}


class TestReal200409(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SAMPLES.exists():
            raise unittest.SkipTest("sin samples_beamng/")
        cls.data = {n: _real(n) for n in V200409}

    def test_phase_a_values(self) -> None:
        for name, (idle, ceil, inertia) in EXPECTED.items():
            with self.subTest(vehicle=name):
                _, ir, res = self.data[name]
                self.assertAlmostEqual(res[MINIMUM].value, ir.engine.idle_speed.value)
                if name != "2020_rpm_legion_grand_touring":
                    self.assertAlmostEqual(res[MINIMUM].value, idle)
                    self.assertAlmostEqual(res[INERTIA].value, inertia)
                self.assertEqual(res[LIMITER].value, ceil)

    def test_all_seven(self) -> None:
        for name in V200409:
            with self.subTest(vehicle=name):
                _, ir, res = self.data[name]
                self.assertEqual(ir.engine.clutch_additional_inertia.value, 0.0)
                for t, rule in NEW_RULES.items():
                    self.assertEqual(res[t].rule, rule)
                    self.assertIn(res[t].status, ("inferred", "known"))
                self.assertEqual(res[POWER_LUT].value, ir.engine.wot_torque_curve.value)
                self.assertEqual(ir.engine.wot_torque_curve.kind, "estimated")


class TestCamsoUnchanged(unittest.TestCase):
    def test_sucette_poubelle(self) -> None:
        got = _real("sucette_poubelle")
        if got is None:
            self.skipTest("sin samples_beamng/")
        _, ir, res = got
        self.assertEqual(ir.engine.device_type.value, "camsoEngine")
        self.assertEqual(res[MINIMUM].rule, "eng.minimum_camso91392@2")
        self.assertEqual(res[LIMITER].rule, "eng.limiter_camso91392@2")
        self.assertEqual(res[INERTIA].rule, "eng.inertia_camso91392@2")
        self.assertEqual(res[POWER_LUT].rule, "eng.power_lut@4")
        for t in (MINIMUM, LIMITER, INERTIA, POWER_LUT):
            self.assertIn("device_type", _rejected_reason(res[t], NEW_RULES[t]))


if __name__ == "__main__":
    unittest.main()
