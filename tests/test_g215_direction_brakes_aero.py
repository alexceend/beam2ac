"""G2.15: dirección, frenos y aerodinámica del 200409 (extracción, IR, reglas, AC).

Vehículos reales del corpus (solo lectura; se omiten si faltan) y variaciones sintéticas
del IR para las condiciones de las reglas. Informe:
``docs/research/g215_direction_brakes_aero_2026-11-03.md``.
"""

from __future__ import annotations

import copy
import json
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.convert.service import ConversionSession
from src.exporters import capabilities
from src.extract.aero import aero_model
from src.extract.steering import build_steering
from src.importers.beamng import import_preview
from src.ir.legacy_facts import ir_to_facts
from src.ir.param import Param
from src.jbeam import resolve
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SEVEN = [
    "2020_rpm_atacama_sport__4x4",
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_patagonia_xt_v8",
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
]
# recorrido |factor|·L0 de la cremallera (inventario de G2.15)
TRAVEL = {
    "2020_rpm_atacama_sport__4x4": 0.12769,
    "2020_rpm_atlanta_gt_3_6t": 0.09773,
    "2020_rpm_legion_grand_touring": 0.10330,
    "2020_rpm_patagonia_xt_v8": 0.11300,
    "2020_rpm_prothean_s": 0.10252,
    "2021_rpm_portland_touring": 0.11083,
    "rpm_omega_one_concept": 0.10299,
    "ibiza": 0.11702,
}
MAXT = "ac:brakes.ini/DATA/MAX_TORQUE"
FS = "ac:brakes.ini/DATA/FRONT_SHARE"
HB = "ac:brakes.ini/DATA/HANDBRAKE_TORQUE"
LOCK = "ac:car.ini/CONTROLS/STEER_LOCK"
NEW = {
    MAXT: "brk.max_torque_200409@1",
    FS: "brk.front_share_200409@1",
    HB: "brk.handbrake_torque_200409@1",
    LOCK: "ctrl.steer_lock_200409@1",
}


def _dir_of(name: str) -> Path | None:
    if name == "ibiza":
        return IBIZA if IBIZA.is_dir() else None
    for top in SAMPLES.glob("*"):
        vd = top / "vehicles" / name
        if vd.is_dir():
            return vd
    return None


def _have(name: str) -> bool:
    return _dir_of(name) is not None


@lru_cache(maxsize=None)
def _source(name: str):
    vd = _dir_of(name)
    pc = "35b84.pc" if name == "ibiza" else sorted(p.name for p in vd.glob("*.pc"))[0]
    return load_vehicle_dir(vd, pc)


@lru_cache(maxsize=None)
def _ir(name: str):
    return import_preview(preview_physics(_source(name), 70.0), 70.0)


def _res(ir) -> dict:
    return {r.target: r for r in ConversionSession.from_ir(ir).resolve()}


@lru_cache(maxsize=None)
def _res_of(name: str) -> dict:
    return _res(_ir(name))


def need(*names):
    return unittest.skipUnless(all(_have(n) for n in names), "corpus no disponible")


