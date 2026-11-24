"""Reglas de motor para camsoEngine.lua 91392d70 (exportador 40340) y regresión."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.facts_bng import Fact
from src.attrs.report import context_from_preview
from src.attrs.store import REPO_CATALOG, Catalog
from src.attrs.validations_bng import info_validations
from src.bench.service import default_beamng_root

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
OLD = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
NEW = "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"
ENGINE_TARGETS = (
    "ac:power.lut",
    "ac:engine.ini/ENGINE_DATA/INERTIA",
    "ac:engine.ini/ENGINE_DATA/LIMITER",
    "ac:engine.ini/ENGINE_DATA/MINIMUM",
    "ac:engine.ini/COAST_REF/RPM",
    "ac:engine.ini/COAST_REF/TORQUE",
    "ac:engine.ini/COAST_REF/NON_LINEARITY",
)


def _lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8", errors="replace").replace("\r", "").split("\n")


def _resolve(vehicle_dir: Path, pc: str):
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    cat = Catalog.load(REPO_CATALOG)
    pv = preview_physics(load_vehicle_dir(vehicle_dir, pc), 70.0)
    ctx = context_from_preview(pv, cat, 70.0)
    eng = ResolutionEngine(cat, ctx)
    return ctx, {t: eng.resolve(t) for t in ENGINE_TARGETS}


class TestRulesInCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.cat = Catalog.load(REPO_CATALOG)

    def test_power_lut_v3_only_widens_the_version(self) -> None:
        v2, v3 = self.cat.rules["eng.power_lut@2"], self.cat.rules["eng.power_lut@3"]
        self.assertEqual(v3.supersedes, "eng.power_lut@2")
        self.assertEqual(S._to_plain(v3.transform), S._to_plain(v2.transform))
        self.assertEqual(v3.inputs, v2.inputs)
        self.assertEqual(S._to_plain(v3.scope), S._to_plain(v2.scope))
        sha = [c for c in v3.conditions if c.fact == "bng.code.camsoEngine_sha256"]
        self.assertEqual((sha[0].op, sha[0].value), ("in", [OLD, NEW]))
        # G2: la @3 queda sustituida por la @4 (mismas condiciones sobre hechos ir.*)
        # (G2.10: la regla paralela eng.power_lut_combustion es otra cadena)
        active = [
            r for r in self.cat.rules_for("ac:power.lut") if r.id == "eng.power_lut"
        ]
        self.assertEqual([r.key for r in active], ["eng.power_lut@4"])

    def test_parallel_rules_only_for_new_version(self) -> None:
        for rid, old in (
            # G2.5b: la del Ibiza vigente es la @2 (hechos ir.*, mismo ámbito)
            ("eng.inertia_camso91392@1", "eng.inertia@2"),
            ("eng.limiter_camso91392@1", "eng.limiter@2"),
            ("eng.minimum_camso91392@1", "eng.minimum@2"),
        ):
            with self.subTest(rule=rid):
                r = self.cat.rules[rid]
                self.assertEqual(r.scope.level, "family")
                self.assertEqual(r.status, "provisional")
                sha = [c for c in r.conditions if c.fact.endswith("_sha256")]
                self.assertEqual((sha[0].op, sha[0].value), ("eq", NEW))
                # la del Ibiza sigue vigente y sin cambios de ámbito
                self.assertIn(old, [x.key for x in self.cat.rules_for(r.target)])
                self.assertEqual(self.cat.rules[old].scope.vehicle, "seat_ibiza_acfr")
        lim = self.cat.rules["eng.limiter_camso91392@1"]
        self.assertIn("bng.engine.rev_limiter", [c.fact for c in lim.conditions])
        ine = self.cat.rules["eng.inertia_camso91392@1"]
        self.assertIn(
            "bng.engine.clutch_additional_inertia", [c.fact for c in ine.conditions]
        )

    def test_coast_rules_not_extended(self) -> None:
        # G2.7: la @2 lleva la condición sobre ir.formats.code_versions.camsoEngine
        sha_facts = (
            "bng.code.camsoEngine_sha256",
            "ir.formats.code_versions.camsoEngine",
        )
        for t in ENGINE_TARGETS[4:]:
            for r in self.cat.rules_for(t):
                # G2.24: las reglas eng.coast_*_combustion exigen
                # device_type == combustionEngine: no son reglas camso y no pueden
                # aplicarse a ninguna versión de camsoEngine.lua
                if any(
                    c.fact == "ir.engine.device_type" and c.value == "combustionEngine"
                    for c in r.conditions
                ):
                    continue
                sha = [c for c in r.conditions if c.fact in sha_facts]
                # G2.27: eng.coast_*_camso91392@1 son reglas PROPIAS de la versión nueva
                # (fila de gas cerrado 0); las del Ibiza siguen limitadas a la suya
                want = NEW if r.id.endswith("_camso91392") else OLD
                self.assertEqual(sha[0].value, want, r.key)


def _facts(**kw) -> dict:
    base = {
        "bng.engine.net_wot_curve": [[1000, 50.0], [4900, 335.53], [9000, 1.0]],
        "bng.engine.peak_torque": 335.53,
        "bng.engine.peak_power": 254.182 * 745.69987158227,
        "bng.info.torque": 335,
        "bng.info.implied_power": 254.349,
    }
    base.update(kw)
    # G2.8: validations_bng lee los hechos ir.* del MISMO parámetro del IR
    ids = {k: _IR_OF.get(k, k) for k in base}
    return {ids[k]: Fact(ids[k], v, "", "x", []) for k, v in base.items()}


_IR_OF = {
    "bng.engine.net_wot_curve": "ir.engine.wot_torque_curve",
    "bng.engine.peak_torque": "ir.engine.peak_torque",
    "bng.engine.peak_power": "ir.engine.peak_power",
    "bng.info.torque": "ir.declared.torque",
    "bng.info.implied_power": "ir.declared.implied_power",
    "bng.info.exporter_version": "ir.formats.exporter_version",
}


class TestDeclaredTorqueByExporter(unittest.TestCase):
    def result(self, **kw) -> str:
        vs = info_validations("v", "c", _facts(**kw))
        return next(v for v in vs if v.id.endswith("engine_curve")).result

    def test_truncation_only_for_exporter_40340(self) -> None:
        self.assertEqual(self.result(**{"bng.info.exporter_version": "40340"}), "pass")
        self.assertEqual(self.result(**{"bng.info.exporter_version": "38022"}), "fail")
        self.assertEqual(self.result(), "fail")  # versión desconocida: ±0,5

    def test_truncation_limits(self) -> None:
        ex = {"bng.info.exporter_version": "40340"}
        self.assertEqual(self.result(**ex, **{"bng.info.torque": 336}), "pass")  # −0,47
        self.assertEqual(self.result(**ex, **{"bng.info.torque": 337}), "fail")
        self.assertEqual(self.result(**ex, **{"bng.info.torque": 334}), "fail")  # 1,53


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestIbizaRegression(unittest.TestCase):
    def test_engine_targets_unchanged(self) -> None:
        ctx, res = _resolve(IBIZA, "35b84.pc")
        self.assertEqual(ctx.fact("bng.info.exporter_version").value, "38022")
        self.assertEqual(ctx.fact("bng.engine.clutch_additional_inertia").value, 0.0)
        self.assertEqual(res["ac:power.lut"].rule, "eng.power_lut@4")
        self.assertEqual(res["ac:power.lut"].status, "known")
        expected = {
            "ac:engine.ini/ENGINE_DATA/INERTIA": ("eng.inertia@2", 0.10457800255063),
            "ac:engine.ini/ENGINE_DATA/LIMITER": ("eng.limiter@2", 10000.0),
            "ac:engine.ini/ENGINE_DATA/MINIMUM": ("eng.minimum@2", 1400.0),
            "ac:engine.ini/COAST_REF/TORQUE": ("eng.coast_torque@2", 66.1),  # G2.7
        }
        for t, (rule, value) in expected.items():
            with self.subTest(target=t):
                self.assertEqual((res[t].rule, res[t].value), (rule, value))
                self.assertEqual(res[t].status, "inferred")


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestNewVersionVehicles(unittest.TestCase):
    def test_lua_equivalence_except_closed_throttle(self) -> None:
        old = _lines(IBIZA / "lua/powertrain/camsoEngine.lua")
        for lua in SAMPLES.glob("*/vehicles/*/lua/powertrain/camsoEngine.lua"):
            with self.subTest(lua=lua.parts[-5]):
                new = _lines(lua)
                self.assertEqual(old[:723], new[:723])
                self.assertEqual(old[728:], new[725:])
                self.assertIn("max(1, min(99", old[727])
                self.assertIn("max(0, min(99", new[724])

    def test_sucette_engine_resolution(self) -> None:
        d = next(SAMPLES.glob("*/vehicles/sucette_poubelle"))
        ctx, res = _resolve(d, "f5893.pc")
        self.assertEqual(ctx.fact("bng.code.camsoEngine_sha256").value, NEW)
        self.assertEqual(
            ctx.fact("bng.engine.rev_limiter").value, "independiente_del_conflicto"
        )
        self.assertEqual(res["ac:power.lut"].rule, "eng.power_lut@4")
        self.assertEqual(res["ac:power.lut"].status, "known")
        self.assertEqual(res["ac:engine.ini/ENGINE_DATA/LIMITER"].value, 7000.0)
        self.assertEqual(res["ac:engine.ini/ENGINE_DATA/MINIMUM"].value, 1600.0)
        self.assertEqual(
            res["ac:engine.ini/ENGINE_DATA/INERTIA"].rule,
            "eng.inertia_camso91392@2",  # G2.2: misma regla con entradas ir.*
        )
        for t in ENGINE_TARGETS[4:]:  # freno motor: regla propia desde G2.27
            self.assertEqual(res[t].status, "inferred", t)
            # G2.31: la @2 (mínimos cuadrados) sustituye al ancla en el techo
            self.assertTrue(res[t].rule.endswith("_camso91392@2"), t)
        self.assertEqual(res["ac:engine.ini/COAST_REF/RPM"].value, 7000.0)
        note = ctx.fact("bng.engine.coast_ref_torque").note
        self.assertIn("fila 0", note)


if __name__ == "__main__":
    unittest.main()


# Rutas del equipo de quien ejecuta la prueba (sin datos personales): si faltan, la prueba se omite.
PORSCHE_ZIP = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "BeamNG/BeamNG.drive/current/mods/porsche_gt3_rs.zip"
)
COMMON_ZIP = default_beamng_root() / "content/vehicles/common.zip"


@unittest.skipUnless(
    PORSCHE_ZIP.is_file() and COMMON_ZIP.is_file(), "sin el zip del Porsche"
)
class TestPorscheRegression(unittest.TestCase):
    def test_catalog_only_engine_resolution_unchanged(self) -> None:
        """Sin decisiones de proyecto: power.lut no aplica (validación fallida desde
        antes) y las reglas del Ibiza siguen siendo solo propuestas."""
        from src.pipeline.physics_preview import load_vehicle_zip, preview_physics

        cat = Catalog.load(REPO_CATALOG)
        pv = preview_physics(load_vehicle_zip(PORSCHE_ZIP, COMMON_ZIP), 70.0)
        ctx = context_from_preview(pv, cat, 70.0)
        eng = ResolutionEngine(cat, ctx)
        self.assertEqual(ctx.fact("bng.code.camsoEngine_sha256").value, OLD)
        for t in ENGINE_TARGETS[1:4]:
            r = eng.resolve(t)
            self.assertNotIn("camso91392", r.rule or "", t)
