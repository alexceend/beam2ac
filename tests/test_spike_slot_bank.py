"""SPIKE: banco por ranuras (``src.audio.slot_bank``) y lector WAV float de ``bank_patch``."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from src.audio import bank_patch as bp
from src.audio import slot_bank as sb


def _wav_f32(path: Path, vals: list[float], rate: int = 44100) -> None:
    data = struct.pack(f"<{len(vals)}f", *vals)
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data"
    body += struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


class TestSlotBank(unittest.TestCase):
    def test_wav_float_trunc_32767(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.wav"
            _wav_f32(p, [0.0, 1.0, -1.0, 0.5, 2.0])
            rate, pcm = bp.read_wav_mono16(p)
        self.assertEqual(rate, 44100)
        # FMOD: trunc(v * 32767) con recorte
        self.assertEqual(struct.unpack("<5h", pcm), (0, 32767, -32767, 16383, 32767))

    def test_slot_wavs_exact_and_nearest(self) -> None:
        files = {(803, False): "a/EXH_803.wav", (803, True): "a/EXH_803_P.wav",
                 (900, False): "a/EXH_900.wav", (900, True): "a/EXH_900_P.wav"}  # fmt: skip
        names = ["s0_EXH_803_P", "s1_EXH_803", "s2_EXH_880", "limiter"]
        got, notes = sb.slot_wavs(names, files)
        self.assertEqual(got[0], Path("a/EXH_803_P.wav"))
        self.assertEqual(got[1], Path("a/EXH_803.wav"))
        self.assertEqual(got[2], Path("a/EXH_900.wav"))  # 880 -> 900 (más cercana)
        self.assertNotIn(3, got)
        self.assertEqual(len(notes), 1)

    def test_spec_files(self) -> None:
        spec = {"engine": {"e": {"tracks": [{"sounds": [
            {"file": "x/s001_EXH_803_P.wav"}, {"file": "x/s002_EXH_803.wav"}]}]}}}  # fmt: skip
        self.assertEqual(
            sb.spec_files(spec),
            {(803, True): "x/s001_EXH_803_P.wav", (803, False): "x/s002_EXH_803.wav"},
        )


if __name__ == "__main__":
    unittest.main()
