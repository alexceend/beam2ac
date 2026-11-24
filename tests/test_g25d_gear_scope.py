"""G2.5d: ámbito de las 11 reglas de marchas (dt.*_2wd@3) sobre hechos ir.*.

Solo cambian las condiciones del ámbito (bng.drivetrain.traction → ir.drivetrain.layout,
bng.gearbox.type → ir.drivetrain.gearbox.kind); mismos operadores, valores y orden. La
herramienta lo hace solo con ``--scope`` y una lista cerrada.
"""

from __future__ import annotations

import pytest

import copy
import sys
import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import Fact, ResolutionEngine, VehicleContext, in_scope
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import ir_to_facts

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/catalog"))

import migrate_rule_to_ir as tool  # noqa: E402

SAMPLES = ROOT / "samples_beamng"
GEARS = "ac:drivetrain.ini/GEARS/"
GEAR_RULES = {
    "COUNT": "dt.gear_count_2wd",
    "GEAR_R": "dt.gear_r_2wd",
    **{f"GEAR_{k}": f"dt.gear_{k}_2wd" for k in range(1, 9)},
    "FINAL": "dt.final_2wd",
}
OLD_FACTS = ["bng.drivetrain.traction", "bng.gearbox.type"]
NEW_FACTS = ["ir.drivetrain.layout", "ir.drivetrain.gearbox.kind"]
SAME = tool.UNCHANGED + ("inputs", "conditions", "applies_when")


def catalog(without_v3: bool = False) -> Catalog:
    cat = Catalog.load(REPO_CATALOG)
    if without_v3:  # como antes de G2.5d
        for base in GEAR_RULES.values():
            cat.rules.pop(f"{base}@3")
    return cat


def ctx_with(facts: dict[str, object]) -> VehicleContext:
    return VehicleContext(
        "v", "p", {k: Fact(k, v, "", "test") for k, v in facts.items()}
    )


class TestDefinitions(unittest.TestCase):
    def test_eleven_new_versions_only_scope_facts_change(self) -> None:
        cat = catalog()
        self.assertEqual(len(GEAR_RULES), 11)
        for base in GEAR_RULES.values():
            with self.subTest(rule=base):
                v1, v2, v3 = (cat.rules[f"{base}@{n}"] for n in (1, 2, 3))
                self.assertEqual(v3.supersedes, v2.key)
                self.assertEqual(v2.supersedes, v1.key)
                vigentes = [r.key for r in cat.rules_for(v1.target) if r.id == base]
                self.assertEqual(vigentes, [v3.key])
                # @1 y @2 conservan su ámbito bng.*
                for old in (v1, v2):
                    self.assertEqual([c.fact for c in old.scope.conditions], OLD_FACTS)
                self.assertEqual([c.fact for c in v3.scope.conditions], NEW_FACTS)
                self.assertEqual(
                    [(c.op, c.value) for c in v3.scope.conditions],
                    [(c.op, c.value) for c in v2.scope.conditions],
                )
                self.assertEqual(
                    [(c.op, c.value) for c in v2.scope.conditions],
                    [(c.op, c.value) for c in v1.scope.conditions],
                )
                for name in ("level", "vehicle", "variant", "family"):
                    self.assertEqual(getattr(v3.scope, name), getattr(v2.scope, name))
                for name in SAME:
                    if name == "scope":
                        continue
                    self.assertEqual(
                        S._to_plain(getattr(v3, name)),
                        S._to_plain(getattr(v2, name)),
                        name,
                    )
                for name in ("source_doc", "evidence", "history"):
                    a, b = S._to_plain(getattr(v2, name)), S._to_plain(
                        getattr(v3, name)
                    )
                    self.assertEqual(b[: len(a)], a)
                    self.assertEqual(len(b), len(a) + 1)
                tool.check_scope(v2, v3)  # la comprobación de la herramienta


