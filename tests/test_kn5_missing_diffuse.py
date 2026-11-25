"""Un diffuseMap virtual ``_lerpto_`` de BeamNG ausente no tira las demás texturas."""

import json
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_materials import diffuse_sources


class TestMissingDiffuse(unittest.TestCase):
    def test_lerpto_100_and_plain_missing(self) -> None:
        mats = {
            "a": {
                "name": "a",
                "Stages": [{"diffuseMap": "x_lerpto_0xFFFF8000_100.dds"}],
            },
            "b": {
                "name": "b",
                "Stages": [
                    {
                        "diffuseMap": "x_lerpto_0xFF000000_50.dds",
                        "diffuseColor": [0, 1, 0, 1],
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m.materials.json"
            f.write_text(json.dumps(mats), encoding="utf-8")
            got = {
                s.material: s for s in diffuse_sources(["a", "b"], f, Path(tmp), None)
            }
        self.assertEqual(got["a"].texture_name, "beam2ac_col_ff8000.dds")
        self.assertEqual(got["b"].texture_name, "beam2ac_col_00ff00.dds")
        self.assertTrue(got["a"].data.startswith(b"DDS "))


if __name__ == "__main__":
    unittest.main()
