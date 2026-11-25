"""Esqueleto versionado de banco de motor y generador sin FMOD (``src.audio.engine_gen``).

No necesitan corpus ni instalaciones: solo ``src/audio/data/skeleton_engine.*`` y audio sintético.
"""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from src.audio import bank_graph as bg
from src.audio import bank_patch as bp
from src.audio import engine_gen as eg
from src.audio.plan import placement, template


def _wav(path: Path, hz: float, n: int = 4410, rate: int = 44100) -> None:
    import math

    data = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate)))
        for i in range(n)
    )
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    hdr += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    path.write_bytes(hdr + b"data" + struct.pack("<I", len(data)) + data)


def synthetic_spec(wavdir: Path, car: str, rpms: list[float]) -> dict:
    tpl = template()
    eng = copy.deepcopy(tpl["engine"])
    files = set()
    tops = {"engine_ext": 20000.0, "engine_int": 15000.0}
    for en, ev in eng.items():
        ev["rpms_max"] = tops[en]
        for tr in ev["tracks"]:
            load = tr["name"].startswith("load")
            pl = placement(list(rpms), tops[en])
            for p in pl:
                f = wavdir / f"EXH_{int(p['rpm'])}{'_P' if load else ''}.wav"
                if not f.exists():
                    _wav(f, 100 + p["rpm"] / 10)
                p["file"] = str(f)
                files.add(p["file"])
            tr["name"] = f"{car} {tr['name']}"[:60]
            tr["sounds"] = pl
    return {
        "car": car,
        "cars_folder": tpl["cars_folder"],
        "bank_folder": tpl["bank_folder"],
        "fade_shape": tpl["fade_shape"],
        "engine": eng,
        "secondary": tpl["secondary"],
        "audio_files": sorted(files),
    }


class TestSkeleton(unittest.TestCase):
    def test_provenance_matches_files(self) -> None:
        prov = json.loads(
            (eg.DATA / "skeleton_provenance.json").read_text(encoding="utf-8")
        )
        raw, guids, sid = eg.skeleton()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), prov["bank_sha256"])
        self.assertEqual(
            hashlib.sha256(guids.encode("utf-8")).hexdigest(), prov["guids_sha256"]
        )
        self.assertEqual(sid, prov["car_id"])
        self.assertIn("SINTETICO", prov["contents"])

    def test_skeleton_has_no_third_party_audio(self) -> None:
        raw, _, _ = eg.skeleton()
        fsb = bp.bank_fsb(bp.parse_bank(raw)).fsb
        self.assertEqual(len(fsb.samples), 4)
        for s in fsb.samples:  # solo silencio sintético
            self.assertEqual(s.pcm().strip(b"\0"), b"")
        self.assertEqual(bg.check_consistency(raw), [])

    def test_generate_for_any_slot_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for rpms in (
                [803.0, 859.0],
                [803.0, 859.0, 919.0],
                [803.0 * 1.07**k for k in range(12)],
            ):
                spec = synthetic_spec(Path(tmp), f"beam2ac_t{len(rpms)}", rpms)
                raw, gtxt = eg.generate_from_skeleton(
                    spec, lambda f: bp.read_wav_mono16(Path(f))
                )
                self.assertEqual(bg.check_consistency(raw), [], len(rpms))
                self.assertEqual(bp.rebuild_bank(bp.parse_bank(raw)), raw)
                fsb = bp.bank_fsb(bp.parse_bank(raw)).fsb
                self.assertEqual(len(fsb.samples), 2 * len(rpms))
                self.assertIn(f"event:/cars/beam2ac_t{len(rpms)}/engine_ext", gtxt)
                self.assertIn(f"bank:/beam2ac_t{len(rpms)}", gtxt)

    def test_two_cars_do_not_share_guids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:

            def sp(c: str) -> dict:
                return synthetic_spec(Path(tmp), c, [803.0, 859.0, 919.0])

            _, g1 = eg.generate_from_skeleton(sp("beam2ac_a"))
            _, g2 = eg.generate_from_skeleton(sp("beam2ac_b"))

        def ev(g: str) -> set[str]:
            return {x.split()[0] for x in g.splitlines() if "event:/cars/" in x}

        self.assertTrue(ev(g1) and ev(g2))
        self.assertFalse(ev(g1) & ev(g2))

    def test_rejects_bad_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = synthetic_spec(Path(tmp), "beam2ac_x", [803.0, 859.0, 919.0])
            bad = copy.deepcopy(spec)
            for t in bad["engine"]["engine_ext"]["tracks"]:
                t["sounds"].append(dict(t["sounds"][-1]))
            with self.assertRaises(ValueError):
                eg.generate_from_skeleton(bad)
            one = synthetic_spec(Path(tmp), "beam2ac_y", [803.0, 859.0])
            for ev in one["engine"].values():
                for t in ev["tracks"]:
                    t["sounds"] = t["sounds"][:1]
            with self.assertRaises(ValueError):
                eg.generate_from_skeleton(one)


def _probe():
    try:
        from tools.audio import fmod_probe

        return fmod_probe if (fmod_probe.AC / "fmodstudio64.dll").is_file() else None
    except Exception:  # noqa: BLE001 - sin AC o sin DLL
        return None


@unittest.skipUnless(_probe() is not None, "falta el runtime FMOD de AC")
class TestSkeletonPlays(unittest.TestCase):
    def test_generated_bank_makes_sound(self) -> None:
        from src.safe_paths import ensure_safe_output_path

        fp = _probe()
        with tempfile.TemporaryDirectory() as tmp:
            d = ensure_safe_output_path(Path(tmp))
            spec = synthetic_spec(
                d, "beam2ac_snd", [803.0, 1000.0, 1300.0, 1800.0, 2500.0]
            )
            raw, gtxt = eg.generate_from_skeleton(
                spec, lambda f: bp.read_wav_mono16(Path(f))
            )
            bank = d / "beam2ac_snd.bank"
            bank.write_bytes(raw)
            gid = next(
                x.split()[0].strip("{}")
                for x in gtxt.splitlines()
                if x.endswith("event:/cars/beam2ac_snd/engine_ext")
            )
            for rpm in (900.0, 2000.0):
                out = d / f"o{int(rpm)}.wav"
                fp.run(
                    [fp.AC / "content/sfx/common.bank", bank],
                    gid,
                    {"rpms": rpm, "throttle": 1.0},
                    out,
                )
                self.assertGreater(fp.peak(out)[0], 0.01, rpm)


if __name__ == "__main__":
    unittest.main()
