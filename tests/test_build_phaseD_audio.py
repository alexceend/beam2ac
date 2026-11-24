"""Variante de audio de la Fase D (tools/variants/build_phaseD_audio.py)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from src.ac.ingame_package import tree_hashes
from src.ac.kn5_write import box_mesh, write_simple_kn5
from src.safe_paths import PROJECT_ROOT, ProtectedPathError

_spec = importlib.util.spec_from_file_location(
    "build_phaseD_audio", PROJECT_ROOT / "tools/variants/build_phaseD_audio.py"
)
bpa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bpa)
CAR = bpa.CAR_ID
DATA = {
    "car.ini": "[BASIC]\nTOTALMASS=1000\n",
    "engine.ini": "[HEADER]\nPOWER_CURVE=power.lut\n",
    "power.lut": "1000|100\n",
    # MAX_TORQUE y FINAL como en un paquete aprobado real (base ibiza_v18bu); sin
    # ellos el validador marca el coche sin frenos/tracción desde 2026-10-26
    "drivetrain.ini": "[TRACTION]\nTYPE=FWD\n[GEARS]\nFINAL=5.98\n"
    "[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n",
    "suspensions.ini": "[FRONT]\nTYPE=STRUT\n[REAR]\nTYPE=DWB\n",
    "brakes.ini": "[DATA]\nFRONT_SHARE=0.8\nMAX_TORQUE=2536.2\n",
    "tyres.ini": "[FRONT]\nNAME=x\n",
    "lods.ini": "[LOD_0]\nFILE=car.kn5\nIN=0\nOUT=1\n",
    "setup.ini": "[DISPLAY_METHOD]\nSHOW_CLICKS=0\n",
    "driver3d.ini": "[MODEL]\nNAME=driver\n",
    "aero.ini": "[WING_0]\nNAME=BODY\n",
    "lights.ini": "[HEADER]\nVERSION=2\n",
    "dash_cam.ini": "[DASH_CAM]\nPOS=0, 1, 0\nEXP=26\n",
}
AUDIO = {
    "data/sounds.ini": b"[ENGINE]\r\nPOSITION=front\r\n",
    f"sfx/{CAR}.bank": b"RIFF\x00\x00\x00\x00FEV fake-bank",
    "sfx/GUIDs.txt": (
        f"{{a}} bank:/{CAR}\r\n{{b}} event:/cars/{CAR}/engine_ext\r\n" "{c} bus:/\r\n"
    ).encode(),
}


def car_dir(root: Path, name: str, data: dict[str, str], audio: bool) -> Path:
    car = root / name
    for f, text in data.items():
        (car / "data").mkdir(parents=True, exist_ok=True)
        (car / "data" / f).write_text(text, encoding="utf-8")
    write_simple_kn5(
        car / "car.kn5",
        [box_mesh("BODY", (-1, 0, -2), (1, 1, 2))],
        [("m", "ksPerPixel")],
    )
    (car / "ui").mkdir()
    (car / "ui" / "ui_car.json").write_text(
        json.dumps({"name": "Car"}), encoding="utf-8"
    )
    (car / "skins" / "s").mkdir(parents=True)
    (car / "skins" / "s" / "ui_skin.json").write_text("{}", encoding="utf-8")
    if audio:
        for rel, body in AUDIO.items():
            (car / rel).parent.mkdir(parents=True, exist_ok=True)
            (car / rel).write_bytes(body)
    return car


def sealed_pkg(root: Path, data=DATA, audio=AUDIO) -> Path:
    """Paquete de audio de referencia (como pkg_v16sn_sound) con su manifest.json."""
    pkg = root
    car = car_dir(pkg, CAR, data, audio=False)
    for rel, body in audio.items():
        (car / rel).parent.mkdir(parents=True, exist_ok=True)
        (car / rel).write_bytes(body)
    (pkg / "manifest.json").write_text(
        json.dumps({"package_dir": str(car), "files": tree_hashes(car)}),
        encoding="utf-8",
    )
    return pkg


class TestBuildPhaseDAudio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = car_dir(
            self.tmp / "assembly", "seat_ibiza_acfr", DATA, audio=False
        )
        self.v16 = sealed_pkg(self.tmp / "pkg_v16")
        self.v18 = sealed_pkg(self.tmp / "pkg_v18")

    def tearDown(self):
        self._tmp.cleanup()

    def _build(self, out="out", audio=None, **kw):
        return bpa.build(
            self.source, audio or [self.v16, self.v18], self.tmp / out, **kw
        )

    def test_builds_with_byte_identical_audio(self):
        src_before = tree_hashes(self.source)
        ref_before = tree_hashes(self.v16)
        m = self._build(reference_pkg=self.v18)
        pkg = Path(m["package_dir"])
        self.assertEqual(pkg.name, CAR)
        for rel, body in AUDIO.items():
            self.assertEqual((pkg / rel).read_bytes(), body, rel)
        self.assertTrue(all(m["checks"].values()), m["checks"])
        self.assertEqual(tree_hashes(pkg), m["files"])
        self.assertEqual(m["known_gaps"], [])
        self.assertIn("pendiente de prueba manual", m["audio"]["status"])
        self.assertEqual(tree_hashes(self.source), src_before)
        self.assertEqual(tree_hashes(self.v16), ref_before)
        # mismo contenido que la referencia salvo el sufijo de la interfaz
        self.assertEqual(m["reference_comparison"]["different"], ["ui/ui_car.json"])
        self.assertEqual(m["reference_comparison"]["only_in_reference"], [])

    def test_tampered_reference_audio_refused_without_output(self):
        (self.v16 / CAR / "sfx" / f"{CAR}.bank").write_bytes(b"otro banco")
        with self.assertRaises(bpa.VariantError):
            self._build()
        self.assertFalse((self.tmp / "out").exists())

    def test_reference_packages_must_agree(self):
        other = sealed_pkg(
            self.tmp / "pkg_other",
            audio={**AUDIO, "data/sounds.ini": b"[ENGINE]\r\nPOSITION=rear\r\n"},
        )
        with self.assertRaisesRegex(bpa.VariantError, "difiere"):
            self._build(audio=[self.v16, other])
        self.assertFalse((self.tmp / "out").exists())

    def test_existing_output_refused_and_untouched(self):
        (self.tmp / "out").mkdir()
        (self.tmp / "out" / "keep.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ProtectedPathError):
            self._build()
        self.assertEqual(
            sorted(p.name for p in (self.tmp / "out").iterdir()), ["keep.txt"]
        )

    def test_failed_validation_removes_only_new_folder(self):
        (self.source / "data" / "aero.ini").unlink()  # requisito de carga ausente
        with self.assertRaisesRegex(bpa.VariantError, "comprobación fallida"):
            self._build()
        self.assertFalse((self.tmp / "out").exists())
        self.assertTrue(self.v16.is_dir() and self.source.is_dir())

    def test_source_with_audio_refused(self):
        src = car_dir(self.tmp / "a2", "seat_ibiza_acfr", DATA, audio=True)
        with self.assertRaisesRegex(bpa.VariantError, "ya tiene audio"):
            bpa.build(src, [self.v16], self.tmp / "out2")


if __name__ == "__main__":
    unittest.main()
