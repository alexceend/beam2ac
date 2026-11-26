"""Detección de bibliotecas de Steam (src/steam_paths.py): sin tocar el equipo, con carpetas sintéticas."""

import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

from src import steam_paths as sp

BS = chr(92)
VDF = (
    '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:'
    + BS * 2
    + "Steam"
    + BS * 2
    + '"\n'
    '\t}\n\t"1"\n\t{\n\t\t"path"\t\t"{lib}"\n\t}\n}\n'
)


class TestSteamPaths(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "rutas de Windows: solo Windows")
    def test_parse_unescapes_backslashes(self) -> None:
        paths = sp.parse_library_folders(VDF.replace("{lib}", "D:" + BS * 2 + "Games"))
        self.assertEqual(
            [str(p) for p in paths], ["C:" + BS + "Steam", "D:" + BS + "Games"]
        )

    def test_finds_game_in_extra_library(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root, extra = Path(t) / "Steam", Path(t) / "Lib2"
            (root / "steamapps").mkdir(parents=True)
            (extra / "steamapps" / "common" / "assettocorsa").mkdir(parents=True)
            (root / "steamapps" / "libraryfolders.vdf").write_text(
                VDF.replace("{lib}", str(extra).replace(BS, BS * 2)), encoding="utf-8"
            )
            with (
                mock.patch.object(sp, "_registry_steam", return_value=[root]),
                mock.patch.object(sp, "_default_steam_dirs", return_value=[]),
            ):
                self.assertEqual(
                    sp.find_game("assettocorsa"),
                    extra / "steamapps/common/assettocorsa",
                )
                self.assertIsNone(sp.find_game("NoExiste"))

    def test_no_steam_means_none(self) -> None:
        with (
            mock.patch.object(sp, "_registry_steam", return_value=[]),
            mock.patch.object(sp, "_default_steam_dirs", return_value=[]),
        ):
            self.assertEqual(sp.steam_libraries(), [])
            self.assertIsNone(sp.find_game("BeamNG.drive"))


if __name__ == "__main__":
    unittest.main()
