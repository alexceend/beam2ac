"""G2.38: promoción a builds/ y casos pendientes del audio. Sin FMOD, juegos ni suite completa."""

import contextlib
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.audio import fmod_build, pipeline
from src.extract.audio_sources import scan_audio
from src.project import builds
from src.project.export import SCHEMA, VERDICT_BLOCKED, VERDICT_COMPLETE
from tests.test_g237_audio_pipeline import make_mod, write_wav

CAR = "beam2ac_demo"


def make_attempt(
    root,
    name="001",
    verdict=VERDICT_COMPLETE,
    errors=0,
    extra=None,
    audio=None,
    options=None,
    content=b"x",
):
    att = root / "exports" / name
    files = {"data/car.ini": b"[HEADER]\n", f"{CAR}.kn5": content, **(extra or {})}
    for rel, data in files.items():
        (att / CAR / rel).parent.mkdir(parents=True, exist_ok=True)
        (att / CAR / rel).write_bytes(data)
    man = {
        "schema": SCHEMA,
        "kind": "beam2ac_export",
        "car_id": CAR,
        "verdict": verdict,
        "errors": errors,
        "warnings": 0,
        "tested_in_game": False,
        "options": options or {},
        "files": {
            r: {"sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}
            for r, d in files.items()
        },
        **({"audio": {"stages": audio}} if audio else {}),
    }
    (att / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (att / "export_report.md").write_text("r", encoding="utf-8")
    return att


class TestBuilds(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)
        self.root = self.tmp / "builds"

    def tearDown(self):
        self._d.cleanup()

    def kn5(self):
        return (self.root / CAR / CAR / f"{CAR}.kn5").read_bytes()

    def test_promote_copies_and_keeps_history(self):
        att = make_attempt(self.tmp)
        r = builds.promote(att, root=self.root)
        self.assertTrue(r["promoted"], r)
        lv = json.loads((self.root / CAR / "build.json").read_text())["levels"]
        self.assertTrue(lv["structurally_functional"])
        self.assertFalse(
            lv["tested_in_ac"] or lv["physically_validated"] or lv["audio_listened"]
        )
        self.assertTrue((att / CAR / "data/car.ini").is_file())
        self.assertEqual(
            [p.name for p in self.root.iterdir() if p.name.startswith(".")], []
        )

    def test_failed_never_replaces_functional(self):
        builds.promote(make_attempt(self.tmp, "001", content=b"bueno"), root=self.root)
        bad = make_attempt(
            self.tmp, "002", verdict=VERDICT_BLOCKED, errors=2, content=b"malo"
        )
        self.assertFalse(builds.promote(bad, root=self.root)["promoted"])
        self.assertEqual(self.kn5(), b"bueno")

    def test_replace_keeps_history(self):
        builds.promote(make_attempt(self.tmp, "001", content=b"v1"), root=self.root)
        self.assertTrue(
            builds.promote(
                make_attempt(self.tmp, "002", content=b"v2"), root=self.root
            )["replaced"]
        )
        self.assertEqual(self.kn5(), b"v2")
        self.assertEqual(
            (self.tmp / "exports/001" / CAR / f"{CAR}.kn5").read_bytes(), b"v1"
        )

    def test_tampered_source_not_promoted(self):
        builds.promote(make_attempt(self.tmp, "001", content=b"v1"), root=self.root)
        att = make_attempt(self.tmp, "002", content=b"v2")
        (att / CAR / f"{CAR}.kn5").write_bytes(b"alterado")
        r = builds.promote(att, root=self.root)
        self.assertFalse(r["promoted"])
        self.assertIn("íntegro", r["reason"])
        self.assertEqual(self.kn5(), b"v1")

    def test_failure_in_swap_restores_previous(self):
        builds.promote(make_attempt(self.tmp, "001", content=b"v1"), root=self.root)
        real = os.replace

        def flaky(src, dst):
            if Path(src).name.startswith(".tmp_"):
                raise OSError("disco lleno")
            return real(src, dst)

        with mock.patch("src.project.builds.os.replace", flaky):
            r = builds.promote(
                make_attempt(self.tmp, "002", content=b"v2"), root=self.root
            )
        self.assertFalse(r["promoted"])
        self.assertEqual(self.kn5(), b"v1")
        self.assertTrue((self.tmp / "exports/002" / CAR).is_dir())
        self.assertIn("disco lleno", (self.root / builds.LOG).read_text())

    def test_orphan_old_is_recovered(self):
        builds.promote(make_attempt(self.tmp, "001", content=b"v1"), root=self.root)
        os.replace(self.root / CAR, self.root / f".old_{CAR}_dead")
        builds.promote(make_attempt(self.tmp, "002", content=b"v2"), root=self.root)
        self.assertEqual(self.kn5(), b"v2")
        self.assertFalse(list(self.root.glob(".old_*")))

    def test_lock_blocks_concurrent(self):
        builds.promote(make_attempt(self.tmp, "001", content=b"v1"), root=self.root)
        (self.root / f".lock_{CAR}").write_bytes(b"")
        r = builds.promote(make_attempt(self.tmp, "002", content=b"v2"), root=self.root)
        self.assertFalse(r["promoted"])
        self.assertEqual(self.kn5(), b"v1")

    def test_default_root_only_for_output_history(self):
        r = builds.promote(make_attempt(self.tmp))
        self.assertFalse(r["promoted"])
        self.assertIn("fuera de output", r["reason"])

    def test_audio_truthfulness(self):
        st = {"banco_construido": True, "instalado_en_exportacion": True}
        self.assertFalse(
            builds.promote(make_attempt(self.tmp, "001", audio=st), root=self.root)[
                "promoted"
            ]
        )
        a2 = make_attempt(self.tmp, "002", options={"audio_build": True})
        self.assertIn("audio-build", builds.promote(a2, root=self.root)["reason"])
        a3 = make_attempt(
            self.tmp,
            "003",
            extra={f"sfx/{CAR}.bank": b"b"},
            audio={"instalado_en_exportacion": True, "escuchado": True},
        )
        self.assertFalse(builds.promote(a3, root=self.root)["promoted"])
        a4 = make_attempt(
            self.tmp,
            "004",
            extra={f"sfx/{CAR}.bank": b"b"},
            audio={"banco_construido": True, "instalado_en_exportacion": True},
        )
        self.assertTrue(builds.promote(a4, root=self.root)["promoted"])

    def test_path_traversal_in_manifest(self):
        att = make_attempt(self.tmp)
        m = json.loads((att / "manifest.json").read_text())
        m["files"]["../evil"] = {"sha256": "0", "size": 0}
        (att / "manifest.json").write_text(json.dumps(m))
        self.assertFalse(builds.promote(att, root=self.root)["promoted"])


class FakeFmod:
    """Sustituye subprocess.run: fallos intermitentes de fmodstudiocl antes de GUARDADO."""

    def __init__(self, out, fail_script):
        self.out, self.fail, self.calls = out, fail_script, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[1])
        log = self.out / "fmod_project" / "build_events_log.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        if cmd[1] == "-script":
            if self.fail > 0:
                self.fail -= 1
                return SimpleNamespace(returncode=0xC0000374, stdout="", stderr="crash")
            log.write_text("GUARDADO\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class TestFmodRetries(unittest.TestCase):
    def run_build(self, fail):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            fake = FakeFmod(out, fail)
            with mock.patch.object(fmod_build.subprocess, "run", fake):
                try:
                    fmod_build.run_build(out, "fmodstudiocl", CAR)
                    err = None
                except fmod_build.AudioBuildError as exc:
                    err = str(exc)
            return fake.calls, err

    def test_retry_then_success(self):
        calls, err = self.run_build(2)
        self.assertIsNone(err)
        self.assertEqual(calls, ["-script"] * 3 + ["-build"])

    def test_gives_up_after_three_and_does_not_build(self):
        calls, err = self.run_build(5)
        self.assertEqual(calls, ["-script"] * 3)
        self.assertIn("código", err)

    def test_script_error_log_is_failure_even_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            fake = FakeFmod(out, 0)

            def run(cmd, **kw):
                r = fake(cmd, **kw)
                (out / "fmod_project" / "build_events_log.txt").write_text(
                    "GUARDADO\nERROR x\n"
                )
                return r

            with mock.patch.object(fmod_build.subprocess, "run", run):
                with self.assertRaises(fmod_build.AudioBuildError):
                    fmod_build.run_build(out, "fmodstudiocl", CAR)


class TestPipeline(unittest.TestCase):
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
        with self.patched():
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


if __name__ == "__main__":
    unittest.main()
