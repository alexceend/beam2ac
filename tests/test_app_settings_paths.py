"""Rutas elegidas por el usuario: carpetas fuera de output/ se aceptan; las de AC/BeamNG, no."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.app import settings
from src.safe_paths import ProtectedPathError, ensure_safe_output_path


class UserPathsTest(unittest.TestCase):
    def test_projects_and_export_dirs_allowed_game_dirs_not(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ):
            os.environ.pop("BEAM2AC_OUTPUT_ROOTS", None)
            ok = Path(t) / "proy"
            game = (
                Path(t) / "steamapps" / "common" / "assettocorsa" / "content" / "cars"
            )
            game.mkdir(parents=True)
            settings.apply({"projects_dir": str(ok), "beamng_root": "B:/x"})
            self.assertEqual(settings.projects_dir(), ok)
            self.assertEqual(os.environ["BEAM2AC_BEAMNG_ROOT"], "B:/x")
            ensure_safe_output_path(ok / "a")
            with self.assertRaises(ProtectedPathError):
                ensure_safe_output_path(game / "beam2ac_x")
            settings.apply({})
            self.assertNotIn("BEAM2AC_OUTPUT_ROOTS", os.environ)

    def test_save_in_user_file(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ):
            f = Path(t) / "s.json"
            os.environ["APPDATA"] = t
            d = settings.save({"export_dir": t}, f)
            self.assertEqual(settings.load(f), d)
            settings.apply({})


if __name__ == "__main__":
    unittest.main()


class RobustnessTest(unittest.TestCase):
    def test_restores_user_env_and_rejects_protected(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ):
            os.environ["BEAM2AC_AC_ROOT"] = "C:/preset"
            settings.apply({"ac_root": "D:/ac"})
            settings.apply({})
            self.assertEqual(os.environ["BEAM2AC_AC_ROOT"], "C:/preset")
            game = Path(t) / "steamapps" / "common" / "assettocorsa" / "p"
            with self.assertRaises(ValueError):
                settings.save({"projects_dir": str(game)}, Path(t) / "s.json")
            self.assertFalse((Path(t) / "s.json").exists())
            bad = Path(t) / "bad.json"
            bad.write_text("null")
            self.assertEqual(settings.load(bad), {})
            settings.apply({})
