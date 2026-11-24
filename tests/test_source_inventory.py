"""Inventario de archivos de un vehículo por rol y uso (source_inventory)."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.pipeline.source_inventory import classify, inventory, inventory_dir

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"


class TestClassify(unittest.TestCase):
    def test_roles(self) -> None:
        cases = {
            "a/part.jbeam": "interpretado",
            "35b84.pc": "interpretado",
            "info.json": "interpretado",
            "info_35b84.json": "contrastado",
            "paints.meta": "contrastado",
            "35b84.materials.json": "interpretado",
            "dynamicDecals/main.materials.json": "no_interpretado",
            "lua/powertrain/camsoEngine.lua": "solo_huella",
            "lua/a/b/c/deep.lua": "solo_huella",
            "x___y.car": "no_interpretado",
            "license.txt": "no_interpretado",
            "tiredebug.txt": "no_interpretado",
            "mystery.bin": "desconocido",
            "notes.txt": "desconocido",
        }
        for path, status in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify(path).status, status)

    def test_textures_by_use(self) -> None:
        mats = {
            "m": {
                "Stages": [
                    {
                        "diffuseMap": "vehicles/x/col.dds",
                        "normalMap": "vehicles/x/nrm.dds",
                    }
                ]
            }
        }
        inv = inventory(["col.dds", "nrm.dds", "loose.dds", "odd.xyz"], mats, "m.json")
        st = {f["path"]: f["status"] for f in inv["files"]}
        self.assertEqual(st["col.dds"], "interpretado")
        self.assertEqual(st["nrm.dds"], "no_interpretado")
        self.assertEqual(st["loose.dds"], "no_interpretado")
        self.assertEqual(inv["unknown"], ["odd.xyz"])
        self.assertEqual(inv["by_status"]["desconocido"], 1)


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestSamples(unittest.TestCase):
    def test_no_unknown_files_and_meta_only_where_present(self) -> None:
        # exportador 40340 (los 5 de 2026-10-30); los 7 del 200409 no traen
        # <config>.materials.json (usan materials.cs): informe G2.8 §6.2
        for vd in sorted(SAMPLES.glob("thershow_*/vehicles/*")):
            pc = next(vd.glob("*.pc"))
            with self.subTest(vehicle=vd.name):
                inv = inventory_dir(vd, vd / f"{pc.stem}.materials.json")
                self.assertEqual(inv["unknown"], [])
                has_meta = (vd / "paints.meta").is_file()
                self.assertEqual(
                    inv["by_role"].get(
                        "pinturas DefaultN (binario del exportador de Automation)", 0
                    ),
                    int(has_meta),
                )


if __name__ == "__main__":
    unittest.main()
