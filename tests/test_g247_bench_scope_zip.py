"""G2.47: huella acotada por tipo de medida, zips en los bancos y estado de los bancos."""

from __future__ import annotations

import pytest

import json
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.bench.service import default_beamng_root
from src.bench import service
from src.bench.tyre_runner import BenchError
from src.importers import measurements as M
from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip

ROOT = Path(__file__).resolve().parents[1]
ID = "2020_rpm_atlanta_gt_3_6t"
SRC = ROOT / "samples_beamng" / ID / "vehicles" / ID
PC = f"{ID}.pc"
TYRE, AERO = "beamng_tyre_bench", "beamng_aero_bench"
IDLE = ('"idleRPM":645.52730529858', '"idleRPM":700.5')
WEIGHT = ('{"nodeWeight":8.87955}', '{"nodeWeight":20.0}')


def _copy(dest: Path) -> Path:
    d = dest / "vehicles" / ID
    d.mkdir(parents=True)
    for p in [*SRC.glob("*.jbeam"), *SRC.glob("*.pc"), *SRC.glob("info*.json")]:
        shutil.copy2(p, d / p.name)
    return d


def _edit(d: Path, name: str, change: tuple[str, str]) -> None:
    t = (d / name).read_text("utf-8")
    assert change[0] in t
    (d / name).write_text(t.replace(*change, 1), "utf-8")


