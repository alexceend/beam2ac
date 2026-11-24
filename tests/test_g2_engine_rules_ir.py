"""G2.2: inercia y limitador (camsoEngine 91392d70) con entradas del IR."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from src.bench.service import default_beamng_root
from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir import Param, VehicleIR
from src.ir.legacy_facts import ir_to_facts

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
COMMON = Path(
    (default_beamng_root() / "content/vehicles/common.zip").as_posix()
)
OLD = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
NEW = "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"
INERTIA = "ac:engine.ini/ENGINE_DATA/INERTIA"
LIMITER = "ac:engine.ini/ENGINE_DATA/LIMITER"
MINIMUM = "ac:engine.ini/ENGINE_DATA/MINIMUM"
RULES = {
    INERTIA: "eng.inertia_camso91392",
    LIMITER: "eng.limiter_camso91392",
    MINIMUM: "eng.minimum_camso91392",  # G2.3
}


def engine_ir(sha: str = NEW, **over) -> VehicleIR:
    ir = VehicleIR()
    values = {
        "identity.source_tool": ("Automation", "text", "extracted"),
        "engine.device_type": ("camsoEngine", "enum", "extracted"),
        "engine.inertia": (0.0761, "kg·m²", "extracted"),
        "engine.idle_speed": (1600.0, "rpm", "extracted"),
        "engine.clutch_additional_inertia": (0.0, "kg·m²", "computed"),
        "engine.speed_ceiling": (7000.0, "rpm", "computed"),
        "engine.rev_limiter_mode": ("independiente_del_conflicto", "enum", "computed"),
    }
    values.update(over)
    for path, v in values.items():
        if v is not None:
            ir.set(path, Param.of(*v))
    ir.formats.code_versions["camsoEngine"] = Param.of(sha, "text", "extracted")
    return ir


def catalog(drop_v2: bool = False) -> Catalog:
    cat = Catalog.load(REPO_CATALOG)
    if drop_v2:  # como antes de G2.2: la @1 vuelve a estar vigente
        for base in RULES.values():
            cat.rules.pop(f"{base}@2")
    return cat


def resolve(facts: dict, target: str, drop_v2: bool = False, vehicle="veh_test"):
    cat = catalog(drop_v2)
    ctx = context_from_facts(vehicle, "v", facts, cat)
    return ResolutionEngine(cat, ctx).resolve(target)


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_rule_to_ir", ROOT / "tools/catalog/migrate_rule_to_ir.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDefinitions(unittest.TestCase):
    def test_v2_changes_only_references(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        expected = {
            "eng.inertia_camso91392": (
                {"x": "ir.engine.inertia"},
                [
                    "ir.engine.device_type",
                    "ir.formats.code_versions.camsoEngine",
                    "ir.engine.clutch_additional_inertia",
                ],
            ),
            "eng.limiter_camso91392": (
                {"x": "ir.engine.speed_ceiling"},
                [
                    "ir.engine.device_type",
                    "ir.formats.code_versions.camsoEngine",
                    "ir.engine.rev_limiter_mode",
                ],
            ),
        }
        for base, (inputs, facts) in expected.items():
            with self.subTest(rule=base):
                v1, v2 = cat.rules[f"{base}@1"], cat.rules[f"{base}@2"]
                self.assertEqual(v2.supersedes, v1.key)
                vigentes = [r.key for r in cat.rules_for(v1.target) if r.id == base]
                self.assertEqual(vigentes, [v2.key])
                self.assertEqual(v2.inputs, inputs)
                self.assertEqual([c.fact for c in v2.conditions], facts)
                self.assertEqual(
                    [(c.op, c.value) for c in v2.conditions],
                    [(c.op, c.value) for c in v1.conditions],
                )
                for field in (
                    "transform",
                    "scope",
                    "status",
                    "assumptions",
                    "validations",
                    "kind",
                    "target",
                    "applies_when",
                    "exceptions",
                ):
                    self.assertEqual(
                        S._to_plain(getattr(v2, field)),
                        S._to_plain(getattr(v1, field)),
                    )
        # las reglas del Ibiza (ámbito vehículo) siguen vigentes; desde G2.5b en su @2
        self.assertIn("eng.limiter@2", [r.key for r in cat.rules_for(LIMITER)])
        self.assertIn("eng.inertia@2", [r.key for r in cat.rules_for(INERTIA)])

    def test_migration_tool_refuses(self) -> None:
        mod = load_tool()
        cat = Catalog.load(REPO_CATALOG)
        table = mod.legacy_to_ir()
        with self.assertRaises(SystemExit):  # ajuste del destino: sin equivalente IR
            mod.migrate(cat.rules["eng.coast_torque@1"], table)
        with self.assertRaises(SystemExit):  # ya usa hechos ir.*
            mod.migrate(cat.rules["eng.power_lut@4"], table)
        old = cat.rules["eng.minimum_camso91392@1"]
        bad = mod.migrate(old, table)
        bad.transform.output_unit = "rad/s"
        with self.assertRaises(SystemExit):  # diferencia fuera de lo permitido
            mod.check(old, bad)


class TestResolution(unittest.TestCase):
    def test_v2_selected_and_same_result_as_v1(self) -> None:
        facts = ir_to_facts(engine_ir())
        for target, base in RULES.items():
            with self.subTest(target=target):
                new = resolve(facts, target)
                old = resolve(facts, target, drop_v2=True)
                self.assertEqual(new.rule, f"{base}@2")
                self.assertEqual(old.rule, f"{base}@1")
                self.assertEqual(
                    (new.value, new.unit, new.status, new.confidence),
                    (old.value, old.unit, old.status, old.confidence),
                )
                self.assertTrue(all(i["fact"].startswith("ir.") for i in new.inputs))
                self.assertTrue(all(i["fact"].startswith("bng.") for i in old.inputs))

    def test_missing_or_incompatible_inputs_are_rejected(self) -> None:
        cases = {
            "INERTIA sin dato de inercia adicional": (
                INERTIA,
                engine_ir(**{"engine.clutch_additional_inertia": None}),
            ),
            "INERTIA con inercia adicional del embrague": (
                INERTIA,
                engine_ir(
                    **{"engine.clutch_additional_inertia": (0.02, "kg·m²", "computed")}
                ),
            ),
            "INERTIA sin inercia": (INERTIA, engine_ir(**{"engine.inertia": None})),
            "LIMITER sin limitador": (
                LIMITER,
                engine_ir(
                    **{"engine.rev_limiter_mode": ("sin_limitador", "enum", "computed")}
                ),
            ),
            "LIMITER sin techo": (LIMITER, engine_ir(**{"engine.speed_ceiling": None})),
            "MINIMUM sin ralentí": (MINIMUM, engine_ir(**{"engine.idle_speed": None})),
            "versión del Lua desconocida (MINIMUM)": (MINIMUM, engine_ir("0" * 64)),
            "versión del Lua desconocida (INERTIA)": (INERTIA, engine_ir("0" * 64)),
            "versión del Lua desconocida (LIMITER)": (LIMITER, engine_ir("0" * 64)),
        }
        for name, (target, ir) in cases.items():
            with self.subTest(case=name):
                r = resolve(ir_to_facts(ir), target)
                self.assertIsNone(r.value)
                self.assertNotIn("_camso91392@", r.rule or "")
                self.assertTrue(r.rejected)
        # Lua antiguo: la @2 no se aplica; la regla del Ibiza (ámbito vehículo) solo
        # aporta una PROPUESTA sin aplicar (comportamiento documentado del motor)
        r = resolve(ir_to_facts(engine_ir(OLD)), LIMITER)
        self.assertNotIn("_camso91392@", r.rule or "")
        self.assertEqual(r.source, "proposal")
        self.assertTrue(r.requires_approval)
        facts = ir_to_facts(engine_ir())
        facts["ir.engine.speed_ceiling"].unit = "m"  # dimensión incompatible
        r = resolve(facts, LIMITER)
        self.assertIsNone(r.value)
        self.assertTrue(
            any("unidades incompatibles" in x["reason"] for x in r.rejected)
        )


if __name__ == "__main__":
    unittest.main()
