"""Regresión de la lectura de JBeam en zip y del caso ``"clave":,``.

Las pruebas con datos de BeamNG instalados se omiten si las rutas no existen.
"""

import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.bench.service import default_beamng_root
from src.jbeam import (
    PartIndex,
    ZipMember,
    ZipSource,
    load_pc,
    resolve,
    vehicle_dirs_in_zip,
)
from src.jbeam.lax_json import COMMA_AFTER_COLON, EXTRA_COMMA, JBeamSyntaxError, loads

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
BNG_VEHICLES = Path((default_beamng_root() / "content/vehicles").as_posix())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestCommaAfterColon(unittest.TestCase):
    def test_root_part_with_comma_after_colon(self):
        # Caso real: bluebuck_bumper_F.jbeam empieza por {"bluebuck_bumper_F":,
        doc = loads('{"p":,\r\n{\r\n "information":{"name":"x"}\r\n}\r\n}')
        self.assertEqual(doc.value, {"p": {"information": {"name": "x"}}})
        notes = [n for n in doc.leniencies if n.kind == COMMA_AFTER_COLON]
        self.assertEqual([(n.line, n.column, n.detail) for n in notes], [(1, 6, "p")])

    def test_section_table_with_comma_after_colon(self):
        # Caso real: rocks.jbeam  "nodes":,\n [ [cabecera], ... ]
        doc = loads('{"r": {"nodes":,\n [["id","posX"],["n1",1]], "x": 2}}')
        self.assertEqual(doc.value["r"]["nodes"], [["id", "posX"], ["n1", 1]])
        self.assertEqual(doc.value["r"]["x"], 2)
        self.assertEqual(doc.count(COMMA_AFTER_COLON), 1)

    def test_several_commas_after_colon(self):
        doc = loads('{"a":,, 1}')
        self.assertEqual(doc.value, {"a": 1})
        self.assertEqual(doc.count(COMMA_AFTER_COLON), 2)

    def test_no_value_after_commas_is_still_an_error(self):
        # En el juego, '}' tras las comas es error_input.
        for text in ('{"a":,}', '{"a":,', '{"a":, ]}'):
            with self.subTest(text):
                with self.assertRaises(JBeamSyntaxError):
                    loads(text)

    def test_other_comma_cases_unchanged(self):
        doc = loads('{"a": 1,, "b": [1,,2]},\n')
        self.assertEqual(doc.count(EXTRA_COMMA), 3)
        self.assertEqual(doc.count(COMMA_AFTER_COLON), 0)

    def test_comma_inside_string_value_is_text(self):
        self.assertEqual(loads('{"a": ",", "b":","}').value, {"a": ",", "b": ","})


class TestZipSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _zip(self, name: str, members: dict[str, bytes]) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            for member, data in members.items():
                zf.writestr(member, data)
        return path

    def test_prefix_filter_and_order(self):
        z = self._zip(
            "mod.zip",
            {
                "vehicles/a/z.jbeam": b'{"pz": {"slotType": "main"}}',
                "vehicles/a/sub/b.JBEAM": b'{"pb": {"slotType": "x"}}',
                "vehicles/b/c.jbeam": b'{"pc": {"slotType": "y"}}',
                "vehicles/a/readme.txt": b"no",
            },
        )
        self.assertEqual(vehicle_dirs_in_zip(z), ["vehicles/a/", "vehicles/b/"])
        index = PartIndex.from_sources(ZipSource(z, "vehicles/a"))
        self.assertEqual(sorted(index.parts), ["pb", "pz"])
        self.assertEqual(
            [m.member for m in index.files],
            ["vehicles/a/sub/b.JBEAM", "vehicles/a/z.jbeam"],
        )
        part = index["pz"]
        self.assertIsInstance(part.file, ZipMember)
        self.assertEqual(part.file.name, "z.jbeam")
        self.assertIn("mod.zip!/vehicles/a/z.jbeam", str(part.file))

    def test_whole_zip_and_duplicates_first_source_wins(self):
        veh = self._zip("veh.zip", {"vehicles/v/p.jbeam": b'{"shared": {"v": 1}}'})
        common = self._zip(
            "common.zip",
            {"vehicles/common/p.jbeam": b'{"shared": {"v": 2}, "only_common": {}}'},
        )
        index = PartIndex.from_sources(veh, common)
        self.assertEqual(index["shared"].data, {"v": 1})
        self.assertIn("only_common", index)
        self.assertEqual(len(index.duplicates), 1)
        name, first, second = index.duplicates[0]
        self.assertEqual(
            (name, first.zip_path, second.zip_path), ("shared", veh, common)
        )

    def test_syntax_and_encoding_errors_are_recorded_not_raised(self):
        z = self._zip(
            "bad.zip",
            {
                "vehicles/x/ok.jbeam": b'{"ok": {}}',
                "vehicles/x/bad.jbeam": b'{"a": FLT_MAX}',
                "vehicles/x/latin1.jbeam": '{"á": {}}'.encode("latin-1"),
            },
        )
        index = PartIndex.from_sources(z)
        self.assertIn("ok", index)
        self.assertEqual(
            sorted(m.name for m in index.file_errors), ["bad.jbeam", "latin1.jbeam"]
        )

    def test_crlf_positions_match_directory_reader(self):
        text = b'{"p":\r\n {"a": 1\r\n  "b": 2,}\r\n}'
        folder = self.root / "dir"
        folder.mkdir()
        (folder / "p.jbeam").write_bytes(text)
        z = self._zip("p.zip", {"p.jbeam": text})
        from_dir = PartIndex.from_directories(folder)
        from_zip = PartIndex.from_sources(z)
        self.assertEqual(from_dir["p"].data, from_zip["p"].data)
        self.assertEqual(
            list(from_dir.leniencies.values()), list(from_zip.leniencies.values())
        )

    def test_load_pc_from_zip(self):
        z = self._zip(
            "pc.zip",
            {
                "vehicles/v/c.pc": (
                    b'{"format": 2,\r\n "parts": {"s": "p"}, "vars": {"$x": 1}}'
                )
            },
        )
        pc = load_pc(ZipMember(z, "vehicles/v/c.pc"))
        self.assertEqual((pc.format, pc.parts, pc.vars), (2, {"s": "p"}, {"$x": 1}))
        self.assertIsInstance(pc.path, ZipMember)

    def test_zip_is_not_modified_nor_extracted(self):
        z = self._zip(
            "ro.zip", {"vehicles/v/p.jbeam": b'{"p": {}}', "vehicles/v/c.pc": b"{}"}
        )
        before = (
            _sha(z),
            os.stat(z).st_mtime_ns,
            sorted(p.name for p in self.root.iterdir()),
        )
        PartIndex.from_sources(z)
        load_pc(ZipMember(z, "vehicles/v/c.pc"))
        after = (
            _sha(z),
            os.stat(z).st_mtime_ns,
            sorted(p.name for p in self.root.iterdir()),
        )
        self.assertEqual(before, after)

    def test_missing_sources_are_issues(self):
        index = PartIndex.from_sources(self.root / "nope.zip", self.root / "nodir")
        self.assertEqual(len(index.issues), 2)
        self.assertEqual(index.parts, {})


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestIbizaZipEquivalence(unittest.TestCase):
    """Carpeta y zip temporal del mismo vehículo: mismo índice y misma resolución."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.zip = Path(cls.tmp.name) / "seat_ibiza_acfr.zip"
        with zipfile.ZipFile(cls.zip, "w") as zf:
            for path in sorted(IBIZA.rglob("*")):
                if path.suffix.lower() in (".jbeam", ".pc"):
                    rel = path.relative_to(IBIZA.parent.parent).as_posix()
                    zf.write(path, rel)
        cls.dir_index = PartIndex.from_directories(IBIZA)
        cls.zip_index = PartIndex.from_sources(
            ZipSource(cls.zip, "vehicles/seat_ibiza_acfr/")
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_same_parts_and_data(self):
        self.assertEqual(list(self.dir_index.parts), list(self.zip_index.parts))
        for name, part in self.dir_index.parts.items():
            self.assertEqual(part.data, self.zip_index[name].data, name)

    def test_same_leniencies_and_errors(self):
        self.assertEqual(
            list(self.dir_index.leniencies.values()),
            list(self.zip_index.leniencies.values()),
        )
        self.assertEqual(
            len(self.dir_index.file_errors), len(self.zip_index.file_errors)
        )

    def test_from_sources_with_directory_equals_from_directories(self):
        index = PartIndex.from_sources(IBIZA)
        self.assertEqual(index.files, self.dir_index.files)
        self.assertEqual(list(index.parts), list(self.dir_index.parts))

    def test_same_resolution(self):
        pc_dir = load_pc(IBIZA / "35b84.pc")
        pc_zip = load_pc(ZipMember(self.zip, "vehicles/seat_ibiza_acfr/35b84.pc"))
        a = resolve(self.dir_index, pc_dir)
        b = resolve(self.zip_index, pc_zip)
        self.assertEqual([p.name for p in a.active], [p.name for p in b.active])
        self.assertEqual(a.warnings, b.warnings)


@unittest.skipUnless((BNG_VEHICLES / "bluebuck.zip").is_file(), "BeamNG no instalado")
class TestOfficialCommaAfterColon(unittest.TestCase):
    def test_bluebuck_bumper_parses(self):
        member = ZipMember(
            BNG_VEHICLES / "bluebuck.zip", "vehicles/bluebuck/bluebuck_bumper_F.jbeam"
        )
        doc = loads(member.read_text(), source=str(member))
        self.assertIn("bluebuck_bumper_F", doc.value)
        self.assertGreaterEqual(doc.count(COMMA_AFTER_COLON), 1)


if __name__ == "__main__":
    unittest.main()
