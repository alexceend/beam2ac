"""Regresión y reproducibilidad (fase 9): fallos sin resultados válidos parciales, datos
originales
intactos, informes que identifican lo pendiente y resultados repetibles."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.attrs.__main__ import main as attrs_main
from src.attrs.report import build_report, resolve_vehicle
from src.attrs.store import REPO_CATALOG, Catalog
from src.catalog.ac_refs import DEFAULT_AC_CARS, build_index, scan
from src.pipeline.physics_preview import (
    load_vehicle_dir,
    preview_physics,
    rendered_keys,
)
from tests.jbeam_fixtures import simple_car

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
RUN3 = ROOT / "output/spicy_mandala/attribute_inventory_2026-10-25/run3/inventory.json"


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class TestFailuresAreNeverValid(unittest.TestCase):
    """Un vehículo sin motor: la vista previa NO es válida y el informe lo dice."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.veh = Path(self.tmp.name) / "roto"
        self.veh.mkdir()
        (self.veh / "roto.jbeam").write_text(json.dumps(simple_car()), encoding="utf-8")
        (self.veh / "c.pc").write_text(
            json.dumps({"format": 2, "parts": {}, "vars": {}}), encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_flags_failure(self):
        pv = preview_physics(load_vehicle_dir(self.veh, "c.pc"))
        self.assertFalse(pv.ok)
        self.assertIn("engine_model", pv.failed_stages)
        self.assertFalse(pv.stages["drivetrain"]["ok"])
        self.assertNotIn("engine.ini", pv.rendered)

    def test_cli_exit_code_and_report(self):
        out = Path(self.tmp.name) / "informe"
        code = attrs_main(
            [
                "resolve",
                "--vehicle-dir",
                str(self.veh),
                "--pc",
                "c.pc",
                "--out",
                str(out),
            ]
        )
        self.assertEqual(code, 2)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["preview_ok"])
        res = {r["target"]: r for r in report["results"]}
        for target in ("ac:power.lut", "ac:engine.ini/ENGINE_DATA/LIMITER"):
            self.assertEqual(
                res[target]["status"], "manual_required"
            )  # nada del motor se da por resuelto
        self.assertIn(
            "vista previa válida: False",
            (out / "summary.md").read_text(encoding="utf-8"),
        )


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestIbizaRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = tree_hash(IBIZA)
        cls.pv = preview_physics(load_vehicle_dir(IBIZA, "35b84.pc"), 70.0)
        cls.cat = Catalog.load(REPO_CATALOG)
        cls.ctx, cls.res = resolve_vehicle(cls.pv, cls.cat)

    def test_sample_unchanged(self):
        self.assertEqual(tree_hash(IBIZA), self.before)

    def test_reference_values(self):
        f = self.ctx.facts
        self.assertAlmostEqual(f["bng.mass.total"].value, 1226.27, places=2)
        self.assertAlmostEqual(f["bng.engine.peak_torque"].value, 307.8, places=2)
        self.assertEqual(f["bng.engine.rpm_ceiling"].value, 10000)
        self.assertEqual(f["bng.drivetrain.traction"].value, "FWD")
        self.assertEqual(
            [f[f"bng.gearbox.forward_ratio_{k}"].value for k in range(1, 7)],
            [3.14, 1.87, 1.3, 0.99, 0.8, 0.66],
        )

    @unittest.skipUnless(RUN3.is_file(), "evidencia run3 no disponible")
    def test_preview_reproduces_documented_inventory(self):
        run3 = json.loads(RUN3.read_text(encoding="utf-8"))
        self.assertEqual(rendered_keys(self.pv), run3["control"]["rendered_keys"])

    def test_report_identifies_unresolved(self):
        report = build_report(self.pv, self.ctx, self.res, self.cat)
        self.assertEqual(
            sum(report["summary"]["by_status"].values()), report["summary"]["targets"]
        )
        for r in report["results"]:
            with self.subTest(r["target"]):
                if r["status"] == "manual_required":
                    self.assertTrue(
                        r["reasons"] or r["rejected"]
                    )  # siempre explica por qué
                    self.assertNotEqual(r["source"], "rule")
                elif r["status"] != "not_applicable":
                    self.assertIsNotNone(r["value"])
                    self.assertTrue(r["source"])

    def test_report_is_deterministic(self):
        a = json.dumps(
            build_report(self.pv, self.ctx, self.res, self.cat),
            sort_keys=True,
            default=str,
        )
        ctx2, res2 = resolve_vehicle(self.pv, Catalog.load(REPO_CATALOG))
        b = json.dumps(
            build_report(self.pv, ctx2, res2, self.cat), sort_keys=True, default=str
        )
        self.assertEqual(a, b)


@unittest.skipUnless(DEFAULT_AC_CARS.is_dir(), "Assetto Corsa no instalado")
class TestCatalogReproducible(unittest.TestCase):
    def test_index_is_deterministic(self):
        a = json.dumps(build_index(scan(DEFAULT_AC_CARS)), sort_keys=True)
        b = json.dumps(build_index(scan(DEFAULT_AC_CARS)), sort_keys=True)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
