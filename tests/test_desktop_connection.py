"""El cliente que corta la conexión a mitad de respuesta no debe volcar un traceback."""

import threading
import unittest
from http.server import BaseHTTPRequestHandler
from unittest import mock

from src.app.desktop import make_desktop_handler


class ConnectionAbortTest(unittest.TestCase):
    def test_connection_error_is_swallowed(self) -> None:
        handler = make_desktop_handler(mock.Mock(), threading.Lock())
        h = handler.__new__(handler)
        with mock.patch.object(
            BaseHTTPRequestHandler, "handle", side_effect=ConnectionAbortedError
        ):
            h.handle()  # no debe propagar

    def test_other_errors_still_propagate(self) -> None:
        handler = make_desktop_handler(mock.Mock(), threading.Lock())
        h = handler.__new__(handler)
        with mock.patch.object(
            BaseHTTPRequestHandler, "handle", side_effect=ValueError
        ):
            with self.assertRaises(ValueError):
                h.handle()


if __name__ == "__main__":
    unittest.main()
