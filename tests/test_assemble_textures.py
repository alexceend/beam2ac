"""Paso de texturas del generador antiguo (src.ac.assemble.texture_sources)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.ac.assemble import materials_json, texture_sources
from src.ac.kn5_materials import diffuse_sources
from src.jbeam import lax_json

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"


def _infos(d: Path, pc: str):
    info = json.loads((d / "info.json").read_text(encoding="utf-8"))
    cfg = json.loads((d / f"info_{Path(pc).stem}.json").read_text(encoding="utf-8"))
    return info, cfg


def _material_names(d: Path, pc: str) -> list[str]:
    return sorted(lax_json.load(materials_json(d, d / pc)).value)


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestIbizaUnchanged(unittest.TestCase):
    def test_same_sources_as_previous_code(self) -> None:
        info, cfg = _infos(IBIZA, "35b84.pc")
        names = _material_names(IBIZA, "35b84.pc")
        # código anterior: paints[defaultPaintName1]["baseColor"][:3]
        paint = cfg["defaultPaintName1"]
        old = diffuse_sources(
            names,
            materials_json(IBIZA, IBIZA / "35b84.pc"),
            IBIZA,
            tuple(info["paints"][paint]["baseColor"][:3]),
        )
        new, report = texture_sources(names, info, cfg, IBIZA, IBIZA / "35b84.pc")
        self.assertEqual(
            [(s.material, s.kind, s.texture_name, s.data) for s in old],
            [(s.material, s.kind, s.texture_name, s.data) for s in new],
        )
        self.assertEqual(report["paint"], "Default1")
        self.assertNotIn("paint_unresolved", report)


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestWithoutDefaultPaint(unittest.TestCase):
    def test_sucette_textures_without_paint(self) -> None:
        d = next(SAMPLES.glob("*/vehicles/sucette_poubelle"))
        info, cfg = _infos(d, "f5893.pc")
        names = _material_names(d, "f5893.pc")
        sources, report = texture_sources(names, info, cfg, d, d / "f5893.pc")
        self.assertIsNone(report["paint"])
        self.assertNotIn("paint_unresolved", report)  # ningún material la necesita
        self.assertNotIn("pintura_sin_resolver", report["kinds"])
        self.assertGreater(report["kinds"]["diffuseMap"], 0)

    def test_paint_needed_but_unresolved_is_reported(self) -> None:
        d = next(SAMPLES.glob("*/vehicles/autist_cuanto"))
        info, cfg = _infos(d, "b78dc.pc")
        cfg = {k: v for k, v in cfg.items() if k != "defaultPaintName1"}
        names = _material_names(d, "b78dc.pc")
        sources, report = texture_sources(names, info, cfg, d, d / "b78dc.pc")
        self.assertIsNone(report["paint"])
        self.assertEqual(report["kinds"]["pintura_sin_resolver"], 5)
        self.assertEqual(len(report["paint_unresolved"]["materials"]), 5)


if __name__ == "__main__":
    unittest.main()
