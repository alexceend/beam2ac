"""G2.25: DAMP y RELAXATION_LENGTH del neumático del 200409 (decisión del usuario,
medianas del corpus de AC) y error del exportador si faltan (AC leería 0)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.project import export

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "catalog" / "rules"


class TestTyreDampRelaxRules(unittest.TestCase):
    def test_four_rules_constant_provisional_200409(self):
        expected = {"damp": (500.0, "N·s/m"), "relaxation_length": (0.0725, "m")}
        for axle in ("front", "rear"):
            for key, (value, unit) in expected.items():
                p = RULES / f"tyre.{axle}.{key}_200409@1.json"
                r = json.loads(p.read_text("utf-8"))
                self.assertEqual(
                    r["target"], f"ac:tyres.ini/{axle.upper()}/{key.upper()}"
                )
                self.assertEqual(r["transform"]["id"], "constant")
                self.assertEqual(r["transform"]["params"]["value"], value)
                self.assertEqual(r["transform"]["output_unit"], unit)
                self.assertEqual(r["status"], "provisional")
                conds = {(c["fact"], c["op"]) for c in r["conditions"]}
                self.assertIn(("ir.formats.exporter_version", "in"), conds)
                ver = next(
                    c
                    for c in r["conditions"]
                    if c["fact"] == "ir.formats.exporter_version"
                )
                self.assertEqual(ver["value"], ["200409"])  # nunca amplía el ámbito

    def test_values_inside_corpus_range(self):
        survey = (
            ROOT / "output/g225_2026-11-09/diagnostico_collider/encuesta_neumatico.json"
        )
        if not survey.is_file():
            self.skipTest("encuesta de G2.25 no disponible")
        res = json.loads(survey.read_text("utf-8"))["resumen"]
        self.assertEqual(res["sin_damp"], 0)
        self.assertEqual(res["sin_relax"], 0)
        self.assertTrue(res["damp_v10"]["min"] <= 500.0 <= res["damp_v10"]["max"])
        self.assertTrue(res["relax_v10"]["min"] <= 0.0725 <= res["relax_v10"]["max"])

    def test_exporter_treats_absence_as_error(self):
        for axle in ("FRONT", "REAR"):
            for key in ("DAMP", "RELAXATION_LENGTH"):
                self.assertIn(f"ac:tyres.ini/{axle}/{key}", export.ZERO_IS_WRONG_KEYS)


if __name__ == "__main__":
    unittest.main()
