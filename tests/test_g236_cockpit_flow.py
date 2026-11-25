"""G2.36: guardas de cockpit_save (otro vehículo / versión obsoleta) y persistencia versionada."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.app.workspace import Workspace, WorkspaceError
from src.project import cockpit_edits as CE

KEY = "0123456789abcdef_x.pc"


class CockpitSaveGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = Workspace(self.tmp / "p", [], [], None)
        for p in (
            mock.patch.object(CE, "ROOT", self.tmp / "edits"),
            mock.patch.object(CE, "project_key", lambda proj: KEY),
            mock.patch.object(Workspace, "_project", lambda self, pid: object()),
            mock.patch(
                "src.project.cockpit_edits.ensure_safe_output_path", lambda p: p
            ),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_roundtrip_and_restore_keep_history(self):
        r = self.ws.cockpit_save(
            "a", {"key": KEY, "base_version": 0, "items": {"hips": [0, 1, 2]}}
        )
        self.assertEqual(r["version"], 1)
        self.assertEqual(CE.current(KEY)["items"], {"hips": [0.0, 1.0, 2.0]})
        r = self.ws.cockpit_save("a", {"key": KEY, "base_version": 1, "reset": True})
        self.assertEqual((r["version"], r["items"]), (2, {}))
        self.assertEqual(len(CE.versions(KEY)), 2)  # solo añadir

    def test_other_vehicle_rejected(self):
        with self.assertRaises(WorkspaceError):
            self.ws.cockpit_save("a", {"key": "ffffffffffffffff_y.pc", "items": {}})
        self.assertEqual(CE.current(KEY)["version"], 0)

    def test_stale_version_rejected(self):
        self.ws.cockpit_save(
            "a", {"key": KEY, "base_version": 0, "items": {"bonnet": [0, 1, 1]}}
        )
        with self.assertRaises(WorkspaceError):
            self.ws.cockpit_save(
                "a", {"key": KEY, "base_version": 0, "items": {"bonnet": [0, 2, 1]}}
            )
        self.assertEqual(CE.current(KEY)["items"]["bonnet"], [0.0, 1.0, 1.0])

    def test_invalid_values_do_not_corrupt(self):
        self.ws.cockpit_save("a", {"key": KEY, "items": {"hips": [0, 1, 2]}})
        with self.assertRaises(WorkspaceError):
            self.ws.cockpit_save(
                "a", {"key": KEY, "items": {"hips": [0, 1, float("nan")]}}
            )
        self.assertEqual(CE.current(KEY)["version"], 1)


if __name__ == "__main__":
    unittest.main()
