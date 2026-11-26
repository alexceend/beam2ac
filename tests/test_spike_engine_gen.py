"""SPIKE: generador de bancos de motor sin FMOD (``bank_graph`` + ``engine_gen``).

Usan bancos construidos antes por FMOD en ``output/projects/*/audio`` (se omiten si no están).
La validación fuerte (comparación canónica con el banco que construye FMOD para el mismo spec)
se hizo con ``output/spike_self_transformations_2026-11-25/real_validate.py``.
"""

from __future__ import annotations

import json
import struct
import unittest
import uuid
from pathlib import Path

from src.audio import bank_graph as bg
from src.audio import bank_patch as bp
from src.audio import engine_gen as eg

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "output" / "projects"
DIRS = {
    "agus": P / "agus_1_nrt_1_cvt/audio/001_beam2ac_agus_cvt_t3",
    "autist": P / "autist_cuanto/audio/001_beam2ac_autist_cuanto",
}


def _pair(d: Path) -> dict:
    spec = json.loads((d / "spec.json").read_text(encoding="utf-8"))
    car = spec["car"]
    return {
        "spec": spec,
        "car": car,
        "bank": (d / "prebuilt" / f"{car}.bank").read_bytes(),
        "guids": (d / "prebuilt" / "GUIDs.txt").read_text(encoding="utf-8"),
    }


@unittest.skipUnless(
    all(
        (d / "spec.json").exists() and (d / "prebuilt").exists() for d in DIRS.values()
    ),
    "faltan los bancos construidos de referencia",
)
class TestEngineGen(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.a, cls.b = _pair(DIRS["agus"]), _pair(DIRS["autist"])

    def test_generate_matches_spec(self) -> None:
        raw, gtxt = eg.generate(
            self.a["bank"], self.a["guids"], self.a["car"], self.b["spec"]
        )
        self.assertEqual(bg.check_consistency(raw), [])
        events = {
            uuid.UUID(g.strip("{}")).bytes_le: n.split("/")[-1]
            for g, n in (ln.split(None, 1) for ln in gtxt.splitlines())
            if "event:/cars/" in n
        }
        self.assertIn(f"event:/cars/{self.b['car']}/engine_ext", gtxt)
        eb = bg.EngineBank(raw, {g: n.strip() for g, n in events.items()})
        for role, is_load in (("load", True), ("coast", False)):
            want = sorted(
                s["rpm"]
                for t in self.b["spec"]["engine"]["engine_ext"]["tracks"]
                if (" load" in t["name"]) == is_load
                for s in t["sounds"]
            )
            got = sorted(s.rpm for s in eb.slots() if s.name.endswith("_P") == is_load)
            self.assertEqual(got, want, role)
        # el GUID de cada evento es nuevo (no el del molde)
        old = {
            g.strip("{}")
            for g, _ in (x.split(None, 1) for x in self.a["guids"].splitlines())
        }
        new = {
            g.strip("{}")
            for g, n in (x.split(None, 1) for x in gtxt.splitlines())
            if f"event:/cars/{self.b['car']}/" in n
        }
        self.assertTrue(new)
        self.assertFalse(old & new)

    def test_clone_then_delete_is_neutral(self) -> None:
        eb = bg.EngineBank(self.a["bank"])
        n0 = len(eb.slots())
        src = max(eb.slots(), key=lambda s: s.rpm)
        new = eb.clone_slot(src)
        self.assertEqual(len(eb.slots()), n0 + 1)
        self.assertEqual(bg.check_consistency(eb.to_bytes()), [])
        eb.delete_slot(new)
        self.assertEqual(len(eb.slots()), n0)
        self.assertEqual(bg.check_consistency(eb.to_bytes()), [])

    def test_rpms_max_written(self) -> None:
        raw, _ = eg.generate(
            self.a["bank"], self.a["guids"], self.a["car"], self.b["spec"]
        )
        found = {
            struct.unpack_from("<f", c.data, 31)[0]
            for p, c in bp.iter_chunks(bp.parse_bank(raw).chunks)
            if p.endswith("PRMB") and b"rpms" in c.data
        }
        for ev in ("engine_ext", "engine_int"):
            self.assertIn(float(self.b["spec"]["engine"][ev]["rpms_max"]), found)


def _probe():
    try:
        from tools.audio import fmod_probe

        if not fmod_probe.available():
            return None
        return fmod_probe
    except Exception:  # noqa: BLE001 - sin instalación de AC
        return None


@unittest.skipUnless(
    all((d / "spec.json").exists() and (d / "prebuilt").exists() for d in DIRS.values())
    and _probe() is not None,
    "faltan bancos de referencia o el runtime FMOD de AC",
)
class TestPlays(unittest.TestCase):
    """Regresión del ``SNDH``: con otro número de ranuras el banco cargaba pero no sonaba."""

    def test_generated_bank_makes_sound(self) -> None:
        import tempfile

        fp = _probe()
        a, b = _pair(DIRS["agus"]), _pair(DIRS["autist"])
        raw, gtxt = eg.generate(a["bank"], a["guids"], a["car"], b["spec"])
        gid = next(
            g.strip("{}")
            for g, n in (ln.split(None, 1) for ln in gtxt.splitlines())
            if n.strip() == f"event:/cars/{b['car']}/engine_ext"
        )
        from src.safe_paths import ensure_safe_output_path

        with tempfile.TemporaryDirectory() as tmp:
            d = ensure_safe_output_path(Path(tmp))
            bank = d / f"{b['car']}.bank"
            bank.write_bytes(raw)
            out = d / "o.wav"
            fp.run(
                [fp.AC / "content/sfx/common.bank", bank],
                gid,
                {"rpms": 3000.0, "throttle": 1.0},
                out,
            )
            self.assertGreater(fp.peak(out)[0], 0.01)


if __name__ == "__main__":
    unittest.main()
