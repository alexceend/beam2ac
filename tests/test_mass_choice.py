"""Conflicto de masa con la ficha de Automation: la elección del usuario (src/app/mass_choice.py)."""

import tempfile
import unittest
from pathlib import Path

from src.app import diagnostics as D
from src.app import mass_choice as MC
from src.app.workspace import WorkspaceError, _save_mass_decisions
from src.attrs.store import REPO_CATALOG, Catalog

EAGLE = next(
    iter(Path("samples_beamng").glob("thershow_eagle*/vehicles/eagle*")), None
)  # Weight 974 frente a 972,91 kg medidos (Δ −1,09)
TOTALMASS = "ac:car.ini/BASIC/TOTALMASS"


class TestParse(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertIsNone(MC.parse_choice(""))
        self.assertEqual(MC.parse_choice("declared"), {"mode": "declared"})
        self.assertEqual(MC.parse_choice("980,5"), {"mode": "manual", "kg": 980.5})
        for bad in ("abc", "-3", "0"):
            with self.assertRaises(WorkspaceError):
                MC.parse_choice(bad)


@unittest.skipUnless(EAGLE, "corpus no disponible")
class TestEagle(unittest.TestCase):
    def inspect(self, choice=None):
        return D.inspect_vehicle(str(EAGLE), "c27b2.pc", 0, mass_choice=choice)

    def test_conflict_blocks_until_user_chooses(self) -> None:
        r = self.inspect()
        self.assertFalse(r["exportable_today"])
        m = r["mass_conflict"]
        self.assertIsNone(m["choice"])
        self.assertEqual((m["declared_kg"], m["measured_kg"]), (974.0, 972.91))
        self.assertIn(TOTALMASS, m["keys"])
        self.assertEqual(r["mass_decisions"], [])

    def test_each_choice_unblocks_and_totalmass_follows(self) -> None:
        base = {b["target"] for b in self.inspect()["blockers"]}
        self.assertIn(TOTALMASS, base)
        vals = {}
        for choice in ("measured", "declared", "980.91"):
            r = self.inspect(choice)
            self.assertTrue(r["exportable_today"], choice)
            vals[choice] = next(
                d["value"] for d in r["mass_decisions"] if d["target"] == TOTALMASS
            )
        self.assertAlmostEqual(vals["declared"] - vals["measured"], 1.09, places=3)
        self.assertAlmostEqual(vals["980.91"] - vals["measured"], 8.0, places=3)

    def test_decisions_persist_in_project(self) -> None:
        r = self.inspect("declared")
        with tempfile.TemporaryDirectory() as tmp:
            _save_mass_decisions(Path(tmp), r)
            cat = Catalog.load(REPO_CATALOG, Path(tmp))
            vid = r["mass_conflict"]["validation"].rsplit(".", 2)[0]
            self.assertEqual(len(cat.active_decisions(vid, TOTALMASS)), 1)


if __name__ == "__main__":
    unittest.main()
