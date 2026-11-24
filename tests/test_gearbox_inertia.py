"""``[GEARBOX] INERTIA`` del 200409 (investigación 2026-11-10).

Sin la clave AC usa 1,0 kg·m² de inercia de embrague (``Drivetrain`` [C]; medido en las
sesiones de G2.26). La regla escribe el mínimo del corpus de AC solo en el 200409; en el
resto la clave queda «no aplica» (D-INV-GBI) y los proyectos anteriores no cambian.
"""

from __future__ import annotations

import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.project.export import ZERO_IS_WRONG_KEYS, _blocker_severity

ROOT = Path(__file__).resolve().parents[1]
TARGET = "ac:drivetrain.ini/GEARBOX/INERTIA"
RULE = "dt.gearbox_inertia_200409@1"
PROTHEAN = ROOT / "samples_beamng/2020_rpm_prothean_s/vehicles/2020_rpm_prothean_s"
PORTLAND = (
    ROOT / "samples_beamng/2021_rpm_portland_touring/vehicles/2021_rpm_portland_touring"
)
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"


@lru_cache(maxsize=None)
def resolved(vehicle_dir: Path, pc: str):
    from src.attrs.report import resolve_vehicle
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    pv = preview_physics(load_vehicle_dir(vehicle_dir, pc), 0.0)
    _, results = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), "ac_physics", 0.0)
    return {r.target: r for r in results}[TARGET]


class TestGearboxInertia(unittest.TestCase):
    def test_rule_scope(self):
        cat = Catalog.load(REPO_CATALOG)
        rule = cat.rules[RULE]
        conds = {(c.fact, c.op, str(c.value)) for c in rule.conditions}
        self.assertIn(("ir.formats.exporter_version", "in", "['200409']"), conds)
        self.assertEqual(rule.status, "provisional")
        self.assertEqual(rule.kind, "reference")
        # AC no admite 0 (0 = 1,0 kg·m²); el valor está en el rango del corpus de AC
        self.assertTrue(0.01 <= rule.transform.params["value"] <= 0.045)
        app = cat.rules["app.gearbox.inertia@1"]
        self.assertEqual(
            [(c.fact, c.value) for c in app.applies_when],
            [("ir.formats.exporter_version", ["200409"])],
        )

    def test_200409_gets_value(self):
        for vd in (PROTHEAN, PORTLAND):  # DCT y automática con convertidor
            if not vd.is_dir():
                self.skipTest("sin samples_beamng")
            r = resolved(vd, f"{vd.name}.pc")
            self.assertEqual(
                (r.status, r.value, r.rule), ("inferred", 0.01, RULE), vd.name
            )

    @unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
    def test_38022_not_applicable(self):
        # G2.39: el 38022 recibe dt.gearbox_inertia_38022@1 (D-INV-GBI)
        r = resolved(IBIZA, "35b84.pc")
        self.assertEqual((r.status, r.value), ("inferred", 0.01))

    def test_car_basics_200409(self):
        """G2.27: combustible, nombre y FFMULT (clones del Ibiza y una referencia)."""
        if not PROTHEAN.is_dir():
            self.skipTest("sin samples_beamng")
        from src.attrs.report import resolve_vehicle
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        res = {}
        for vd, pc in ((PROTHEAN, f"{PROTHEAN.name}.pc"), (IBIZA, "35b84.pc")):
            pv = preview_physics(load_vehicle_dir(vd, pc), 0.0)
            _, results = resolve_vehicle(
                pv, Catalog.load(REPO_CATALOG), "ac_physics", 0.0
            )
            res[vd.name] = {r.target: r for r in results}
        new, old = res[PROTHEAN.name], res[IBIZA.name]
        for target, value, rule in (
            ("ac:car.ini/FUEL/FUEL", 52.5, "car.fuel_200409@1"),
            ("ac:car.ini/FUEL/MAX_FUEL", 70, "car.max_fuel_200409@1"),
            ("ac:car.ini/CONTROLS/FFMULT", 1.95, "ctrl.ffmult_200409@1"),
        ):
            self.assertEqual(
                (new[target].value, new[target].rule), (value, rule), target
            )
            self.assertNotEqual(
                old[target].rule, rule, target
            )  # el Ibiza sigue con lo suyo
        self.assertEqual(
            new["ac:car.ini/INFO/SCREEN_NAME"].rule, "car.screen_name_200409@1"
        )

    def test_missing_key_is_export_error(self):
        self.assertIn(TARGET, ZERO_IS_WRONG_KEYS)
        self.assertEqual(_blocker_severity({"target": TARGET}, {}), "error")


if __name__ == "__main__":
    unittest.main()
