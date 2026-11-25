"""G1: modelo intermedio (IR), importador, arquitecturas, capacidades y flujo nuevo."""

from __future__ import annotations

import pytest

import ast
import json
import tempfile
import unittest
from pathlib import Path

from src.bench.service import default_beamng_root
from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.facts import Fact
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.exporters.assetto_corsa import build_target
from src.exporters.capabilities import assess
from src.ir import IRError, Param, VehicleIR
from src.ir.architecture import classify
from src.ir.conditions import holds
from src.ir.vehicle import Differential, ensure_entry

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
COMMON = Path((default_beamng_root() / "content/vehicles/common.zip").as_posix())
REFERENCE = ROOT / "tests/fixtures/g1_hechos_referencia.json"
IBIZA_PROJECT = ROOT / "output/projects/ibiza_35b84_b1"


def _sources():
    from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip

    out = {}
    if IBIZA.is_dir():
        out["seat_ibiza_acfr"] = lambda: load_vehicle_dir(IBIZA, "35b84.pc")
    for name in ("porsche_gt3_rs", "spicy_mandala"):
        if (MODS / f"{name}.zip").is_file() and COMMON.is_file():
            out[name] = lambda n=name: load_vehicle_zip(MODS / f"{n}.zip", COMMON)
    for d in sorted(SAMPLES.glob("*/vehicles/*")) if SAMPLES.is_dir() else []:
        out[d.name] = lambda d=d: load_vehicle_dir(d, next(d.glob("*.pc")).name)
    return out


_CACHE: dict[str, VehicleIR] = {}


def ir_of(name: str) -> VehicleIR:
    if name not in _CACHE:
        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import preview_physics

        _CACHE[name] = import_preview(preview_physics(_sources()[name](), 70.0), 70.0)
    return _CACHE[name]


class TestParam(unittest.TestCase):
    def test_states_are_distinct_and_never_invent(self) -> None:
        self.assertEqual(Param.of(None, "rpm", "extracted").status, "missing")
        self.assertIsNone(Param.of(None, "rpm", "extracted").value)
        na = Param.not_applicable("enum", "FWD")
        self.assertEqual((na.status, na.value), ("not_applicable", None))
        un = Param.unparsed({"x": 1}, reason="formato desconocido")
        self.assertEqual(un.original, {"x": 1})
        self.assertEqual(Param.of(0, "N·m", "extracted").value, 0)  # 0 es un valor

    def test_validation(self) -> None:
        with self.assertRaises(IRError):
            Param.of(1.0, "furlongs", "extracted")  # unidad desconocida
        with self.assertRaises(IRError):
            Param(value=None, status="present", kind="extracted", unit="m")
        with self.assertRaises(IRError):
            Param(value=3, status="missing", unit="m")
        with self.assertRaises(IRError):
            Param(status="unparsed", unit="text")  # sin original
        with self.assertRaises(IRError):
            Param.of(1.0, "m", "adivinado")

    def test_ir_paths_units_and_unknown_fields(self) -> None:
        ir = VehicleIR()
        ir.set("engine.idle_speed", Param.of(900, "rpm", "extracted"))
        self.assertEqual(ir.get("engine.idle_speed").value, 900)
        with self.assertRaises(IRError):  # unidad distinta de la del esquema
            ir.set("engine.idle_speed", Param.of(900, "rad/s", "extracted"))
        with self.assertRaises(IRError):
            ir.set("engine.rpm_idle", Param.of(900, "rpm", "extracted"))
        with self.assertRaises(IRError):
            ensure_entry(ir.drivetrain.differentials, "middle", Differential)
        ir.set("drivetrain.gearbox.forward_ratios.1", Param.of(3.1, "1", "extracted"))
        with self.assertRaises(IRError):  # las listas se rellenan en orden
            ir.set("drivetrain.gearbox.forward_ratios.3", Param.of(1, "1", "extracted"))
        paths = dict(ir.walk())
        self.assertIn("drivetrain.gearbox.forward_ratios.1", paths)
        self.assertEqual(paths["steering.wheel_lock"].status, "missing")
        json.dumps(ir.to_dict(), default=str)


