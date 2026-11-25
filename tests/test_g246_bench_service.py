"""G2.46: contrato único de los bancos (CLI + aplicación de escritorio), sin BeamNG.

Se sustituyen solo las fronteras con el juego (``launch``/``beamng_running``/``Popen``);
la preparación, la recogida, la huella y las tareas de la aplicación son las reales."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from src.app.workspace import Workspace, WorkspaceError
from src.bench import aero_runner as ar
from src.bench import service
from src.bench import tyre_runner as tr
from src.importers import measurements as M

ROOT = Path(__file__).resolve().parents[1]
G215 = ROOT / "output/g215_direction_brakes_aero_2026-11-03/banco"
NAME = "2020_rpm_atlanta_gt_3_6t"
SAMPLE = ROOT / "samples_beamng" / NAME / "vehicles" / NAME
DUMP = G215 / "userpath/current/beam2ac_diraero" / f"{NAME}.json"
HAVE = DUMP.is_file() and SAMPLE.is_dir()


def _fake_launch(work, session, beamng_root, timeout=900, cancel=None, tick=None):
    """El «juego»: deja el volcado de G2.15 donde lo dejaría el banco aerodinámico."""
    user = Path(work) / f"userpath_{session}" / "current"
    (user / ar.AERO_DIR).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DUMP, user / ar.AERO_DIR / f"{NAME}.json")
    shutil.copyfile(G215 / "userpath/current/beamng.log", user / "beamng.log")
    if tick:
        tick(1.0)
    if cancel and cancel():
        raise tr.BenchCancelled("cancelada")
    return {"exit": 0, "seconds": 1.0, "newer_files": 0}


@unittest.skipUnless(HAVE, "faltan la muestra o el volcado de G2.15")
class TestService(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g246_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # medidas y trabajos en una carpeta vacía: el coche «no tiene medida»
        for mod, name in ((M, "MEASUREMENTS_ROOT"), (ar, "MEASUREMENTS_ROOT")):
            p = mock.patch.object(mod, name, self.tmp / "meas")
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(service, "JOBS_ROOT", self.tmp / "jobs")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(tr, "beamng_running", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_needs_follow_the_blockers_and_the_aero_kind(self) -> None:
        blockers = [
            {"target": "ac:tyres.ini/FRONT/RATE"},
            {"target": "ac:aero.ini/WING_0/NAME"},
        ]
        got = {n["kind"]: n for n in service.needs(SAMPLE, None, blockers)}
        self.assertEqual(set(got), {"tyres", "aero"})
        self.assertEqual(got["aero"]["minutes"], 4)
        # sin bloqueos de aerodinámica no se pide el banco aerodinámico
        self.assertEqual(
            [n["kind"] for n in service.needs(SAMPLE, None, blockers[:1])], ["tyres"]
        )
        self.assertTrue(service.uses_triangle_aero(SAMPLE))
        # un vehículo con cefaero (aerodinámica analítica) nunca pide el aerodinámico
        sample38 = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
        if sample38.is_dir():
            self.assertFalse(service.uses_triangle_aero(sample38, "35b84.pc"))

    def test_the_work_dir_depends_on_the_fingerprint_not_the_name(self) -> None:
        a = service.work_dir("aero", SAMPLE)
        copy = self.tmp / "otro_nombre" / NAME
        shutil.copytree(SAMPLE, copy, ignore=shutil.ignore_patterns("*.dds", "*.dae"))
        self.assertEqual(a, service.work_dir("aero", copy))

    def test_run_writes_the_measurement_and_a_second_run_is_refused(self) -> None:
        with mock.patch.object(ar, "launch", _fake_launch):
            rec = service.run("aero", SAMPLE, label="aero_test")
        self.assertTrue(Path(rec["_target"]).is_file())
        self.assertEqual(rec["kind"], "beamng_aero_bench")
        self.assertIsNotNone(service.measurement("aero", SAMPLE))
        with self.assertRaises(tr.BenchError):  # ya medido: no se repite
            service.run("aero", SAMPLE)
        # el bloqueo se libera siempre
        self.assertFalse(service._lock_path().exists())

    def test_resume_reuses_finished_sessions_and_sets_aside_partial_ones(self) -> None:
        work = service.work_dir("aero", SAMPLE)
        calls = []

        def once(w, s, root, **kw):
            calls.append(s)
            return _fake_launch(w, s, root, **kw)

        # s1 hecha en un intento anterior; s2 a medias (carpeta sin resultados)
        with mock.patch.object(ar, "prepare", wraps=ar.prepare):
            ar.prepare(work, "s1", SAMPLE)
        _fake_launch(work, "s1", None)
        ar.prepare(work, "s2", SAMPLE)
        st = service.status("aero", SAMPLE)
        self.assertEqual(st["sessions"], {"s1": "hecha", "s2": "preparada"})
        with mock.patch.object(ar, "launch", once):
            service.run("aero", SAMPLE, label="aero_resume")
        self.assertEqual(calls, ["s2"])  # solo la que faltaba
        self.assertTrue(
            list(work.glob("userpath_s2_parcial_*"))
        )  # apartada, no borrada

    def test_cancel_inside_a_session_leaves_no_measurement_and_no_lock(self) -> None:
        with mock.patch.object(ar, "launch", _fake_launch):
            with self.assertRaises(tr.BenchCancelled):
                service.run("aero", SAMPLE, cancel=lambda: True)
        self.assertIsNone(service.measurement("aero", SAMPLE))
        self.assertFalse(service._lock_path().exists())
        self.assertEqual(service.status("aero", SAMPLE)["measurement"], "")

    def test_refuses_with_beamng_running_and_with_a_foreign_lock(self) -> None:
        with mock.patch.object(tr, "beamng_running", return_value=True):
            with self.assertRaises(tr.BenchError):
                service.run("aero", SAMPLE)
        lock = service._lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": 4, "t": time.time()}), "utf-8")  # PID 4 vivo
        with mock.patch.object(service, "_pid_alive", return_value=True):
            with self.assertRaises(tr.BenchError):
                service.run("aero", SAMPLE)
        self.assertTrue(lock.exists())  # no se pisa el bloqueo de otro
        with mock.patch.object(service, "_pid_alive", return_value=False):
            with mock.patch.object(ar, "launch", _fake_launch):
                service.run(
                    "aero", SAMPLE, label="aero_orphan_lock"
                )  # huérfano: se retira


class TestLaunchCancel(unittest.TestCase):
    def test_cancel_kills_the_game_tree_and_raises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="g246_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        exe = tmp / tr.BEAMNG_EXE
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")

        class FakeProc:
            pid = 99999
            returncode = None

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("x", timeout)

        ticks = []
        with (
            mock.patch.object(tr, "beamng_running", return_value=False),
            mock.patch.object(tr.subprocess, "Popen", return_value=FakeProc()),
            mock.patch.object(tr, "_kill_tree") as kill,
        ):
            with self.assertRaises(tr.BenchCancelled):
                tr.launch(tmp, "s1", tmp, cancel=lambda: True, tick=ticks.append)
        kill.assert_called_once()
        self.assertTrue(ticks)
        self.assertFalse((tmp / "lanzamiento_s1.json").exists())  # sin resultado


@unittest.skipUnless(HAVE, "faltan la muestra o el volcado de G2.15")
class TestWorkspaceBench(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g246_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for mod, name in ((M, "MEASUREMENTS_ROOT"), (ar, "MEASUREMENTS_ROOT")):
            p = mock.patch.object(mod, name, self.tmp / "meas")
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(service, "JOBS_ROOT", self.tmp / "jobs")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(tr, "beamng_running", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.ws = Workspace(
            self.tmp / "projects", source_dirs=[], source_zips=[], pairing=None
        )

    def _wait(self, jid: str) -> dict:
        for _ in range(600):
            j = self.ws.job(jid)
            if j["status"] != "en_curso":
                return j
            time.sleep(0.05)
        self.fail("la tarea no terminó")

    def test_start_follow_and_result(self) -> None:
        info = self.ws.bench_info(str(SAMPLE))
        self.assertEqual({b["kind"] for b in info["benches"]}, {"tyres", "aero"})
        self.assertIsNone(info["job"])
        with mock.patch.object(ar, "launch", _fake_launch):
            job = self.ws.bench_start({"kind": "aero", "path": str(SAMPLE)})
            j = self._wait(job["id"])
        self.assertEqual(j["status"], "hecha", j["error"])
        self.assertTrue(Path(j["result"]["measurement"]).is_file())
        # ya medido: la interfaz no deja repetirlo
        with self.assertRaises(WorkspaceError):
            self.ws.bench_start({"kind": "aero", "path": str(SAMPLE)})

    def test_only_one_bench_at_a_time(self) -> None:
        gate = []

        def slow(work, session, root, timeout=900, cancel=None, tick=None):
            while not gate:
                if cancel and cancel():
                    raise tr.BenchCancelled("cancelada")
                time.sleep(0.02)
            return _fake_launch(work, session, root)

        with mock.patch.object(ar, "launch", slow):
            job = self.ws.bench_start({"kind": "aero", "path": str(SAMPLE)})
            for _ in range(200):  # espera a que la sesión esté en marcha
                if self.ws.job(job["id"])["log"]:
                    break
                time.sleep(0.02)
            with self.assertRaises(WorkspaceError):
                self.ws.bench_start({"kind": "tyres", "path": str(SAMPLE)})
            self.assertEqual(self.ws.bench_info(str(SAMPLE))["job"]["id"], job["id"])
            self.ws.cancel(job["id"])  # cancelar DENTRO de la sesión
            j = self._wait(job["id"])
        self.assertEqual(j["status"], "cancelada")
        self.assertIsNone(service.measurement("aero", SAMPLE))
        self.assertFalse(service._lock_path().exists())

    def test_invalid_requests(self) -> None:
        for body in (
            {"kind": "nada", "path": str(SAMPLE)},
            {"kind": "aero", "path": str(self.tmp)},
            {"kind": "aero", "path": str(SAMPLE), "label": "../x"},
        ):
            with self.subTest(body=body), self.assertRaises(WorkspaceError):
                self.ws.bench_start(body)


if __name__ == "__main__":
    unittest.main()
