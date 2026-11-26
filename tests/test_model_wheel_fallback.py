"""Un volante del perfil que no encaja con la exportación no debe tumbar la etapa ``model``."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ac.steer_wheel import SteerWheelError
from src.project.manifest import SourceSpec, create_project
from src.project.orchestrator import run_project
from src.project.stages import STAGES_WITH_MODEL

IBIZA = Path("sample_beamng/vehicles/seat_ibiza_acfr")


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestProfileWheelFallback(unittest.TestCase):
    def test_profile_wheel_failure_is_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = create_project(
                Path(tmp) / "p", SourceSpec("dir", str(IBIZA), "35b84.pc")
            )
            (p.root / "steering_wheel.json").write_text(
                '{"enabled": true}', encoding="utf-8"
            )
            err = SteerWheelError("sin vértices de aro ('lenkrad')")
            with mock.patch("src.ac.steer_wheel.rig_steering_wheel", side_effect=err):
                run_project(p, STAGES_WITH_MODEL, log=lambda _: None)
            report = next((p.root / "stages" / "model").glob("*/model.json"))
            data = json.loads(report.read_text(encoding="utf-8"))
        warns = [w for w in data["warnings"] if "volante" in w]
        self.assertTrue(any("no encaja" in w for w in warns), warns)
        self.assertIsNone(data["steering_wheel"])


if __name__ == "__main__":
    unittest.main()
