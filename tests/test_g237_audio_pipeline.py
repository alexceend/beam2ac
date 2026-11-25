"""G2.37: pipeline WAV → plan → proyecto FMOD. Sin FMOD ni juegos (WAV sintéticos)."""

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.audio import pipeline
from src.audio.plan import build_plan, placement
from src.audio.wavinfo import wav_info
from src.extract.audio_sources import scan_audio


def write_wav(path: Path, fmt: str = "f", amp: float = 0.1, n: int = 4410) -> None:
    """RIFF a mano: ``wave`` no escribe float. fmt 'f' = float32 (Automation), 'h' = PCM16."""
    vals = [amp * ((i % 50) / 25 - 1) for i in range(n)]
    data = (
        struct.pack(f"<{n}f", *vals)
        if fmt == "f"
        else struct.pack(f"<{n}h", *[int(v * 32767) for v in vals])
    )
    bits, tag = (32, 3) if fmt == "f" else (16, 1)
    fmt_chunk = struct.pack(
        "<HHIIHH", tag, 1, 44100, 44100 * bits // 8, bits // 8, bits
    )
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + fmt_chunk
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def make_mod(
    root: Path, load=(1000, 2000, 4000), coast=(1000, 2000, 4000), bad=()
) -> Path:
    eng = root / "art/sound/engine/H1"
    eng.mkdir(parents=True)
    (root / "art/sound/blends").mkdir(parents=True)
    series = []
    for name, rpms, suffix in (("load", load, "_P"), ("coast", coast, "")):
        pts = []
        for r in rpms:
            f = eng / f"EXH_{r}{suffix}.wav"
            if (name, r) in bad:
                f.write_bytes(b"RIFFxxxx")
            else:
                write_wav(f, "f" if name == "load" else "h", 0.3 + r / 1e5)
            pts.append([f"art/sound/engine/H1/{f.name}", r])
        if rpms:
            series.append(pts)
    (root / "art/sound/blends/H1.sfxBlend2D.json").write_text(
        json.dumps({"eventName": "event:>Engine>x", "samples": series}),
        encoding="utf-8",
    )
    veh = root / "vehicles/car"
    veh.mkdir(parents=True)
    return veh


class TestWav(unittest.TestCase):
    def test_float_and_pcm(self):
        with tempfile.TemporaryDirectory() as d:
            f, h = Path(d, "a.wav"), Path(d, "b.wav")
            write_wav(f, "f")
            write_wav(h, "h")
            self.assertTrue(wav_info(f)["ok"] and wav_info(h)["ok"])
            self.assertEqual((wav_info(f)["format"], wav_info(f)["bits"]), (3, 32))
            self.assertAlmostEqual(wav_info(f)["duration_s"], 0.1, places=3)
            write_wav(Path(d, "z.wav"), "f", amp=0.0)
            self.assertFalse(wav_info(Path(d, "z.wav"))["ok"])
            Path(d, "t.wav").write_bytes(b"RIFF\0\0\0\0WAVE")
            self.assertFalse(wav_info(Path(d, "t.wav"))["ok"])


class TestPlan(unittest.TestCase):
    def test_placement_covers_and_crossfades(self):
        pl = placement([1000, 2000, 4000], 20000)
        self.assertEqual(pl[0]["start"], 0.0)
        self.assertAlmostEqual(pl[-1]["start"] + pl[-1]["length"], 20000)
        for a, b in zip(pl, pl[1:]):
            self.assertEqual(
                a["fade_out"], b["fade_in"]
            )  # mismo fundido en ambos sonidos

    def test_ready_through_vehicle_dir(self):
        with tempfile.TemporaryDirectory() as d:
            veh = make_mod(Path(d))
            sc = scan_audio(
                veh
            )  # art/sound cuelga de la raíz del mod, no de vehicles/<id>
            self.assertTrue(sc["present"])
            p = build_plan(sc, "beam2ac_t", 800, 6000)
            self.assertEqual(p["status"], "listo")
            ext = p["spec"]["engine"]["engine_ext"]
            self.assertEqual([len(t["sounds"]) for t in ext["tracks"]], [3, 3])
            self.assertEqual(ext["rpms_max"], 20000.0)
            self.assertEqual(len(p["spec"]["secondary"]), 17)
            self.assertTrue(
                any("sobre el ralentí" in w for w in p["warnings"])
            )  # 1000 > 800

    def test_missing_series_is_explicit_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            veh = make_mod(Path(d), coast=())
            p = build_plan(scan_audio(veh), "beam2ac_t")
            self.assertEqual(p["status"], "listo_con_fallback")
            self.assertTrue(any("reutiliza" in w for w in p["warnings"]))
            tr = p["spec"]["engine"]["engine_ext"]["tracks"]
            self.assertEqual(len(tr[1]["sounds"]), 3)

    def test_unusable_sample_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            veh = make_mod(Path(d), bad=(("load", 2000),))
            p = build_plan(scan_audio(veh), "beam2ac_t")
            self.assertEqual([x["rpm"] for x in p["dropped"]], [2000])
            self.assertEqual(p["series_used"], {"load": 2, "coast": 3})

    def test_no_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = build_plan(scan_audio(Path(d)), "beam2ac_t")
            self.assertEqual(p["status"], "sin_fuente")
            self.assertEqual(pipeline.ladder(p["status"])["state"], "sin_fuente")


