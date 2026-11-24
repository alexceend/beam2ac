"""G2.45: el banco de neumáticos integrado reproduce las medidas ya guardadas."""

import json
import tempfile
import unittest
from pathlib import Path

from src.bench.service import default_beamng_root
from src.bench import tyre_runner as tr

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "output/g244_2026-11-14/banco_neumatico"
MOD = ROOT / "output/g244_2026-11-14/mods_src/composed_fox/vehicles/composed_fox"
STORED = ROOT / "measurements/beamng_bench/tyre/composed_fox.json"


class TestLua(unittest.TestCase):
    def test_lua_is_byte_identical_to_the_benched_scripts(self) -> None:
        if not BENCH.is_dir():
            self.skipTest("banco de G2.44 no disponible")
        for rel in (
            "ge/extensions/beam2acTyreGE.lua",
            "vehicle/extensions/beam2acTyre.lua",
        ):
            self.assertEqual(
                tr.sha(tr.LUA_SRC / rel), tr.sha(BENCH / "lua_src/lua" / rel)
            )


class TestPrepare(unittest.TestCase):
    @unittest.skipUnless(MOD.is_dir(), "mods extraídos no disponibles")
    def test_prepare_isolated_userpath_without_touching_the_source(self) -> None:
        from src.safe_paths import new_output_dir

        import time

        work = new_output_dir(ROOT / f"output/g245_2026-11-14/prep_{time.time_ns()}")
        user = tr.prepare(work, "s1", MOD)
        cfg = json.loads((user / "beam2ac_tyre/config.json").read_text("utf-8"))
        v = cfg["vehicles"][0]
        self.assertEqual((v["name"], v["tag"]), ("composed_fox", "s1"))
        self.assertTrue((user / "lua/ge/extensions/beam2acTyreGE.lua").is_file())
        tr.check_copy(MOD, user / "vehicles/composed_fox")  # copia idéntica
        with self.assertRaises(tr.BenchError):  # no se reutiliza una sesión
            tr.prepare(work, "s1", MOD)

    def test_pc_must_be_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.pc", "b.pc"):
                (Path(d) / n).write_text("{}")
            with self.assertRaises(tr.BenchError):
                tr.pc_of(Path(d))
            self.assertEqual(tr.pc_of(Path(d), "b.pc"), "b.pc")


@unittest.skipUnless(
    BENCH.is_dir() and MOD.is_dir() and STORED.is_file(), "banco de G2.44 no disponible"
)
class TestCollectReproducesStored(unittest.TestCase):
    def test_same_fingerprint_and_axles(self) -> None:
        rec = tr.collect(
            BENCH,
            ["s1", "s2"],
            MOD,
            out_dir=Path(tempfile.gettempdir()) / "g245_nada",
            dry_run=True,
        )
        old = json.loads(STORED.read_text("utf-8"))
        self.assertEqual(rec["source_fingerprint"], old["source_fingerprint"])
        self.assertEqual(rec["axles"], old["axles"])


class TestProtocol(unittest.TestCase):
    CFG = BENCH / "userpath_s1/current/beam2ac_tyre/config.json"

    @unittest.skipUnless(CFG.is_file(), "banco de G2.44 no disponible")
    def test_protocol_equals_the_one_that_was_run(self) -> None:
        from src.bench.protocol import protocol

        run = json.loads(self.CFG.read_text("utf-8"))["vehicles"][0]
        self.assertEqual(run["protocol"], "completo")
        self.assertEqual(protocol("completo"), run["segments"])


class TestFingerprintIgnoresCommonZip(unittest.TestCase):
    def test_same_fingerprint_with_and_without_common_zip(self) -> None:
        import os

        from src.importers.measurements import source_fingerprint
        from src.pipeline.physics_preview import load_vehicle_zip

        mods = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "BeamNG/BeamNG.drive/current/mods"
        )
        zip_ = mods / "composed_fox.zip"
        common = Path(
            (default_beamng_root() / "content/vehicles/common.zip").as_posix()
        )
        if not (zip_.is_file() and common.is_file()):
            self.skipTest("mod o common.zip no instalados")
        a = source_fingerprint(load_vehicle_zip(zip_, None, "4974f.pc"))
        b = source_fingerprint(load_vehicle_zip(zip_, common, "4974f.pc"))
        self.assertEqual(a, b)
        self.assertEqual(a, json.loads(STORED.read_text("utf-8"))["source_fingerprint"])


if __name__ == "__main__":
    unittest.main()
