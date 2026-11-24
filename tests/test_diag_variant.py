"""Variantes diagnósticas del tipo trasero (src.ac.diag_variant)."""

import json
import tempfile
import unittest
from pathlib import Path

from src.ac.car_validate import read_ini
from src.ac.diag_variant import (
    AERO_MINIMAL,
    DRIVER3D_MINIMAL,
    insert_rear_type,
    main,
    missing_keys,
    prepare_aero_variant,
    prepare_driver3d_variant,
    prepare_variant,
)
from src.ac.ingame_package import UI_SUFFIX, PackageError, tree_hashes
from src.safe_paths import ProtectedPathError

SUSP = (
    "[BASIC]\nWHEELBASE=2.5\n\n[FRONT]\nTYPE=STRUT\nTRACK=1.4\n\n"
    "[REAR]\n; PENDIENTE TYPE\nTRACK=1.43\n\n; PENDIENTE:\n;  - REAR TYPE: x\n"
)
REF_AXLE = (
    "[REAR]\nTYPE=AXLE\nTRACK=1.35\nHUB_MASS=100\n"
    "[AXLE]\nLINK_COUNT=5\nJ0_CAR=1,2,3\nJ0_AXLE=1,2,3\n"
)


class TestInsert(unittest.TestCase):
    def test_inserts_only_type_after_header(self):
        out = insert_rear_type(SUSP, "AXLE")
        ini = read_ini_text(out)
        self.assertEqual(ini["REAR"], {"TYPE": "AXLE", "TRACK": "1.43"})
        self.assertEqual(ini["FRONT"]["TYPE"], "STRUT")
        added = [line for line in out.splitlines() if line not in SUSP.splitlines()]
        self.assertEqual(len(added), 1)
        self.assertNotIn("[", added[0].partition(";")[2])
        self.assertIn("DIAGNOSTICO", added[0])

    def test_rejects_existing_type_unknown_type_and_bad_headers(self):
        with self.assertRaises(PackageError):
            insert_rear_type(insert_rear_type(SUSP, "DWB"), "AXLE")
        with self.assertRaises(PackageError):
            insert_rear_type(SUSP, "ML")
        with self.assertRaises(PackageError):
            insert_rear_type("[FRONT]\nTYPE=STRUT\n", "DWB")


class TestMissing(unittest.TestCase):
    def test_compares_names_only(self):
        ours = {"REAR": {"TYPE": "AXLE", "TRACK": "1.43"}}
        ref = {
            "REAR": {"TYPE": "AXLE", "TRACK": "9", "HUB_MASS": "1"},
            "AXLE": {"A": "1"},
        }
        self.assertEqual(missing_keys(ours, ref), {"REAR": ["HUB_MASS"], "AXLE": ["A"]})


