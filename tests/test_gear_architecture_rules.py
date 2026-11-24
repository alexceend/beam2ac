"""Relaciones de transmisión por arquitectura (2WD, caja manual o secuencial)."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_preview
from src.attrs.store import REPO_CATALOG, Catalog

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
GEARS = "ac:drivetrain.ini/GEARS/"


def resolve(d: Path, pc: str, targets: list[str]):
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    cat = Catalog.load(REPO_CATALOG)
    pv = preview_physics(load_vehicle_dir(d, pc), 70.0)
    eng = ResolutionEngine(cat, context_from_preview(pv, cat, 70.0))
    return {t: eng.resolve(t) for t in targets}


class TestRules(unittest.TestCase):
    def test_architecture_scope(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        for key in ("COUNT", "GEAR_R", "GEAR_1", "GEAR_8", "FINAL"):
            rules = {r.key: r for r in cat.rules_for(GEARS + key)}
            arch = [r for r in rules.values() if r.scope.level == "architecture"]
            self.assertEqual(len(arch), 1, key)
            conds = {c.fact: c.value for c in arch[0].scope.conditions}
            # G2.5d: ámbito sobre hechos ir.* (mismos operadores y valores que @1/@2)
            self.assertEqual(conds["ir.drivetrain.layout"], ["FWD", "RWD"])
            self.assertEqual(
                conds["ir.drivetrain.gearbox.kind"],
                ["manualGearbox", "sequentialGearbox"],
            )
            # la regla del Ibiza sigue vigente
            self.assertTrue(any(r.scope.level == "vehicle" for r in rules.values()))


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestIbiza(unittest.TestCase):
    def test_vehicle_rule_still_wins(self) -> None:
        res = resolve(IBIZA, "35b84.pc", [GEARS + "GEAR_1", GEARS + "FINAL"])
        self.assertEqual(res[GEARS + "GEAR_1"].rule, "dt.gear_1@2")  # G2.5b
        self.assertEqual(res[GEARS + "FINAL"].value, 5.98)


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestSamples(unittest.TestCase):
    def test_2wd_resolved_awd_stays_proposal(self) -> None:
        quail = next(SAMPLES.glob("*/vehicles/quail_mirabilis"))
        res = resolve(quail, "fbd0e.pc", [GEARS + "GEAR_7", GEARS + "FINAL"])
        self.assertEqual(
            res[GEARS + "GEAR_7"].rule, "dt.gear_7_2wd@3"
        )  # G2.4: entradas ir.*; G2.5d: ámbito ir.*
        self.assertEqual(res[GEARS + "GEAR_7"].value, 0.6)  # caja secuencial
        self.assertEqual(res[GEARS + "FINAL"].status, "known")
        autist = next(SAMPLES.glob("*/vehicles/autist_cuanto"))
        res = resolve(autist, "b78dc.pc", [GEARS + "FINAL"])
        # G2.30: regla propia del AWD manual del 40340 (la de arquitectura 2WD sigue sin aplicarse)
        self.assertEqual(res[GEARS + "FINAL"].rule, "dt.final_awd_manual_40340@1")


if __name__ == "__main__":
    unittest.main()
