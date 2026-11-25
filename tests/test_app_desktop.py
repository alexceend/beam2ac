"""Fase C3: aplicación de escritorio (src/app/workspace.py, src/app/desktop.py)."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.app.desktop import make_desktop_server
from src.app.workspace import Workspace, WorkspaceError

IBIZA_DIR = Path("sample_beamng/vehicles")
IBIZA = IBIZA_DIR / "seat_ibiza_acfr"
REAL_ATTEMPT = Path("output/projects/ibiza_35b84_b1/exports/005")


class FakeResult:
    def __init__(self, name: str) -> None:
        self.name, self.status, self.error = name, "ok", None


def fake_runner(project, stages, log=print):
    log("[hecha] falsa")
    time.sleep(0.2)
    return [FakeResult(s.name) for s in stages]


def wait(ws: Workspace, jid: str) -> dict:
    for _ in range(200):
        j = ws.job(jid)
        if j["status"] != "en_curso":
            return j
        time.sleep(0.05)
    raise AssertionError("la tarea no terminó")


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        pairing = self.tmp / "pairing.json"
        pairing.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "bng": "seat_ibiza_acfr",
                            "ac": "ks_x",
                            "classification": "hipótesis",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.ws = Workspace(
            self.tmp / "projects", [IBIZA_DIR], [], pairing, runner=fake_runner
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new(self, name: str = "ibz") -> dict:
        return self.ws.create(
            {"name": name, "kind": "dir", "path": str(IBIZA), "pc": "35b84.pc"}
        )

    def test_create_list_and_refuse(self) -> None:
        p = self.new()
        self.assertEqual(p["id"], "ibz")
        self.assertEqual([x["id"] for x in self.ws.projects()], ["ibz"])
        for bad in ("../x", "con espacio", "", "a/b", ".oculto"):
            with self.assertRaises(WorkspaceError):
                self.ws.create({"name": bad, "kind": "dir", "path": str(IBIZA)})
        with self.assertRaisesRegex(WorkspaceError, "no se sobrescribe"):
            self.new()
        with self.assertRaises(WorkspaceError):
            self.ws.create(
                {"name": "y", "kind": "dir", "path": str(IBIZA), "pc": "no.pc"}
            )

    def test_sources_and_references(self) -> None:
        src = {s["vehicle"]: s for s in self.ws.sources()}
        self.assertIn("35b84.pc", src["seat_ibiza_acfr"]["configs"])
        self.assertEqual(self.ws.references("seat_ibiza_acfr")[0]["ac"], "ks_x")
        self.assertEqual(self.ws.references("otro"), [])

    def test_jobs_one_at_a_time(self) -> None:
        self.new()
        j = self.ws.run("ibz")
        with self.assertRaisesRegex(WorkspaceError, "en curso"):
            self.ws.run("ibz")
        done = wait(self.ws, j["id"])
        self.assertEqual(done["status"], "hecha")
        self.assertIn("[hecha] falsa", done["log"])

    def test_export_needs_confirmation_and_valid_options(self) -> None:
        self.new()
        with self.assertRaisesRegex(WorkspaceError, "confirm"):
            self.ws.export("ibz", {})
        with self.assertRaisesRegex(WorkspaceError, "car_id"):
            self.ws.export("ibz", {"confirm": True, "car_id": "Mal Id"})
        with self.assertRaisesRegex(
            WorkspaceError, "ruta de Assetto Corsa"
        ):  # obligatoria: de ella sale el piloto
            self.ws.export(
                "ibz", {"confirm": True, "ac_root": str(self.tmp / "no_existe")}
            )
        self.assertEqual(self.ws.exports("ibz"), [])  # nada se creó

    def test_files_confined_to_project(self) -> None:
        self.new()
        self.assertIn("schema", self.ws.read_file("ibz", "project.json")["text"])
        for bad in ("../../README.md", "..\\x.json", "C:/Windows/win.ini"):
            with self.assertRaises(WorkspaceError):
                self.ws.read_file("ibz", bad)
        opened = []
        self.ws.open_path("ibz", "decisions", opened.append)
        self.assertTrue(opened[0].endswith("decisions"))
        with self.assertRaises(WorkspaceError):
            self.ws.open_path("ibz", "..", opened.append)

    def test_precheck_and_detail_after_real_run(self) -> None:
        from src.project.orchestrator import run_project

        self.new()
        self.assertFalse(self.ws.precheck("ibz")["available"])
        self.ws._runner = run_project  # flujo real (sin modelo 3D)
        j = wait(self.ws, self.ws.run("ibz")["id"])
        self.assertEqual(j["status"], "hecha", j)
        d = self.ws.detail("ibz")
        names = {s["name"]: s["status"] for s in d["stages"]}
        self.assertEqual(names["resolved_ini"], "ok")
        self.assertEqual(names["model"], "no_ejecutada")
        pc = d["precheck"]
        self.assertTrue(pc["available"])
        self.assertIn(
            "ac:brakes.ini/DATA/FRONT_SHARE", {b["target"] for b in pc["errors"]}
        )


class TestDesktopHttp(unittest.TestCase):
    """La aplicación arranca, sirve su frontal y rechaza peticiones peligrosas."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.ws = Workspace(
            cls.tmp / "projects", [IBIZA_DIR], [], None, runner=fake_runner
        )
        cls.server = make_desktop_server(cls.ws, 0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://%s:%d" % cls.server.server_address[:2]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read()

    def post(self, path: str, body: dict):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_local_only_and_pages(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for path in (
            "/",
            "/static/desktop.js",
            "/static/scene.html",
            "/static/scene.js",
            "/static/desktop.css",
            "/static/index.html",
        ):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body)
        status, body = self.get("/api/workspace")
        self.assertEqual(json.loads(body)["projects"], [])

    def test_dangerous_requests_refused(self) -> None:
        self.assertEqual(self.post("/api/export", {"project": "nada"})[0], 400)
        self.assertEqual(self.post("/api/projects", {"name": "../x"})[0], 400)
        try:
            self.get("/api/file?project=..&path=x")
            self.fail("debía fallar")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
        try:
            self.get("/static/../server.py")
            self.fail("debía fallar")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


@unittest.skipUnless((REAL_ATTEMPT / "manifest.json").is_file(), "sin el intento 005")
class TestSceneFromRealExport(unittest.TestCase):
    def test_scene(self) -> None:
        ws = Workspace(REAL_ATTEMPT.parents[2], [], [], None)
        s = ws.scene("ibiza_35b84_b1", "005")
        self.assertEqual(
            sorted(s["wheels"]), ["WHEEL_LF", "WHEEL_LR", "WHEEL_RF", "WHEEL_RR"]
        )
        self.assertEqual(s["driver_eyes"], [0.355, 1.144, -0.344])
        self.assertTrue(s["driver_pose"])
        self.assertIsNotNone(s["collider_box"])
        self.assertIsNotNone(s["cameras"]["BONNET_CAMERA_POS"])
        detail = ws.export_detail("ibiza_35b84_b1", "005")
        self.assertTrue(detail["inspect"]["intact"])
        self.assertIn("NO es una prueba en el juego", detail["validation"])


if __name__ == "__main__":
    unittest.main()


class TestEditorTableUnits(unittest.TestCase):
    def test_table_attribute_offers_its_unit(self) -> None:
        from src.app.service import _compatible_units
        from src.attrs.store import Catalog

        cat = Catalog.load("catalog")
        for tid in ("ac:power.lut", "ac:tyres.ini/FRONT/WEAR_CURVE/table"):
            units = _compatible_units(cat.attributes[tid])
            self.assertEqual(units[0], cat.attributes[tid].unit)
        self.assertIn(
            "N·m", _compatible_units(cat.attributes["ac:brakes.ini/DATA/MAX_TORQUE"])
        )
