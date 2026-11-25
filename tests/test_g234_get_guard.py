"""G2.34: los GET comprueban Host/Origin igual que los POST (rebinding de DNS)."""

from __future__ import annotations

import http.client
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from src.app import post_guard
from src.app.desktop import make_desktop_server
from src.app.server import make_server
from src.app.workspace import Workspace


class TestGetProblem(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(post_guard.get_problem({"Host": "127.0.0.1:8000"}, 8000))
        self.assertIsNone(post_guard.get_problem({"Host": "LOCALHOST:8000"}, 8000))

    def test_rejected(self):
        for headers in (
            {},
            {"Host": "evil.example:8000"},
            {"Host": "127.0.0.1:9"},
            {"Host": "127.0.0.1:8000", "Origin": "http://evil.example"},
            {"Host": "127.0.0.1:8000", "Origin": "null"},
        ):
            self.assertEqual(post_guard.get_problem(headers, 8000)[0], 403, headers)


class _Http:
    def get(self, path, host=None, **extra):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("GET", path, skip_host=True)
        c.putheader("Host", host or f"127.0.0.1:{self.port}")
        for k, v in extra.items():
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out


class TestDesktopGet(_Http, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_desktop_server(Workspace(cls.tmp / "p", [], [], None), 0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_normal_get_works(self):
        self.assertEqual(self.get("/api/workspace")[0], 200)
        self.assertEqual(self.get("/")[0], 200)

    def test_foreign_host_and_origin_blocked_everywhere(self):
        for path in ("/", "/static/desktop.js", "/api/workspace", "/api/environment"):
            self.assertEqual(self.get(path, host="evil.example")[0], 403, path)
            self.assertEqual(self.get(path, Origin="http://evil.example")[0], 403, path)


class TestReviewGet(_Http, unittest.TestCase):
    def test_review_server_blocks_foreign_host(self):
        from src.app.service import ReviewService

        try:
            service = ReviewService.__new__(ReviewService)
        except Exception:  # pragma: no cover
            self.skipTest("ReviewService no instanciable")
        server = make_server(service, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.port = server.server_address[1]
        try:
            self.assertEqual(self.get("/api/state", host="evil.example")[0], 403)
            self.assertEqual(self.get("/nada")[0], 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
