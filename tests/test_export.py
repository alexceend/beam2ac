"""Fase B2: exportación aislada (``src/project/export.py``) y etapa ``model``.

Sin Blender por defecto: el flujo real del Ibiza se exporta con ``with_model=False``
(el KN5 falta y el veredicto debe ser «bloqueado»). La exportación con Blender se
activa con ``BEAM2AC_BLENDER_TESTS=1``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.ac.car_validate import read_ini_ac
from src.project import export as E
from src.project.manifest import SourceSpec, create_project

IBIZA = Path("sample_beamng/vehicles/seat_ibiza_acfr")


def quiet(_: str) -> None:
    pass


class FakeProject:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.id = "fake"


def pf(rel: str, text: str, **prov) -> E.PackageFile:
    return E.PackageFile(rel, text.encode("utf-8"), prov or {"test": True})


MIN_FILES = [
    pf("data/car.ini", "[INFO]\nSCREEN_NAME=X\n[BASIC]\nTOTALMASS=1000\n"),
    pf("data/engine.ini", "[HEADER]\nPOWER_CURVE=power.lut\n"),
    pf("data/power.lut", "0|10\n1000|20\n"),
    pf(
        "data/drivetrain.ini",
        "[GEARS]\nFINAL=4\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n",
    ),
    pf("data/suspensions.ini", "[REAR]\nTYPE=DWB\n"),
    pf("data/brakes.ini", "[DATA]\nMAX_TORQUE=2000\nFRONT_SHARE=0.6\n"),
    pf("data/tyres.ini", "[FRONT]\nWEAR_CURVE=w.lut\n"),
    pf("data/aero.ini", "[WING_0]\nNAME=BODY\n"),
    pf("data/setup.ini", "[DISPLAY_METHOD]\nSHOW_CLICKS=0\n"),
    pf("data/lods.ini", "[LOD_0]\nFILE=beam2ac_x.kn5\nIN=0\nOUT=1500\n"),
    pf("data/driver3d.ini", "[MODEL]\nNAME=driver\n"),
    pf("ui/ui_car.json", "{}"),
    pf("skins/default/ui_skin.json", "{}"),
]


class Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.project = FakeProject(self.tmp / "proj")
        self.project.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def export(self, files, issues=(), car_id="beam2ac_x") -> E.ExportResult:
        return E.write_export(
            self.project, list(files), list(issues), {"vehicle": "x/y"}, car_id, {}
        )


class TestValidation(Tmp):
    def test_missing_resources_and_broken_references_block(self) -> None:
        res = self.export(MIN_FILES)
        self.assertEqual(res.verdict, E.VERDICT_BLOCKED)
        text = " ".join(i.detail for i in res.errors)
        self.assertIn("w.lut", text)  # referencia rota de tyres.ini
        self.assertIn("beam2ac_x.kn5", text)  # lods.ini apunta a un KN5 ausente
        self.assertIn("collider.kn5", text)
        # lo opcional solo advierte
        codes = {i.code for i in res.warnings}
        self.assertIn("paquete.opcional_ausente", codes)
        self.assertFalse(any(i.code == "paquete.opcional_ausente" for i in res.errors))

    def test_missing_required_file_blocks(self) -> None:
        files = [f for f in MIN_FILES if f.rel != "data/brakes.ini"]
        res = self.export(files)
        self.assertTrue(any("brakes.ini" in i.detail for i in res.errors))

    def test_duplicate_paths_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "dos orígenes"):
            self.export(MIN_FILES + [pf("DATA/car.ini", "")])

    def test_bad_car_id_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.export(MIN_FILES, car_id="Coche Raro")
        self.assertEqual(E.default_car_id("Seat-Ibiza ACFR"), "beam2ac_seat_ibiza_acfr")

    def test_warnings_do_not_block(self) -> None:
        only_warning = [E.Issue("warning", "x", "aviso")]
        with mock.patch.object(E, "validate_package", return_value=only_warning):
            res = self.export(MIN_FILES)
        self.assertEqual(res.verdict, E.VERDICT_COMPLETE)
        self.assertEqual(res.errors, [])
        man = json.loads((res.dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(man["installed"])
        self.assertFalse(man["tested_in_game"])
        with mock.patch.object(E, "validate_package", return_value=[]):
            blocked = self.export(MIN_FILES, [E.Issue("error", "y", "falta algo")])
        self.assertEqual(blocked.verdict, E.VERDICT_BLOCKED)

    def test_blocker_severity(self) -> None:
        attrs = {"ac:suspensions.ini/REAR/TYPE": {"required_for_load": True}}
        sev = E._blocker_severity
        self.assertEqual(
            sev({"kind": "conflicto", "target": "ac:brakes.ini/DATA/FRONT_SHARE"}, {}),
            "error",
        )
        self.assertEqual(
            sev(
                {
                    "kind": "decisión_necesaria",
                    "target": "ac:suspensions.ini/REAR/TYPE",
                },
                attrs,
            ),
            "error",
        )
        self.assertEqual(
            sev({"kind": "decisión_necesaria", "target": "ac:tyres.ini/FRONT/X"}, {}),
            "warning",
        )


class TestAttempts(Tmp):
    def test_new_dir_each_attempt_sealed_and_deterministic(self) -> None:
        a = self.export(MIN_FILES)
        b = self.export(MIN_FILES)
        self.assertNotEqual(a.dir, b.dir)
        self.assertEqual((a.dir.name, b.dir.name), ("001", "002"))
        ma = json.loads((a.dir / "manifest.json").read_text(encoding="utf-8"))
        mb = json.loads((b.dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(ma, mb)  # lo volátil está solo en attempt.json
        for name in ("export_report.json", "export_report.md"):
            self.assertEqual(
                (a.dir / name).read_bytes(),
                (b.dir / name).read_bytes().replace(b"002", b"001"),
            )
        # sellada: otro proceso no puede escribir dentro (este, que la creó, sí)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from src.safe_paths import ensure_safe_output_path as e; "
                "e(sys.argv[1])",
                str(a.car_dir / "data" / "extra.ini"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(probe.returncode, 0)
        self.assertIn("ProtectedPathError", probe.stderr)
        for rel, meta in ma["files"].items():
            self.assertTrue(meta["provenance"], rel)

    def test_inspect_detects_changes(self) -> None:
        res = self.export(MIN_FILES)
        info = E.inspect_export(res.dir)
        self.assertTrue(info["intact"])
        self.assertEqual(info["verdict"], E.VERDICT_BLOCKED)
        (res.car_dir / "data" / "car.ini").write_text("x", encoding="utf-8")
        (res.car_dir / "data" / "nuevo.ini").write_text("x", encoding="utf-8")
        info = E.inspect_export(res.dir)
        self.assertFalse(info["intact"])
        self.assertEqual(info["changed"], ["data/car.ini"])
        self.assertEqual(info["unlisted"], ["data/nuevo.ini"])


class TestCatalogTypes(unittest.TestCase):
    def test_text_decisions_target_text_attributes(self) -> None:
        """Regresión: 6 atributos de tyres.ini declarados como número con valor de
        texto (SHORT_NAME, WEAR_CURVE, PERFORMANCE_CURVE); AC los lee como texto."""
        from src.attrs.store import Catalog

        cat = Catalog.load("catalog")
        bad = [
            d.target
            for d in cat.decisions
            if isinstance(d.value, str)
            and cat.attributes[d.target].dtype not in ("text", "enum")
        ]
        self.assertEqual(bad, [])


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestIbizaExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_without_model_is_blocked_and_traced(self) -> None:
        p = create_project(self.tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc"))
        res = E.export_project(p, with_model=False, log=quiet)
        self.assertEqual(res.verdict, E.VERDICT_BLOCKED)
        codes = {i.code for i in res.errors}
        self.assertIn("etapa.model", codes)
        targets = {i.target for i in res.errors}
        self.assertIn("ac:brakes.ini/DATA/FRONT_SHARE", targets)
        man = json.loads((res.dir / "manifest.json").read_text(encoding="utf-8"))
        # data/ sale de resolved_ini (atributos), nunca de los borradores
        for rel, meta in man["files"].items():
            if rel.startswith("data/") and rel not in (
                "data/lods.ini",
                "data/driver3d.ini",
                "data/ai.ini",
            ):
                if rel in ("data/power.lut", "data/engine.ini") and meta[
                    "provenance"
                ].get("turbo_lag"):
                    continue  # turbo con lag (src/ac/adjustable_turbo.py): sale de resolved_ini + transformación
                if rel == "data/setup.ini" and meta["provenance"].get("setup_items"):
                    continue  # presión de neumáticos (siempre) y reparto de frenada: plantilla de opciones sobre resolved_ini
                self.assertEqual(meta["provenance"]["stage"], "resolved_ini", rel)
        car = read_ini_ac(res.car_dir / "data" / "car.ini")
        self.assertEqual(car.sections["BASIC"]["TOTALMASS"], "1119.57029")
        self.assertNotIn("GRAPHICS", car.sections)  # sin modelo no hay ojos
        brakes = man["files"]["data/brakes.ini"]["provenance"]
        self.assertIn("ibz.base.brakes.DATA.MAX_TORQUE", brakes["decisions"])
        # determinismo: un segundo intento da el mismo manifiesto
        again = E.export_project(p, with_model=False, log=quiet)
        self.assertNotEqual(again.dir, res.dir)
        man2 = json.loads((again.dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man, man2)

    def test_editor_decision_reaches_export(self) -> None:
        """Una decisión del editor (ReviewService) sobre FRONT_SHARE llega al
        paquete y retira ese error; los demás bloqueos siguen."""
        from src.app.service import ReviewService
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        p = create_project(self.tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc"))
        svc = ReviewService(
            preview_physics(load_vehicle_dir(IBIZA, "35b84.pc")), p.root
        )
        svc.decide(
            {
                "action": "manual_value",
                "target": "ac:brakes.ini/DATA/FRONT_SHARE",
                "value": 0.6,
                "unit": "1",
                "justification": "prueba B2 (valor ficticio, solo en temporal)",
                "scope": "vehicle",
                "status": "provisional",
            }
        )
        res = E.export_project(p, with_model=False, log=quiet)
        brakes = read_ini_ac(res.car_dir / "data" / "brakes.ini").sections["DATA"]
        self.assertEqual(brakes["FRONT_SHARE"], "0.6")
        self.assertNotIn(
            "ac:brakes.ini/DATA/FRONT_SHARE", {i.target for i in res.errors}
        )
        self.assertEqual(res.verdict, E.VERDICT_BLOCKED)  # LUT y KN5 siguen faltando

    @unittest.skipUnless(
        os.environ.get("BEAM2AC_BLENDER_TESTS") == "1",
        "construcción con Blender desactivada (BEAM2AC_BLENDER_TESTS=1 para activarla)",
    )
    def test_export_with_model(self) -> None:
        p = create_project(self.tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc"))
        res = E.export_project(p, log=quiet)
        codes = {i.code for i in res.errors}
        self.assertNotIn("etapa.model", codes)
        self.assertNotIn("kn5.nulos", codes)
        self.assertNotIn("coherencia.ruedas", codes)
        self.assertTrue((res.car_dir / "beam2ac_seat_ibiza_acfr.kn5").is_file())
        car = read_ini_ac(res.car_dir / "data" / "car.ini")
        self.assertIn("DRIVEREYES", car.sections["GRAPHICS"])


if __name__ == "__main__":
    unittest.main()
