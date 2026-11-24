"""Opción «palanca de cambios»: drivetrain.ini [GEARBOX] SUPPORTS_SHIFTER=0/1 (solo si se pide)."""

import unittest

from src.app.workspace import WorkspaceError, _parse_shifter
from src.project.export import PackageFile, _apply_supports_shifter


def _run(text, options):
    files = [PackageFile("data/drivetrain.ini", text.encode(), {})]
    texts = {"drivetrain.ini": text}
    issues: list = []
    _apply_supports_shifter(options, files, texts, issues)
    return texts["drivetrain.ini"], issues


class TestSupportsShifter(unittest.TestCase):
    def test_not_requested_untouched(self):
        t = "[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n"
        self.assertEqual(_run(t, {})[0], t)

    def test_adds_and_replaces(self):
        t = "[GEARS]\nCOUNT=5\n\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n"
        out = _run(t, {"supports_shifter": True})[0]
        self.assertIn("[GEARBOX]\nSUPPORTS_SHIFTER=1\n", out)
        out = _run(out, {"supports_shifter": False})[0]
        self.assertEqual(out.count("SUPPORTS_SHIFTER"), 1)
        self.assertIn("SUPPORTS_SHIFTER=0", out)

    def test_creates_section(self):
        out = _run("[GEARS]\nCOUNT=5\n", {"supports_shifter": True})[0]
        self.assertTrue(out.endswith("[GEARBOX]\nSUPPORTS_SHIFTER=1\n"))

    def test_parse(self):
        self.assertEqual(
            _parse_shifter({"shifter": False}), {"supports_shifter": False}
        )
        self.assertEqual(_parse_shifter({}), {})
        with self.assertRaises(WorkspaceError):
            _parse_shifter({"shifter": "1"})


if __name__ == "__main__":
    unittest.main()
