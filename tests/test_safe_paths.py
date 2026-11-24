import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.safe_paths import (
    DEFAULT_OUTPUT_DIR,
    OUTPUT_ROOTS_ENV,
    PROJECT_ROOT,
    PROTECTED_ROOTS_ENV,
    ProtectedPathError,
    claim_output_dir,
    ensure_safe_output_path,
    is_owned,
    is_protected,
    new_output_dir,
    protection_reason,
    protected_user_roots,
)

IS_WINDOWS = os.name == "nt"
INGAME_CYCLE = DEFAULT_OUTPUT_DIR / "seat_ibiza_acfr" / "ingame_cycle"


def _junction(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


class TestSamples(unittest.TestCase):
    def test_sample_dirs_are_protected(self):
        for rel in (
            "sample_assetto",
            "sample_assetto/data/power.lut",
            "sample_beamng/vehicles/x/y.jbeam",
            "samples_beamng/mod/vehicles/x/paints.meta",
            "sample_assetto_unpacked",
            "sample_assetto_unpacked/ks_toyota_ae86/data/suspensions.ini",
        ):
            with self.subTest(rel=rel):
                self.assertTrue(is_protected(PROJECT_ROOT / rel))
                with self.assertRaises(ProtectedPathError):
                    ensure_safe_output_path(PROJECT_ROOT / rel)

    def test_relative_and_dotdot_paths_are_resolved(self):
        sneaky = PROJECT_ROOT / "output" / ".." / "sample_assetto" / "data" / "a.lut"
        with self.assertRaises(ProtectedPathError):
            ensure_safe_output_path(sneaky)

    def test_similar_names_are_not_protected(self):
        # "sample_assetto_copy" no es la muestra (pero tampoco es una raíz permitida).
        self.assertFalse(is_protected(PROJECT_ROOT / "sample_assetto_copy" / "a.lut"))


class TestAllowedRoots(unittest.TestCase):
    def test_new_paths_under_output_are_allowed(self):
        target = DEFAULT_OUTPUT_DIR / "car" / "data" / "power.lut"
        self.assertEqual(ensure_safe_output_path(target), target.resolve())
        new_pkg = INGAME_CYCLE / "pkg_v99_does_not_exist" / "car" / "data" / "car.ini"
        self.assertIsNone(protection_reason(new_pkg))

    def test_temp_dir_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(protection_reason(Path(d) / "x" / "y.ini"))

    def test_relative_path_resolved_against_cwd(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            try:
                os.chdir(d)
                self.assertEqual(
                    ensure_safe_output_path("rel/file.ini"),
                    (Path(d) / "rel" / "file.ini").resolve(),
                )
            finally:
                os.chdir(cwd)

    def test_outside_allowed_roots_is_refused(self):
        for p in (
            PROJECT_ROOT / "docs" / "x.md",
            PROJECT_ROOT / "src" / "x.py",
            PROJECT_ROOT / "sample_assetto_copy" / "a.lut",
            PROJECT_ROOT.parent / "beam2ac_elsewhere" / "x.ini",
        ):
            with self.subTest(p=p), self.assertRaises(ProtectedPathError):
                ensure_safe_output_path(p)

    def test_extra_output_root_from_environment(self):
        extra = PROJECT_ROOT.parent / "beam2ac_extra_out_test"
        self.assertIsNotNone(protection_reason(extra / "x.ini"))
        with mock.patch.dict(os.environ, {OUTPUT_ROOTS_ENV: str(extra)}):
            self.assertIsNone(protection_reason(extra / "x.ini"))
        self.assertFalse(extra.exists())  # comprobar no crea nada


class TestGames(unittest.TestCase):
    def test_game_installs_by_name(self):
        for p in (
            r"G:\SteamLibrary\steamapps\common\assettocorsa\content\cars\x\car.ini",
            "G:/SteamLibrary/steamapps/common/AssettoCorsa/content/cars/x",
            r"D:\Games\steamapps\common\BeamNG.drive\vehicles\x\x.jbeam",
            r"D:\Games\steamapps\common\BeamNG.tech",
        ):
            with self.subTest(p=p):
                reason = protection_reason(p)
                self.assertIsNotNone(reason)
                self.assertIn("juego", reason)

    def test_game_install_inside_allowed_root_by_marker(self):
        for marker in (
            "acs.exe",
            "AssettoCorsa.exe",
            "BeamNG.drive.exe",
            "Bin64/BeamNG.drive.x64.exe",
            "Content Manager.exe",
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as d:
                game = Path(d) / "somegame"
                (game / marker).parent.mkdir(parents=True, exist_ok=True)
                (game / marker).write_bytes(b"")
                with self.assertRaises(ProtectedPathError):
                    ensure_safe_output_path(game / "content" / "cars" / "x" / "car.ini")
                with self.assertRaises(ProtectedPathError):
                    ensure_safe_output_path(game)

    def test_user_game_folders(self):
        roots = protected_user_roots()
        names = {r.name.lower() for r in roots}
        self.assertTrue({"assetto corsa", "beamng.drive"} <= names)
        docs_ac = [r for r in roots if r.name.lower() == "assetto corsa"]
        for root in docs_ac:
            for f in ("controls.ini", "race.ini", "video.ini"):
                with self.subTest(f=f), self.assertRaises(ProtectedPathError):
                    ensure_safe_output_path(root / "cfg" / f)
        if os.environ.get("LOCALAPPDATA"):
            with self.assertRaises(ProtectedPathError):
                ensure_safe_output_path(
                    Path(os.environ["LOCALAPPDATA"]) / "BeamNG.drive" / "0.32" / "x"
                )

    def test_protected_root_from_environment(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(protection_reason(Path(d) / "x"))
            with mock.patch.dict(os.environ, {PROTECTED_ROOTS_ENV: d}):
                with self.assertRaises(ProtectedPathError):
                    ensure_safe_output_path(Path(d) / "x")


class TestSealedPackages(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pkg = self.root / "ingame_cycle" / "pkg_vx"
        (self.pkg / "car" / "data").mkdir(parents=True)
        (self.pkg / "car" / "data" / "car.ini").write_text("[A]\n", encoding="utf-8")
        (self.pkg / "manifest.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_sealed_package_and_content_refused(self):
        for p in (
            self.pkg,
            self.pkg / "manifest.json",
            self.pkg / "car",
            self.pkg / "car" / "data" / "car.ini",
            self.pkg / "car" / "new_file.ini",
            self.root / "ingame_cycle" / "new" / ".." / "pkg_vx" / "car" / "x",
        ):
            with self.subTest(p=p), self.assertRaises(ProtectedPathError):
                ensure_safe_output_path(p)
        self.assertEqual(
            (self.pkg / "car" / "data" / "car.ini").read_text(encoding="utf-8"), "[A]\n"
        )

    def test_sibling_new_package_allowed(self):
        self.assertIsNone(
            protection_reason(self.root / "ingame_cycle" / "pkg_vy" / "x")
        )

    @unittest.skipUnless(IS_WINDOWS, "equivalencias de ruta de Windows")
    def test_windows_equivalent_spellings_refused(self):
        p = str(self.pkg / "car" / "data" / "car.ini")
        for variant in (
            p.upper(),
            p.replace("\\", "/"),
            str(self.pkg) + "." + "\\car\\x.ini",
            str(self.pkg) + " " + "\\car\\x.ini",
            str(self.pkg) + "\\car.\\data\\car.ini",
            "\\\\?\\" + p,
        ):
            with self.subTest(variant=variant), self.assertRaises(ProtectedPathError):
                ensure_safe_output_path(variant)

    @unittest.skipUnless(IS_WINDOWS, "uniones de Windows")
    def test_junction_into_sealed_package_or_game_refused(self):
        link = self.root / "link_to_pkg"
        _junction(link, self.pkg)
        with self.assertRaises(ProtectedPathError):
            ensure_safe_output_path(link / "car" / "data" / "car.ini")
        game = self.root / "fake_ac"
        game.mkdir()
        (game / "acs.exe").write_bytes(b"")
        link2 = self.root / "link_to_game"
        _junction(link2, game)
        with self.assertRaises(ProtectedPathError):
            ensure_safe_output_path(link2 / "content" / "cars" / "x")
        link3 = self.root / "link_to_docs"
        _junction(link3, PROJECT_ROOT / "docs")
        with self.assertRaises(ProtectedPathError):
            ensure_safe_output_path(link3 / "x.md")

    def test_symlink_into_sealed_package_refused(self):
        link = self.root / "sym_to_pkg"
        try:
            os.symlink(self.pkg, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("sin permiso para crear enlaces simbólicos")
        with self.assertRaises(ProtectedPathError):
            ensure_safe_output_path(link / "car" / "x.ini")

    def test_existing_approved_packages_refused(self):
        pkgs = sorted(INGAME_CYCLE.glob("pkg_*")) if INGAME_CYCLE.is_dir() else []
        sealed = [p for p in pkgs if (p / "manifest.json").is_file()]
        if not sealed:
            self.skipTest("no hay paquetes en ingame_cycle")
        for pkg in sealed:
            with self.subTest(pkg=pkg.name):
                with self.assertRaises(ProtectedPathError):
                    ensure_safe_output_path(pkg / "manifest.json")
                for car in (c for c in pkg.iterdir() if c.is_dir()):
                    with self.assertRaises(ProtectedPathError):
                        ensure_safe_output_path(car / "data" / "car.ini")


class TestNewOutputDirs(unittest.TestCase):
    def test_new_output_dir_creates_registers_and_refuses_existing(self):
        with tempfile.TemporaryDirectory() as d:
            out = new_output_dir(Path(d) / "pkg_new")
            self.assertTrue(out.is_dir() and is_owned(out))
            (out / "manifest.json").write_text("{}", encoding="utf-8")
            # propia de este proceso: se puede seguir escribiendo dentro
            self.assertIsNone(protection_reason(out / "car" / "data" / "car.ini"))
            self.assertIsNone(protection_reason(out / "manifest.json"))
            with self.assertRaises(ProtectedPathError):
                new_output_dir(Path(d) / "pkg_new")

    def test_claim_refuses_sealed_and_accepts_unsealed(self):
        with tempfile.TemporaryDirectory() as d:
            sealed = Path(d) / "sealed"
            sealed.mkdir()
            (sealed / "manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ProtectedPathError):
                claim_output_dir(sealed)
            with self.assertRaises(ProtectedPathError):
                new_output_dir(sealed / "inner")
            free = Path(d) / "free"
            free.mkdir()
            self.assertEqual(claim_output_dir(free), free.resolve())
            self.assertTrue(is_owned(free))

    def test_new_output_dir_refuses_forbidden_targets(self):
        # Ninguna ruta real de juego: si el guardián fallara, solo escribiría en temp.
        with tempfile.TemporaryDirectory() as d:
            fake_ac = Path(d) / "steamapps" / "common" / "assettocorsa"
            fake_ac.mkdir(parents=True)
            for p in (
                PROJECT_ROOT / "sample_assetto" / "x",
                PROJECT_ROOT / "docs" / "x",
                fake_ac / "content" / "cars" / "beam2ac_x",
            ):
                with self.subTest(p=p), self.assertRaises(ProtectedPathError):
                    new_output_dir(p)
                self.assertFalse(Path(p).exists())


if __name__ == "__main__":
    unittest.main()
