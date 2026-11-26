"""Las URLs que la ventana nativa puede abrir en el navegador son una lista cerrada."""

import unittest

from src.app import desktop_window


class TestOpenUrlWhitelist(unittest.TestCase):
    def test_author_links_are_allowed(self) -> None:
        self.assertIn(
            "https://github.com/alexceend/beam2ac", desktop_window.DOWNLOAD_URLS
        )

    def test_only_https(self) -> None:
        for u in desktop_window.DOWNLOAD_URLS:
            self.assertTrue(u.startswith("https://"), u)


if __name__ == "__main__":
    unittest.main()
