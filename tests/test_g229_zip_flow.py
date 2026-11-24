"""G2.29: análisis y conversión directa desde un zip (mod sin descomprimir)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.app import diagnostics
from src.app.workspace import Workspace, WorkspaceError
from src.project.model import ModelError, extract_vehicle
from tests.test_app_desktop import wait
from tests.test_g228_convert_flow import OK, fake_export

ATLANTA = Path(__file__).parents[1] / "samples_beamng" / "2020_rpm_atlanta_gt_3_6t"


@unittest.skipUnless(ATLANTA.is_dir(), "muestra del Atlanta no disponible")
class TestZip(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.zip = cls.tmp / "atlanta.zip"
        with zipfile.ZipFile(cls.zip, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(ATLANTA.rglob("*")):
                if p.is_file() and p.suffix.lower() != ".wav":
                    z.write(p, p.relative_to(ATLANTA).as_posix())
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)

    def test_analysis_from_zip_equals_folder(self) -> None:
        z = diagnostics.inspect_vehicle(str(self.zip))
        d = diagnostics.inspect_vehicle(str(ATLANTA))
        self.assertTrue(z["ok"], z)
        self.assertEqual((z["kind"], d["kind"]), ("zip", "dir"))
        for k in (
            "vehicle",
            "configs",
            "identity",
            "blockers",
            "coverage",
            "components",
            "default_car_id",
            "exportable_today",
        ):
            self.assertEqual(z[k], d[k], k)

    def test_bad_zips_are_explained(self) -> None:
        empty = self.tmp / "vacio.zip"
        with zipfile.ZipFile(empty, "w") as z:
            z.writestr("leeme.txt", "x")
        with self.assertRaisesRegex(WorkspaceError, "0 vehículos"):
            diagnostics.find_source(empty)

    def test_convert_creates_a_zip_project(self) -> None:
        ws = Workspace(self.tmp / "projects", [], [], None)
        export = fake_export()
        with (
            mock.patch("src.project.export.default_ac_root", return_value=None),
            mock.patch("src.app.workspace._require_ac_root", return_value=str(self.tmp)),
            mock.patch("src.app.diagnostics.inspect_vehicle", return_value=OK),
            mock.patch("src.project.export.export_project", export),
        ):
            job = wait(
                ws, ws.convert({"path": str(self.zip), "car_id": "beam2ac_z"})["id"]
            )
        self.assertEqual(job["status"], "hecha", job)
        src = export.call_args.args[0].source
        self.assertEqual((src.kind, Path(src.path)), ("zip", self.zip))
        self.assertEqual(job["result"]["project"], "2020_rpm_atlanta_gt_3_6t")

    def test_extraction_refuses_members_outside_the_vehicle(self) -> None:
        bad = self.tmp / "malo.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("vehicles/x/ok.dae", "a")
            # «..» y «\» ya se descartan; la letra de OTRA unidad escaparía en Windows
            drive = "Q:" if self.tmp.drive.upper() != "Q:" else "R:"
            z.writestr(f"vehicles/x/{drive}/beam2ac_fuera.dae", "b")
        dest = self.tmp / "extraido"
        with self.assertRaisesRegex(ModelError, "fuera del vehículo"):
            extract_vehicle(bad, "vehicles/x/", dest)

    def test_extraction_takes_what_textures_and_paint_need(self) -> None:
        root = extract_vehicle(
            self.zip, "vehicles/2020_rpm_atlanta_gt_3_6t/", self.tmp / "ok"
        )
        self.assertTrue((root / "materials.cs").is_file())
        self.assertTrue(any(root.glob("*.pc")))


if __name__ == "__main__":
    unittest.main()
