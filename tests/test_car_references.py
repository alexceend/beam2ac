"""Pruebas de las comprobaciones de referencias de src.ac.car_validate (paquetes
sintéticos).
"""

import struct
import tempfile
import unittest
from pathlib import Path

from src.ac.car_validate import (
    changed_protected,
    duplicate_keys,
    knh_bones,
    validate_references,
    validate_structure,
)
from src.ac.kn5_materials import Kn5Texture, build_kn5, solid_dds, split_kn5
from src.ac.kn5_write import box_mesh, write_simple_kn5


def _knh(nodes):
    """knh con una cadena lineal de nodos (nombre, matriz identidad, 1 hijo salvo el
    último).
    """
    out = b""
    for i, name in enumerate(nodes):
        b = name.encode("latin1")
        out += (
            struct.pack("<i", len(b))
            + b
            + struct.pack("<16f", *[float(i == j) for i in range(4) for j in range(4)])
        )
        out += struct.pack("<i", 0 if i == len(nodes) - 1 else 1)
    return out


class TestReferences(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.car = Path(self._tmp.name) / "car_x"
        (self.car / "data").mkdir(parents=True)
        (self.car / "skins" / "s0").mkdir(parents=True)
        (self.car / "data" / "lods.ini").write_text(
            "[LOD_0]\nFILE=car_x.kn5\nIN=0\nOUT=1500\n", encoding="utf-8"
        )
        write_simple_kn5(
            self.car / "car_x.kn5",
            [
                box_mesh("GEO_a", (0, 0, 0), (1, 1, 1)),
                box_mesh("GEO_b", (2, 0, 0), (3, 1, 1), material=1),
            ],
            [("mat_a", "ksPerPixel"), ("mat_b", "ksPerPixel")],
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _set_slot(self, tex, embed):
        p = self.car / "car_x.kn5"
        parts = split_kn5(p.read_bytes())
        parts.materials[0].slots = [("txDiffuse", 0, tex)]
        if embed:
            parts.textures.append(Kn5Texture(tex, solid_dds((1, 2, 3))))
        p.write_bytes(build_kn5(parts))

    def _errors(self, **kw):
        return validate_references(self.car, **kw).errors

    def test_clean_package_passes(self):
        self.assertEqual(self._errors(), [])

    def test_missing_texture_detected_and_skin_or_embedded_resolves_it(self):
        self._set_slot("paint.dds", embed=False)
        self.assertTrue(any("paint.dds" in e for e in self._errors()))
        (self.car / "skins" / "s0" / "paint.dds").write_bytes(solid_dds((1, 1, 1)))
        self.assertEqual(self._errors(), [])

    def test_embedded_texture_passes(self):
        self._set_slot("paint.dds", embed=True)
        self.assertEqual(self._errors(), [])

    def test_ext_config_unknown_material_detected_wildcards_not_errors(self):
        ext = self.car / "extension"
        ext.mkdir()
        (ext / "ext_config.ini").write_text(
            "[SHADER_REPLACEMENT_0]\nMATERIALS = mat_a, mat_zz\nCULL_MODE = NONE\n",
            encoding="utf-8",
        )
        self.assertTrue(any("mat_zz" in e for e in self._errors()))
        (ext / "ext_config.ini").write_text(
            "[SHADER_REPLACEMENT_0]\n"
            "MATERIALS = mat_a, '{ shader:ksWindscreen? }'\n"
            "CULL_MODE = NONE\n",
            encoding="utf-8",
        )
        self.assertEqual(self._errors(), [])

    def test_mirror_name_must_be_unique_existing_mesh(self):
        (self.car / "data" / "mirrors.ini").write_text(
            "[MIRROR_0]\nNAME=GEO_a\n", encoding="utf-8"
        )
        self.assertEqual(self._errors(), [])
        (self.car / "data" / "mirrors.ini").write_text(
            "[MIRROR_0]\nNAME=GEO_missing\n", encoding="utf-8"
        )
        self.assertTrue(any("MIRROR_0" in e for e in self._errors()))

    def test_knh_truncated_and_bones_against_driver_model(self):
        (self.car / "data" / "driver3d.ini").write_text(
            "[MODEL]\nNAME=drv\nPOSITION=0,0,0\n", encoding="utf-8"
        )
        good = _knh(["SCENE_ROOT", "DRIVER:DRIVER", "DRIVER:RIG_Hips"])
        (self.car / "driver_base_pos.knh").write_bytes(good[:-10])
        self.assertTrue(any("knh" in e for e in self._errors()))
        (self.car / "driver_base_pos.knh").write_bytes(good)
        self.assertEqual(
            knh_bones(self.car / "driver_base_pos.knh"),
            ["SCENE_ROOT", "DRIVER:DRIVER", "DRIVER:RIG_Hips"],
        )
        drivers = Path(self._tmp.name) / "driver"
        drivers.mkdir()
        write_simple_kn5(
            drivers / "drv.kn5",
            [box_mesh("DRIVER:RIG_Hips", (0, 0, 0), (1, 1, 1))],
            [("m", "ksPerPixel")],
        )
        errs = self._errors(driver_dir=drivers)
        self.assertTrue(
            any("DRIVER:DRIVER" in e for e in errs), errs
        )  # el modelo sintético no tiene DRIVER:DRIVER
        write_simple_kn5(
            drivers / "drv.kn5",
            [box_mesh("DRIVER:RIG_Hips", (0, 0, 0), (1, 1, 1))],
            [("m", "ksPerPixel")],
            root="DRIVER:DRIVER",
        )
        self.assertEqual(self._errors(driver_dir=drivers), [])

    def test_sfx_bank_must_match_folder(self):
        sfx = self.car / "sfx"
        sfx.mkdir()
        (sfx / "other.bank").write_bytes(b"x")
        (sfx / "GUIDs.txt").write_text("{0} bank:/other\n", encoding="utf-8")
        self.assertEqual(sum("sfx" in e for e in self._errors()), 2)
        (sfx / "car_x.bank").write_bytes(b"x")
        (sfx / "GUIDs.txt").write_text("{0} bank:/car_x\n", encoding="utf-8")
        self.assertEqual(self._errors(), [])

    def test_duplicate_keys(self):
        p = self.car / "data" / "car.ini"
        p.write_text(
            "[CONTROLS]\nSTEER_LOCK=450\nSTEER_LOCK=423 ; repetida\n[GRAPHICS]\nX=1\n",
            encoding="utf-8",
        )
        self.assertEqual(duplicate_keys(p), ["[CONTROLS] STEER_LOCK"])
        self.assertTrue(any("STEER_LOCK" in e for e in self._errors()))

    def test_ksanim_reference_resolved_in_animations_folder(self):
        (self.car / "ui").mkdir()
        (self.car / "ui" / "ui_car.json").write_text("{}", encoding="utf-8")
        (self.car / "data" / "driver3d.ini").write_text(
            "[STEER_ANIMATION]\nNAME=steer.ksanim\nLOCK=360\n", encoding="utf-8"
        )
        (self.car / "animations").mkdir()
        (self.car / "animations" / "steer.ksanim").write_bytes(b"x")
        v = validate_structure(self.car, ())
        self.assertEqual([e for e in v.errors if "ksanim" in e], [])
        (self.car / "animations" / "steer.ksanim").unlink()
        self.assertTrue(
            any("ksanim" in e for e in validate_structure(self.car, ()).errors)
        )

    def test_changed_protected(self):
        other = Path(self._tmp.name) / "car_y"
        (other / "data").mkdir(parents=True)
        (other / "data" / "lods.ini").write_bytes(
            (self.car / "data" / "lods.ini").read_bytes()
        )
        self.assertEqual(changed_protected(self.car, other, ["data/lods.ini"]), [])
        (other / "data" / "lods.ini").write_text(
            "[LOD_0]\nFILE=z.kn5\n", encoding="utf-8"
        )
        self.assertEqual(
            changed_protected(self.car, other, ["data/lods.ini", "data/engine.ini"]),
            ["data/lods.ini", "data/engine.ini"],
        )


if __name__ == "__main__":
    unittest.main()
