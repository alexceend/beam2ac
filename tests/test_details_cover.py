"""Detalles del coche y portada (src/project/details.py, cover.py)."""

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.project import details as D
from src.project.cover import CoverError, to_jpeg


def png(w: int, h: int) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


class TestDetails(unittest.TestCase):
    def test_clean_and_apply(self) -> None:
        d = D.clean(
            {
                "description": " hola ",
                "class": "Race",
                "year": "1995",
                "tags": "rwd; #race, ",
                "brand": "",
            }
        )
        self.assertEqual(
            d,
            {
                "description": "hola",
                "class": "race",
                "year": 1995,
                "tags": ["rwd", "#race"],
            },
        )
        ui = {"brand": "X", "tags": ["a"], "name": "n"}
        self.assertEqual(
            sorted(D.apply_to_ui(ui, d)), ["class", "description", "tags", "year"]
        )
        self.assertEqual(
            (ui["brand"], ui["tags"], ui["year"]), ("X", ["rwd", "#race"], 1995)
        )

    def test_invalid(self) -> None:
        for bad in (
            {"class": "boat"},
            {"year": "abc"},
            {"year": 3000},
            {"brand": "x" * 61},
            {"tags": ["a"] * 21},
        ):
            with self.assertRaises(D.DetailsError):
                D.clean(bad)

    def test_save_and_cover_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = SimpleNamespace(root=Path(tmp))
            D.save(proj, {"brand": "Eagle"}, png(2000, 800))
            self.assertEqual(D.load(proj), {"brand": "Eagle"})
            from PIL import Image

            im = Image.open(io.BytesIO(D.cover_bytes(proj)))
            self.assertEqual((im.size, im.format), ((1024, 576), "JPEG"))
            D.save(proj, {}, None, clear_cover=True)
            self.assertIsNone(D.cover_bytes(proj))
            with self.assertRaises(CoverError):
                D.save(proj, {}, b"no es una imagen")


class TestGenericHips(unittest.TestCase):
    def test_ibiza_relation_is_identity(self) -> None:
        from src.project.local_driver import IBIZA_HIPS, IBIZA_WHEEL, hips_from_wheel

        h = hips_from_wheel({"rim_centre": IBIZA_WHEEL[0], "rim_axis": IBIZA_WHEEL[1]})
        for a, b in zip(h, IBIZA_HIPS):
            self.assertAlmostEqual(a, b, places=3)


class TestRenderLock(unittest.TestCase):
    def test_concurrent_requests_render_once(self) -> None:
        import threading
        import time
        from unittest import mock

        from src.project import cover

        cover._CACHE.clear()
        with tempfile.TemporaryDirectory() as tmp:
            kn5 = Path(tmp) / "a.kn5"
            kn5.write_bytes(b"x")
            calls, running, peak = [], [0], [0]

            def fake(*a, **k):
                running[0] += 1
                peak[0] = max(peak[0], running[0])
                time.sleep(0.2)
                calls.append(1)
                running[0] -= 1
                return b"jpg"

            with mock.patch.object(cover, "_render", fake):
                ts = [
                    threading.Thread(target=cover.render_cover, args=(kn5,))
                    for _ in range(5)
                ]
                [t.start() for t in ts]
                [t.join() for t in ts]
        self.assertEqual(
            (len(calls), peak[0]), (1, 1)
        )  # una sola portada, el resto reutiliza el resultado


class TestCoverJpeg(unittest.TestCase):
    def test_crops_tall_and_wide(self) -> None:
        from PIL import Image

        for w, h in ((300, 900), (1920, 400)):
            self.assertEqual(
                Image.open(io.BytesIO(to_jpeg(png(w, h)))).size, (1024, 576)
            )


class TestRender(unittest.TestCase):
    def test_render_synthetic_car(self) -> None:
        """Una caja con dos materiales (carrocería y cristal) se dibuja como JPEG 1024×576."""
        from PIL import Image

        from src.ac.kn5_write import box_mesh, write_simple_kn5
        from src.project.cover import render_cover

        with tempfile.TemporaryDirectory() as tmp:
            kn5 = write_simple_kn5(
                Path(tmp) / "car.kn5",
                [
                    box_mesh("body", (-0.9, 0.3, -2.0), (0.9, 1.0, 2.0)),
                    box_mesh("glass", (-0.8, 1.0, -0.8), (0.8, 1.3, 0.8), material=1),
                ],
                [("paint", "ksPerPixel"), ("glass", "ksPerPixelAlpha")],
            )
            jpg = render_cover(kn5)
        im = Image.open(io.BytesIO(jpg))
        self.assertEqual((im.size, im.format), ((1024, 576), "JPEG"))
        self.assertGreater(
            len(set(im.convert("RGB").getdata())), 20
        )  # no es un fondo liso


class TestRenderErrors(unittest.TestCase):
    def test_corrupt_kn5_is_a_cover_error(self) -> None:
        from src.ac.kn5_write import box_mesh, write_simple_kn5
        from src.project.cover import CoverError, render_cover

        with tempfile.TemporaryDirectory() as tmp:
            good = write_simple_kn5(
                Path(tmp) / "ok.kn5",
                [box_mesh("body", (-1, 0, -2), (1, 1, 2))],
                [("paint", "ksPerPixel")],
            )
            raw = bytearray(good.read_bytes())
            cases = {"texto.kn5": b"esto no es un KN5", "vacio.kn5": b""}
            for i in range(0, 60, 3):  # cabecera y nombres con bytes alterados
                bad = bytearray(raw)
                bad[16 + i] ^= 0x92
                cases[f"flip{i}.kn5"] = bytes(bad)
            for name, data in cases.items():
                path = Path(tmp) / name
                path.write_bytes(data)
                try:
                    render_cover(path)
                except CoverError:
                    pass  # el único error permitido


if __name__ == "__main__":
    unittest.main()
