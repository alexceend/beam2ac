"""Candidatos de emparejamiento BeamNG ↔ AC (src/catalog/pairing.py), sintéticos."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.catalog import pairing as P


def bng(name: str, brand: str = "", power=None, weight=None, automation=True):
    cfgs = [{"file": "info_x.json", "power_hp": power, "weight_kg": weight}]
    return P.BngVehicle("x.zip", name, brand, None, cfgs, automation)


class TestCompare(unittest.TestCase):
    def test_full_model_and_power_is_candidate(self) -> None:
        c = P.compare(
            bng("Porsche GT3 RS", power=499, weight=1190),
            P.AcCar("ks_porsche_911_gt3_rs", "Porsche", None, 500, 1420),
        )
        self.assertEqual(c.classification, "candidato")
        self.assertTrue(any("masa" in x and "orientativo" in x for x in c.against))
        self.assertTrue(any("-0.2%" in x for x in c.for_))  # diferencia con signo
        self.assertEqual(c.review, "pendiente")
        self.assertTrue(any("Automation" in x for x in c.not_comparable))

    def test_partial_model_is_hypothesis(self) -> None:
        c = P.compare(
            bng("Porsche GT3 RS", power=499),
            P.AcCar("ks_porsche_911_gt3_r_2016", "Porsche", None, 500, None),
        )
        self.assertEqual(c.classification, "hipótesis")
        self.assertTrue(any("['rs']" in x for x in c.against))

    def test_power_out_of_tolerance_is_hypothesis(self) -> None:
        c = P.compare(
            bng("Porsche GT3 RS", power=499),
            P.AcCar("ks_porsche_911_gt3_rs", "Porsche", None, 300, None),
        )
        self.assertEqual(c.classification, "hipótesis")

    def test_no_data_still_candidate_but_marked(self) -> None:
        c = P.compare(
            bng("Porsche GT3 RS"),
            P.AcCar("ks_porsche_911_gt3_rs", "Porsche", None, None, None),
        )
        self.assertEqual(c.classification, "candidato")
        self.assertIn("sin potencia ni masa comparables", c.not_comparable)

    def test_no_brand_or_no_model_no_pair(self) -> None:
        # marcas ficticias de BeamNG: no se infiere su coche real
        self.assertIsNone(
            P.compare(
                bng("ETK 800", "ETK", automation=False),
                P.AcCar("ks_bmw_m3_e30", "BMW", None, 200, 1200),
            )
        )
        self.assertIsNone(
            P.compare(
                bng("Porsche Something"),
                P.AcCar("ks_porsche_911_gt3_rs", "Porsche", None, 1, 1),
            )
        )

    def test_number_parsing(self) -> None:
        self.assertEqual(P.number("500hp"), 500.0)
        self.assertEqual(P.number("1,25 kg"), 1.25)
        self.assertIsNone(P.number("--km/h"))
        self.assertIsNone(P.number(True))


class TestLoaders(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def test_ac_inventory_excludes_project_packages(self) -> None:
        inv = self.tmp / "ac.json"
        inv.write_text(
            json.dumps(
                {
                    "cars": [
                        {"id": "beam2ac_test", "brand": "Seat", "specs": {}},
                        {
                            "id": "ks_x",
                            "brand": "X",
                            "year": 2001,
                            "specs": {"bhp": "100bhp"},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        cars = P.load_ac_inventory(inv)
        self.assertEqual([c.id for c in cars], ["ks_x"])
        self.assertEqual((cars[0].year, cars[0].power_hp), (2001, 100.0))

    def test_mod_zip_metadata(self) -> None:
        mods = self.tmp / "mods"
        mods.mkdir()
        with zipfile.ZipFile(mods / "m.zip", "w") as zf:
            zf.writestr(
                "vehicles/m/info.json",
                json.dumps(
                    {
                        "Name": "Porsche GT3 RS",
                        "Brand": "",
                        "Type": "Automation",
                        "Author": "Camshaft Software LTD",
                        "ExporterVersion": 38022,
                    }
                ),
            )
            zf.writestr(
                "vehicles/m/info_a.json", json.dumps({"Power": 499, "Weight": 1190})
            )
        (mods / "roto.zip").write_bytes(b"no es un zip")
        v = P.load_mod_zips(mods)
        self.assertEqual(len(v), 1)
        self.assertTrue(v[0].automation)
        self.assertEqual(v[0].configs[0]["power_hp"], 499.0)


if __name__ == "__main__":
    unittest.main()