class TestSteeringExtraction(unittest.TestCase):
    """Semántica de nodeBeam.lua/hydros.lua/input.lua: cremallera = |factor|·L0."""

    @need(*SEVEN, "ibiza")
    def test_rack_travel_and_lock(self):
        for name, travel in TRAVEL.items():
            with self.subTest(vehicle=name):
                src = _source(name)
                st = build_steering(resolve(src.index, src.pc))
                self.assertEqual(st.mechanism, "rack")
                self.assertAlmostEqual(st.rack_travel, travel, places=5)
                self.assertEqual(st.steering_wheel_lock, 450.0)
                self.assertEqual(st.steering_wheel_lock_source, "hydro")
                self.assertEqual(len(st.hydros), 2)
                self.assertEqual(
                    {round(h.factor, 9) for h in st.hydros},
                    {round(st.hydros[0].factor, 9), -round(st.hydros[0].factor, 9)},
                )

    @need(*SEVEN)
    def test_ir_steering(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                ir = _ir(name)
                self.assertEqual(ir.steering.mechanism.value, "rack")
                lock = ir.steering.steering_wheel_lock
                self.assertEqual(
                    (lock.value, lock.unit, lock.kind), (450.0, "deg", "extracted")
                )
                tr = ir.steering.rack_travel
                self.assertEqual((tr.unit, tr.kind), ("m", "computed"))
                self.assertAlmostEqual(tr.value, TRAVEL[name], places=5)
                # ángulo de RUEDA: ausente con motivo, nunca 0. G2.16: la relación en
                # el centro se calcula con un DWB delantero medido (todos salvo el
                # puntal de atlanta, que conserva el motivo)
                # G2.19: el puntal en raíl de atlanta también da la relación
                # (src/extract/linkage.py, strut_rail); antes conservaba el motivo
                pending = [ir.steering.wheel_lock]
                sr = ir.steering.steering_ratio
                self.assertEqual((sr.status, sr.kind), ("present", "computed"))
                for p in pending:
                    self.assertEqual(p.status, "missing")
                    self.assertIn("cinemática", p.reason)


class TestBrakesIR(unittest.TestCase):
    @need(*SEVEN)
    def test_200409_brake_details(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                b = _ir(name).brakes
                for pos in ("front", "rear"):
                    ax = b.axles[pos]
                    self.assertEqual(ax.input_split.value, 1.0)
                    self.assertEqual(ax.input_split.kind, "extracted")
                    self.assertEqual(ax.thermal_model.value, "enabled")
                    self.assertIn(ax.pad_material.value, ("sport", "semi-race"))
                    self.assertEqual(ax.abs.value, "enabled")
                    self.assertTrue(ax.abs_slip_target.present)
                    self.assertGreater(ax.disc_diameter.value, 0)
                self.assertEqual(b.axles["front"].parking_torque_per_wheel.value, 0.0)
                self.assertGreater(b.axles["rear"].parking_torque_per_wheel.value, 0)
                self.assertEqual(
                    b.front_share_full_pedal.value, b.front_share_below_knee.value
                )

    @need("ibiza")
    def test_ibiza_knee_and_no_abs(self):
        b = _ir("ibiza").brakes
        self.assertEqual(b.axles["rear"].input_split.value, 0.5)
        self.assertEqual(b.axles["front"].abs.value, "disabled")
        self.assertEqual(b.axles["front"].abs_slip_target.status, "not_applicable")

    @need("2020_rpm_legion_grand_touring")
    def test_no_new_bng_facts(self):
        facts = ir_to_facts(_ir("2020_rpm_legion_grand_touring"))
        new = (
            "input_split",
            "thermal_model",
            "pad_material",
            "abs",
            "disc_diameter",
        )
        for k in facts:
            if k.startswith("bng."):
                self.assertFalse(any(k.endswith("." + n) for n in new), k)
                self.assertNotIn("steering", k)
        self.assertIn("ir.brakes.axles.rear.input_split", facts)
        self.assertIn("ir.steering.steering_wheel_lock", facts)


class TestAero(unittest.TestCase):
    @need(*SEVEN, "ibiza")
    def test_model_and_reason(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                src = _source(name)
                self.assertEqual(aero_model(resolve(src.index, src.pc)), "triangles")
                ir = _ir(name)
                self.assertEqual(ir.aero.model.value, "triangles")
                for p in (ir.aero.cda, ir.aero.cla_front, ir.aero.cla_rear):
                    self.assertEqual(p.status, "missing")
                    self.assertIn("triángulos", p.reason)
                feats = {f.feature for f in capabilities.assess(ir)}
                self.assertIn("aero.triangles", feats)
                self.assertIn("steering.geometry", feats)
        ib = _ir("ibiza")
        self.assertEqual(ib.aero.model.value, "cefaero")
        self.assertTrue(ib.aero.cda.present)
        self.assertNotIn("aero.triangles", {f.feature for f in capabilities.assess(ib)})


class TestRules(unittest.TestCase):
    def test_rules_limited_to_200409(self):
        cat = Catalog.load(REPO_CATALOG)
        rules = (
            {r.key: r for r in cat.rules.values()}
            if isinstance(cat.rules, dict)
            else {r.key: r for r in cat.rules}
        )
        for key in NEW.values():
            with self.subTest(rule=key):
                r = rules[key]
                self.assertEqual(r.status, "provisional")
                self.assertEqual(r.scope.family, "automation_export")
                self.assertIn(
                    ("ir.formats.exporter_version", "in", ["200409"]),
                    [(c.fact, c.op, c.value) for c in r.conditions],
                )
                self.assertEqual(r.open_issues, [])

    @need(*SEVEN)
    def test_exported_values(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                ir, res = _ir(name), _res_of(name)
                tf = ir.brakes.axles["front"].max_torque_per_wheel.value
                tr = ir.brakes.axles["rear"].max_torque_per_wheel.value
                for t, rule in NEW.items():
                    self.assertEqual((res[t].rule, res[t].status), (rule, "inferred"))
                self.assertAlmostEqual(res[MAXT].value, tf + tr, places=6)
                # FRONT_SHARE usa los pares sin redondear; el IR los guarda a 0,01 N·m
                self.assertAlmostEqual(res[FS].value, tf / (tf + tr), places=5)
                self.assertEqual(
                    res[HB].value,
                    ir.brakes.axles["rear"].parking_torque_per_wheel.value,
                )
                self.assertEqual(res[LOCK].value, 450.0)
                # G2.16: con DWB delantero medido, STEER_RATIO se resuelve por su regla
                # (tests/test_g216_linkage.py); el puntal de atlanta sigue pendiente.
                # LSRR: valor propuesto, manual_required (unidad 'undocumented')
                sr = res["ac:car.ini/CONTROLS/STEER_RATIO"]
                if name == "2020_rpm_atlanta_gt_3_6t":
                    # G2.19: puntal en raíl medido ⇒ regla del puntal (antes
                    # manual_required)
                    self.assertEqual(
                        (sr.rule, sr.status),
                        ("ctrl.steer_ratio_rod_behind_strut_200409@1", "inferred"),
                    )
                else:
                    self.assertEqual(
                        (sr.rule, sr.status),
                        ("ctrl.steer_ratio_rod_behind_200409@1", "inferred"),
                    )
                # G2.17 (D-G216-LSRR): con DWB delantero LSRR se resuelve por la
                # regla @2 (unidad m/° documentada en la regla); atlanta (puntal) y la
                # aerodinámica siguen pendientes
                lsrr = res["ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO"]
                if name == "2020_rpm_atlanta_gt_3_6t":
                    # G2.19: clon de @2 para el puntal en raíl (antes manual_required)
                    self.assertEqual(
                        (lsrr.rule, lsrr.status),
                        ("ctrl.linear_steer_rod_ratio_strut_200409@1", "inferred"),
                    )
                else:
                    self.assertEqual(
                        (lsrr.rule, lsrr.status),
                        ("ctrl.linear_steer_rod_ratio_200409@2", "inferred"),
                    )
                # G2.19 (D-G215-AERO): con la medida del banco asociada al origen
                # (measurements/), las alas de AC se resuelven por las reglas
                # aero.*_200409@1 (antes: manual_required, aerodinámica pendiente)
                gain = res["ac:aero.ini/WING_0/CL_GAIN"]
                self.assertEqual(
                    (gain.rule, gain.status, gain.value),
                    ("aero.wing0_cl_gain_200409@1", "inferred", 1.0),
                )

    @need("2020_rpm_atlanta_gt_3_6t")
    def test_knee_blocks_brake_rules(self):
        """Con codo de pedal (input_split < 1) el freno lineal de AC no es exacto."""
        ir = copy.deepcopy(_ir("2020_rpm_atlanta_gt_3_6t"))
        ir.set("brakes.axles.rear.input_split", Param.of(0.5, "1", "extracted"))
        res = _res(ir)
        self.assertNotEqual(res[MAXT].rule, NEW[MAXT])
        self.assertNotEqual(res[FS].rule, NEW[FS])
        self.assertEqual(res[HB].rule, NEW[HB])  # no depende del codo

    @need("2020_rpm_atlanta_gt_3_6t")
    def test_front_handbrake_blocks_handbrake_rule(self):
        ir = copy.deepcopy(_ir("2020_rpm_atlanta_gt_3_6t"))
        ir.set(
            "brakes.axles.front.parking_torque_per_wheel",
            Param.of(1000.0, "N·m", "extracted"),
        )
        self.assertNotEqual(_res(ir)[HB].rule, NEW[HB])

    @need("ibiza")
    def test_not_applied_outside_200409_ibiza_unchanged(self):
        res = _res_of("ibiza")
        for t, rule in NEW.items():
            self.assertNotEqual(res[t].rule, rule)
        self.assertEqual(res[FS].rule, "brk.front_share@2")


class TestEvidence(unittest.TestCase):
    EV = ROOT / "output/g215_direction_brakes_aero_2026-11-03/banco/resultados.json"

    @unittest.skipUnless(EV.is_file(), "sin evidencia del banco")
    def test_bench_aero_scales_with_v2(self):
        d = json.loads(self.EV.read_text("utf-8"))
        for name, v in d.items():
            rows = [
                r
                for r in v["aero"]
                if r["v"] >= 20 and r["pitch"] == 0 and r["yaw"] == 0
            ]
            if not rows:
                continue
            cda = [r["CdA"] for r in rows]
            with self.subTest(vehicle=name):
                self.assertLess((max(cda) - min(cda)) / max(cda), 0.01)


if __name__ == "__main__":
    unittest.main()
