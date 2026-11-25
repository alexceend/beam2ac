"""Defecto sin volante: solo se monta con la casilla «Volante» (steering_wheel.json activado)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.app.workspace import _write_wheel
from src.project.model import WHEEL_FILE, tool_inputs, wheel_override_of


class TestWheelDefault(unittest.TestCase):
    def test_default_is_no_wheel_and_changes_model_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = SimpleNamespace(root=Path(tmp))
            self.assertIsNone(wheel_override_of(p))
            off = tool_inputs(p)
            _write_wheel(Path(tmp) / WHEEL_FILE, None)
            self.assertEqual(wheel_override_of(p), {"enabled": True})
            self.assertNotEqual(tool_inputs(p), off)

    def test_disabled_file_and_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p, f = SimpleNamespace(root=Path(tmp)), Path(tmp) / WHEEL_FILE
            f.write_text(
                json.dumps({"enabled": False, "rim_material": "x"}), encoding="utf-8"
            )
            self.assertIsNone(wheel_override_of(p))
            f.write_text(
                json.dumps({"rim_material": "x"}), encoding="utf-8"
            )  # formato anterior
            self.assertEqual(wheel_override_of(p)["rim_material"], "x")

    def test_enable_keeps_chosen_rim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / WHEEL_FILE
            _write_wheel(f, "rim_mat")
            _write_wheel(f, None, keep=True)
            self.assertEqual(
                json.loads(f.read_text("utf-8")),
                {"enabled": True, "rim_material": "rim_mat"},
            )
            _write_wheel(f, None)  # «clear»: vuelve a detección automática
            self.assertEqual(json.loads(f.read_text("utf-8")), {"enabled": True})


if __name__ == "__main__":
    unittest.main()


class TestRequireAcRoot(unittest.TestCase):
    """La ruta de AC es obligatoria (de ella sale el piloto): sin content/driver no se exporta."""

    def test_requires_content_driver(self) -> None:
        import tempfile
        from pathlib import Path

        from src.app.workspace import WorkspaceError, _require_ac_root

        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(WorkspaceError, "ruta de Assetto Corsa"):
                _require_ac_root(t)
            (Path(t) / "content" / "driver").mkdir(parents=True)
            self.assertEqual(_require_ac_root(t), t)
