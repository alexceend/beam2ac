"""Regresión del extractor antiguo y protección de las muestras.

El baseline reproduce el comportamiento ANTIGUO (par bruto, throttle 100), no el
valor físico correcto; ver docs/baseline/engine_translation_baseline.md.
"""

import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from tools import physics_translator_diy as legacy
from src.safe_paths import DEFAULT_OUTPUT_DIR, PROJECT_ROOT, ProtectedPathError

JBEAM = PROJECT_ROOT / legacy.DEFAULT_JBEAM
BASELINE = PROJECT_ROOT / "docs" / "baseline" / "power_curve_baseline.lut"
SAMPLES = (PROJECT_ROOT / "sample_beamng", PROJECT_ROOT / "sample_assetto")


def _sample_listing() -> dict[str, int]:
    """Ruta relativa -> tamaño de todos los archivos de las muestras."""
    return {
        str(p.relative_to(PROJECT_ROOT)): p.stat().st_size
        for root in SAMPLES
        for p in root.rglob("*")
        if p.is_file()
    }


def _read_lut(path: Path) -> list[str]:
    return [
        ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]


class TestLegacyExtractor(unittest.TestCase):
    def test_default_output_is_outside_samples(self):
        out = Path(legacy.DEFAULT_LUT_OUTPUT).resolve()
        self.assertIn(DEFAULT_OUTPUT_DIR.resolve(), out.parents)

    def test_matches_baseline_and_leaves_samples_untouched(self):
        before = _sample_listing()
        jbeam_hash = hashlib.sha256(JBEAM.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "power.lut"
            with contextlib.redirect_stdout(io.StringIO()):
                points = legacy.extract_real_torque_curve(str(JBEAM), out)

            self.assertEqual(len(points), 96)
            self.assertEqual(points[0][0], 500)
            self.assertEqual(points[-1][0], 10000)
            self.assertEqual(max(points, key=lambda p: p[1]), (7100, 337.28))
            self.assertEqual(_read_lut(out), _read_lut(BASELINE))

        self.assertEqual(_sample_listing(), before)
        self.assertEqual(hashlib.sha256(JBEAM.read_bytes()).hexdigest(), jbeam_hash)

    def test_refuses_to_write_into_samples(self):
        target = PROJECT_ROOT / "sample_assetto" / "data" / "should_not_exist.lut"
        with self.assertRaises(ProtectedPathError):
            legacy.extract_real_torque_curve(str(JBEAM), target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
