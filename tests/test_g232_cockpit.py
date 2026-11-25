"""G2.32: ajustes de cámaras/piloto (almacén versionado), caderas por defecto y aplicación al exportar."""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from src.project.export import default_ac_root
from src.ac import cameras, driver_seat
from src.project import cockpit_edits as CE
from src.project.export import _apply_camera_edits

ROOT = Path(__file__).resolve().parents[1]
AC = Path((default_ac_root() or Path("assettocorsa")).as_posix())
IBIZA_KN5 = next(
    iter((ROOT / "output/projects/ibiza_35b84_b1/exports/004").glob("*/*.kn5")), None
)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._root = CE.ROOT
        CE.ROOT = Path(self.tmp.name) / "cockpit_edits"
        self.key = "0123456789abcdef_x.pc"

    def tearDown(self):
        CE.ROOT = self._root
        self.tmp.cleanup()

    def test_versions_append_only_and_reset(self):
        self.assertEqual(CE.current(self.key)["version"], 0)
        a = CE.save(self.key, {"hips": [0.3, 0.5, 0.1]})
        b = CE.save(self.key, {"hips": [0.3, 0.45, 0.1], "bonnet": [0, 1, 1]})
        r = CE.save(self.key, {}, "restaurado")
        self.assertEqual((a["version"], b["version"], r["version"]), (1, 2, 3))
        self.assertEqual(CE.current(self.key)["items"], {})
        self.assertEqual(len(CE.versions(self.key)), 3)  # nada se sobrescribe
        self.assertEqual(CE.versions(self.key)[0]["items"]["hips"], [0.3, 0.5, 0.1])

    def test_validation(self):
        for bad in (
            {"nope": [0, 0, 0]},
            {"hips": [0, 0]},
            {"hips": [0, 0, float("nan")]},
            {"hips": [0, 99, 0]},
            {"hips": ["a", 0, 0]},
            {"hips": [10**400, 0, 0]},
            {"hips": [True, False, 0]},
            ["hips"],
        ):
            with self.assertRaises(CE.EditsError):
                CE.save(self.key, bad)
        with self.assertRaises(CE.EditsError):
            CE.current("../../etc")


class TestVersionsBeyond999(unittest.TestCase):
    def test_v1000_is_found(self):
        with tempfile.TemporaryDirectory() as t:
            old, CE.ROOT = CE.ROOT, Path(t)
            try:
                key = "0123456789abcdef_x.pc"
                d = CE.ROOT / key
                d.mkdir()
                for n in (999, 1000):
                    (d / f"v{n:03d}.json").write_text(
                        '{"version": %d, "items": {}}' % n, "utf-8"
                    )
                self.assertEqual(CE.current(key)["version"], 1000)
                self.assertEqual(CE.save(key, {})["version"], 1001)
            finally:
                CE.ROOT = old


class TestSeat(unittest.TestCase):
    def test_ibiza_pair_reproduced(self):
        hips, rep = driver_seat.default_hips((0.355, 1.144, -0.344), lambda x, z: 5.0)
        self.assertEqual(hips, (0.34, 0.52, -0.1))
        self.assertEqual(rep["lowered_m"], 0.0)

    def test_headroom_lowers_never_raises(self):
        eyes = (0.3, 1.2, 0.0)
        hi, _ = driver_seat.default_hips(
            eyes, lambda x, z: 2.5
        )  # techo alto: no cambia
        lo, rep = driver_seat.default_hips(eyes, lambda x, z: 1.30)  # techo bajo: baja
        self.assertEqual(hi[1], round(1.2 - 0.624, 4))
        self.assertLess(lo[1], hi[1])
        self.assertAlmostEqual(
            lo[1]
            + driver_seat.HEAD_BONE_ABOVE_HIPS
            + driver_seat.VALIDATED_ROOF_MINUS_HEAD,
            1.30,
            places=3,
        )

    @unittest.skipUnless(IBIZA_KN5, "sin KN5 del Ibiza en output/")
    def test_resolve_eyes_keeps_plausible_and_replaces_implausible(self):
        e, info = driver_seat.resolve_eyes(IBIZA_KN5, (0.355, 1.144, -0.344))
        self.assertEqual((e, info["source"]), ((0.355, 1.144, -0.344), "beamng"))
        e2, info2 = driver_seat.resolve_eyes(
            IBIZA_KN5, (0.0, 0.23, 0.037)
        )  # estilo Autist
        self.assertEqual(info2["source"], "default")
        self.assertGreater(e2[1], 1.0)
        e3, info3 = driver_seat.resolve_eyes(IBIZA_KN5, None)  # estilo 200409
        self.assertEqual(info3["source"], "default")
        self.assertAlmostEqual(e3[0], driver_seat.SEAT_X)

    @unittest.skipUnless(
        (AC / "content/cars/ks_toyota_ae86/driver_base_pos.knh").is_file(),
        "sin AC local",
    )
    def test_hips_only_file_places_hips(self):
        from src.project.local_driver import hips_only_files

        files, rep = hips_only_files(AC, (0.3, 0.5, 0.0))
        self.assertEqual(list(files), ["driver_base_pos.knh"])
        for a, b in zip(rep["pose"]["hips_after"], (0.3, 0.5, 0.0)):
            self.assertAlmostEqual(a, b, places=4)
        self.assertFalse(rep["distributable"])


class TestApply(unittest.TestCase):
    def _cams(self):
        z = (0.0, 0.0, 0.0)
        return cameras.Cameras(
            z,
            26,
            (0, 0.5, 1),
            (0, 1.0, 1.1),
            (0, 0.575, 2),
            (0, 0.9, 1.8),
            2.0,
            1.0,
            1.0,
            0.2,
        )

    def test_no_edits_keeps_cameras_but_dash_follows_eyes(self):
        c = self._cams()
        out = _apply_camera_edits(c, (0.3, 1.1, 0.0), {}, (0, -0.5, 0.1))
        self.assertEqual(out.bonnet_body, c.bonnet_body)
        self.assertEqual(out.bumper_kn5, c.bumper_kn5)
        self.assertEqual(out.dash_pos, (0.3, 1.1, 0.0))

    def test_bonnet_edit_is_independent_and_converts_frame(self):
        c = self._cams()
        out = _apply_camera_edits(
            c, (0, 0, 0), {"bonnet": [0.0, 1.2, 1.3]}, (0, -0.5, 0.1)
        )
        self.assertEqual(out.bonnet_kn5, (0.0, 1.2, 1.3))
        self.assertEqual(
            out.bonnet_body, (0.0, 0.7, 1.4)
        )  # cuerpo = KN5 + GRAPHICS_OFFSET
        self.assertEqual(out.bumper_body, c.bumper_body)  # el otro no se toca
        self.assertTrue(dataclasses.is_dataclass(out))


if __name__ == "__main__":
    unittest.main()
