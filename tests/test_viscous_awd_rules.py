"""AWD con diferenciales ``viscous`` (agus_1_nrt_1, 40340): las reglas derivadas resuelven TYPE y los 10 AWD/*.

Aproximación sin probar en AC (docs/research/diferencial_viscoso_y_electronica_ac_2026-11-21.md §1).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGUS = ROOT / "samples_beamng" / "agus_1_nrt_1" / "vehicles" / "agus_1_nrt_1"
RULES = ROOT / "catalog" / "rules"


class TestViscousRules(unittest.TestCase):
    def test_all_viscous_rules_are_provisional_and_gated_on_viscous(self) -> None:
        files = sorted(RULES.glob("dt.*viscous_40340@1.json"))
        self.assertEqual(len(files), 11)
        for p in files:
            d = json.loads(p.read_text("utf-8"))
            self.assertEqual(d["status"], "provisional", p.name)
            facts = {(c["fact"], str(c["value"])) for c in d["conditions"]}
            self.assertIn(
                ("ir.drivetrain.differentials.center.kind", "viscous"), facts, p.name
            )
            self.assertTrue(
                any("viscousTorque" in a or "viscous" in a for a in d["assumptions"]),
                p.name,
            )

    @unittest.skipUnless(AGUS.is_dir(), "muestra agus_1_nrt_1 ausente")
    def test_agus_awd_keys_resolve(self) -> None:
        from src.app import diagnostics

        r = diagnostics.inspect_vehicle(str(AGUS), "7dfd7.pc")
        self.assertTrue(r["exportable_today"], r["blockers"])
        self.assertEqual(r["blockers"], [])

    @unittest.skipUnless(AGUS.is_dir(), "muestra agus_1_nrt_1 ausente")
    def test_agus_values(self) -> None:
        """TYPE, reparto y precargas (2·viscousTorque; el central además ×FINAL), POWER/COAST = 0."""
        from src.attrs.report import resolve_vehicle
        from src.attrs.store import REPO_CATALOG, Catalog
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        pv = preview_physics(load_vehicle_dir(AGUS, "7dfd7.pc"), 70.0)
        _, res = resolve_vehicle(pv, Catalog.load(REPO_CATALOG))
        got = {
            r.target.split("drivetrain.ini/")[1]: r.value
            for r in res
            if "drivetrain.ini/" in r.target
        }
        self.assertEqual(got["TRACTION/TYPE"], "AWD")
        self.assertAlmostEqual(got["AWD/FRONT_SHARE"], 43.0, places=3)
        self.assertAlmostEqual(got["AWD/FRONT_DIFF_PRELOAD"], 380.78, places=2)
        self.assertAlmostEqual(got["AWD/REAR_DIFF_PRELOAD"], 380.78, places=2)
        self.assertAlmostEqual(
            got["AWD/CENTRE_DIFF_PRELOAD"], 2 * 190.39 * 7.01, places=2
        )
        for k in ("CENTRE", "FRONT", "REAR"):
            self.assertEqual(got[f"AWD/{k}_DIFF_POWER"], 0)
            self.assertEqual(got[f"AWD/{k}_DIFF_COAST"], 0)


if __name__ == "__main__":
    unittest.main()
