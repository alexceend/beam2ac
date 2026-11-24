"""G2.4: marchas por arquitectura (dt.*_2wd) y masa total con entradas del IR."""

from __future__ import annotations

import pytest

import json
import unittest
from pathlib import Path

from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import ir_to_facts

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
GEARS = "ac:drivetrain.ini/GEARS/"
GEAR_RULES = {
    "COUNT": "dt.gear_count_2wd",
    "GEAR_R": "dt.gear_r_2wd",
    **{f"GEAR_{k}": f"dt.gear_{k}_2wd" for k in range(1, 9)},
    "FINAL": "dt.final_2wd",
}
TOTALMASS = "ac:car.ini/BASIC/TOTALMASS"
MIGRATED = [*GEAR_RULES.values(), "car.totalmass"]


def as_of_g24(cat: Catalog) -> Catalog:
    """Catálogo como al cerrar G2.4: sin las @3 de G2.5d (ámbito ir.*), que se
    prueban en tests/test_g25d_gear_scope.py."""
    for base in GEAR_RULES.values():
        cat.rules.pop(f"{base}@3", None)
    return cat


def catalog(drop_new: bool = False) -> Catalog:
    cat = as_of_g24(Catalog.load(REPO_CATALOG))
    if drop_new:  # como antes de G2.4: las @1 vuelven a estar vigentes
        for base in MIGRATED:
            cat.rules.pop(f"{base}@2")
    return cat


def facts_of(d: Path, pc: str):
    from src.importers.beamng import import_preview
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    src = load_vehicle_dir(d, pc)
    return src, ir_to_facts(import_preview(preview_physics(src, 70.0), 70.0))


def resolve(src, facts, target: str, drop_new: bool = False, project=None):
    cat = as_of_g24(Catalog.load(REPO_CATALOG, project)) if project else catalog()
    if drop_new:
        for base in MIGRATED:
            cat.rules.pop(f"{base}@2")
    ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
    return ResolutionEngine(cat, ctx).resolve(target)