class TestConditionsAndArchitecture(unittest.TestCase):
    def synthetic(self, layout: str, box: str, diffs: dict[str, str]) -> VehicleIR:
        ir = VehicleIR()
        ir.set("drivetrain.layout", Param.of(layout, "enum", "computed"))
        ir.set("drivetrain.gearbox.kind", Param.of(box, "enum", "extracted"))
        for role, kind in diffs.items():
            ensure_entry(ir.drivetrain.differentials, role, Differential)
            ir.set(
                f"drivetrain.differentials.{role}.kind",
                Param.of(kind, "enum", "extracted"),
            )
        return ir

    def test_vehicle_architecture_and_components(self) -> None:
        a = classify(
            self.synthetic("RWD", "sequentialGearbox", {"rear": "torqueVectoring"})
        )
        self.assertEqual(a["vehicle"], "rwd_sequential")
        self.assertIn("diff_torque_vectoring", a["components"])
        b = classify(
            self.synthetic(
                "AWD",
                "manualGearbox",
                {"center": "lsd", "front": "lsd", "rear": "open"},
            )
        )
        self.assertEqual(b["vehicle"], "awd_center_diff")

    def test_missing_data_is_not_classified(self) -> None:
        ir = VehicleIR()
        a = classify(ir)
        self.assertIsNone(a["vehicle"])
        self.assertTrue(a["unclassified_reason"])
        # un dato ausente nunca cumple eq
        self.assertFalse(
            holds(ir, {"path": "drivetrain.layout", "op": "eq", "value": "FWD"})
        )
        self.assertTrue(holds(ir, {"path": "drivetrain.layout", "op": "absent"}))


def _context(vehicle: str, facts: dict[str, Fact]):
    cat = Catalog.load(REPO_CATALOG)
    return cat, context_from_facts(vehicle, "v", facts, cat)


class TestRuleEngineContracts(unittest.TestCase):
    def test_incompatible_units_are_detected(self) -> None:
        """Hechos del IR (los que consume la regla vigente, G2.2) con el techo de rpm
        en metros: la regla debe rechazarlo por unidades."""
        cat, ctx = _context(
            "x",
            {
                fid: Fact(fid, value, unit, kind)
                for fid, value, unit, kind in (
                    ("ir.engine.device_type", "camsoEngine", "enum", "extracted"),
                    (
                        "ir.formats.code_versions.camsoEngine",
                        "91392d703ac3d54a9c45dfa2fb6ba7ef"
                        "1f87aa6df6c1e4e95f61a3763f8de8bb",
                        "text",
                        "extracted",
                    ),
                    ("ir.engine.rev_limiter_mode", "activo", "enum", "computed"),
                    ("bng.info.type", "Automation", "text", "extracted"),
                    ("ir.engine.speed_ceiling", 7000, "m", "computed"),
                )
            },
        )
        r = ResolutionEngine(cat, ctx).resolve("ac:engine.ini/ENGINE_DATA/LIMITER")
        self.assertNotEqual(r.status, "inferred")
        self.assertTrue(
            any("unidades incompatibles" in x["reason"] for x in r.rejected)
        )

    def test_contradictory_decisions_are_ambiguous(self) -> None:
        cat, ctx = _context("veh_x", {})
        target = "ac:engine.ini/ENGINE_DATA/LIMITER"
        for i, v in enumerate((7000, 7200)):
            cat.decisions.append(
                S.Decision(
                    id=f"test.contradiction.{i}",
                    target=target,
                    vehicle="veh_x",
                    action="manual_value",
                    justification="prueba",
                    author="test",
                    date="2026-10-30",
                    scope=S.Scope(level="vehicle", vehicle="veh_x"),
                    value=v,
                    unit="rpm",
                )
            )
        r = ResolutionEngine(cat, ctx).resolve(target)
        self.assertEqual(r.status, "ambiguous")
        self.assertTrue(any("valores distintos" in x for x in r.open_issues))

    def test_target_model_incomplete_without_source(self) -> None:
        """El exportador funciona sin el origen: un IR vacío y sin resultados da un
        modelo incompleto que explica por qué."""
        cat = Catalog.load(REPO_CATALOG)
        t = build_target(VehicleIR(), [], cat)
        self.assertFalse(t.complete)
        self.assertFalse(t.installable)
        self.assertTrue(any(not c["ok"] for c in t.validation))


