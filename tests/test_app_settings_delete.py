"""Ajustes de usuario, borrado de proyectos, masa del piloto editable y audio de un zip (2026-11-17)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.app import settings
from src.app.workspace import Workspace, WorkspaceError
from src.audio.pipeline import extract_zip_audio


def make_project(root: Path, name: str, mass: float = 70.0) -> Path:
    d = root / name
    (d / "decisions").mkdir(parents=True)
    (d / "project.json").write_text(
        json.dumps(
            {
                "id": name,
                "source": {"kind": "dir", "path": "x", "pc": None},
                "driver_mass_kg": mass,
            }
        ),
        "utf-8",
    )
    return d


class TestProjects(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = Workspace(
            root=self.root, source_dirs=[], source_zips=[], pairing=None
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_delete_needs_confirmation_and_moves_to_trash(self) -> None:
        d = make_project(self.root, "a1")
        (d / "exports" / "001").mkdir(parents=True)
        (d / "exports" / "001" / "manifest.json").write_text(
            "{}", "utf-8"
        )  # paquete sellado
        self.ws.trash = self.root.parent / (self.root.name + "_papelera")
        self.addCleanup(shutil.rmtree, self.ws.trash, True)
        with self.assertRaises(WorkspaceError):
            self.ws.delete("a1", {})
        self.assertTrue(d.exists())
        res = self.ws.delete("a1", {"confirm": True})
        self.assertFalse(d.exists())
        self.assertTrue(
            (Path(res["moved_to"]) / "exports" / "001" / "manifest.json").is_file()
        )
        self.assertEqual(self.ws.projects(), [])

    def test_delete_refuses_bad_ids(self) -> None:
        for bad in ("..", "../x", "nope"):
            with self.assertRaises(WorkspaceError):
                self.ws.delete(bad, {"confirm": True})

    def test_delete_refuses_protected_history(self) -> None:
        d = make_project(self.root, "hist")
        protected = d / "exports" / "001"
        protected.mkdir(parents=True)
        with mock.patch("src.safe_paths.HISTORICAL_DIRS", (protected,)):
            with self.assertRaises(WorkspaceError):
                self.ws.delete("hist", {"confirm": True})
        self.assertTrue(d.exists())

    def test_driver_mass_is_editable_and_validated(self) -> None:
        make_project(self.root, "m1")
        self.assertEqual(
            self.ws.set_driver_mass("m1", {"driver_mass": 82.5})["driver_mass_kg"], 82.5
        )
        for bad in (-1, 999, "abc"):
            with self.assertRaises(WorkspaceError):
                self.ws.set_driver_mass("m1", {"driver_mass": bad})
        self.assertEqual(self.ws.summary("m1")["driver_mass_kg"], 82.5)


class TestSettings(unittest.TestCase):
    def test_save_load_apply_roundtrip(self) -> None:
        keep = {
            k: os.environ.get(k)
            for k in ("BEAM2AC_AC_ROOT", "BEAM2AC_BLENDER", "BEAM2AC_FMOD_STUDIO")
        }
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "s.json"
            data = settings.save(
                {
                    "fmod_path": '"C:/F/FMOD Studio 1.08.12"',
                    "driver_mass": "75",
                    "ac_root": "",
                },
                f,
            )
            self.assertEqual(
                data, {"fmod_path": "C:/F/FMOD Studio 1.08.12", "driver_mass": 75.0}
            )
            self.assertEqual(settings.load(f), data)
            self.assertEqual(
                os.environ["BEAM2AC_FMOD_STUDIO"], "C:/F/FMOD Studio 1.08.12"
            )
            settings.save({}, f)  # quitar el ajuste lo quita del entorno
            self.assertNotEqual(
                os.environ.get("BEAM2AC_FMOD_STUDIO"), "C:/F/FMOD Studio 1.08.12"
            )
            with self.assertRaises(WorkspaceError):
                settings.save({"driver_mass": "mucho"}, f)
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_steam_root_derives_ac_and_beamng(self) -> None:
        keep = {
            k: os.environ.get(k) for k in ("BEAM2AC_AC_ROOT", "BEAM2AC_BEAMNG_ROOT")
        }
        settings.apply({"steam_root": "S:/Lib"})
        self.assertTrue(os.environ["BEAM2AC_AC_ROOT"].endswith("assettocorsa"))
        self.assertTrue(os.environ["BEAM2AC_BEAMNG_ROOT"].endswith("BeamNG.drive"))
        settings.apply({})
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class TestZipAudio(unittest.TestCase):
    def test_extracts_only_art_sound_and_stays_inside(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            z = Path(t) / "m.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("art/sound/engine/h/a.wav", b"w")
                zf.writestr("art/sound/blends/h.sfxBlend2D.json", b"{}")
                zf.writestr("vehicles/x/a.jbeam", b"{}")
                zf.writestr("art/sound/../../evil.txt", b"x")
            root = extract_zip_audio(z, Path(t) / "out")
            self.assertTrue((root / "art/sound/engine/h/a.wav").is_file())
            self.assertFalse((root / "vehicles").exists())
            self.assertFalse((Path(t) / "evil.txt").exists())


if __name__ == "__main__":
    unittest.main()
