"""G2.33: correcciones de G2.32 — origen de los POST y errores 400 del editor de cámaras (rápidas, sin juegos)."""

import http.client
import json
import shutil
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from src.app import post_guard
from src.app.desktop import make_desktop_server
from src.app.workspace import Workspace, WorkspaceError, _cockpit_errors

OK = {"Host": "127.0.0.1:8000", "Content-Type": "application/json"}


class TestPostGuard(unittest.TestCase):
    def test_valid_without_origin(self):
        self.assertIsNone(post_guard.post_problem(OK, 8000))
        self.assertIsNone(
            post_guard.post_problem(
                {**OK, "Host": "localhost:8000", "Origin": "http://localhost:8000"},
                8000,
            )
        )
        self.assertIsNone(
            post_guard.post_problem(
                {**OK, "Content-Type": "application/json; charset=utf-8"}, 8000
            )
        )

    def test_rejections(self):
        self.assertEqual(
            post_guard.post_problem({**OK, "Origin": "http://evil.example"}, 8000)[0],
            403,
        )
        self.assertEqual(
            post_guard.post_problem({**OK, "Origin": "null"}, 8000)[0], 403
        )
        self.assertEqual(
            post_guard.post_problem({**OK, "Host": "evil.example:8000"}, 8000)[0], 403
        )
        self.assertEqual(
            post_guard.post_problem({**OK, "Host": "127.0.0.1:9"}, 8000)[0], 403
        )
        self.assertEqual(
            post_guard.post_problem({"Content-Type": "application/json"}, 8000)[0], 403
        )  # sin Host
        self.assertEqual(
            post_guard.post_problem({**OK, "Content-Type": "text/plain"}, 8000)[0], 415
        )
        self.assertEqual(post_guard.post_problem({"Host": OK["Host"]}, 8000)[0], 415)

    def test_content_length(self):
        self.assertEqual(post_guard.content_length({"Content-Length": "12"}), 12)
        self.assertEqual(post_guard.content_length({}), 0)
        self.assertEqual(post_guard.content_length({"Content-Length": "x"}), -1)


class TestCockpitErrors(unittest.TestCase):
    def test_expected_failures_become_400s(self):
        for exc in (
            ValueError("v"),
            KeyError("k"),
            OSError("o"),
            struct.error("s"),
            json.JSONDecodeError("m", "d", 0),
        ):

            @_cockpit_errors
            def f(self, pid, exc=exc):
                raise exc

            with self.assertRaises(WorkspaceError):
                f(None, "p")

    def test_programming_errors_stay_500(self):
        @_cockpit_errors
        def g(self, pid):
            raise TypeError("bug")

        with self.assertRaises(TypeError):
            g(None, "p")


class TestHttp(unittest.TestCase):
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

    def post(self, headers, body=b'{"job": "x"}', path="/api/cancel"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest("POST", path, skip_host=True)
        for k, v in headers.items():
            c.putheader(k, v)
        c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out

    def h(self, **extra):
        return {
            "Host": f"127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            **extra,
        }

    def test_foreign_origin_host_and_type_rejected(self):
        self.assertEqual(self.post(self.h(Origin="http://evil.example"))[0], 403)
        self.assertEqual(self.post({**self.h(), "Host": "evil.example"})[0], 403)
        self.assertEqual(self.post({**self.h(), "Content-Type": "text/plain"})[0], 415)

    def test_valid_post_reaches_the_handler(self):
        status, _ = self.post(self.h(Origin=f"http://127.0.0.1:{self.port}"))
        self.assertNotIn(status, (403, 415))

    def test_bad_utf8_body_is_400(self):
        self.assertEqual(self.post(self.h(), body=b"\xff\xfe{")[0], 400)

    def test_cockpit_unknown_project_is_400(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/cockpit?project=nope")
        self.assertEqual(c.getresponse().status, 400)


if __name__ == "__main__":
    unittest.main()