def read_ini_text(text: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ini"
        p.write_text(text, encoding="utf-8")
        return read_ini(p)


class TestPrepare(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        pkg = self.tmp / "ref" / "beam2ac_test_car"
        (pkg / "data").mkdir(parents=True)
        (pkg / "data" / "lods.ini").write_text("[LOD_0]\nFILE=a.kn5\n")
        (pkg / "data" / "suspensions.ini").write_text(SUSP, encoding="utf-8")
        self.pkg = pkg
        self.ref = self.tmp / "ref_susp.ini"
        self.ref.write_text(REF_AXLE)
        self.ac = self.tmp / "ac"
        (self.ac / "content" / "cars" / "abarth500").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_variant_copy_reference_untouched(self):
        before = tree_hashes(self.pkg)
        m = prepare_variant(
            self.pkg, "AXLE", self.ref, self.tmp / "out", "beam2ac_diag_axle", self.ac
        )
        self.assertEqual(tree_hashes(self.pkg), before)
        self.assertTrue(m["id_check"]["id_free"])
        self.assertFalse(m["installed"])
        d = m["diagnostic"]
        self.assertEqual(d["missing_type_specific"]["REAR"], ["HUB_MASS"])
        self.assertEqual(
            d["missing_type_specific"]["AXLE"], ["J0_AXLE", "J0_CAR", "LINK_COUNT"]
        )
        self.assertEqual(d["invented_values"], [])
        saved = json.loads((self.tmp / "out" / "manifest.json").read_text("utf-8"))
        self.assertEqual(saved["files"], tree_hashes(Path(m["package_dir"])))
        self.assertEqual(
            sorted(p.name for p in (self.ac / "content" / "cars").iterdir()),
            ["abarth500"],
        )

    def test_existing_id_is_refused(self):
        (self.ac / "content" / "cars" / "beam2ac_diag_axle").mkdir()
        with self.assertRaises(PackageError):
            prepare_variant(
                self.pkg,
                "AXLE",
                self.ref,
                self.tmp / "out",
                "beam2ac_diag_axle",
                self.ac,
            )

    def test_aero_minimal_only_structure(self):
        before = tree_hashes(self.pkg)
        m = prepare_variant(
            self.pkg,
            "DWB",
            self.ref,
            self.tmp / "out",
            "beam2ac_diag_aero_dwb",
            self.ac,
            aero_minimal=True,
        )
        self.assertEqual(tree_hashes(self.pkg), before)
        self.assertFalse((self.pkg / "data" / "aero.ini").exists())
        aero = Path(m["package_dir"]) / "data" / "aero.ini"
        self.assertEqual(aero.read_text(encoding="utf-8"), AERO_MINIMAL)
        ini = read_ini(aero)
        self.assertEqual(ini, {"WING_0": {"NAME": "BODY"}})  # sin valores físicos
        self.assertIn("data/aero.ini", m["files"])
        self.assertTrue(m["diagnostic"]["aero_minimal"])
        self.assertTrue(any("aero.ini" in c for c in m["changes_vs_source"]))

    def test_aero_minimal_refuses_existing_aero(self):
        (self.pkg / "data" / "aero.ini").write_text("[WING_0]\nNAME=X\n")
        with self.assertRaises(PackageError):
            prepare_variant(
                self.pkg,
                "AXLE",
                self.ref,
                self.tmp / "out",
                "beam2ac_diag_aero_axle",
                self.ac,
                aero_minimal=True,
            )
        self.assertFalse((self.tmp / "out").exists())


if __name__ == "__main__":
    unittest.main()


class TestDriver3dVariant(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        base = self.tmp / "base" / "beam2ac_diag_aero_axle"
        (base / "data").mkdir(parents=True)
        (base / "ui").mkdir()
        (base / "data" / "lods.ini").write_text("[LOD_0]\nFILE=a.kn5\n")
        (base / "data" / "aero.ini").write_text("[WING_0]\nNAME=BODY\n")
        # ui_car.json ya con el sufijo de prueba, como en las variantes reales.
        (base / "ui" / "ui_car.json").write_text(
            json.dumps({"name": "Car" + UI_SUFFIX}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.base = base
        self.ac = self.tmp / "ac"
        (self.ac / "content" / "cars" / "ks_x").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_driver3d_added(self):
        before = tree_hashes(self.base)
        m = prepare_driver3d_variant(
            self.base, self.tmp / "out", "beam2ac_diag_driver", self.ac
        )
        self.assertEqual(tree_hashes(self.base), before)
        self.assertEqual(m["diagnostic"]["diff_vs_base"], ["data/driver3d.ini"])
        pkg = Path(m["package_dir"])
        self.assertEqual(
            (pkg / "data" / "driver3d.ini").read_text(encoding="utf-8"),
            DRIVER3D_MINIMAL,
        )
        self.assertEqual(
            read_ini(pkg / "data" / "driver3d.ini"),
            {"MODEL": {"NAME": "driver", "POSITION": "0,0,0"}},
        )
        self.assertFalse((pkg / "driver_base_pos.knh").exists())
        self.assertTrue(m["id_check"]["id_free"])
        self.assertEqual(
            [p.name for p in (self.ac / "content" / "cars").iterdir()], ["ks_x"]
        )

    def test_refuses_existing_driver3d(self):
        (self.base / "data" / "driver3d.ini").write_text("[MODEL]\nNAME=x\n")
        with self.assertRaises(PackageError):
            prepare_driver3d_variant(
                self.base, self.tmp / "out", "beam2ac_diag_driver", self.ac
            )


# suspensions.ini como el del ensamblado editado a mano: [REAR] ya tiene TYPE,
# con CRLF (los bytes deben quedar idénticos en la copia).
SUSP_WITH_TYPE = (
    "[FRONT]\r\nTYPE=STRUT\r\nTRACK=1.475\r\n\r\n"
    "[REAR]\r\nTYPE=DWB\t\t; brazo arrastrado\r\nTRACK=1.43\r\n"
).encode("utf-8")


class TestAeroVariant(unittest.TestCase):
    """prepare_aero_variant: solo añade aero.ini; no toca [REAR] TYPE."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        base = self.tmp / "pkg" / "beam2ac_test2_car"
        (base / "data").mkdir(parents=True)
        (base / "ui").mkdir()
        (base / "data" / "lods.ini").write_text("[LOD_0]\nFILE=a.kn5\n")
        (base / "data" / "suspensions.ini").write_bytes(SUSP_WITH_TYPE)
        (base / "ui" / "ui_car.json").write_text(
            json.dumps({"name": "Car" + UI_SUFFIX}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.base = base
        self.ac = self.tmp / "ac"
        (self.ac / "content" / "cars" / "ks_x").mkdir(parents=True)
        self.out = self.tmp / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_aero_added_and_rear_type_preserved(self):
        before = tree_hashes(self.base)
        m = prepare_aero_variant(self.base, self.out, "beam2ac_test2aero_car", self.ac)
        self.assertEqual(tree_hashes(self.base), before)  # base intacta
        pkg = Path(m["package_dir"])
        self.assertEqual(m["diagnostic"]["diff_vs_base"], ["data/aero.ini"])
        self.assertTrue(m["diagnostic"]["rear_type_untouched"])
        self.assertEqual(
            (pkg / "data" / "aero.ini").read_text(encoding="utf-8"), AERO_MINIMAL
        )
        self.assertEqual(
            read_ini(pkg / "data" / "aero.ini"), {"WING_0": {"NAME": "BODY"}}
        )
        # suspensions.ini idéntico byte a byte: TYPE=DWB conservado, sin TYPE añadido.
        copy = (pkg / "data" / "suspensions.ini").read_bytes()
        self.assertEqual(copy, SUSP_WITH_TYPE)
        self.assertEqual(copy.count(b"TYPE=DWB"), 1)
        self.assertNotIn(b"DIAGNOSTICO", copy)
        saved = json.loads((self.out / "manifest.json").read_text("utf-8"))
        self.assertEqual(saved["files"], tree_hashes(pkg))
        self.assertTrue(m["id_check"]["id_free"])
        self.assertEqual(
            [p.name for p in (self.ac / "content" / "cars").iterdir()], ["ks_x"]
        )

    def test_refuses_existing_aero_and_writes_nothing(self):
        (self.base / "data" / "aero.ini").write_text("[WING_0]\nNAME=X\n")
        before = tree_hashes(self.base)
        with self.assertRaisesRegex(PackageError, "aero.ini"):
            prepare_aero_variant(self.base, self.out, "beam2ac_test2aero_car", self.ac)
        self.assertFalse(self.out.exists())
        self.assertEqual(tree_hashes(self.base), before)

    def test_refuses_existing_destination_without_touching_it(self):
        dst = self.out / "beam2ac_test2aero_car"
        dst.mkdir(parents=True)
        (dst / "evidencia.txt").write_text("no tocar")
        with self.assertRaises(PackageError):
            prepare_aero_variant(self.base, self.out, "beam2ac_test2aero_car", self.ac)
        self.assertEqual(sorted(p.name for p in dst.iterdir()), ["evidencia.txt"])
        self.assertFalse((self.out / "manifest.json").exists())

    def test_refuses_existing_manifest_without_touching_it(self):
        self.out.mkdir()
        (self.out / "manifest.json").write_text('{"previo": true}')
        # Desde la fase A la carpeta con manifest.json ya está sellada
        # (ProtectedPathError de src.safe_paths); antes lo detectaba diag_variant
        # (PackageError).
        with self.assertRaises((PackageError, ProtectedPathError)):
            prepare_aero_variant(self.base, self.out, "beam2ac_test2aero_car", self.ac)
        self.assertEqual((self.out / "manifest.json").read_text(), '{"previo": true}')
        self.assertEqual([p.name for p in self.out.iterdir()], ["manifest.json"])

    def test_refuses_base_that_is_not_a_test_package(self):
        (self.base / "ui" / "ui_car.json").write_text(
            json.dumps({"name": "Car"}, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(PackageError, "paquete de prueba"):
            prepare_aero_variant(self.base, self.out, "beam2ac_test2aero_car", self.ac)
        self.assertFalse(self.out.exists())

    def test_existing_installed_id_is_refused_without_output(self):
        (self.ac / "content" / "cars" / "beam2ac_test2aero_car").mkdir()
        with self.assertRaises(PackageError):
            prepare_aero_variant(self.base, self.out, "beam2ac_test2aero_car", self.ac)
        self.assertFalse(self.out.exists())

    def test_cli_subcommand(self):
        rc = main(
            [
                "aero",
                str(self.base),
                "--test-id",
                "beam2ac_test2aero_car",
                "--out",
                str(self.out),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(
            (self.out / "beam2ac_test2aero_car" / "data" / "aero.ini").is_file()
        )
        self.assertEqual(
            (
                self.out / "beam2ac_test2aero_car" / "data" / "suspensions.ini"
            ).read_bytes(),
            SUSP_WITH_TYPE,
        )


class TestRearTypeVariantPreconditions(unittest.TestCase):
    """prepare_variant ya no deja copias parciales cuando falla la validación."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        pkg = self.tmp / "ref" / "beam2ac_test_car"
        (pkg / "data").mkdir(parents=True)
        (pkg / "data" / "lods.ini").write_text("[LOD_0]\nFILE=a.kn5\n")
        (pkg / "data" / "suspensions.ini").write_bytes(SUSP_WITH_TYPE)
        self.pkg = pkg
        self.ref = self.tmp / "ref_susp.ini"
        self.ref.write_text(REF_AXLE)
        self.out = self.tmp / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def test_existing_rear_type_fails_before_copy(self):
        before = tree_hashes(self.pkg)
        for aero in (False, True):
            with self.subTest(aero_minimal=aero):
                with self.assertRaisesRegex(PackageError, "REAR ya tiene TYPE"):
                    prepare_variant(
                        self.pkg,
                        "DWB",
                        self.ref,
                        self.out,
                        "beam2ac_diag_dwb",
                        aero_minimal=aero,
                    )
                self.assertFalse(self.out.exists())  # ni copia ni manifiesto
        self.assertEqual(tree_hashes(self.pkg), before)

    def test_existing_variant_is_not_deleted_on_failure(self):
        dst = self.out / "beam2ac_diag_dwb"
        dst.mkdir(parents=True)
        (dst / "evidencia.txt").write_text("no tocar")
        (self.out / "manifest.json").write_text('{"previo": true}')
        with self.assertRaises(PackageError):
            prepare_variant(self.pkg, "DWB", self.ref, self.out, "beam2ac_diag_dwb")
        self.assertEqual(sorted(p.name for p in dst.iterdir()), ["evidencia.txt"])
        self.assertEqual((self.out / "manifest.json").read_text(), '{"previo": true}')

    def test_missing_reference_fails_before_copy(self):
        (self.pkg / "data" / "suspensions.ini").write_text(SUSP, encoding="utf-8")
        with self.assertRaisesRegex(PackageError, "referencia"):
            prepare_variant(
                self.pkg,
                "DWB",
                self.tmp / "no_existe.ini",
                self.out,
                "beam2ac_diag_dwb",
            )
        self.assertFalse(self.out.exists())