class TestDefinitions(unittest.TestCase):
    def test_new_versions_change_only_references(self) -> None:
        cat = catalog()
        for base in MIGRATED:
            with self.subTest(rule=base):
                v1, v2 = cat.rules[f"{base}@1"], cat.rules[f"{base}@2"]
                self.assertEqual(v2.supersedes, v1.key)
                vigentes = [r.key for r in cat.rules_for(v1.target) if r.id == base]
                self.assertEqual(vigentes, [v2.key])
                refs = list(v2.inputs.values()) + [
                    c.fact for c in v2.conditions + v2.applies_when
                ]
                self.assertFalse(
                    [r for r in refs if r.startswith(("bng.", "project."))]
                )
                self.assertEqual(
                    [(c.op, c.value) for c in v2.applies_when],
                    [(c.op, c.value) for c in v1.applies_when],
                )
                for field in (
                    "transform",
                    "scope",
                    "status",
                    "assumptions",
                    "validations",
                    "kind",
                    "target",
                    "exceptions",
                ):
                    a = json.dumps(getattr(v1, field), default=lambda o: o.__dict__)
                    b = json.dumps(getattr(v2, field), default=lambda o: o.__dict__)
                    self.assertEqual(a, b, field)

    def test_architecture_scope_is_untouched(self) -> None:
        """Las condiciones del ámbito de arquitectura siguen con hechos bng.*:
        cambiarlas es cambiar el ámbito (pendiente de decisión)."""
        cat = catalog()
        for base in GEAR_RULES.values():
            facts = [c.fact for c in cat.rules[f"{base}@2"].scope.conditions]
            self.assertEqual(facts, ["bng.drivetrain.traction", "bng.gearbox.type"])

    def test_inputs(self) -> None:
        cat = catalog()
        self.assertEqual(
            cat.rules["dt.gear_3_2wd@2"].inputs,
            {"x": "ir.drivetrain.gearbox.forward_ratios.3"},
        )
        self.assertEqual(
            cat.rules["dt.final_2wd@2"].inputs, {"x": "ir.drivetrain.final_ratio"}
        )
        self.assertEqual(
            cat.rules["car.totalmass@2"].inputs,
            {
                "total": "ir.mass.total",
                "driver": "ir.mass.driver",
                "hub_front": "ac:suspensions.ini/FRONT/HUB_MASS",
                "hub_rear": "ac:suspensions.ini/REAR/HUB_MASS",
            },
        )


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestGears(unittest.TestCase):
    @pytest.mark.ampliada
    def test_selected_and_equivalent_to_v1(self) -> None:
        for name, pc, count in (
            ("sucette_poubelle", "f5893.pc", 4),
            ("quail_mirabilis", "fbd0e.pc", 7),
        ):
            src, facts = facts_of(next(SAMPLES.glob(f"*/vehicles/{name}")), pc)
            for key, base in GEAR_RULES.items():
                with self.subTest(vehicle=name, key=key):
                    new = resolve(src, facts, GEARS + key)
                    old = resolve(src, facts, GEARS + key, drop_new=True)
                    self.assertEqual(new.rule, f"{base}@2")
                    self.assertEqual(
                        (new.value, new.unit, new.status, new.confidence),
                        (old.value, old.unit, old.status, old.confidence),
                    )
                    if key.startswith("GEAR_") and key != "GEAR_R":
                        k = int(key.split("_")[1])
                        expected = "known" if k <= count else "not_applicable"
                        self.assertEqual(new.status, expected)

    def test_awd_not_in_architecture_scope(self) -> None:
        src, facts = facts_of(
            next(SAMPLES.glob("*/vehicles/autist_cuanto")), "b78dc.pc"
        )
        r = resolve(src, facts, GEARS + "FINAL")
        # G2.30: regla propia de la caja manual AWD del 40340; nunca la de arquitectura 2WD
        self.assertEqual(r.rule, "dt.final_awd_manual_40340@1")
        self.assertNotIn("_2wd@", r.rule or "")

    def test_missing_ratio_is_not_invented(self) -> None:
        src, facts = facts_of(
            next(SAMPLES.glob("*/vehicles/sucette_poubelle")), "f5893.pc"
        )
        del facts["ir.drivetrain.gearbox.forward_ratios.2"]
        r = resolve(src, facts, GEARS + "GEAR_2")
        # la @2 no se aplica. Desde G2.5b la regla del Ibiza (dt.gear_2@2) también lee
        # el hecho ir.*, así que tampoco hay propuesta: ningún valor (nunca inventado)
        self.assertNotEqual(r.rule, "dt.gear_2_2wd@2")
        for rule in ("dt.gear_2_2wd@2", "dt.gear_2@2"):
            self.assertTrue(
                any(
                    x["rule"] == rule and "falta el hecho" in x["reason"]
                    for x in r.rejected
                ),
                rule,
            )
        self.assertEqual(r.source, "")
        self.assertIsNone(r.value)
        self.assertFalse(r.requires_approval)


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestTotalMass(unittest.TestCase):
    PROJECT = ROOT / "output/projects/ibiza_35b84_b1"

    def test_ibiza_selected_and_equivalent(self) -> None:
        src, facts = facts_of(IBIZA, "35b84.pc")
        project = self.PROJECT if self.PROJECT.is_dir() else None
        new = resolve(src, facts, TOTALMASS, project=project)
        old = resolve(src, facts, TOTALMASS, drop_new=True, project=project)
        self.assertEqual(new.rule, "car.totalmass@2")
        self.assertEqual(old.rule, "car.totalmass@1")
        self.assertEqual(
            (new.value, new.unit, new.status, new.confidence),
            (old.value, old.unit, old.status, old.confidence),
        )
        used = {i.get("fact") or i.get("attribute") for i in new.inputs}
        self.assertIn("ir.mass.total", used)
        self.assertIn("ir.mass.driver", used)

    def test_missing_driver_mass_is_not_zero(self) -> None:
        src, facts = facts_of(IBIZA, "35b84.pc")
        del facts["ir.mass.driver"]
        project = self.PROJECT if self.PROJECT.is_dir() else None
        r = resolve(src, facts, TOTALMASS, project=project)
        self.assertNotEqual(r.rule, "car.totalmass@2")
        self.assertTrue(
            r.value is None or r.source == "decision",
            "sin masa del piloto la regla no puede aplicarse con un 0 implícito",
        )


if __name__ == "__main__":
    unittest.main()