class TestPipelineNoFmod(unittest.TestCase):
    def test_python_builder_is_the_fallback_without_fmod(self):
        """Sin FMOD el generador propio construye el banco (esqueleto versionado)."""
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "auto"}),
        ):
            veh = make_mod(Path(d) / "mod")
            out = Path(d) / "out"
            r = pipeline.run(veh, "beam2ac_t", out, fmod_path=Path(d) / "no_fmod")
            self.assertIsNone(r["error"])
            self.assertTrue(r["stages"]["banco_construido"])
            self.assertIn("sin FMOD", r["note"])
            self.assertTrue((Path(r["prebuilt"]) / "beam2ac_t.bank").is_file())
            self.assertFalse(r["stages"]["escuchado"])

    def test_auto_falls_back_to_fmod_project_when_generator_cannot(self):
        """Con 1 muestra por papel el generador no puede: se prepara el proyecto de FMOD como antes."""
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "auto"}),
        ):
            veh = make_mod(Path(d) / "mod", load=(1000,), coast=(1000,))
            out = Path(d) / "out"
            r = pipeline.run(veh, "beam2ac_t", out, fmod_path=Path(d) / "no_fmod")
            self.assertIsNone(r["error"])
            self.assertEqual(r["state"], "proyecto_generado")
            self.assertTrue(any("generador sin FMOD" in w for w in r["warnings"]))
            self.assertTrue((Path(r["out"]) / "LEEME.md").is_file())

    def test_generator_crash_on_odd_wav_falls_back(self):
        """Un WAV con cabecera rara (bits=4) no debe romper el flujo: respaldo al proyecto de FMOD."""
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "auto"}),
        ):
            veh = make_mod(Path(d) / "mod")
            odd = Path(d) / "mod/art/sound/engine/H1/EXH_2000_P.wav"
            hdr = b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 22050, 1, 4)
            hdr += b"data" + struct.pack("<I", 64) + bytes(64)
            odd.write_bytes(b"RIFF" + struct.pack("<I", len(hdr)) + hdr)
            r = pipeline.run(veh, "beam2ac_t", Path(d) / "out", fmod_path=Path(d) / "x")
            self.assertIsNone(r["error"])
            self.assertEqual(r["state"], "proyecto_generado")

    def test_python_mode_reports_generator_error(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "python"}),
        ):
            veh = make_mod(Path(d) / "mod", load=(1000,), coast=(1000,))
            r = pipeline.run(veh, "beam2ac_t", Path(d) / "out", fmod_path=Path(d) / "x")
            self.assertIn("al menos 2 ranuras", r["error"])
            self.assertFalse(r["stages"]["banco_construido"])

    def test_unknown_builder_is_an_error(self):
        with mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "bogus"}):
            with self.assertRaises(ValueError):
                pipeline.builder_mode()

    def test_project_prepared_not_built(self):
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "fmod"}),
        ):
            veh = make_mod(Path(d) / "mod")
            out = Path(d) / "out"
            r = pipeline.run(veh, "beam2ac_t", out, fmod_path=Path(d) / "no_fmod")
            self.assertIsNone(r["error"])
            self.assertEqual(r["state"], "proyecto_generado")
            self.assertFalse(r["stages"]["banco_construido"])
            self.assertIn("no disponible", r["note"])
            self.assertTrue(
                (out / "LEEME.md").is_file() and (out / "build_events.js").is_file()
            )
            spec = json.loads((out / "spec.json").read_text(encoding="utf-8"))
            self.assertTrue(
                all("/Assets/beam2ac_audio/" in f for f in spec["audio_files"])
            )
            self.assertEqual(r["stages"]["escuchado"], False)

    def test_audio_stages_never_claim_listening(self):
        st = pipeline.ladder("listo", project=True, bank=True, installed=True)["stages"]
        self.assertFalse(st["escuchado"] or st["validado_en_ac"])


BUILT = Path("output/g237_2026-11-13/atacama_build3/prebuilt")


@unittest.skipUnless(
    BUILT.is_dir(), "sin el banco construido en G2.37 (build manual con FMOD)"
)
class TestGeneratedBankSharesManualPath(unittest.TestCase):
    def test_generated_bank_loads_like_a_manual_one(self):
        from src.ac.audio import AudioInputError, load_prebuilt

        a = load_prebuilt(BUILT, "beam2ac_g237_atacama")
        self.assertEqual(len(a.events), 19)
        self.assertEqual(
            json.loads((BUILT / pipeline.BUILD_META).read_text())["car_id"],
            "beam2ac_g237_atacama",
        )
        with self.assertRaises(AudioInputError):  # otro id sin source_id: no se adivina
            load_prebuilt(BUILT, "beam2ac_otro")


if __name__ == "__main__":
    unittest.main()