@unittest.skipUnless(SRC.is_dir(), "falta la muestra de Atlanta")
class TestScopedFingerprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.base = _copy(cls.tmp / "base")
        cls.src = load_vehicle_dir(cls.base, PC)
        cls.fp = {k: M.scoped_fingerprint(cls.src, k) for k in M.SCOPES}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scoped(self, name: str, edit) -> dict[str, bool]:
        d = _copy(self.tmp / name)
        edit(d)
        s = load_vehicle_dir(d, PC)
        return {k: M.scoped_fingerprint(s, k) == self.fp[k] for k in M.SCOPES}

    def test_engine_change_keeps_both_but_changes_full_fingerprint(self):
        edit = lambda d: _edit(d, "camso_engine.jbeam", IDLE)  # noqa: E731
        self.assertEqual(self.scoped("e", edit), {TYRE: True, AERO: True})
        d = _copy(self.tmp / "e2")
        edit(d)
        self.assertNotEqual(
            M.source_fingerprint(load_vehicle_dir(d, PC)),
            M.source_fingerprint(self.src),
        )

    def test_inactive_part_keeps_both(self):
        def add(d):
            (d / "x.jbeam").write_text(
                '{"Orphan":{"information":{"name":"x"},"slotType":"Nobody",'
                '"nodes":[["id","posX","posY","posZ"],["zz1",0,0,5]]}}',
                "utf-8",
            )

        self.assertEqual(self.scoped("i", add), {TYRE: True, AERO: True})

    def _substitute(self, slot: str, alt: str, body: str):
        def sub(d):
            (d / "alt.jbeam").write_text(
                '{"%s":{"information":{"name":"alt"},"slotType":"%s",%s}}'
                % (alt, slot, body),
                "utf-8",
            )
            (d / PC).write_text(
                '{"vars":{},"parts":{"%s":"%s"},"format":2}' % (slot, alt), "utf-8"
            )

        return sub

    def test_substituted_turbo_part_keeps_both(self):
        sub = self._substitute("Camso_Turbo", "Turbo_alt", '"turbocharger":{"x":1}')
        self.assertEqual(self.scoped("s", sub), {TYRE: True, AERO: True})

    def test_substituted_stability_control_invalidates_both(self):
        # el ESC actúa sobre las ruedas durante el banco: conservador, se vuelve a medir
        sub = self._substitute(
            "Camso_ESC",
            "Camso_ESC_alt",
            '"controller":[["fileName"],["esc"]],"esc":{"a":0}',
        )
        self.assertEqual(self.scoped("s2", sub), {TYRE: False, AERO: False})

    def test_node_weight_invalidates_both(self):
        r = self.scoped("m", lambda d: _edit(d, "wheels_front.jbeam", WEIGHT))
        self.assertEqual(r, {TYRE: False, AERO: False})

    def test_triangle_data_invalidates_only_aero(self):
        def tri(d):
            t = (d / "suspension_F.jbeam").read_text("utf-8")
            i = t.index('"triangles"')
            j = t.index("]", t.index("[", t.index("[", i) + 1))
            (d / "suspension_F.jbeam").write_text(
                t[:j] + ',{"dragCoef":0.123}' + t[j:], "utf-8"
            )

        self.assertEqual(self.scoped("t", tri), {TYRE: True, AERO: False})

    def test_find_prefers_exact_and_flags_compatible(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        rec = {
            "schema": M.SCHEMA,
            "kind": TYRE,
            "source_fingerprint": M.source_fingerprint(self.src),
            "scoped_fingerprint": self.fp[TYRE],
        }
        (root / "t.json").write_text(json.dumps(rec), "utf-8")
        self.assertEqual(M.find(self.src, TYRE, root)[1]["_match"], "exacta")
        d = _copy(self.tmp / "f")
        _edit(d, "camso_engine.jbeam", IDLE)
        variant = load_vehicle_dir(d, PC)
        self.assertEqual(M.find(variant, TYRE, root)[1]["_match"], "compatible")
        d2 = _copy(self.tmp / "f2")
        _edit(d2, "wheels_front.jbeam", WEIGHT)
        self.assertIsNone(M.find(load_vehicle_dir(d2, PC), TYRE, root))
        self.assertIsNone(M.find(self.src, AERO, root))  # otro tipo: nunca
        # un registro anterior SIN huella acotada solo vale por huella exacta
        del rec["scoped_fingerprint"]
        (root / "t.json").write_text(json.dumps(rec), "utf-8")
        self.assertIsNone(M.find(variant, TYRE, root))
        self.assertIsNotNone(M.find(self.src, TYRE, root))

    def test_status_reuse_and_state(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with (
            mock.patch.object(M, "MEASUREMENTS_ROOT", root),
            mock.patch.object(service, "JOBS_ROOT", root / "jobs"),
        ):
            st = service.status("tyres", self.base, PC)
            self.assertEqual((st["reuse"], st["state"]), ("falta", "pendiente"))
            work = service.work_dir("tyres", self.base, PC)
            (work / "userpath_s1").mkdir(parents=True)
            self.assertEqual(
                service.status("tyres", self.base, PC)["state"], "incompleto"
            )
            rec = {
                "schema": M.SCHEMA,
                "kind": TYRE,
                "label": ID,
                "source_fingerprint": M.source_fingerprint(self.src),
                "scoped_fingerprint": self.fp[TYRE],
            }
            (root / "t.json").write_text(json.dumps(rec), "utf-8")
            st = service.status("tyres", self.base, PC)
            self.assertEqual((st["reuse"], st["state"]), ("exacta", "completado"))
            d = _copy(self.tmp / "g")
            _edit(d, "wheels_front.jbeam", WEIGHT)
            st = service.status("tyres", d, PC)
            self.assertEqual((st["reuse"], len(st["stale"])), ("obsoleta", 1))


def _mkzip(path: Path, members: dict[str, bytes], symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for k, v in members.items():
            zf.writestr(k, v)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(zi, "../../x")
    return path


class TestBenchZip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pc = b'{"vars":{},"parts":{},"format":2}'
        self.ok = {
            "vehicles/car1/car1.pc": self.pc,
            "vehicles/car1/car1.jbeam": b"{}",
            "vehicles/car1/sub/a.dds": b"x",
            "vehicles/car1/lua/e.lua": b"--",
        }

    def mat(self, z: Path) -> Path:
        return service.materialize(z, self.tmp / "jobs")

    def test_valid_zip_extracts_everything_once_and_never_touches_zip(self):
        z = _mkzip(self.tmp / "a.zip", self.ok)
        before = z.read_bytes()
        d = self.mat(z)
        self.assertEqual(d.name, "car1")
        self.assertTrue((d / "sub" / "a.dds").is_file())
        self.assertTrue((d / "lua" / "e.lua").is_file())
        mt = (d / "car1.pc").stat().st_mtime_ns
        self.assertEqual(self.mat(z), d)  # reutiliza: no vuelve a extraer
        self.assertEqual((d / "car1.pc").stat().st_mtime_ns, mt)
        self.assertEqual(z.read_bytes(), before)

    def test_directory_passthrough(self):
        self.assertEqual(service.materialize(self.tmp), self.tmp)

    def test_rejections(self):
        cases = {
            "corrupto": None,
            "dos_vehiculos": {**self.ok, "vehicles/car2/car2.pc": self.pc},
            "sin_pc": {"vehicles/car1/car1.jbeam": b"{}"},
            "sin_vehiculo": {"readme.txt": b"x"},
        }
        for name, members in cases.items():
            z = self.tmp / f"{name}.zip"
            if members is None:
                z.write_bytes(b"PK\x03\x04 esto no es un zip")
            else:
                _mkzip(z, members)
            with self.subTest(name), self.assertRaises(BenchError):
                self.mat(z)

    def test_symlink_member_rejected(self):
        z = _mkzip(self.tmp / "s.zip", self.ok, symlink="vehicles/car1/link")
        with self.assertRaisesRegex(BenchError, "simb"):
            self.mat(z)

    def test_path_traversal_never_escapes(self):
        names = ("evil.txt", "evil2.txt", "evil3.txt")
        evil = {
            **self.ok,
            "vehicles/car1/../../evil.txt": b"x",
            "vehicles/car1/..\\..\\evil2.txt": b"x",
            "vehicles/car1/C:\\evil3.txt": b"x",
        }
        z = _mkzip(self.tmp / "t.zip", evil)
        try:
            d = self.mat(z)
        except BenchError:
            d = None  # rechazar también es correcto
        jobs = (self.tmp / "jobs").resolve()
        for p in self.tmp.rglob("*"):
            if p.name in names:
                self.assertTrue(p.resolve().is_relative_to(jobs), p)
        if d is not None:
            self.assertTrue(d.resolve().is_relative_to(jobs))

    def test_wrapper_folder_nested_zip_truncated_and_absolute_member(self):
        wrapped = {"mod/" + k: v for k, v in self.ok.items()}
        z = _mkzip(self.tmp / "w.zip", wrapped)
        with self.assertRaises(BenchError):  # carpeta envolvente: no se adivina
            self.mat(z)
        inner = _mkzip(self.tmp / "inner.zip", self.ok)
        nested = _mkzip(self.tmp / "n.zip", {"inner.zip": inner.read_bytes()})
        with self.assertRaises(BenchError):  # zip dentro de zip: no se abre
            self.mat(nested)
        good = _mkzip(self.tmp / "g.zip", self.ok).read_bytes()
        trunc = self.tmp / "trunc.zip"
        trunc.write_bytes(good[: len(good) // 2])
        with self.assertRaises(BenchError):
            self.mat(trunc)
        absolute = _mkzip(
            self.tmp / "abs.zip", {**self.ok, "/vehicles/car1/abs.txt": b"x"}
        )
        try:
            d = self.mat(absolute)
            self.assertTrue(d.resolve().is_relative_to((self.tmp / "jobs").resolve()))
        except BenchError:
            pass
        self.assertFalse(Path("/vehicles/car1/abs.txt").exists())

    def test_status_reads_zip_without_extracting(self):
        z = _mkzip(self.tmp / "r.zip", self.ok)
        with mock.patch.object(service, "JOBS_ROOT", self.tmp / "jobs"):
            self.assertEqual(service.vehicle_id(z), "car1")
            service.uses_triangle_aero  # noqa: B018  (solo comprueba que existe)
        self.assertFalse((self.tmp / "jobs").exists())

    def test_partial_extraction_is_set_aside_and_redone(self):
        z = _mkzip(self.tmp / "p.zip", self.ok)
        d = self.mat(z)
        (d.parents[1] / ".extraido").unlink()  # corte antes de terminar
        (d / "car1.jbeam").write_text("basura")
        d2 = self.mat(z)
        self.assertEqual((d2 / "car1.jbeam").read_bytes(), b"{}")
        self.assertTrue(list((self.tmp / "jobs" / "zips").glob("*_parcial_*")))

    @unittest.skipUnless(SRC.is_dir(), "falta la muestra de Atlanta")
    def test_zip_fingerprints_equal_folder_and_zip_reads(self):
        z = self.tmp / "atl.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for p in [*SRC.glob("*.jbeam"), *SRC.glob("*.pc"), *SRC.glob("info*.json")]:
                zf.write(p, f"vehicles/{ID}/{p.name}")
        folder = load_vehicle_dir(self.mat(z), PC)
        zipped = load_vehicle_zip(z, None, PC)
        self.assertEqual(M.source_fingerprint(folder), M.source_fingerprint(zipped))
        self.assertEqual(
            M.scoped_fingerprint(folder, AERO), M.scoped_fingerprint(zipped, AERO)
        )


@unittest.skipUnless(
    SRC.is_dir()
    and (ROOT / "output/g215_direction_brakes_aero_2026-11-03/banco").is_dir(),
    "faltan la muestra o el volcado de G2.15",
)
class TestWorkspaceZipAndStates(unittest.TestCase):
    """Un zip llega al banco por la aplicación; el estado distingue cancelado/completado."""

    def setUp(self):
        from src.app.workspace import Workspace
        from src.bench import aero_runner as ar
        from src.bench import tyre_runner as tr

        self.ar, self.tr = ar, tr
        self.tmp = Path(tempfile.mkdtemp(prefix="g247_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for mod, name, val in (
            (M, "MEASUREMENTS_ROOT", self.tmp / "meas"),
            (ar, "MEASUREMENTS_ROOT", self.tmp / "meas"),
            (service, "JOBS_ROOT", self.tmp / "jobs"),
        ):
            p = mock.patch.object(mod, name, val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(tr, "beamng_running", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.zip = self.tmp / "atl.zip"
        with zipfile.ZipFile(self.zip, "w") as zf:
            for f in [*SRC.glob("*.jbeam"), *SRC.glob("*.pc"), *SRC.glob("info*.json")]:
                zf.write(f, f"vehicles/{ID}/{f.name}")
        self.ws = Workspace(
            self.tmp / "projects", source_dirs=[], source_zips=[], pairing=None
        )

    def _wait(self, jid):
        import time

        for _ in range(600):
            j = self.ws.job(jid)
            if j["status"] != "en_curso":
                return j
            time.sleep(0.05)
        self.fail("la tarea no terminó")

    def test_zip_bench_cancel_resume_complete(self):
        import time

        from tests.test_g246_bench_service import _fake_launch

        gate = []

        def slow(work, session, root, timeout=900, cancel=None, tick=None):
            while not gate:
                if cancel and cancel():
                    raise self.tr.BenchCancelled("cancelada")
                time.sleep(0.02)
            return _fake_launch(work, session, root)

        body = {"kind": "aero", "path": str(self.zip)}
        info = self.ws.bench_info(str(self.zip))
        st = {b["kind"]: b for b in info["benches"]}
        self.assertEqual(
            (st["aero"]["state"], st["aero"]["reuse"]), ("pendiente", "falta")
        )
        with mock.patch.object(self.ar, "launch", slow):
            job = self.ws.bench_start(body)
            for _ in range(200):
                if self.ws.job(job["id"])["log"]:
                    break
                time.sleep(0.02)
            with self.assertRaises(Exception):  # doble pulsación: rechazada
                self.ws.bench_start(body)
            self.ws.cancel(job["id"])
            self.ws.cancel(job["id"])  # idempotente mientras sigue en curso
            self.assertEqual(self._wait(job["id"])["status"], "cancelada")
        st = {b["kind"]: b for b in self.ws.bench_info(str(self.zip))["benches"]}
        self.assertEqual(st["aero"]["state"], "cancelado")
        self.assertEqual(st["aero"]["measurement"], "")  # nada a medias como válido
        with mock.patch.object(self.ar, "launch", _fake_launch):  # reanudar
            j = self._wait(self.ws.bench_start(body)["id"])
        self.assertEqual(j["status"], "hecha", j["error"])
        st = {b["kind"]: b for b in self.ws.bench_info(str(self.zip))["benches"]}
        self.assertEqual(
            (st["aero"]["state"], st["aero"]["reuse"]), ("completado", "exacta")
        )

    def test_explicit_taken_label_fails_before_any_session(self):
        (self.tmp / "meas/beamng_bench/aero").mkdir(parents=True)
        (self.tmp / "meas/beamng_bench/aero/taken.json").write_text(
            json.dumps(
                {
                    "schema": M.SCHEMA,
                    "kind": "beamng_aero_bench",
                    "source_fingerprint": "0" * 64,
                }
            ),
            "utf-8",
        )
        calls = []
        with mock.patch.object(self.ar, "launch", lambda *a, **k: calls.append(a)):
            with self.assertRaises(self.tr.BenchError):
                service.run("aero", SRC, label="taken")
        self.assertEqual(calls, [])

    def test_stale_matches_only_the_same_vehicle_id(self):
        root = self.tmp / "meas"
        root.mkdir(exist_ok=True)
        base = {
            "schema": M.SCHEMA,
            "kind": "beamng_tyre_bench",
            "source_fingerprint": "x",
        }
        for name, vid in (("otro", ID + "_turbo"), ("mismo", ID + "_0123abcd")):
            (root / f"{name}.json").write_text(
                json.dumps({**base, "label": {"vehicle_id": vid}}), "utf-8"
            )
        got = [p.name for p in service.stale_measurements("tyres", SRC, root=root)]
        self.assertEqual(got, ["mismo.json"])


COMMON = Path((default_beamng_root() / "content/vehicles/common.zip").as_posix())
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"


@unittest.skipUnless(COMMON.is_file() and MODS.is_dir(), "faltan common.zip o los mods")
class TestCommonZipReal(unittest.TestCase):
    @pytest.mark.ampliada
    def test_common_zip_never_changes_any_fingerprint_of_real_mods(self):
        zips = sorted(MODS.glob("*.zip"))
        self.assertTrue(zips)
        for z in zips:
            a, b = load_vehicle_zip(z, None), load_vehicle_zip(z, COMMON)
            with self.subTest(z.name):
                self.assertEqual(M.source_fingerprint(a), M.source_fingerprint(b))
                for kind in M.SCOPES:
                    self.assertEqual(
                        M.scoped_fingerprint(a, kind), M.scoped_fingerprint(b, kind)
                    )


if __name__ == "__main__":
    unittest.main()