class TestTool(unittest.TestCase):
    def test_scope_facts_closed_list_checked_against_adapter(self) -> None:
        self.assertEqual(
            {k: tool.scope_fact(k) for k in OLD_FACTS}, dict(zip(OLD_FACTS, NEW_FACTS))
        )
        for bad in (
            "bng.drivetrain.driven_wheels",
            "bng.engine.type",
            "bng.gearbox.forward_count",
            "bng.mass.total",
            "project.anything",
        ):
            with self.subTest(fact=bad), self.assertRaises(tool.MigrationError):
                tool.scope_fact(bad)

    def test_normal_mode_still_never_touches_scope(self) -> None:
        cat = catalog(without_v3=True)
        with self.assertRaises(tool.MigrationError):  # entradas ya ir.*; ámbito no
            tool.migrate(cat.rules["dt.gear_1_2wd@2"], tool.legacy_to_ir())
        old = cat.rules["dt.gear_1_2wd@1"]
        new = tool.migrate(old, tool.legacy_to_ir())
        self.assertEqual(S._to_plain(new.scope), S._to_plain(old.scope))

    def test_refusals(self) -> None:
        cat = catalog()
        with self.assertRaises(tool.MigrationError):  # ya migrada
            tool.migrate_scope(cat.rules["dt.gear_1_2wd@3"])
        with self.assertRaises(tool.MigrationError):  # ámbito de vehículo
            tool.migrate_scope(cat.rules["dt.gear_1@2"])
        with self.assertRaises(tool.MigrationError):  # entradas aún bng.*
            tool.migrate_scope(cat.rules["dt.gear_1_2wd@1"])
        v2 = cat.rules["dt.gear_1_2wd@2"]
        other = copy.deepcopy(v2)
        other.scope.conditions.append(
            S.Condition(fact="bng.engine.type", op="eq", value="x")
        )
        with self.assertRaises(tool.MigrationError):  # hecho fuera de la lista
            tool.migrate_scope(other)

    def test_check_scope_rejects_any_other_change(self) -> None:
        v2 = catalog().rules["dt.gear_1_2wd@2"]
        good = tool.migrate_scope(v2, "2026-11-01")
        mutations = {
            "valor": lambda r: r.scope.conditions[0].value.append("AWD"),
            "operador": lambda r: setattr(r.scope.conditions[1], "op", "not_in"),
            "orden": lambda r: r.scope.conditions.reverse(),
            "hecho": lambda r: setattr(r.scope.conditions[0], "fact", "ir.x"),
            "condición de más": lambda r: r.scope.conditions.append(
                S.Condition(fact="ir.drivetrain.layout", op="exists")
            ),
            "nivel": lambda r: setattr(r.scope, "level", "generic"),
            "entradas": lambda r: r.inputs.update(x="ir.drivetrain.final_ratio"),
            "aplicabilidad": lambda r: setattr(r.applies_when[0], "value", 2),
            "transformación": lambda r: setattr(r.transform, "output_unit", "rad"),
        }
        for name, mutate in mutations.items():
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.subTest(change=name), self.assertRaises(tool.MigrationError):
                tool.check_scope(v2, bad)


class TestScopeEvaluation(unittest.TestCase):
    """Hechos ausentes o con otro valor: nunca se supone ni se recupera el bng.*."""

    def setUp(self) -> None:
        cat = catalog()
        self.v2 = cat.rules["dt.gear_1_2wd@2"].scope
        self.v3 = cat.rules["dt.gear_1_2wd@3"].scope

    def test_architectures(self) -> None:
        cases = [
            ("FWD", "manualGearbox", True),
            ("RWD", "sequentialGearbox", True),
            ("AWD", "manualGearbox", False),
            ("FWD", "automaticGearbox", False),
            ("XWD", "manualGearbox", False),
        ]
        for layout, box, expected in cases:
            with self.subTest(layout=layout, box=box):
                ctx = ctx_with(
                    {
                        "ir.drivetrain.layout": layout,
                        "ir.drivetrain.gearbox.kind": box,
                        "bng.drivetrain.traction": layout,
                        "bng.gearbox.type": box,
                    }
                )
                self.assertEqual(in_scope(self.v3, ctx)[0], expected)
                self.assertEqual(in_scope(self.v2, ctx)[0], expected)

    def test_missing_ir_fact_does_not_fall_back(self) -> None:
        both = {"bng.drivetrain.traction": "FWD", "bng.gearbox.type": "manualGearbox"}
        for present in (
            {},
            {"ir.drivetrain.gearbox.kind": "manualGearbox"},
            {"ir.drivetrain.layout": "FWD"},
        ):
            with self.subTest(present=present):
                ok, why = in_scope(self.v3, ctx_with({**both, **present}))
                self.assertIs(ok, False)
                self.assertIn("falta el hecho ir.drivetrain.", why)
                self.assertTrue(in_scope(self.v2, ctx_with(both))[0])
        ok, why = in_scope(self.v3, ctx_with({}))
        self.assertIs(ok, False)
        self.assertIn("falta el hecho ir.drivetrain.layout", why)
        self.assertIn("falta el hecho ir.drivetrain.gearbox.kind", why)


