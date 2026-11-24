"""Importador de la base física (tools/catalog/import_physics_base.py): criterio de
«reproducido por regla». Regresión de FRONT_SHARE (fase B2/B3)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

TOOL = Path(__file__).resolve().parents[1] / "tools/catalog/import_physics_base.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("import_physics_base", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def res(status: str = "inferred", value=0.8570005029149358, approval=False):
    return SimpleNamespace(
        source="rule", value=value, status=status, requires_approval=approval
    )


class TestRuleReproduces(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = load_tool()

    def test_usable_rule_reproduces(self) -> None:
        self.assertTrue(self.tool.rule_reproduces(res(), "0.8570"))

    def test_ambiguous_rule_does_not_replace_the_approved_value(self) -> None:
        # FRONT_SHARE: misma cifra, pero la regla tiene una cuestión abierta
        self.assertFalse(self.tool.rule_reproduces(res("ambiguous"), "0.8570"))

    def test_proposal_or_other_value_does_not_reproduce(self) -> None:
        self.assertFalse(self.tool.rule_reproduces(res(approval=True), "0.8570"))
        self.assertFalse(self.tool.rule_reproduces(res(), "0.8180"))
        self.assertFalse(self.tool.rule_reproduces(res(value=None), "0.8570"))


if __name__ == "__main__":
    unittest.main()
