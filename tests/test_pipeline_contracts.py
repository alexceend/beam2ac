"""Contratos de los pipelines (fase 8): registro completo, herramientas opcionales y
adaptadores aislados."""

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from src.pipeline.contracts import (
    PIPELINES,
    audio_capabilities,
    available_operations,
    geometry_plan_summary,
    spec,
    tool_status,
)

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
REQUESTED = {
    "geometry",
    "materials_textures",
    "engine",
    "drivetrain",
    "suspension",
    "tyres",
    "brakes",
    "aero",
    "cameras",
    "driver",
    "lights",
    "audio",
}


class TestRegistry(unittest.TestCase):
    def test_every_requested_pipeline_is_documented(self):
        ids = {p.id for p in PIPELINES}
        self.assertTrue(REQUESTED <= ids, REQUESTED - ids)
        for p in PIPELINES:
            with self.subTest(p.id):
                for field in ("modules", "inputs", "outputs", "limitations"):
                    self.assertTrue(getattr(p, field), field)
                self.assertTrue(p.repeatable and p.writes)
                for module in p.modules:
                    path = module.split(" ")[0]
                    if path.startswith("docs/") and not (ROOT / path).exists():
                        continue  # docs/ no está en git
                    self.assertTrue((ROOT / path).exists(), path)

    def test_only_audio_is_optional(self):
        self.assertEqual([p.id for p in PIPELINES if p.optional], ["audio"])


class TestToolsAreOptionalAndNeverExecuted(unittest.TestCase):
    def test_detection_does_not_run_anything(self):
        with (
            mock.patch.object(
                subprocess, "Popen", side_effect=AssertionError("no se ejecuta nada")
            ),
            mock.patch.object(
                subprocess, "run", side_effect=AssertionError("no se ejecuta nada")
            ),
        ):
            status = tool_status()
        self.assertEqual(set(status), {"ac_root"})

    def test_audio_needs_no_external_tools(self):
        tools = {"ac_root": {"available": False}}
        audio = audio_capabilities(tools)
        for op in ("build", "prebuilt", "none"):
            self.assertTrue(audio["operations"][op]["available"], op)
        self.assertIn("banco de sonido propio", audio["status"])
        for p in PIPELINES:
            if p.id in ("audio", "driver", "assembly"):
                continue
            with self.subTest(p.id):
                self.assertTrue(
                    all(o["available"] for o in available_operations(p, tools).values())
                )
        self.assertFalse(
            available_operations(spec("driver"), tools)["pose"]["available"]
        )


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestGeometryAdapter(unittest.TestCase):
    def test_plan_without_subprocesses(self):
        with mock.patch.object(
            subprocess, "run", side_effect=AssertionError("no se lanza nada")
        ):
            g = geometry_plan_summary(IBIZA, IBIZA / "35b84.pc")
        self.assertEqual(g["wheels"], ["WHEEL_LF", "WHEEL_LR", "WHEEL_RF", "WHEEL_RR"])
        self.assertEqual(len(g["empties"]), 8)
        self.assertEqual(set(g["unit_meter"].values()), {0.01})


if __name__ == "__main__":
    unittest.main()
