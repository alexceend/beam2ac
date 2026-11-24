"""G2.44: mods 38022 por reglas clonadas del 40340; el Ibiza no cambia."""

import json
import unittest
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.importers.beamng import _static_or_dynamic_stiffness

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / "output/g244_2026-11-14/mods_src"
BASE = ROOT / "output/g244_2026-11-14/ibiza_base_pre38022.json"


class TestStiffnessFallback(unittest.TestCase):
    def test_static_positive_wins_then_dynamic_then_none(self) -> None:
        dyn = {"vosc_3hz": {"stiffness": 1.1e6}}
        self.assertEqual(
            _static_or_dynamic_stiffness({"vertical_stiffness_static": 5e5}, dyn), 5e5
        )
        self.assertEqual(
            _static_or_dynamic_stiffness({"vertical_stiffness_static": -1e6}, dyn),
            1.1e6,
        )
        self.assertIsNone(
            _static_or_dynamic_stiffness({"vertical_stiffness_static": -1e6}, {})
        )


class TestCatalog38022(unittest.TestCase):
    def test_clones_are_38022_only_provisional_and_exclude_ibiza_aero(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        clones = [
            r
            for r in cat.rules.values()
            if r.id.endswith("_38022") and r.version == 1 and "G2.44" in r.notes
        ]
        self.assertGreater(len(clones), 200)
        for r in clones:
            with self.subTest(rule=r.key):
                self.assertEqual(r.status, "provisional")
                vs = [
                    c.value
                    for c in r.conditions
                    if c.fact == "ir.formats.exporter_version"
                ]
                self.assertEqual(vs, [["38022"]])
                if r.id.startswith("aero."):
                    self.assertIn(
                        ("ir.identity.vehicle_id", "ne", "seat_ibiza_acfr"),
                        [(c.fact, c.op, c.value) for c in r.conditions],
                    )


@unittest.skipUnless(
    MODS.is_dir() and BASE.is_file(),
    "mods extraídos / instantánea del Ibiza no disponibles",
)
class TestMods(unittest.TestCase):
    def test_four_mods_have_no_blockers_with_their_own_measurements(self) -> None:
        from src.app.diagnostics import inspect_vehicle

        for mod, vid in (
            ("composed_fox", "composed_fox"),
            ("porsche_gt3_rs", "porsche_gt3_rs"),
            ("rune_susui_espol_vtec", "susui_espol_vtec"),
            ("spicy_mandala", "spicy_mandala"),
        ):
            r = inspect_vehicle(str(MODS / mod / "vehicles" / vid))
            with self.subTest(mod):
                self.assertEqual(r["blockers"], [])
                self.assertTrue(r["exportable_today"])

    def test_ibiza_resolution_unchanged(self) -> None:
        from src.convert.service import ConversionSession
        from src.ir.architecture import classify
        from tests.test_ir import IBIZA_PROJECT, ir_of

        ir = ir_of("seat_ibiza_acfr")
        classify(ir)
        s = ConversionSession(ir, Catalog.load(REPO_CATALOG, IBIZA_PROJECT))
        base = json.loads(BASE.read_text("utf-8"))
        self.assertEqual(s.target().files, base["files"])
        res = {
            r.target: [str(r.value), r.source, r.rule, r.status] for r in s.resolve()
        }
        for k, v in base["res"].items():
            self.assertEqual(res.get(k), v, k)


if __name__ == "__main__":
    unittest.main()