def facts_of(name: str, pc: str):
    from src.importers.beamng import import_preview
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    src = load_vehicle_dir(next(SAMPLES.glob(f"*/vehicles/{name}")), pc)
    return src, ir_to_facts(import_preview(preview_physics(src, 70.0), 70.0))


def resolve(src, facts, target: str, without_v3: bool = False):
    cat = catalog(without_v3)
    ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
    return ResolutionEngine(cat, ctx).resolve(target)


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestVehicles(unittest.TestCase):
    CASES = (  # (vehículo, pc, tracción, caja, marchas)
        ("hyacinth_sage", "70548.pc", "FWD", "manualGearbox", None),
        ("sucette_poubelle", "f5893.pc", "FWD", "manualGearbox", 4),
        ("quail_mirabilis", "fbd0e.pc", "RWD", "sequentialGearbox", 7),
    )

    @pytest.mark.ampliada
    def test_2wd_selected_and_equivalent_to_v2(self) -> None:
        for name, pc, layout, box, count in self.CASES:
            src, facts = facts_of(name, pc)
            self.assertEqual(facts["ir.drivetrain.layout"].value, layout)
            self.assertEqual(facts["ir.drivetrain.gearbox.kind"].value, box)
            for key, base in GEAR_RULES.items():
                with self.subTest(vehicle=name, key=key):
                    new = resolve(src, facts, GEARS + key)
                    old = resolve(src, facts, GEARS + key, without_v3=True)
                    self.assertEqual(new.rule, f"{base}@3")
                    self.assertEqual(old.rule, f"{base}@2")
                    self.assertEqual(
                        (new.value, new.unit, new.status, new.confidence, new.source),
                        (old.value, old.unit, old.status, old.confidence, old.source),
                    )
                    if count and key.startswith("GEAR_") and key != "GEAR_R":
                        k = int(key.split("_")[1])
                        expected = "known" if k <= count else "not_applicable"
                        self.assertEqual(new.status, expected)

    def test_quail_torque_vectoring_is_rwd(self) -> None:
        src, facts = facts_of("quail_mirabilis", "fbd0e.pc")
        self.assertEqual(facts["ir.drivetrain.layout"].value, "RWD")
        self.assertEqual(
            facts["ir.drivetrain.differentials.rear.kind"].value, "torqueVectoring"
        )
        self.assertNotIn("ir.drivetrain.differentials.front.kind", facts)

    def test_awd_stays_out_of_2wd_scope(self) -> None:
        src, facts = facts_of("autist_cuanto", "b78dc.pc")
        self.assertEqual(facts["ir.drivetrain.layout"].value, "AWD")
        cat = catalog()
        ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
        for base in GEAR_RULES.values():
            with self.subTest(rule=base):
                self.assertIs(in_scope(cat.rules[f"{base}@3"].scope, ctx)[0], False)
        for key in GEAR_RULES:
            with self.subTest(key=key):
                new = resolve(src, facts, GEARS + key)
                old = resolve(src, facts, GEARS + key, without_v3=True)
                self.assertNotIn("_2wd@", new.rule or "")
                self.assertEqual(new.to_dict(), old.to_dict())

    def test_missing_ir_layout_in_real_vehicle(self) -> None:
        src, facts = facts_of("sucette_poubelle", "f5893.pc")
        del facts["ir.drivetrain.layout"]  # el bng.* sigue presente
        self.assertIn("bng.drivetrain.traction", facts)
        r = resolve(src, facts, GEARS + "GEAR_1")
        # fuera del ámbito 2WD: solo queda la propuesta del Ibiza, sin aplicar (como
        # en un AWD); nunca la @3 con el hecho bng.* ni un valor supuesto
        self.assertEqual((r.source, r.rule), ("proposal", "dt.gear_1@2"))
        self.assertTrue(r.requires_approval)


if __name__ == "__main__":
    unittest.main()
