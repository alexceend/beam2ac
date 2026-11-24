"""Paquetes defectuosos de extremo a extremo (G2.42): cada defecto inyectado en una copia de una build real debe dar error."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from src.project.export import validate_package

ROOT = Path(__file__).resolve().parents[1]
BUILDS = sorted((ROOT / "builds").glob("beam2ac_g2*_*/beam2ac_*"))
BASES = [b for b in BUILDS if (b / "data/drivetrain.ini").is_file() and "ibiza" not in b.name]


def sub(path: Path, pat: str, rep: str) -> None:
    txt = path.read_text(encoding="utf-8")
    new, n = re.subn(pat, rep, txt, count=1, flags=re.M)
    assert n, f"el defecto no se pudo inyectar en {path.name}: {pat}"
    path.write_text(new, encoding="utf-8")


DEFECTS = {
    "sin_aero": lambda d: (d / "data/aero.ini").unlink(),
    "sin_FINAL": lambda d: sub(d / "data/drivetrain.ini", r"^FINAL=.*\n", ""),
    "frenos_sin_MAX_TORQUE": lambda d: sub(d / "data/brakes.ini", r"^MAX_TORQUE=.*\n", ""),
    "FRONT_SHARE_texto": lambda d: sub(d / "data/brakes.ini", r"^FRONT_SHARE=.*", "FRONT_SHARE=abc"),
    "sin_power_lut": lambda d: (d / "data/power.lut").unlink(),
    "marcha_a_cero": lambda d: sub(d / "data/drivetrain.ini", r"^GEAR_1=.*", "GEAR_1=0"),
    "TOTALMASS_cero": lambda d: sub(d / "data/car.ini", r"^TOTALMASS=.*", "TOTALMASS=0"),
}


class TestDefectivePackages(unittest.TestCase):
    def setUp(self):
        if not BASES:
            self.skipTest("sin builds")

    def _run(self, base: Path, defect: str):
        with tempfile.TemporaryDirectory() as t:
            car = Path(t) / base.name
            shutil.copytree(base, car)
            kn5 = f"{base.name}.kn5"
            clean = [i for i in validate_package(car, kn5) if i.severity == "error"]
            DEFECTS[defect](car)
            broken = [i for i in validate_package(car, kn5) if i.severity == "error"]
            return clean, broken

    def test_each_defect_is_an_error_not_a_pass(self):
        base = BASES[0]
        for name in DEFECTS:
            with self.subTest(defect=name):
                clean, broken = self._run(base, name)
                self.assertGreater(len(broken), len(clean), f"{name}: sin error nuevo; base={[i.code for i in clean]}")


if __name__ == "__main__":
    unittest.main()
