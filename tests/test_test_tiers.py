"""La organización por niveles no pierde ni oculta pruebas."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _collect(*args: str) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {ln.split(" ")[0] for ln in out.splitlines() if "::" in ln}


class TestTiers(unittest.TestCase):
    def test_complete_level_is_exactly_habitual_plus_ampliada_plus_not_required(self):
        habitual = _collect("tests")
        ampliada = _collect("tests", "-m", "ampliada")
        apartados = _collect("tests_not_required", "-m", "")
        completa = _collect("tests", "tests_not_required", "-m", "")
        self.assertTrue(ampliada and apartados)
        self.assertFalse(habitual & ampliada)
        self.assertFalse(habitual & apartados)
        self.assertEqual(habitual | ampliada | apartados, completa)

    def test_runner_lists_every_test_directory(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import run_tests
        finally:
            sys.path.pop(0)
        dirs = {p.name for p in ROOT.iterdir() if p.name.startswith("tests")}
        dirs = {d for d in dirs if (ROOT / d).is_dir() and d != "tests_snapshot"}
        self.assertEqual(set(run_tests.ALL_DIRS), dirs)