class TestLayering(unittest.TestCase):
    """El IR y los exportadores no dependen del formato de origen."""

    FORBIDDEN = (
        "src.jbeam",
        "src.extract",
        "src.pipeline",
        "src.importers",
        "src.mesh",
    )

    def imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                out.add(node.module)
            elif isinstance(node, ast.Import):
                out.update(a.name for a in node.names)
        return out

    def test_ir_core_and_exporters_do_not_import_the_source(self) -> None:
        files = [
            ROOT / "src/ir/param.py",
            ROOT / "src/ir/vehicle.py",
            ROOT / "src/ir/conditions.py",
            ROOT / "src/ir/architecture.py",
            ROOT / "src/exporters/capabilities.py",
            ROOT / "src/exporters/assetto_corsa.py",
        ]
        for f in files:
            with self.subTest(file=f.name):
                bad = [m for m in self.imports(f) if m.startswith(self.FORBIDDEN)]
                self.assertEqual(bad, [])
        # el núcleo del IR tampoco conoce el destino
        for f in files[:4]:
            self.assertFalse(
                [
                    m
                    for m in self.imports(f)
                    if m.startswith(("src.ac", "src.exporters"))
                ]
            )

    def test_importers_do_not_write_the_target(self) -> None:
        for f in (ROOT / "src/importers").glob("*.py"):
            with self.subTest(file=f.name):
                bad = [
                    m
                    for m in self.imports(f)
                    if m.startswith(("src.exporters", "src.project"))
                ]
                self.assertEqual(bad, [])


# G2.5c: la lógica de tracción pasó de src/ac/drivetrain.py a src/extract/traction.py.
# Lista CERRADA de localizadores de código movidos (misma lógica, otro módulo); el
# fixture congelado conserva los antiguos y se traducen aquí, sin reescribirlo.
MOVED_CODE_LOCATORS = {
    "analyse/traction_type": "classify_traction/traction_type",
    "awd_axles (differential.lua l. 315-317, 64-65)": (
        "awd_axle_roles (differential.lua l. 315-317, 64-65)"
    ),
    # G2.8: forced_induction también mira el compresor (supercharger)
    "read_raw_engine: turbo": "read_raw_engine: turbocharger, supercharger",
}


def _moved(fixture_facts: dict) -> dict:
    out = {}
    for k, f in fixture_facts.items():
        refs = [
            [t, MOVED_CODE_LOCATORS.get(loc, loc) if t == "code" else loc, sha]
            for t, loc, sha in f["refs"]
        ]
        out[k] = {**f, "refs": refs}
    return out


@unittest.skipUnless(REFERENCE.is_file(), "sin fixture de referencia")
class TestEquivalenceWithLegacyFacts(unittest.TestCase):
    """Los hechos derivados del IR son los de antes de G1 (fixture congelado)."""

    @pytest.mark.ampliada
    def test_all_available_reference_vehicles(self) -> None:
        from src.ir.legacy_facts import ir_to_facts

        ref = json.loads(REFERENCE.read_text(encoding="utf-8"))["vehicles"]
        available = [n for n in ref if n in _sources()]
        self.assertGreaterEqual(len(available), 1)
        for name in available:
            with self.subTest(vehicle=name):
                facts = ir_to_facts(ir_of(name))
                got = {
                    k: {
                        "value": json.loads(json.dumps(f.value)),
                        "unit": f.unit,
                        "kind": f.kind,
                        "note": f.note,
                        "refs": [[r.type, r.locator, r.sha256] for r in f.refs],
                    }
                    for k, f in facts.items()
                    if not k.startswith("ir.")  # G2: hechos nativos añadidos
                }
                self.assertEqual(got, _moved(ref[name]))


