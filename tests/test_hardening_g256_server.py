"""G2.56: las acciones con efectos del servidor local exigen confirm: true y los ajustes se validan antes de aplicarse."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.app import settings
from src.app.desktop import make_desktop_server
from src.app.workspace import Workspace


class TestServerConfirm(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        ws = Workspace(Path(self.tmp.name), [], [], None)
        self.srv = make_desktop_server(ws, 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_convert_and_bench_need_confirm(self) -> None:
        for path in ("/api/convert", "/api/bench_start"):
            code, out = self.post(path, {})
            self.assertGreaterEqual(code, 400, path)
            self.assertIn("confirm", out["error"], path)

    def test_settings_output_dirs_need_confirm(self) -> None:
        code, out = self.post("/api/settings", {"projects_dir": self.tmp.name})
        self.assertGreaterEqual(code, 400)
        self.assertIn("confirm", out["error"])


class TestSettingsTooBroad(unittest.TestCase):
    def test_drive_root_and_home_are_refused_before_apply(self) -> None:
        for folder in (Path.home().anchor, str(Path.home())):
            with self.assertRaises(ValueError):
                settings.save(
                    {"projects_dir": folder}, Path(tempfile.gettempdir()) / "x.json"
                )


if __name__ == "__main__":
    unittest.main()
