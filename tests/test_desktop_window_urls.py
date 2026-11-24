"""Las URLs que la ventana nativa puede abrir en el navegador son una lista cerrada."""

import unittest

from src.app import blender_addon, desktop_window


class TestOpenUrlWhitelist(unittest.TestCase):
    def test_addon_source_is_allowed(self) -> None:
        # el botón «Ver el complemento en GitHub» de Ajustes pasa blender_addon.REPO_URL
        self.assertIn(blender_addon.REPO_URL, desktop_window.DOWNLOAD_URLS)

    def test_only_https(self) -> None:
        for u in desktop_window.DOWNLOAD_URLS:
            self.assertTrue(u.startswith("https://"), u)


if __name__ == "__main__":
    unittest.main()
