"""Paquete aislado para la prueba en el juego (src.ac.ingame_package)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.ac.ingame_package import (
    UI_SUFFIX,
    PackageError,
    prepare_test_package,
    tree_hashes,
)
from src.safe_paths import PROJECT_ROOT, ProtectedPathError


def _car(root: Path, car_id: str = "mycar") -> Path:
    car = root / "src_cars" / car_id
    (car / "data").mkdir(parents=True)
    (car / "ui").mkdir()
    (car / "data" / "lods.ini").write_text("[LOD_0]\nFILE=mycar.kn5\n")
    (car / "mycar.kn5").write_bytes(b"sc6969")
    (car / "ui" / "ui_car.json").write_text(json.dumps({"name": "My Car"}))
    return car


def _install(root: Path, ids: list[str]) -> Path:
    ac = root / "ac"
    for i in ids:
        (ac / "content" / "cars" / i).mkdir(parents=True)
    return ac


class TestPackage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.car = _car(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_copy_with_unique_id_and_source_intact(self) -> None:
        before = tree_hashes(self.car)
        ac = _install(self.root, ["abarth500", "mycar"])
        m = prepare_test_package(self.car, self.root / "out", ac_root=ac)
        self.assertEqual(m["test_id"], "beam2ac_test_mycar")
        self.assertTrue(m["id_check"]["id_free"])
        self.assertEqual(m["id_check"]["similar_ids"], ["mycar"])
        self.assertFalse(m["installed"])
        self.assertEqual(tree_hashes(self.car), before)
        dst = self.root / "out" / "beam2ac_test_mycar"
        self.assertEqual((dst / "mycar.kn5").read_bytes(), b"sc6969")
        ui = json.loads((dst / "ui" / "ui_car.json").read_text(encoding="utf-8"))
        self.assertEqual(ui["name"], "My Car" + UI_SUFFIX)
        self.assertIn("sfx/beam2ac_test_mycar.bank", m["known_gaps"][0])
        self.assertTrue((self.root / "out" / "manifest.json").is_file())
        # Nada escrito en la "instalación".
        self.assertEqual(
            sorted(p.name for p in (ac / "content" / "cars").iterdir()),
            ["abarth500", "mycar"],
        )

    def test_rerun_refused_without_touching_first_copy(self) -> None:
        m = prepare_test_package(self.car, self.root / "out")
        dst = Path(m["package_dir"])
        before = tree_hashes(self.root / "out")
        with self.assertRaises(PackageError):  # misma carpeta: nunca se borra
            prepare_test_package(self.car, self.root / "out")
        self.assertEqual(tree_hashes(self.root / "out"), before)
        ui = json.loads((dst / "ui" / "ui_car.json").read_text(encoding="utf-8"))
        self.assertEqual(ui["name"].count(UI_SUFFIX), 1)

    def test_sealed_output_from_another_run_refused(self) -> None:
        out = self.root / "sealed_out"
        out.mkdir()
        (out / "manifest.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ProtectedPathError):
            prepare_test_package(self.car, out, test_id="beam2ac_test_other")
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["manifest.json"])

    def test_rejects_existing_id(self) -> None:
        ac = _install(self.root, ["beam2ac_test_mycar"])
        with self.assertRaises(PackageError):
            prepare_test_package(self.car, self.root / "out", ac_root=ac)

    def test_rejects_output_inside_install(self) -> None:
        ac = _install(self.root, ["x"])
        with self.assertRaises(PackageError):
            prepare_test_package(self.car, ac / "content" / "cars", ac_root=ac)

    def test_rejects_bad_id_and_overlap(self) -> None:
        with self.assertRaises(PackageError):
            prepare_test_package(self.car, self.root / "out", test_id="Bad-Id")
        with self.assertRaises(PackageError):
            prepare_test_package(self.car, self.car, test_id="inner")

    def test_rejects_non_car_and_samples(self) -> None:
        with self.assertRaises(PackageError):
            prepare_test_package(self.root, self.root / "out")
        with self.assertRaises(ProtectedPathError):
            prepare_test_package(self.car, PROJECT_ROOT / "sample_assetto" / "x")


if __name__ == "__main__":
    unittest.main()
