"""Versiones de camsoEngine.lua y fila de gas cerrado (freno motor)."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.ac.engine import fit_coast_ref
from src.extract.engine_camso import CAMSO_ENGINE_VERSIONS, build_engine_model

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
OLD = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
NEW = "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestCamsoVersions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from src.pipeline.physics_preview import load_vehicle_dir

        src = load_vehicle_dir(IBIZA, "35b84.pc")
        from src.jbeam.resolver import resolve

        cls.resolved = resolve(src.index, src.pc)
        cls.lua_sha = src.lua_sha256["lua/powertrain/camsoEngine.lua"]

    def test_registry(self) -> None:
        self.assertEqual(CAMSO_ENGINE_VERSIONS[OLD]["closed_throttle"], 1)
        self.assertEqual(CAMSO_ENGINE_VERSIONS[NEW]["closed_throttle"], 0)
        self.assertEqual(self.lua_sha, OLD)

    def test_ibiza_unchanged_with_its_version(self) -> None:
        legacy = build_engine_model(self.resolved)
        known = build_engine_model(self.resolved, OLD)
        self.assertEqual(legacy.coast, known.coast)
        self.assertEqual(fit_coast_ref(known).torque, fit_coast_ref(legacy).torque)
        self.assertIn("fila 1", known.status["coast_curve"])
        self.assertIn("no comprobada", legacy.status["coast_curve"])

    def test_new_version_uses_row_zero(self) -> None:
        m = build_engine_model(self.resolved, NEW)
        self.assertIn("fila 0", m.status["coast_curve"])
        self.assertTrue(m.coast)
        self.assertEqual(
            [p.net for p in m.curve],
            [p.net for p in build_engine_model(self.resolved, OLD).curve],
        )  # la curva a fondo (fila 99) no depende de la versión

    def test_unknown_or_missing_version_leaves_coast_pending(self) -> None:
        for sha in ("0" * 64, ""):
            with self.subTest(sha=sha):
                m = build_engine_model(self.resolved, sha)
                self.assertEqual(m.coast, [])
                self.assertIsNone(fit_coast_ref(m))
                self.assertTrue(m.status["coast_curve"].startswith("pendiente"))
                self.assertTrue(any("gas cerrado" in i for i in m.issues))


if __name__ == "__main__":
    unittest.main()
