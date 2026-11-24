"""Instalación explícita en content/cars (src/project/install.py)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from src.project import install as I


class TestInstall(unittest.TestCase):
    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        (self.tmp / "ac" / "content" / "cars").mkdir(parents=True)
        self.car = self.tmp / "src" / "beam2ac_x"
        (self.car / "data").mkdir(parents=True)
        (self.car / "data" / "car.ini").write_text("new", encoding="utf-8")

    def tearDown(self) -> None:
        self._t.cleanup()

    def test_install_then_overwrite_keeps_backup(self) -> None:
        ac = self.tmp / "ac"
        r = I.install_to_ac(self.car, ac, "beam2ac_x", True)
        dest = ac / "content" / "cars" / "beam2ac_x"
        self.assertEqual((dest / "data" / "car.ini").read_text(), "new")
        self.assertIsNone(r["replaced_backup"])
        (self.car / "data" / "car.ini").write_text("newer", encoding="utf-8")
        r = I.install_to_ac(self.car, ac, "beam2ac_x", True)
        self.assertEqual((dest / "data" / "car.ini").read_text(), "newer")
        bk = Path(r["replaced_backup"])
        self.addCleanup(shutil.rmtree, bk, ignore_errors=True)  # la copia va a output/_papelera
        self.assertEqual((bk / "data" / "car.ini").read_text(), "new")

    def test_failed_copy_keeps_old_car_and_leaves_no_temp(self) -> None:
        from unittest import mock

        ac = self.tmp / "ac"
        I.install_to_ac(self.car, ac, "beam2ac_x", True)
        dest = ac / "content" / "cars" / "beam2ac_x"
        real = shutil.copytree

        def boom(src, dst, *a, **k):
            real(src, dst, *a, **k)  # copia hecha a medias o entera: igual, falla después
            raise OSError("disco lleno")

        with mock.patch.object(shutil, "copytree", boom), self.assertRaises(OSError):
            I.install_to_ac(self.car, ac, "beam2ac_x", True)
        self.assertEqual((dest / "data" / "car.ini").read_text(), "new")  # el coche anterior sigue ahí
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["beam2ac_x"])  # sin carpeta temporal

    def test_refusals(self) -> None:
        ac = self.tmp / "ac"
        for cid in ("ks_toyota", "../x", "beam2ac_"):
            with self.assertRaises(I.InstallError):
                I.install_to_ac(self.car, ac, cid, True)
        I.install_to_ac(self.car, ac, "beam2ac_x", False)
        with self.assertRaises(I.InstallError):  # existe y no se pidió sobrescribir
            I.install_to_ac(self.car, ac, "beam2ac_x", False)
        with self.assertRaises(I.InstallError):
            I.install_to_ac(self.car, None, "beam2ac_x", True)


if __name__ == "__main__":
    unittest.main()
