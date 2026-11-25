"""G2.46: banco aerodinámico integrado (``src/bench/aero_runner``). Sin BeamNG: se
reutilizan las sesiones guardadas de G2.15 (sesión 4) como si las hubiera dejado el
banco."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.bench import aero_runner as ar
from src.bench.tyre_runner import BenchError

ROOT = Path(__file__).resolve().parents[1]
G215 = ROOT / "output/g215_direction_brakes_aero_2026-11-03/banco"
NAME = "2020_rpm_atlanta_gt_3_6t"
SAMPLE = ROOT / "samples_beamng" / NAME / "vehicles" / NAME
STORED = ROOT / "measurements/beamng_bench/aero" / f"{NAME}.json"
HAVE = (G215 / "userpath/current/beam2ac_diraero" / f"{NAME}.json").is_file()


def _session(tmp: Path, s: str) -> None:
    """Sesión sintética = el volcado de G2.15 colocado donde lo deja el banco nuevo."""
    user = tmp / f"userpath_{s}" / "current"
    (user / ar.AERO_DIR).mkdir(parents=True)
    shutil.copyfile(
        G215 / "userpath/current/beam2ac_diraero" / f"{NAME}.json",
        user / ar.AERO_DIR / f"{NAME}.json",
    )
    shutil.copytree(SAMPLE, user / "vehicles" / NAME)
    shutil.copyfile(G215 / "userpath/current/beamng.log", user / "beamng.log")


class TestAeroLua(unittest.TestCase):
    def test_same_cases_as_g215_and_no_steering(self) -> None:
        new = (ar.SCRIPT).read_text("utf-8")
        old = (G215 / "lua_src/lua/vehicle/extensions/beam2acDirAero.lua").read_text(
            "utf-8"
        )
        block = old[old.index("local AERO = {") : old.index("local SETTLE_STEER")]
        self.assertIn(block.split("local SETTLE")[0].strip(), new)
        self.assertIn("local SETTLE_AERO, SAMPLE_AERO = 3.0, 1.0", new)
        # sin la fase de dirección: no toca hydros.update
        code = " ".join(x for x in new.splitlines() if not x.startswith("--"))
        self.assertNotIn("hydros.update", code)
        self.assertNotIn("setBeamLengthRefRatio", code)

    def test_ge_loads_the_aero_extension_and_survives_missing_clearPauseStack(self):
        ge = (ar.LUA_SRC / "ge/extensions/beam2acAeroGE.lua").read_text("utf-8")
        self.assertIn("beam2acAero.start", ge)
        self.assertIn("if simTimeAuthority.clearPauseStack then", ge)
        self.assertIn("beam2ac_aero/config.json", ge)


@unittest.skipUnless(HAVE and SAMPLE.is_dir(), "faltan las sesiones de G2.15")
class TestAeroCollect(unittest.TestCase):
    def test_collect_reproduces_the_stored_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for s in ("s1", "s2"):
                _session(tmp, s)
            rec = ar.collect(tmp, ["s1", "s2"], SAMPLE, out_dir=tmp / "out")
            stored = json.loads(STORED.read_text("utf-8"))
            self.assertEqual(rec["source_fingerprint"], stored["source_fingerprint"])
            self.assertEqual(rec["cases"], stored["cases"])
            self.assertEqual(rec["repeatability"]["s2"]["max_rel"], 0.0)
            self.assertTrue((tmp / "out" / f"{NAME}.json").is_file())

    def test_modified_copy_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for s in ("s1", "s2"):
                _session(tmp, s)
            pc = next((tmp / "userpath_s2/current/vehicles" / NAME).glob("*.pc"))
            pc.write_text(pc.read_text("utf-8") + " ", "utf-8")
            with self.assertRaises(BenchError):
                ar.collect(tmp, ["s1", "s2"], SAMPLE, out_dir=tmp / "out")

    def test_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for s in ("s1", "s2"):
                _session(tmp, s)
            ar.collect(tmp, ["s1", "s2"], SAMPLE, out_dir=tmp / "out")
            with self.assertRaises(BenchError):
                ar.collect(tmp, ["s1", "s2"], SAMPLE, out_dir=tmp / "out")

    def test_repeatability_detects_a_perturbation(self) -> None:
        d = json.loads(STORED.read_text("utf-8"))["cases"]
        e = json.loads(json.dumps(d))
        for c in e:
            c["F"][0] *= 1.01
        r = ar.repeatability(d, e)
        self.assertAlmostEqual(r["max_rel"], 0.01, delta=0.0105)
        self.assertGreater(r["max_rel"], 0)


class TestAeroPrepare(unittest.TestCase):
    @unittest.skipUnless(SAMPLE.is_dir(), "falta la muestra")
    def test_prepare_isolated_and_not_twice(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            before = {p: p.stat().st_mtime for p in SAMPLE.rglob("*") if p.is_file()}
            user = ar.prepare(tmp, "s1", SAMPLE)
            cfg = json.loads((user / ar.AERO_DIR / "config.json").read_text("utf-8"))
            self.assertEqual(cfg["vehicles"][0]["name"], NAME)
            self.assertTrue((user / "lua/ge/extensions/beam2acAeroGE.lua").is_file())
            self.assertTrue((user / "lua/vehicle/extensions/beam2acAero.lua").is_file())
            with self.assertRaises(BenchError):
                ar.prepare(tmp, "s1", SAMPLE)
            self.assertEqual(
                before, {p: p.stat().st_mtime for p in SAMPLE.rglob("*") if p.is_file()}
            )


if __name__ == "__main__":
    unittest.main()
