"""Pruebas retiradas con la dependencia de Blender y FMOD (rama technical-spike-self-transformations).

Se conservan como TEXTO (el código que probaban ya no existe en la aplicación): reutilización de bancos,
reintentos y proyecto preparado de FMOD, detección de FMOD, descarga del complemento KN5. Si algún día
se recupera esa vía, se restauran desde aquí.
"""

import pytest

pytest.skip(
    "pruebas retiradas: la aplicación ya no usa Blender ni FMOD",
    allow_module_level=True,
)

RETIRADAS = {
    "tests/test_g237_audio_pipeline.py::TestPipelineNoFmod (HEAD)": r'''class TestPipelineNoFmod(unittest.TestCase):
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


''',
    "tests/test_pipeline_contracts.py::test_detect_fmod_version_and_files (HEAD)": r"""    def test_detect_fmod_version_and_files(self):
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


""",
    "tests/test_desktop_window_urls.py::test_addon_source_is_allowed (HEAD)": r"""    def test_addon_source_is_allowed(self) -> None:
        # el botón «Ver el complemento en GitHub» de Ajustes pasa blender_addon.REPO_URL
        self.assertIn(blender_addon.REPO_URL, desktop_window.DOWNLOAD_URLS)

""",
    "tests/test_g238_builds_audio.py::TestPipeline (HEAD)": r"""class TestPipeline(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.veh = make_mod(self.tmp / "mod")
        self.audio = self.tmp / "audio"
        self.audio.mkdir()
        self.built = 0

    def tearDown(self):
        self._d.cleanup()

    def fake_fmod(self):
        d = self.tmp / "FMOD Studio 1.08.12"
        d.mkdir(exist_ok=True)
        (d / "fmodstudiocl.exe").write_bytes(b"")
        (d / "FMOD Studio.exe").write_bytes(b"")
        return d

    def patched(self, sdk="sdkA"):
        def prepare(spec, out, cl):
            (out / "LEEME.md").write_text("órdenes")

        def collect(out, car, events):
            self.built += 1
            dst = out / "prebuilt"
            dst.mkdir()
            (dst / f"{car}.bank").write_bytes(b"BANK%d" % self.built)
            (dst / "GUIDs.txt").write_text("g")
            return dst

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(fmod_build, "prepare_project", prepare))
        stack.enter_context(mock.patch.object(fmod_build, "run_build", lambda *a: {}))
        stack.enter_context(mock.patch.object(fmod_build, "collect", collect))
        stack.enter_context(
            mock.patch.object(fmod_build, "sdk_tree_sha256", lambda *a: sdk)
        )
        stack.enter_context(
            mock.patch(
                "src.ac.audio.load_prebuilt", lambda *a, **k: SimpleNamespace(events={})
            )
        )
        return stack

    def run_pipe(self, n, fmod=None):
        return pipeline.run(
            self.veh,
            CAR,
            self.audio / f"{n:03d}",
            800,
            6000,
            fmod_path=fmod or self.fake_fmod(),
        )

    def test_fmod_absent_prepares_but_does_not_claim_bank(self):
        with (
            self.patched(),
            mock.patch.dict(os.environ, {"BEAM2AC_AUDIO_BUILDER": "fmod"}),
        ):
            r = self.run_pipe(1, fmod=self.tmp / "no_existe" / "FMOD Studio 1.08.12")
        self.assertTrue(r["stages"]["proyecto_generado"])
        self.assertFalse(r["stages"]["banco_construido"])
        self.assertIn("NO construido", r["note"])
        self.assertEqual(self.built, 0)
        self.assertFalse((self.audio / "001" / "prebuilt").exists())
        self.assertEqual(r["state"], "proyecto_generado")

    def test_reuse_only_with_identical_inputs(self):
        with self.patched():
            r1 = self.run_pipe(1)
            r2 = self.run_pipe(2)
        self.assertTrue(r1["stages"]["banco_construido"])
        self.assertEqual(self.built, 1)
        self.assertIn("reused_from", r2)
        self.assertTrue((self.audio / "002/prebuilt" / f"{CAR}.bank").is_file())
        wav = next((self.tmp / "mod/art/sound/engine/H1").glob("EXH_1000_P.wav"))
        write_wav(wav, "f", amp=0.9)  # un WAV distinto → reconstruye
        with self.patched():
            r3 = self.run_pipe(3)
        self.assertNotIn("reused_from", r3)
        self.assertEqual(self.built, 2)
        with self.patched(sdk="sdkB"):  # otra plantilla del SDK → reconstruye
            self.assertNotIn("reused_from", self.run_pipe(4))
        for bank in self.audio.glob(
            f"*/prebuilt/{CAR}.bank"
        ):  # bancos manipulados → reconstruye
            bank.write_bytes(b"corrupto")
        with self.patched(sdk="sdkB"):
            self.assertNotIn("reused_from", self.run_pipe(5))
        self.assertEqual(self.built, 4)

    def test_failed_build_leaves_previous_untouched(self):
        with self.patched():
            self.run_pipe(1)
        bank = self.audio / "001/prebuilt" / f"{CAR}.bank"
        before = bank.read_bytes()
        with (
            self.patched(sdk="sdkB"),
            mock.patch.object(
                fmod_build,
                "run_build",
                side_effect=fmod_build.AudioBuildError("FMOD murió"),
            ),
        ):
            r = self.run_pipe(2)
        self.assertIn("FMOD murió", r["error"])
        self.assertFalse(r["stages"]["banco_construido"])
        self.assertEqual(bank.read_bytes(), before)

    def test_malformed_rpm_does_not_crash(self):
        bl = self.tmp / "mod/art/sound/blends/H1.sfxBlend2D.json"
        data = json.loads(bl.read_text())
        data["samples"][0][0][1] = "abc"
        data["samples"][0][1][1] = "nan"
        bl.write_text(json.dumps(data), encoding="utf-8")
        scan = scan_audio(self.veh)
        problems = [p for b in [scan] for p in b.get("problems", [])]
        self.assertGreaterEqual(len(problems), 2)
        with self.patched():
            self.run_pipe(1)  # no lanza

    def test_ladder_never_marks_listening(self):
        for status in ("sin_fuente", "listo", "listo_con_fallback"):
            st = pipeline.ladder(status, True, True, True)["stages"]
            self.assertFalse(
                st["escuchado"] or st["validado_en_ac"] or st["validado_acusticamente"]
            )
        self.assertTrue(pipeline.ladder("listo")["stages"]["plan_generado"])
        self.assertFalse(pipeline.ladder("sin_fuente")["stages"]["plan_generado"])


""",
}
