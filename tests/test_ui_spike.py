"""Prueba técnica de la interfaz: three.js local, sin CDN y puente Python (sin ventana).

La autoprueba con ventana real (pywebview en .venv-ui) solo se ejecuta con
BEAM2AC_WEBVIEW_TESTS=1.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.app.server import STATIC, resolve_static
from src.app.webview_spike import SpikeApi, make_static_server

ROOT = Path(__file__).resolve().parents[1]
VENDOR = STATIC / "vendor" / "three-0.160.0"
VENV_PY = ROOT / ".venv-ui" / "Scripts" / "python.exe"


class TestVendoredThree(unittest.TestCase):
    def test_files_match_manifest(self):
        manifest = json.loads((VENDOR / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(
            (manifest["package"], manifest["version"], manifest["license"]),
            ("three", "0.160.0", "MIT"),
        )
        self.assertTrue(manifest["npm_integrity"].startswith("sha512-"))
        for rel, info in manifest["files"].items():
            with self.subTest(rel):
                data = (VENDOR / rel).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"])
        self.assertIn(
            "REVISION = '160'", (VENDOR / "three.module.js").read_text(encoding="utf-8")
        )


class TestSpikePage(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "spike" / "spike.html").read_text(encoding="utf-8")
        self.js = (STATIC / "spike" / "spike.js").read_text(encoding="utf-8")

    def test_no_external_resources(self):
        self.assertIn("default-src 'self'", self.html)
        self.assertIn("connect-src 'self'", self.html)
        for text in (self.html, self.js):
            self.assertIsNone(
                re.search(r"https?://", text)
            )  # ningún CDN ni origen externo
        imports = json.loads(
            re.search(
                r'<script type="importmap">(.*?)</script>', self.html, re.S
            ).group(1)
        )
        self.assertEqual(
            imports["imports"]["three"], "/static/vendor/three-0.160.0/three.module.js"
        )

    def test_bridge_api(self):
        api = SpikeApi({"vehicle": "x", "wheel_centers_ac": {}, "radius": 0.3})
        self.assertEqual(api.get_scene()["vehicle"], "x")
        self.assertEqual(
            api.report_position("t", 1.0, 2.0, 3.0)["echo"], [1.0, 2.0, 3.0]
        )
        self.assertEqual(api.received[-1]["position"], [1.0, 2.0, 3.0])


class TestSpikeServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.requests = make_static_server()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_module_mime_and_traversal(self):
        with urllib.request.urlopen(
            self.url + "/static/vendor/three-0.160.0/three.module.js", timeout=30
        ) as r:
            self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"))
        for path in (
            "/static/../server.py",
            "/static/vendor/../../server.py",
            "/static/spike/../../app.py",
            "/server.py",
        ):
            with self.subTest(path):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(self.url + path, timeout=30)
                self.assertEqual(ctx.exception.code, 404)
        self.assertIsNone(resolve_static("..\\server.py"))
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


@unittest.skipUnless(
    os.environ.get("BEAM2AC_WEBVIEW_TESTS") == "1" and VENV_PY.is_file(),
    "ventana real desactivada (BEAM2AC_WEBVIEW_TESTS=1 y .venv-ui)",
)
class TestWebviewSelftest(unittest.TestCase):
    def test_selftest_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "spike"
            proc = subprocess.run(
                [
                    str(VENV_PY),
                    "-B",
                    "-m",
                    "src.app.webview_spike",
                    "--selftest",
                    "--timeout",
                    "45",
                    "--out",
                    str(out),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=150,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:]
            )
            result = json.loads((out / "spike_result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertLess(result["roundtrip_max_abs_error_m"], 1e-4)
            self.assertTrue(result["only_local_allowed_paths"])


if __name__ == "__main__":
    unittest.main()
