"""Decales (opacityMap): la máscara pasa a ser el alfa de una textura RGBA con alphaBlendMode 1."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_materials import diffuse_sources

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def _stage(**kw):
    return {"Stages": [{"diffuseColor": [1.0, 0.5, 0.0, 1], **kw}]}


@unittest.skipUnless(Image, "necesita Pillow")
class TestDecals(unittest.TestCase):
    def _run(self, materials):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            mask = Image.new("L", (8, 8), 0)
            mask.putpixel((1, 1), 255)
            mask.save(d / "mask.png")
            (d / "broken.png").write_bytes(b"no es una imagen")
            (d / "m.materials.json").write_text(json.dumps(materials), encoding="utf-8")
            return diffuse_sources(list(materials), d / "m.materials.json", d, None)

    def test_mask_becomes_alpha_and_blends(self):
        (s,) = self._run({"decal": _stage(opacityMap="mask.png")})
        self.assertEqual((s.kind, s.blend, s.alpha_tested), ("decal", 1, 0))
        img = Image.open(io.BytesIO(s.data)).convert("RGBA")
        self.assertEqual(img.size, (8, 8))
        alphas = {img.getpixel((x, y))[3] for x in range(8) for y in range(8)}
        self.assertEqual(alphas, {0, 255})
        for got, want in zip(img.getpixel((1, 1))[:3], (255, 128, 0)):
            self.assertAlmostEqual(got, want, delta=4)  # DXT5 pierde unos bits
        self.assertGreater(len(s.data), 128 + 16)  # DXT5 con mipmaps

    def test_second_uv_set_stamp_is_not_a_decal(self):
        (s,) = self._run(
            {"paintstamp": _stage(opacityMap="mask.png", opacityMapUseUV=1)}
        )
        self.assertEqual(s.kind, "diffuseColor")
        self.assertEqual(s.blend, 0)

    def test_unreadable_mask_keeps_the_material_and_says_so(self):
        a, b = self._run(
            {"x": _stage(opacityMap="broken.png"), "y": _stage(opacityMap="falta.png")}
        )
        for s in (a, b):
            self.assertEqual((s.kind, s.blend), ("diffuseColor", 0))
            self.assertIn("opacityMap", s.note)


if __name__ == "__main__":
    unittest.main()
