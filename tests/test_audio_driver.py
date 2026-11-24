"""Fase C1: audio precompilado (src/ac/audio.py) y piloto local
(src/project/local_driver.py)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from src.ac import audio as A
from src.project import local_driver as L

V16 = Path(
    "output/seat_ibiza_acfr/ingame_cycle/pkg_v16sn_sound/"
    "beam2ac_test16sn_seat_ibiza_acfr/sfx"
)
OLD = "beam2ac_test16sn_seat_ibiza_acfr"


def fake_bank(tmp: Path, car: str, guids: dict[str, str], in_bank: set[str]) -> Path:
    d = tmp / "bank"
    d.mkdir()
    blob = b"RIFF" + b"".join(uuid.UUID(g).bytes_le for g in in_bank) + b"\x00" * 8
    (d / f"{car}.bank").write_bytes(blob)
    lines = [f"{g} {n}" for n, g in guids.items()]
    (d / "GUIDs.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


class TestPrebuilt(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.g = {
            "bank:/car_a": str(uuid.uuid4()),
            "event:/cars/car_a/engine_ext": str(uuid.uuid4()),
            "event:/cars/car_b/engine_ext": str(uuid.uuid4()),  # de otro coche
            "bus:/grp_engine_ext": str(uuid.uuid4()),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bank(self, in_bank=None) -> Path:
        keys = in_bank or {
            self.g["bank:/car_a"],
            self.g["event:/cars/car_a/engine_ext"],
        }
        return fake_bank(self.tmp, "car_a", self.g, keys)

    def test_valid_and_trimmed(self) -> None:
        a = A.load_prebuilt(self.bank(), "car_a")
        text = a.guids.decode()
        self.assertIn("bank:/car_a", text)
        self.assertNotIn("car_b", text)  # recortado
        self.assertEqual((a.events, a.buses), (["engine_ext"], 1))

    def test_guid_not_in_bank_refused(self) -> None:
        d = self.bank(in_bank={self.g["bank:/car_a"]})
        with self.assertRaisesRegex(A.AudioInputError, "no contiene los GUID"):
            A.load_prebuilt(d, "car_a")

    def test_other_id_needs_source_and_is_rebound(self) -> None:
        d = self.bank()
        with self.assertRaisesRegex(A.AudioInputError, "falta car_x.bank"):
            A.load_prebuilt(d, "car_x")
        a = A.load_prebuilt(d, "car_x", source_id="car_a")
        text = a.guids.decode()
        self.assertIn(f"{self.g['bank:/car_a']} bank:/car_x", text)
        self.assertIn(
            f"{self.g['event:/cars/car_a/engine_ext']} event:/cars/car_x/", text
        )
        self.assertNotIn("car_a", text)
        self.assertEqual(a.source["rebound_to"], "car_x")

    def test_common_and_loose_audio_refused(self) -> None:
        d = self.bank()
        with self.assertRaisesRegex(A.AudioInputError, "Kunos"):
            A.load_prebuilt(d, "x", source_id="common")
        (d / "a.wav").write_bytes(b"x")
        with self.assertRaisesRegex(A.AudioInputError, "audio suelto"):
            A.load_prebuilt(d, "car_a")

    def test_sounds_ini(self) -> None:
        self.assertIn("POSITION=front", A.render_sounds_ini("front", "x"))
        with self.assertRaises(A.AudioInputError):
            A.render_sounds_ini("middle", "x")


@unittest.skipUnless((V16 / f"{OLD}.bank").is_file(), "banco de v16sn no disponible")
class TestV16Bank(unittest.TestCase):
    def test_real_bank_trim_equals_v16sn_and_rebinds(self) -> None:
        a = A.load_prebuilt(V16, OLD)
        self.assertEqual(a.guids, (V16 / "GUIDs.txt").read_bytes())
        self.assertEqual((len(a.events), a.buses), (19, 23))
        b = A.load_prebuilt(V16, "beam2ac_seat_ibiza_acfr", source_id=OLD)
        self.assertNotIn(OLD, b.guids.decode())
        old = [ln.split()[0] for ln in a.guids.decode().splitlines()]
        new = [ln.split()[0] for ln in b.guids.decode().splitlines()]
        self.assertEqual(old, new)  # mismos GUID
        with self.assertRaises(A.AudioInputError):
            A.load_prebuilt(V16, "beam2ac_seat_ibiza_acfr")


class TestLocalDriverErrors(unittest.TestCase):
    def test_requirements(self) -> None:
        with self.assertRaisesRegex(L.LocalDriverError, "sin perfil"):
            L.local_driver_files(Path("."), {"profile": {}}, Path("."))
        prof = {
            "path": "src/ac/vehicle_profiles/seat_ibiza_acfr.json",
            "sha256": "0" * 64,
        }
        with self.assertRaisesRegex(L.LocalDriverError, "cambió"):
            L.local_driver_files(Path("."), {"profile": prof}, Path("."))


class TestExportOptions(unittest.TestCase):
    def test_local_driver_without_model_or_ac_root_is_error(self) -> None:
        from src.project import export as E

        tmp = Path(tempfile.mkdtemp())
        try:
            src = tmp / "source"
            src.mkdir()
            (src / "source.json").write_text(
                '{"vehicle_id": "v", "variant": "c"}', encoding="utf-8"
            )

            class P:
                root = tmp
                catalog_path = Path("catalog")
                source = type("S", (), {"kind": "dir", "path": str(tmp), "pc": "c.pc"})

            files, issues, ctx = E.collect(
                P(),
                {"source": src},
                {},
                "beam2ac_v",
                {"local_driver": True, "audio_bank": str(tmp / "nada")},
            )
            codes = {i.code for i in issues if i.severity == "error"}
            self.assertIn("piloto_local", codes)
            self.assertIn("audio", codes)
            self.assertIn("data/driver3d.ini", [f.rel for f in files])  # genérico
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
