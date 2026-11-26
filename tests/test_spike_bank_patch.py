"""Spike de parcheo de bancos FMOD (``src/audio/bank_patch.py``).

Se omite si no existen los bancos de referencia (``builds/`` del proyecto y el banco
oficial de la instalación local de AC, solo lectura).
"""

from __future__ import annotations

import math
import shutil
import struct
import tempfile
import unittest
import uuid
import wave
from pathlib import Path

from src.ac.audio import load_prebuilt
from src.audio import bank_patch as bp
from src.safe_paths import ensure_safe_output_path

ROOT = Path(__file__).resolve().parents[1]
CARS = ("beam2ac_agus_1_nrt_1", "beam2ac_seat_ibiza_acfr")


def _project_dirs() -> list[Path]:
    dirs = [ROOT / "builds" / c / c / "sfx" for c in CARS]
    return [d for d in dirs if (d / f"{d.parent.name}.bank").is_file()]


def _kunos_bank() -> Path | None:
    try:
        from src.steam_paths import find_game

        root = find_game("assettocorsa")
    except Exception:  # noqa: BLE001 - sin instalación
        return None
    p = (
        Path(root)
        / "content/cars/ks_toyota_celica_st185/sfx/ks_toyota_celica_st185.bank"
    )
    return p if p.is_file() else None


def _banks() -> list[Path]:
    out = [d / f"{d.parent.name}.bank" for d in _project_dirs()]
    k = _kunos_bank()
    return out + ([k] if k else [])


def _sine(path: Path, rate: int, hz: float, secs: float) -> None:
    n = int(rate * secs)
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / rate)))
        for i in range(n)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


@unittest.skipUnless(len(_banks()) >= 3, "faltan los bancos de referencia")
class TestRoundtrip(unittest.TestCase):
    def test_riff_y_fsb5_byte_a_byte(self) -> None:
        for p in _banks():
            buf = p.read_bytes()
            bank = bp.parse_bank(buf)
            self.assertEqual(bp.rebuild_bank(bank), buf, p.name)
            snd = bank.find(b"SND ")
            assert snd is not None
            parsed = bp.bank_fsb(bank)
            self.assertEqual(bp.build_snd(parsed), snd.data, p.name)
            if p.parent.parent.name != "sfx" and "ks_" not in p.name:
                self.assertTrue(all(s.channels == 1 for s in parsed.fsb.samples))

    def test_sndh_coincide_con_el_fsb5(self) -> None:
        for p in _banks():
            buf = p.read_bytes()
            bank = bp.parse_bank(buf)
            (h,) = bp.find_leaf(bank.chunks, b"SNDH")
            _, off, size = struct.unpack("<III", h.data)
            self.assertEqual(buf[off : off + 4], b"FSB5")
            self.assertEqual(size, len(bp.build_fsb5(bp.bank_fsb(bank).fsb)))


@unittest.skipUnless(_project_dirs(), "faltan los bancos del proyecto")
class TestPatch(unittest.TestCase):
    def test_parche_conserva_estructura_y_guids(self) -> None:
        src = _project_dirs()[0]
        car = src.parent.name
        buf = (src / f"{car}.bank").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            tmpd = Path(tmp)
            w0, w5 = tmpd / "a.wav", tmpd / "b.wav"
            _sine(w0, 44100, 440.0, 0.25)
            _sine(w5, 22050, 220.0, 0.5)  # se remuestrea a 44100
            out = bp.patch_bank(buf, {0: w0, 5: w5})
            self.assertNotEqual(out, buf)
            old, new = bp.parse_bank(buf), bp.parse_bank(out)
            self.assertEqual(bp.rebuild_bank(new), out)
            a, b = bp.bank_fsb(old).fsb, bp.bank_fsb(new).fsb
            self.assertEqual([s.name for s in a.samples], [s.name for s in b.samples])
            # clave de caché del FSB5: distinta del original, 8 bytes finales iguales
            self.assertNotEqual(a.hash24[:16], b.hash24[:16])
            self.assertEqual(a.hash24[16:], b.hash24[16:])
            self.assertEqual(b.samples[0].samples, 11025)
            self.assertEqual(b.samples[5].samples, 22050)
            for i in range(len(a.samples)):
                if i not in (0, 5):
                    self.assertEqual(a.samples[i].data, b.samples[i].data)
            self.assertEqual(len(b.samples[0].data) % 32, 0)
            # PROJ intacto salvo SNDH (tamaño del FSB5)
            pa, pb = old.find(b"LIST"), new.find(b"LIST")
            assert pa is not None and pb is not None
            (ha,) = bp.find_leaf([pa], b"SNDH")
            (hb,) = bp.find_leaf([pb], b"SNDH")
            ha.data = hb.data
            self.assertEqual(bp._emit(pa), bp._emit(pb))
            # validación de GUID que usa el proyecto
            dest = ensure_safe_output_path(tmpd / "sfx")
            dest.mkdir()
            (dest / f"{car}.bank").write_bytes(out)
            shutil.copy(src / "GUIDs.txt", dest / "GUIDs.txt")
            pre = load_prebuilt(dest, car)
            self.assertEqual(pre.bank, out)

    def test_guids_no_encontrados_son_buses_maestros(self) -> None:
        src = _project_dirs()[0]
        car = src.parent.name
        buf = (src / f"{car}.bank").read_bytes()
        miss = [
            n
            for g, n in bp.parse_guids((src / "GUIDs.txt").read_text())
            if not bp.find_all(buf, uuid.UUID(g).bytes_le)
        ]
        self.assertTrue(miss)
        self.assertTrue(all(n.startswith("bus:/") for n in miss), miss)

    def test_reguid_sustituye_todo_sin_cambiar_largos(self) -> None:
        src = _project_dirs()[0]
        car = src.parent.name
        buf = (src / f"{car}.bank").read_bytes()
        pairs = bp.parse_guids((src / "GUIDs.txt").read_text())
        # solo eventos y banco: los buses son del banco maestro y se comparten
        own = [g for g, n in pairs if not n.startswith("bus:/")]
        mapping = {g: str(uuid.uuid4()) for g in own}
        out, counts = bp.reguid(buf, mapping)
        self.assertEqual(len(out), len(buf))
        self.assertTrue(all(counts[str(uuid.UUID(g))] >= 1 for g in own))
        for g, n in mapping.items():
            old = uuid.UUID(g).bytes_le
            self.assertEqual(bp.find_all(out, old), [])
            self.assertEqual(
                len(bp.find_all(out, uuid.UUID(n).bytes_le)), counts[str(uuid.UUID(g))]
            )
        new, old_bank = bp.parse_bank(out), bp.parse_bank(buf)
        self.assertEqual(bp.rebuild_bank(new), out)
        self.assertEqual(
            [len(c.data) for _, c in bp.iter_chunks(new.chunks)],
            [len(c.data) for _, c in bp.iter_chunks(old_bank.chunks)],
        )
        # los GUID del banco nuevo pasan la validación con un GUIDs.txt reescrito
        text = (src / "GUIDs.txt").read_text()
        for g, n in mapping.items():
            text = text.replace(g.strip("{}"), n)
        with tempfile.TemporaryDirectory() as tmp:
            dest = ensure_safe_output_path(Path(tmp) / "sfx")
            dest.mkdir()
            (dest / f"{car}.bank").write_bytes(out)
            (dest / "GUIDs.txt").write_text(text)
            load_prebuilt(dest, car)


if __name__ == "__main__":
    unittest.main()
