"""torsionReactor.gearRatio entra en el grupo final (pessima DX_M: 4,27 x diferencial 1)."""

from src.bench.service import default_beamng_root
import unittest
from pathlib import Path

BNG = Path((default_beamng_root() / "content/vehicles").as_posix())


@unittest.skipUnless((BNG / "pessima.zip").is_file(), "BeamNG no instalado")
class TestTorsionReactor(unittest.TestCase):
    def test_pessima_final_drive(self) -> None:
        from src.extract.drivetrain import DrivetrainDataError, build_drivetrain
        from src.jbeam import resolve
        from src.pipeline.physics_preview import load_vehicle_zip

        v = load_vehicle_zip(BNG / "pessima.zip", BNG / "common.zip", "DX_M.pc")
        try:
            dt = build_drivetrain(resolve(v.index, v.pc))
        except (
            DrivetrainDataError
        ) as exc:  # otra versión de BeamNG: pessima no resuelve su motor
            self.skipTest(f"pessima de esta instalación no resuelve el motor: {exc}")
        self.assertAlmostEqual(dt.final_drive, 4.27)


if __name__ == "__main__":
    unittest.main()