@unittest.skipUnless(SAMPLES.is_dir() and IBIZA.is_dir(), "sin muestras")
class TestRealVehicles(unittest.TestCase):
    def test_every_reference_vehicle_builds_an_ir(self) -> None:
        expected = {
            "seat_ibiza_acfr": "fwd_manual",
            "porsche_gt3_rs": "rwd_manual",
            "spicy_mandala": "rwd_manual",
            "autist_cuanto": "awd_center_diff",
            "hyacinth_sage": "fwd_manual",
            "quail_mirabilis": "rwd_sequential",
            "sucette_poubelle": "fwd_manual",
            "sucette_poubelle_turbo": "fwd_manual",
            # añadidos a samples_beamng/ después (camsoEngine; clasificación medida 2026-11-25)
            "agus_1_nrt_1": "awd_center_diff",
            "eagle_inca_p395_dale_earnhardt_edition": "rwd_manual",
            "turbot_aster": None,
        }
        # G2.8: exportador 200409 (añadidos el 2026-11-01): sin arquitectura (caja DCT
        # o convertidor, chasis). G2.9: el motor combustionEngine ya llega al IR (curva
        # con turbo = estimación del juego, «estimated»); antes quedaba ausente
        not_convertible = {
            "2020_rpm_atacama_sport__4x4",
            "2020_rpm_atlanta_gt_3_6t",
            "2020_rpm_legion_grand_touring",
            "2020_rpm_patagonia_xt_v8",
            "2020_rpm_prothean_s",
            "2021_rpm_portland_touring",
            "rpm_omega_one_concept",
        }
        # G2.11: con la caja DCT o automática en el IR, los 5 cuya geometría se lee son
        # tracción total con central. G2.13: atacama y patagonia ya leen la geometría
        # (nodeOffset con sign(0) = 0 e id repetido en una pieza, reglas del juego), así
        # que también llegan al IR como tracción total con central (antes: None)
        g211_awd = {
            "2020_rpm_atacama_sport__4x4",
            "2020_rpm_patagonia_xt_v8",
            "2020_rpm_atlanta_gt_3_6t",
            "2020_rpm_legion_grand_touring",
            "2020_rpm_prothean_s",
            "2021_rpm_portland_touring",
            "rpm_omega_one_concept",
        }
        for name in sorted(set(_sources()) & not_convertible):
            with self.subTest(vehicle=name):
                ir = ir_of(name)
                self.assertEqual(
                    classify(ir)["vehicle"],
                    "awd_center_diff" if name in g211_awd else None,
                )
                self.assertEqual(ir.engine.device_type.value, "combustionEngine")
                p = ir.engine.wot_torque_curve
                self.assertEqual(
                    (p.status, p.unit, p.kind),
                    ("present", "table:rpm->N·m", "estimated"),
                )
        self.assertEqual(sorted(set(_sources()) - set(expected) - not_convertible), [])
        for name in sorted(set(_sources()) & set(expected)):
            with self.subTest(vehicle=name):
                ir = ir_of(name)
                self.assertEqual(classify(ir)["vehicle"], expected[name])
                # misma representación normalizada para el mismo tipo de dato
                p = ir.engine.wot_torque_curve
                self.assertEqual((p.status, p.unit), ("present", "table:rpm->N·m"))
                self.assertEqual(ir.engine.idle_speed.unit, "rpm")
                self.assertEqual(ir.steering.wheel_lock.status, "missing")
                # un FWD/RWD no tiene diferencial central: no aplica, no ausente
                if expected[name] != "awd_center_diff":
                    self.assertEqual(
                        ir.drivetrain.differentials["center"].kind.status,
                        "not_applicable",
                    )

    def test_unknown_source_data_is_kept(self) -> None:
        ir = ir_of("autist_cuanto")
        sources = [u.locator for u in ir.unknown]
        self.assertIn("Camso_ElectricMotor_F", sources)
        self.assertEqual(ir.unknown[0].raw, "Camso_ElectricMotor_F_b78dc")

    def test_one_rule_reused_by_two_vehicles_and_rejected_by_a_third(self) -> None:
        """Una sola regla, condicionada a la arquitectura, para varios vehículos."""
        from src.convert.service import ConversionSession

        rule = S.Rule(
            id="test.arch_limiter",
            version=1,
            target="ac:engine.ini/ENGINE_DATA/LIMITER",
            kind="computed",
            transform=S.Transform(
                id="identity", input_units={"x": "rpm"}, output_unit="rpm"
            ),
            scope=S.Scope(level="generic"),
            status="provisional",
            inputs={"x": "bng.engine.rpm_ceiling"},
            conditions=[
                S.Condition(fact="ir.architecture.vehicle", op="eq", value="fwd_manual")
            ],
            evidence=[S.EvidenceRef(type="doc", path="tests/test_ir.py")],
            history=[
                S.HistoryEntry(
                    date="2026-10-30", author="t", action="create", reason="t"
                )
            ],
        )
        used = {}
        for name in ("sucette_poubelle", "hyacinth_sage", "autist_cuanto"):
            s = ConversionSession.from_ir(ir_of(name))
            s.catalog.rules = {
                k: r for k, r in s.catalog.rules.items() if r.target != rule.target
            }
            s.catalog.rules[rule.key] = rule
            res = {r.target: r for r in s.resolve()}[rule.target]
            used[name] = (res.rule, res.status, res.value)
        self.assertEqual(used["sucette_poubelle"][0], "test.arch_limiter@1")
        self.assertEqual(used["hyacinth_sage"][0], "test.arch_limiter@1")
        self.assertEqual(used["sucette_poubelle"][2], 7000.0)
        self.assertEqual(used["hyacinth_sage"][2], 11000.0)
        self.assertEqual(used["autist_cuanto"][1], "manual_required")

    def test_capabilities_explain_losses(self) -> None:
        quail = {f.feature: f.level for f in assess(ir_of("quail_mirabilis"))}
        self.assertEqual(quail["differential.torque_vectoring"], "not_representable")
        self.assertEqual(quail["gearbox.sequential_mode"], "pending_research")
        # G2.20: sin medida del banco de neumáticos la capacidad es
        # tyres.model_unmeasured; G2.29: el quail ya tiene medida (tyres.model)
        self.assertEqual(quail["tyres.model"], "approximation")
        self.assertNotIn("tyres.model_unmeasured", quail)
        autist = {f.feature: f.level for f in assess(ir_of("autist_cuanto"))}
        # G2.12: el cálculo 4WD de AC analizado; central lsd con reparto fijo = FRONT_SHARE
        self.assertEqual(autist["drivetrain.awd_center_split"], "direct")
        self.assertEqual(autist["drivetrain.awd_wheel_inertia"], "fidelity_loss")
        self.assertNotIn("drivetrain.awd_center", autist)
        sucette = {f.feature for f in assess(ir_of("sucette_poubelle"))}
        self.assertNotIn("differential.lsd_dynamics", sucette)  # diferencial abierto

    def test_new_flow_session_and_cli(self) -> None:
        from src.convert.__main__ import main
        from src.convert.service import ConversionSession

        d = next(SAMPLES.glob("*/vehicles/sucette_poubelle"))
        s = ConversionSession.from_source(d)
        t = s.target()
        self.assertFalse(t.complete)
        self.assertIn("engine.ini", t.files)
        ex = s.explain("ac:power.lut")
        self.assertEqual(ex["resolution"]["rule"], "eng.power_lut@4")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "inspect"
            self.assertEqual(main(["inspect", str(d), "--out", str(out)]), 0)
            self.assertTrue((out / "ir.json").is_file())
            self.assertTrue((out / "target/data/engine.ini").is_file())

    @unittest.skipUnless(IBIZA_PROJECT.is_dir(), "sin el proyecto del Ibiza")
    def test_ibiza_new_flow_equals_resolved_ini_stage(self) -> None:
        """Regresión: con las decisiones de su proyecto, el flujo nuevo produce los
        mismos INI que la última etapa resolved_ini guardada (y que la física de los
        exports validados)."""
        import os

        from src.convert.service import ConversionSession

        runs = (IBIZA_PROJECT / "runs.jsonl").read_text("utf-8").splitlines()
        keys = [
            json.loads(x)["stages"]["resolved_ini"]["key"]
            for x in reversed(runs)
            if json.loads(x)["stages"].get("resolved_ini", {}).get("key")
        ]
        stage = IBIZA_PROJECT / "stages/resolved_ini" / keys[0] / "data"
        # La etapa guardada es de antes de G2.39 (decisión del usuario D-INV-GBI/D-G227-IBIZA:
        # INERTIA 0,01 y LSD 2WD ×2 = PRELOAD 200 + POWER 0,5 + COAST 0,05 para el 38022,
        # `dt.preload@4`…). Esas reglas se retiran del catálogo para comparar con ella;
        # el comportamiento actual lo cubre tests/test_g239_38022_rules.py.
        cat = Catalog.load(REPO_CATALOG, IBIZA_PROJECT)
        for key in [
            k
            for k in cat.rules
            if k.endswith(("_38022@1", "dt.preload@4", "app.gearbox.inertia@3"))
            or k in ("app.diff2wd.power@2", "app.diff2wd.coast@2")
        ]:
            cat.rules.pop(key)
        ir = ir_of("seat_ibiza_acfr")
        classify(ir)
        s = ConversionSession(ir, cat)
        files = s.target().files

        def same(st) -> bool:  # ¿coincide byte a byte con esa etapa guardada?
            return sorted(files) == sorted(p.name for p in st.glob("*")) and all(
                (st / n).read_bytes() == tx.replace("\n", os.linesep).encode("utf-8")
                for n, tx in files.items()
            )

        # Cada exportación nueva del proyecto añade una etapa con las reglas ACTUALES: la comparación vale con
        # la más reciente que sea anterior a G2.39 (la que corresponde a las reglas retiradas arriba).
        for k in keys:
            if same(IBIZA_PROJECT / "stages/resolved_ini" / k / "data"):
                return
        self.assertEqual(sorted(files), sorted(p.name for p in stage.glob("*")))
        for name, text in files.items():
            with self.subTest(file=name):
                disk = text.replace("\n", os.linesep).encode("utf-8")
                self.assertEqual((stage / name).read_bytes(), disk)


if __name__ == "__main__":
    unittest.main()
