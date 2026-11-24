import unittest
from pathlib import Path

from src.extract.audio_sources import audio_spec, scan_audio

SAMPLE = Path("samples_beamng/2020_rpm_atacama_sport__4x4")


@unittest.skipUnless(SAMPLE.is_dir(), "sin muestra")
class TestAudioSources(unittest.TestCase):
    def test_scan_sample(self):
        sc = scan_audio(SAMPLE)
        self.assertTrue(sc["present"])
        roles = {s["role"] for b in sc["blends"] for s in b["series"]}
        self.assertEqual(roles, {"load", "coast"})
        self.assertEqual(sc["blends"][0]["missing_files"], [])

    def test_spec_flags_limiter(self):
        sc = scan_audio(SAMPLE)
        spec = audio_spec(sc, 800, 99999)
        self.assertEqual(spec["status"], "extraido")
        self.assertTrue(any("limitador" in w for w in spec["warnings"]))

    def test_no_audio(self):
        sc = scan_audio(Path("src"))
        self.assertFalse(sc["present"])
        self.assertEqual(audio_spec(sc, 800, 7000)["status"], "sin_fuente")


if __name__ == "__main__":
    unittest.main()
