"""Plan de mallas con .dae leídos del zip (B2): igual que la carpeta; zip intacto."""

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.mesh.build import plan_for_vehicle, plan_for_zip
from src.mesh.dae import read_dae
from src.jbeam import ZipMember

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
SPICY = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods/spicy_mandala.zip"


def summary(plan):
    return (
        [
            (o.name, o.mesh, o.part, o.parent, o.vertex_count, o.matrix)
            for o in plan.objects
        ],
        [(e.name, e.wheel, e.matrix, e.parent) for e in plan.empties],
        plan.wheel_centers,
        sorted(plan.unit_meter.values()),
        sorted(plan.up_axis.values()),
    )


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestIbizaZipEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.zip = Path(cls.tmp.name) / "seat_ibiza_acfr.zip"
        with zipfile.ZipFile(cls.zip, "w") as zf:
            for p in sorted(IBIZA.rglob("*")):
                if p.suffix.lower() in (".jbeam", ".pc", ".dae"):
                    zf.write(p, p.relative_to(IBIZA.parent.parent).as_posix())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_same_plan_from_folder_and_zip(self):
        before = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        a = plan_for_vehicle(IBIZA, IBIZA / "35b84.pc")
        b = plan_for_zip(self.zip)
        self.assertEqual(summary(a), summary(b))
        self.assertEqual(hashlib.sha256(self.zip.read_bytes()).hexdigest(), before)

    def test_read_dae_member(self):
        member = next(
            m for m in zipfile.ZipFile(self.zip).namelist() if m.endswith(".dae")
        )
        dae = read_dae(ZipMember(self.zip, member))
        self.assertEqual((dae.unit_meter, dae.up_axis), (0.01, "Z_UP"))
        self.assertTrue(dae.path.name.endswith(".dae"))


@unittest.skipUnless(SPICY.is_file(), "spicy_mandala no instalado")
class TestSpicyZip(unittest.TestCase):
    def test_plan_without_extracting(self):
        before = hashlib.sha256(SPICY.read_bytes()).hexdigest()
        plan = plan_for_zip(SPICY)
        self.assertEqual(len(plan.empties), 8)
        self.assertEqual(
            sorted(plan.wheel_centers), ["WHEEL_LF", "WHEEL_LR", "WHEEL_RF", "WHEEL_RR"]
        )
        self.assertEqual(set(plan.unit_meter.values()), {0.01})
        self.assertEqual(hashlib.sha256(SPICY.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
