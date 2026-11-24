"""Interfaz de atributos: servicio y API HTTP local (sin navegador), con el Ibiza de
muestra."""

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.app.server import make_server
from src.app.service import ReviewService, ServiceError
from src.attrs.store import REPO_CATALOG
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
_PV: list = []
HUB_F = "ac:suspensions.ini/FRONT/HUB_MASS"
TOTAL = "ac:car.ini/BASIC/TOTALMASS"


def preview():
    if not _PV:
        _PV.append(preview_physics(load_vehicle_dir(IBIZA, "35b84.pc")))
    return _PV[0]


def catalog_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(REPO_CATALOG.rglob("*")):
        if p.is_file():
            h.update(p.as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestReviewService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog_before = catalog_hash()
        self.svc = ReviewService(preview(), Path(self.tmp.name) / "proyecto")

    def tearDown(self):
        self.assertEqual(
            catalog_hash(), self.catalog_before
        )  # el catálogo del repo no se toca
        self.tmp.cleanup()

    def test_state_and_listing(self):
        st = self.svc.state()
        self.assertEqual(
            (st["vehicle"], st["variant"], st["preview_ok"]),
            ("seat_ibiza_acfr", "35b84", True),
        )
        manual = self.svc.attributes("manual_required")
        # 5 pasan a no aplicable por TYPE (tarea B4); +4 tablas LUT sin decisión
        self.assertEqual(len(manual), 43)  # fase C5: +33 atributos de alas sin decisión
        self.assertTrue(all(i["verifiable"] == "sin valor" for i in manual))
        found = self.svc.attributes(text="TOTALMASS")
        self.assertEqual([i["target"] for i in found], [TOTAL])

    def test_detail(self):
        d = self.svc.attribute(TOTAL)
        self.assertEqual(d["result"]["rule"], "car.totalmass@2")  # G2.4: entradas ir.*
        self.assertEqual(
            [i["name"] for i in d["result"]["inputs"]],
            ["total", "driver", "hub_front", "hub_rear"],
        )
        self.assertIn("kg", d["compatible_units"])
        self.assertNotIn("m", d["compatible_units"])
        with self.assertRaises(ServiceError):
            self.svc.attribute("ac:no/existe")

    def test_manual_value_recalculates_dependents_and_revert_restores(self):
        before = self.svc.attribute(TOTAL)["result"]["value"]
        out = self.svc.decide(
            {
                "target": HUB_F,
                "action": "manual_value",
                "value": "50",
                "unit": "kg",
                "justification": "prueba",
                "status": "validated",
            }
        )
        self.assertEqual(
            (out["result"]["status"], out["result"]["value"]), ("known", 50)
        )
        after = self.svc.attribute(TOTAL)["result"]["value"]
        self.assertAlmostEqual(
            before - after, 2 * (50 - 49.15), places=6
        )  # TOTALMASS depende de HUB_MASS
        self.svc.decide(
            {
                "target": HUB_F,
                "action": "revert",
                "justification": "volver al valor importado",
            }
        )
        self.assertEqual(self.svc.attribute(HUB_F)["result"]["value"], 49.15)
        hist = self.svc.attribute(HUB_F)["decisions"]
        self.assertEqual(len(hist), 3)  # importada + manual + reversión: nada se borra
        lines = (
            (Path(self.tmp.name) / "proyecto" / "decisions" / "seat_ibiza_acfr.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(len(lines), 2)

    def test_validation_errors(self):
        bad = [
            {
                "target": HUB_F,
                "action": "manual_value",
                "value": "50",
                "unit": "m",
                "justification": "x",
            },
            {
                "target": HUB_F,
                "action": "manual_value",
                "value": "50",
                "unit": "kg",
                "justification": " ",
            },
            {"target": HUB_F, "action": "magia", "justification": "x"},
            {"target": "ac:nada", "action": "defer", "justification": "x"},
            {
                "target": HUB_F,
                "action": "manual_value",
                "value": "50",
                "unit": "kg",
                "justification": "x",
                "scope": "generic",
            },
            {
                "target": HUB_F,
                "action": "revert",
                "justification": "x",
            },  # la importada no sustituye a nada
        ]
        for body in bad:
            with self.subTest(body):
                with self.assertRaises(ServiceError):
                    self.svc.decide(body)

    def test_family_scope_is_saved_but_not_applied(self):
        target = "ac:suspensions.ini/FRONT/BUMP_STOP_RATE"
        out = self.svc.decide(
            {
                "target": target,
                "action": "manual_value",
                "value": "1000",
                "unit": "N/m",
                "justification": "para la familia",
                "scope": "family",
            }
        )
        self.assertIn("no lo aplica", out["note"])
        self.assertEqual(out["result"]["status"], "manual_required")

    def test_transform_defer_and_reject(self):
        target = "ac:suspensions.ini/FRONT/BUMP_STOP_RATE"
        out = self.svc.decide(
            {
                "target": target,
                "action": "transform",
                "transform_id": "scale",
                "inputs": {"x": "bng.suspension.front.spring_rate_beam"},
                "params": {"factor": 2},
                "unit": "N/m",
                "justification": "ejemplo de transformación explícita",
            }
        )
        self.assertEqual(out["result"]["status"], "inferred")
        spring = self.svc.ctx.facts["bng.suspension.front.spring_rate_beam"].value
        self.assertAlmostEqual(out["result"]["value"], 2 * spring)
        with self.assertRaises(
            ServiceError
        ):  # unidades incompatibles en la transformación
            self.svc.decide(
                {
                    "target": target,
                    "action": "transform",
                    "transform_id": "identity",
                    "inputs": {"x": "bng.mass.total"},
                    "unit": "N/m",
                    "justification": "mal",
                }
            )
        out = self.svc.decide(
            {"target": target, "action": "defer", "justification": "esperar datos"}
        )
        self.assertEqual(out["result"]["status"], "manual_required")
        out = self.svc.decide(
            {
                "target": "ac:engine.ini/ENGINE_DATA/LIMITER",
                "action": "reject",
                "justification": "no acepto la hipótesis",
                "status": "validated",
            }
        )
        self.assertEqual(out["result"]["status"], "manual_required")
        self.assertEqual(out["decision"]["reference"]["rejected_rule"], "eng.limiter@2")


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.svc = ReviewService(preview(), Path(cls.tmp.name) / "p")
        cls.server = make_server(cls.svc, 0)
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=30) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Content-Type")

    def post(self, body):
        req = urllib.request.Request(
            self.url + "/api/decision",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_bound_to_localhost(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_frontend_and_api(self):
        code, html, ctype = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("revisión de atributos", html)
        code, js, _ = self.get("/static/app.js")
        self.assertIn("textContent", js)
        self.assertNotIn("innerHTML", js)  # nada de HTML construido desde datos
        code, body, _ = self.get("/api/state")
        self.assertEqual(json.loads(body)["vehicle"], "seat_ibiza_acfr")
        code, body, _ = self.get("/api/attribute?target=" + urllib.request.quote(TOTAL))
        self.assertEqual(json.loads(body)["result"]["target"], TOTAL)

    def test_errors_and_traversal(self):
        for path in ("/static/../server.py", "/static/..%2Fserver.py", "/api/nada"):
            with self.subTest(path):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(path)
                self.assertEqual(ctx.exception.code, 404)
        code, body = self.post(
            {
                "target": HUB_F,
                "action": "manual_value",
                "value": "1",
                "unit": "m",
                "justification": "x",
            }
        )
        self.assertEqual(code, 400)
        self.assertIn("incompatible", body["error"])
        code, body = self.post(
            {
                "target": HUB_F,
                "action": "manual_value",
                "value": "48",
                "unit": "kg",
                "justification": "desde la API",
            }
        )
        self.assertEqual((code, body["result"]["value"]), (200, 48))


if __name__ == "__main__":
    unittest.main()
