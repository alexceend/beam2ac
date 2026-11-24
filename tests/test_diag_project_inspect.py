"""El diagnóstico de un proyecto usa SUS decisiones y se etiqueta con el proyecto."""

import unittest
from pathlib import Path

from src.app.workspace import PROJECTS_ROOT, Workspace, WorkspaceError

B1 = PROJECTS_ROOT / "ibiza_35b84_b1"


class TestProjectInspect(unittest.TestCase):
    def test_unknown_project(self) -> None:
        with self.assertRaises(WorkspaceError):
            Workspace().inspect("x", None, None, "no_existe_xyz")

    @unittest.skipUnless(B1.is_dir() and Path("sample_beamng").is_dir(), "sin proyecto")
    def test_decisions_of_project_apply(self) -> None:
        ws = Workspace()
        r = ws.inspect("", None, None, "ibiza_35b84_b1")
        self.assertEqual(r["project"], "ibiza_35b84_b1")
        # FRONT_SHARE lo resuelve una decisión del proyecto: sin ella bloquea
        self.assertNotIn(
            "ac:brakes.ini/DATA/FRONT_SHARE", [b["target"] for b in r["blockers"]]
        )


if __name__ == "__main__":
    unittest.main()
