"""Fase B4: tablas LUT referenciadas (opción L1), valores V1 y decisión F1.

Ver docs/research/propuesta_frontshare_luts_2026-10-26.md.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.ac.car_validate import read_ini_ac
from src.attrs import units
from src.attrs.store import Catalog
from src.project import resolved_ini as R

IBIZA = Path("sample_beamng/vehicles/seat_ibiza_acfr")
WEAR = "ac:tyres.ini/FRONT/WEAR_CURVE"
WEAR_R = "ac:tyres.ini/REAR/WEAR_CURVE"
TC = "ac:tyres.ini/THERMAL_FRONT/PERFORMANCE_CURVE"
FLAT_WEAR = [[0, 100], [1000, 100]]
FLAT_TC = [[0, 1.0], [50, 1.0], [100, 1.0], [150, 1.0], [200, 1.0]]


def quiet(_: str) -> None:
    pass


def result(target: str, value: Any, unit: str, status: str = "inferred", **kw):
    base = {
        "target": target,
        "status": status,
        "value": value,
        "unit": unit,
        "source": "decision",
        "rule": "",
        "decision": f"d.{target}",
        "requires_approval": False,
        "confidence": "E1",
    }
    base.update(kw)
    return base


ATTRS = {
    WEAR: {"unit": "text", "dtype": "text"},
    WEAR_R: {"unit": "text", "dtype": "text"},
    WEAR + "/table": {"unit": "table:km->%", "dtype": "table"},
    WEAR_R + "/table": {"unit": "table:km->%", "dtype": "table"},
}


class TestSchema(unittest.TestCase):
    def test_catalog_table_attributes(self) -> None:
        cat = Catalog.load("catalog")
        targets = cat.targets["ac_physics"]
        expected = {
            "ac:tyres.ini/FRONT/WEAR_CURVE/table": "table:km->%",
            "ac:tyres.ini/REAR/WEAR_CURVE/table": "table:km->%",
            "ac:tyres.ini/THERMAL_FRONT/PERFORMANCE_CURVE/table": "table:°C->1",
            "ac:tyres.ini/THERMAL_REAR/PERFORMANCE_CURVE/table": "table:°C->1",
        }
        for tid, unit in expected.items():
            a = cat.attributes[tid]
            self.assertEqual((a.dtype, a.unit), ("table", unit))
            ref = R.table_reference(tid)
            self.assertEqual(cat.attributes[ref].dtype, "text")
            # la tabla va justo después de la clave que nombra el archivo
            self.assertEqual(targets.index(tid), targets.index(ref) + 1)
        # sin nombres de archivo de un coche concreto en el esquema
        for a in cat.attributes.values():
            self.assertNotIn("diag_flat", a.id)

    def test_table_reference_and_units(self) -> None:
        self.assertEqual(R.table_reference(WEAR + "/table"), WEAR)
        self.assertIsNone(R.table_reference("ac:power.lut"))
        self.assertIsNone(R.table_reference("ac:x/table"))
        self.assertEqual(units.convert(1, "km", "m"), 1000.0)
        units.validate_unit("table:°C->1")
        self.assertFalse(units.compatible("°C", "m"))


class TestTableStructure(unittest.TestCase):
    def test_valid_and_invalid(self) -> None:
        self.assertEqual(R.table_problems(FLAT_TC), [])
        for bad, why in (
            ([[0, 1]], "al menos 2"),
            ([[0, 1], [0, 2]], "creciente"),
            ([[1, 1], [0, 2]], "creciente"),
            ([[0, 1], [1, "x"]], "no numérico"),
            ([[0, 1], [1, float("nan")]], "no finito"),
            ([[0, 1], [1, True]], "no numérico"),
            ([[0, 1, 2], [1, 2]], "[x, y]"),
            ("0|1", "lista"),
        ):
            problems = R.table_problems(bad)
            self.assertTrue(problems, bad)
            self.assertIn(why, " ".join(problems), bad)
        with self.assertRaisesRegex(ValueError, "tabla inválida"):
            R.render_table([[0, 1], [0, 2]])

    def test_serialization_roundtrip(self) -> None:
        text = R.render_table(FLAT_TC)
        self.assertEqual(text, "0|1\n50|1\n100|1\n150|1\n200|1\n")
        self.assertEqual(R.parse_lut(text), [(x, float(y)) for x, y in FLAT_TC])
        self.assertEqual(
            R.parse_lut("; c\n0|100\n\n1000|100 ; fin\n"), [(0, 100), (1000, 100)]
        )
        for bad in ("0|1|2\n", "a|1\n", "0;1\n0 1\n"):
            with self.assertRaises(ValueError):
                R.parse_lut(bad)


class TestBuild(unittest.TestCase):
    def build(self, results):
        return R.build(results, ATTRS)

    def test_table_written_with_referenced_name(self) -> None:
        res = self.build(
            [
                result(WEAR, "flat_wear.lut", "text"),
                result(WEAR + "/table", FLAT_WEAR, "table:km->%"),
            ]
        )
        self.assertEqual(res.blockers, [])
        self.assertEqual(res.files["flat_wear.lut"], "0|100\n1000|100\n")
        self.assertIn("WEAR_CURVE=flat_wear.lut", res.files["tyres.ini"])
        tr = next(t for t in res.traces if t.target == WEAR + "/table")
        self.assertEqual((tr.file, tr.section, tr.key), ("flat_wear.lut", None, None))
        self.assertEqual(tr.decision, f"d.{WEAR}/table")

    def test_shared_file_same_table_once_two_traces(self) -> None:
        res = self.build(
            [
                result(WEAR, "w.lut", "text"),
                result(WEAR_R, "w.lut", "text"),
                result(WEAR + "/table", FLAT_WEAR, "table:km->%"),
                result(WEAR_R + "/table", FLAT_WEAR, "table:km->%"),
            ]
        )
        self.assertEqual(res.blockers, [])
        self.assertEqual([t.file for t in res.traces].count("w.lut"), 2)

    def test_shared_file_different_tables_is_conflict(self) -> None:
        res = self.build(
            [
                result(WEAR, "w.lut", "text"),
                result(WEAR_R, "w.lut", "text"),
                result(WEAR + "/table", FLAT_WEAR, "table:km->%"),
                result(WEAR_R + "/table", [[0, 100], [10, 90]], "table:km->%"),
            ]
        )
        self.assertNotIn("w.lut", res.files)
        kinds = {(b.target, b.kind) for b in res.blockers}
        self.assertIn((WEAR + "/table", "conflicto"), kinds)
        self.assertIn((WEAR_R + "/table", "conflicto"), kinds)
        self.assertIn((WEAR, "archivo"), kinds)  # la clave apunta a algo no escrito

    def test_unresolved_or_bad_reference_blocks(self) -> None:
        for ref in (
            result(WEAR, None, "text", status="manual_required"),
            result(WEAR, "../x.lut", "text"),
            result(WEAR, "x.ini", "text"),
        ):
            res = self.build([ref, result(WEAR + "/table", FLAT_WEAR, "table:km->%")])
            self.assertFalse(any(f.endswith(".lut") for f in res.files), ref)
            self.assertIn(
                (WEAR + "/table", "archivo"), {(b.target, b.kind) for b in res.blockers}
            )

    def test_invalid_table_or_unit_blocks(self) -> None:
        res = self.build(
            [
                result(WEAR, "w.lut", "text"),
                result(WEAR + "/table", [[0, 100], [0, 90]], "table:km->%"),
            ]
        )
        self.assertNotIn("w.lut", res.files)
        b = next(b for b in res.blockers if b.target == WEAR + "/table")
        self.assertEqual(b.kind, "valor")
        self.assertIn("creciente", b.detail)
        res = self.build(
            [
                result(WEAR, "w.lut", "text"),
                result(WEAR + "/table", FLAT_WEAR, "table:m->%"),
            ]
        )
        self.assertEqual(
            next(b for b in res.blockers if b.target == WEAR + "/table").kind,
            "unidad",
        )


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestDecisionsReachExport(unittest.TestCase):
    """F1 + V1 registradas con la interfaz (ReviewService) en un proyecto temporal."""

    def setUp(self) -> None:
        from src.app.service import ReviewService
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics
        from src.project.manifest import SourceSpec, create_project

        self.tmp = Path(tempfile.mkdtemp())
        self.project = create_project(
            self.tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc")
        )
        self.svc = ReviewService(
            preview_physics(load_vehicle_dir(IBIZA, "35b84.pc")), self.project.root
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def decide(self, target: str, value: Any, unit: str, **kw) -> dict:
        return self.svc.decide(
            {
                "action": "manual_value",
                "target": target,
                "value": value,
                "unit": unit,
                "justification": "prueba B4 (proyecto temporal)",
                "scope": "vehicle",
                "status": "provisional",
                **kw,
            }
        )

    def test_invalid_values_refused(self) -> None:
        from src.app.service import ServiceError

        with self.assertRaisesRegex(ServiceError, "tabla inválida"):
            self.decide(WEAR + "/table", [[0, 100], [0, 90]], "table:km->%")
        with self.assertRaisesRegex(ServiceError, "incompatible"):
            self.decide(WEAR + "/table", FLAT_WEAR, "table:°C->1")
        with self.assertRaisesRegex(ServiceError, "evidencia"):
            self.decide(
                WEAR + "/table", FLAT_WEAR, "table:km->%", evidence=[{"type": "file"}]
            )
        self.assertFalse(any(self.project.decisions_dir.glob("*.jsonl")))

    def test_f1_v1_reach_package(self) -> None:
        from src.project import export as E

        ev = [{"type": "file", "path": "x/diag_flat_wear.lut", "sha256": "abc"}]
        out = self.decide("ac:brakes.ini/DATA/FRONT_SHARE", 0.857, "1")
        self.assertEqual(out["result"]["status"], "inferred")
        for sec in ("FRONT", "REAR"):
            d = self.decide(
                f"ac:tyres.ini/{sec}/WEAR_CURVE/table",
                FLAT_WEAR,
                "table:km->%",
                evidence=ev,
            )
            self.assertEqual(d["decision"]["evidence"][-1]["sha256"], "abc")
            self.decide(
                f"ac:tyres.ini/THERMAL_{sec}/PERFORMANCE_CURVE/table",
                FLAT_TC,
                "table:°C->1",
            )
        res = E.export_project(self.project, with_model=False, log=quiet)
        data = res.car_dir / "data"
        brakes = read_ini_ac(data / "brakes.ini").sections["DATA"]
        self.assertEqual(
            (brakes["FRONT_SHARE"], brakes["MAX_TORQUE"]), ("0.857", "2536.2")
        )
        self.assertEqual(
            (data / "diag_flat_wear.lut").read_text(encoding="utf-8"),
            "0|100\n1000|100\n",
        )
        self.assertEqual(
            R.parse_lut((data / "diag_flat_tcurve.lut").read_text(encoding="utf-8")),
            [(x, float(y)) for x, y in FLAT_TC],
        )
        man = json.loads((res.dir / "manifest.json").read_text(encoding="utf-8"))
        prov = man["files"]["data/diag_flat_wear.lut"]["provenance"]
        self.assertEqual(prov["stage"], "resolved_ini")
        self.assertEqual(len(prov["decisions"]), 2)  # delantero y trasero
        # sin modelo solo faltan el KN5 y lo que depende de él
        targets = {i.target for i in res.errors}
        self.assertNotIn("ac:brakes.ini/DATA/FRONT_SHARE", targets)
        self.assertFalse(any("lut" in i.detail.lower() for i in res.errors), res.errors)
        self.assertIn("etapa.model", {i.code for i in res.errors})


class TestExportLutValidation(unittest.TestCase):
    def test_bad_lut_in_package_is_error(self) -> None:
        from src.project import export as E

        tmp = Path(tempfile.mkdtemp())
        try:
            car = tmp / "beam2ac_x"
            (car / "data").mkdir(parents=True)
            (car / "data" / "tyres.ini").write_text(
                "[FRONT]\nWEAR_CURVE=w.lut\n", encoding="utf-8"
            )
            (car / "data" / "w.lut").write_text("0|100\n0|90\n", encoding="utf-8")
            issues = E.validate_package(car, "beam2ac_x.kn5")
            lut = [i for i in issues if i.code == "paquete.lut"]
            self.assertEqual(len(lut), 1)
            self.assertEqual(lut[0].severity, "error")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
