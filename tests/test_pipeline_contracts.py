"""Contratos de los pipelines (fase 8): registro completo, herramientas opcionales y
adaptadores aislados."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.pipeline.contracts import (
    PIPELINES,
    audio_capabilities,
    available_operations,
    detect_fmod,
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
        self.assertEqual(
            set(status),
            {
                "blender_3_6",
                "addon_assetto_corsa_tools",
                "fmod_studio_1_08_12",
                "ac_root",
            },
        )

    def test_without_fmod_only_bank_build_is_unavailable(self):
        tools = {
            "blender_3_6": {"available": True},
            "addon_assetto_corsa_tools": {"available": True},
            "fmod_studio_1_08_12": {"available": False},
            "ac_root": {"available": False},
        }
        audio = audio_capabilities(tools)
        self.assertFalse(audio["operations"]["build"]["available"])
        self.assertTrue(audio["operations"]["prebuilt"]["available"])
        self.assertTrue(audio["operations"]["none"]["available"])
        self.assertIn("el resto funciona", audio["status"])
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

    def test_detect_fmod_version_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "FMOD Studio 2.02.04"
            wrong.mkdir()
            for exe in ("fmodstudiocl.exe", "FMOD Studio.exe"):
                (wrong / exe).write_bytes(b"")
            info = detect_fmod(wrong)
            self.assertTrue(info["found"])
            self.assertFalse(info["compatible"])
            good = Path(tmp) / "FMOD Studio 1.08.12"
            good.mkdir()
            self.assertFalse(detect_fmod(good)["found"])  # faltan los ejecutables


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestGeometryAdapter(unittest.TestCase):
    def test_plan_without_blender(self):
        with mock.patch.object(
            subprocess, "run", side_effect=AssertionError("no se lanza Blender")
        ):
            g = geometry_plan_summary(IBIZA, IBIZA / "35b84.pc")
        self.assertEqual(g["wheels"], ["WHEEL_LF", "WHEEL_LR", "WHEEL_RF", "WHEEL_RR"])
        self.assertEqual(len(g["empties"]), 8)
        self.assertEqual(set(g["unit_meter"].values()), {0.01})


if __name__ == "__main__":
    unittest.main()
