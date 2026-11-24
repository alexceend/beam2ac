"""Etapa ``resolved_ini`` (fase B1): INI de AC desde los atributos resueltos.

Lógica pura con resultados sintéticos (estados, unidades, conflictos, trazabilidad,
nada inventado) y flujo real con la muestra del Ibiza (decisión del editor → INI,
caché e invalidación, comparación con una referencia).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.ac.car_validate import read_ini_ac
from src.project import resolved_ini as R
from src.project.manifest import SourceSpec, create_project
from src.project.orchestrator import run_project

IBIZA = Path("sample_beamng/vehicles/seat_ibiza_acfr")
BASE_DATA = Path(
    "output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25/seat_ibiza_acfr/data"
)


def quiet(_: str) -> None:
    pass


def attr(unit: str, dtype: str = "number") -> dict[str, Any]:
    return {"unit": unit, "dtype": dtype}


def result(
    target: str, status: str = "known", value: Any = 1.0, unit: str = "m", **kw: Any
) -> dict[str, Any]:
    base = {
        "target": target,
        "status": status,
        "value": value,
        "unit": unit,
        "source": "rule",
        "rule": "r.test@1",
        "decision": "",
        "requires_approval": False,
        "transform": None,
        "inputs": {"x": "bng:test"},
        "confidence": "E2",
        "assumptions": [],
        "open_issues": [],
        "reasons": [],
    }
    base.update(kw)
    return base


T = "ac:suspensions.ini/FRONT/"


class TestBuild(unittest.TestCase):
    def test_epistemic_distinction_preserved(self) -> None:
        res = R.build(
            [
                result(T + "A", "known"),
                result(T + "B", "inferred"),
                result(
                    T + "C", "inferred", source="decision", rule="", decision="dec.c"
                ),
                result(T + "D", "format_requirement"),
            ],
            {T + k: attr("m") for k in "ABCD"},
        )
        self.assertEqual(
            [t.epistemic for t in res.traces],
            ["confirmado", "inferido", "decidido", "requisito_de_formato"],
        )
        self.assertEqual(res.blockers, [])

    def test_manual_required_is_blocker_not_zero(self) -> None:
        res = R.build(
            [
                result(T + "A"),
                result(
                    T + "B", "manual_required", value=None, reasons=["ninguna regla"]
                ),
            ],
            {T + "A": attr("m"), T + "B": attr("m")},
        )
        text = res.files["suspensions.ini"]
        self.assertIn("A=1\n", text)
        self.assertNotIn("B=", text)  # ausente: nunca 0 ni otro valor por defecto
        self.assertEqual(
            [(b.target, b.kind) for b in res.blockers],
            [(T + "B", "decisión_necesaria")],
        )

    def test_conflicts_and_proposals_not_written(self) -> None:
        res = R.build(
            [
                result(T + "A", "ambiguous", value=None, open_issues=["dos reglas"]),
                result(T + "B", "inferred", value=2.0, requires_approval=True),
                result(T + "C", "inferred", value=None),
            ],
            {T + k: attr("m") for k in "ABC"},
        )
        self.assertEqual(res.files, {})
        self.assertEqual(
            [b.kind for b in res.blockers],
            ["conflicto", "propuesta_pendiente", "valor"],
        )

    def test_unit_conversion_and_incompatibility(self) -> None:
        res = R.build(
            [
                result(T + "A", value=250.0, unit="mm"),
                result(T + "B", value=3.0, unit="kg"),
                result(T + "C", value=[10.0, 20.0], unit="cm"),
                result(T + "D", value=[[0, 1], [1, 2]], unit="rpm|N·m"),
            ],
            {
                T + "A": attr("m"),
                T + "B": attr("m"),
                T + "C": attr("m", "vector"),
                "ac:power.lut": attr("rpm|N·m", "table"),
            },
        )
        self.assertIn("A=0.25\n", res.files["suspensions.ini"])
        self.assertIn("C=0.1, 0.2\n", res.files["suspensions.ini"])
        self.assertNotIn("B=", res.files["suspensions.ini"])
        kinds = {b.target: b.kind for b in res.blockers}
        self.assertEqual(kinds[T + "B"], "unidad")
        self.assertEqual(kinds[T + "D"], "valor")  # destino no catalogado
        tr = {t.target: t for t in res.traces}
        self.assertEqual((tr[T + "A"].attribute_unit, tr[T + "A"].unit), ("mm", "m"))

    def test_table_unit_must_match(self) -> None:
        res = R.build(
            [result("ac:power.lut", value=[[0, 1]], unit="rpm|N·m")],
            {"ac:power.lut": attr("rpm|kW", "table")},
        )
        self.assertEqual(res.files, {})
        self.assertEqual(res.blockers[0].kind, "unidad")

    def test_dtype_mismatch_blocks(self) -> None:
        res = R.build(
            [result(T + "A", value="diag.lut", unit="undocumented")],
            {T + "A": attr("undocumented", "number")},
        )
        self.assertEqual(res.files, {})
        self.assertEqual(res.blockers[0].kind, "valor")
        self.assertIn("catálogo", res.blockers[0].detail)

    def test_text_rejects_ini_syntax(self) -> None:
        for bad in ("a;b", "a\nb", "[X]", "a=b"):
            with self.assertRaises(ValueError):
                R.render_value(bad, "text")

    def test_referenced_file_must_be_generated(self) -> None:
        pc = "ac:engine.ini/HEADER/POWER_CURVE"
        attrs = {pc: attr("text", "text"), "ac:power.lut": attr("rpm|N·m", "table")}
        missing = R.build([result(pc, value="power.lut", unit="text")], attrs)
        self.assertEqual([b.kind for b in missing.blockers], ["archivo"])
        self.assertIn("POWER_CURVE=power.lut", missing.files["engine.ini"])
        ok = R.build(
            [
                result(pc, value="power.lut", unit="text"),
                result("ac:power.lut", value=[[0, 10.5], [1000, 20]], unit="rpm|N·m"),
            ],
            attrs,
        )
        self.assertEqual(ok.blockers, [])
        self.assertEqual(ok.files["power.lut"], "0|10.5\n1000|20\n")

    def test_every_written_key_traced_with_evidence(self) -> None:
        results = [
            result(T + "A", value=1.5),
            result(
                T + "B", "inferred", source="decision", rule="", decision="d.b", value=2
            ),
            result("ac:car.ini/BASIC/TOTALMASS", value=1000.0, unit="kg"),
        ]
        attrs = {
            T + "A": attr("m"),
            T + "B": attr("m"),
            "ac:car.ini/BASIC/TOTALMASS": attr("kg"),
        }
        ev = {"r.test@1": [{"type": "doc", "path": "x.md"}], "d.b": [{"path": "y"}]}
        res = R.build(results, attrs, ev)
        traced = {(t.file, t.section, t.key) for t in res.traces}
        tmp = Path(tempfile.mkdtemp())
        try:
            for name, text in res.files.items():
                (tmp / name).write_text(text, encoding="utf-8")
                ini = read_ini_ac(tmp / name)
                for sec, keys in ini.sections.items():
                    for k in keys:
                        self.assertIn((name, sec, k), traced)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for t in res.traces:
            self.assertTrue(t.rule or t.decision)
            self.assertTrue(t.evidence)
            self.assertTrue(t.unit and t.confidence and t.status)
        doc = res.to_trace_json()
        self.assertEqual(doc["schema"], "beam2ac.resolved_ini/1")
        json.dumps(doc)  # serializable

    def test_deterministic(self) -> None:
        results = [result(T + "A", value=0.1 + 0.2), result(T + "B", value=-0.0)]
        attrs = {T + "A": attr("m"), T + "B": attr("m")}
        a, b = R.build(results, attrs), R.build(results, attrs)
        self.assertEqual(a.files, b.files)
        self.assertIn("A=0.3\n", a.files["suspensions.ini"])
        self.assertIn("B=0\n", a.files["suspensions.ini"])

    def test_not_applicable_recorded(self) -> None:
        res = R.build(
            [result(T + "A", "not_applicable", value=None)], {T + "A": attr("m")}
        )
        self.assertEqual((res.files, res.not_applicable), ({}, [T + "A"]))


class TestCompare(unittest.TestCase):
    def test_classification(self) -> None:
        self.assertEqual(R.compare_value("1.5", "1.5"), "idéntico")
        self.assertEqual(R.compare_value("500", "500.0"), "mismo_valor_otro_formato")
        self.assertEqual(
            R.compare_value("0, -0.114", "0.0000, -0.1140"), "mismo_valor_otro_formato"
        )
        self.assertEqual(
            R.compare_value("1119.57029", "1119.6"), "igual_a_la_precisión"
        )
        self.assertEqual(R.compare_value("1119.5", "1119.6"), "diferente")
        self.assertEqual(R.compare_value("DG", "DH"), "diferente")
        self.assertEqual(R.compare_value("1, 2", "1"), "diferente")

    def test_tables(self) -> None:
        self.assertEqual(
            R.compare_tables("0|1\n", "0|1.0 ; c\n")["status"],
            "mismo_valor_otro_formato",
        )
        self.assertEqual(R.compare_tables("0|1\n", "0|1\n1|2\n")["status"], "diferente")


def _stage_files(run: dict[str, Any]) -> Path:
    return run["resolved_ini"].dir


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestIbizaResolvedIni(unittest.TestCase):
    """Flujo real (origen de solo lectura; proyecto en temporal)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_all(self, p) -> dict[str, Any]:
        from src.project.stages import STAGES

        return {r.name: r for r in run_project(p, STAGES, log=quiet)}

    def test_flow_trace_and_base_values(self) -> None:
        ref = BASE_DATA if BASE_DATA.is_dir() else None
        p = create_project(
            self.tmp / "ibz",
            SourceSpec("dir", str(IBIZA), "35b84.pc"),
            reference_data=ref,
        )
        res = self.run_all(p)
        self.assertEqual(res["resolved_ini"].status, "ok")
        d = res["resolved_ini"].dir
        trace = json.loads((d / "trace.json").read_text(encoding="utf-8"))
        brakes = read_ini_ac(d / "data" / "brakes.ini").sections["DATA"]
        # decisión de la base aprobada (decidido) y regla (inferido) en el INI
        self.assertEqual(brakes["MAX_TORQUE"], "2536.2")
        keys = {k["target"]: k for k in trace["keys"]}
        self.assertEqual(keys["ac:brakes.ini/DATA/MAX_TORQUE"]["epistemic"], "decidido")
        self.assertEqual(keys["ac:car.ini/BASIC/TOTALMASS"]["epistemic"], "inferido")
        # FRONT_SHARE es un conflicto abierto: no se escribe y el validador lo señala
        self.assertNotIn("FRONT_SHARE", brakes)
        kinds = {(b["target"], b["kind"]) for b in trace["blockers"]}
        self.assertIn(("ac:brakes.ini/DATA/FRONT_SHARE", "conflicto"), kinds)
        self.assertTrue(any(k == "validación" for _, k in kinds))
        # el conflicto con el generador (TOTALMASS del borrador) se informa
        self.assertIn(
            "ac:car.ini/BASIC/TOTALMASS",
            [c["target"] for c in trace["conflicts_with_generators"]],
        )
        comp_rep = json.loads(
            (res["report"].dir / "compatibility.json").read_text(encoding="utf-8")
        )
        self.assertEqual(comp_rep["resolved_ini"]["keys"], len(trace["keys"]))
        self.assertFalse(comp_rep["installable_package"])
        if ref is not None:
            comp = json.loads((d / "comparison.json").read_text(encoding="utf-8"))
            # G2.39: PRELOAD 200 (x2, decisión D-G227-IBIZA) frente a 100 de la base v18bu
            self.assertLessEqual(comp["counts"].get("diferente", 0), 1)
            self.assertIn("igual_a_la_precisión", comp["counts"])  # TOTALMASS
            self.assertTrue(comp["not_covered"])  # sin equivalencia completa
        # caché
        again = self.run_all(p)
        self.assertEqual({r.status for r in again.values()}, {"cached"})

    def test_editor_decision_reaches_ini_and_invalidates(self) -> None:
        from src.app.service import ReviewService
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        target = "ac:suspensions.ini/FRONT/BUMP_STOP_RATE"
        p = create_project(self.tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc"))
        first = self.run_all(p)
        d1 = first["resolved_ini"].dir
        front = read_ini_ac(d1 / "data" / "suspensions.ini").sections["FRONT"]
        self.assertNotIn("BUMP_STOP_RATE", front)  # sin decisión: ausente, no 0
        svc = ReviewService(
            preview_physics(load_vehicle_dir(IBIZA, "35b84.pc")), p.root
        )

        def decide(value: float) -> None:
            svc.decide(
                {
                    "action": "manual_value",
                    "target": target,
                    "value": value,
                    "unit": "N/m",
                    "justification": "prueba B1",
                    "scope": "vehicle",
                    "status": "provisional",
                }
            )

        decide(150000)
        second = self.run_all(p)
        self.assertEqual(second["physics"].status, "cached")
        self.assertEqual(second["resolved_ini"].status, "ok")
        d2 = second["resolved_ini"].dir
        self.assertNotEqual(d1, d2)
        self.assertTrue((d1 / "trace.json").is_file())  # la anterior no se toca
        front = read_ini_ac(d2 / "data" / "suspensions.ini").sections["FRONT"]
        self.assertEqual(front["BUMP_STOP_RATE"], "150000")
        trace = json.loads((d2 / "trace.json").read_text(encoding="utf-8"))
        k = next(x for x in trace["keys"] if x["target"] == target)
        self.assertEqual(k["epistemic"], "decidido")
        self.assertTrue(k["decision"])
        # una decisión distinta vuelve a invalidar y cambia el valor
        svc2 = ReviewService(
            preview_physics(load_vehicle_dir(IBIZA, "35b84.pc")), p.root
        )
        svc2.decide(
            {
                "action": "manual_value",
                "target": target,
                "value": 160000,
                "unit": "N/m",
                "justification": "prueba B1 (cambio)",
                "scope": "vehicle",
                "status": "provisional",
            }
        )
        third = self.run_all(p)
        self.assertEqual(third["resolved_ini"].status, "ok")
        front = read_ini_ac(third["resolved_ini"].dir / "data" / "suspensions.ini")
        self.assertEqual(front.sections["FRONT"]["BUMP_STOP_RATE"], "160000")

    def test_reference_change_invalidates(self) -> None:
        ref = self.tmp / "ref"
        ref.mkdir()
        (ref / "car.ini").write_text("[BASIC]\nTOTALMASS=1119.6\n", encoding="utf-8")
        p = create_project(
            self.tmp / "ibz",
            SourceSpec("dir", str(IBIZA), "35b84.pc"),
            reference_data=ref,
        )
        first = self.run_all(p)
        comp = json.loads(
            (first["resolved_ini"].dir / "comparison.json").read_text(encoding="utf-8")
        )
        mass = next(
            k for k in comp["keys"] if k["target"] == "ac:car.ini/BASIC/TOTALMASS"
        )
        self.assertEqual(mass["status"], "igual_a_la_precisión")
        self.assertIn("sin_archivo_de_referencia", comp["counts"])
        (ref / "car.ini").write_text("[BASIC]\nTOTALMASS=1200\n", encoding="utf-8")
        second = self.run_all(p)
        self.assertEqual(second["attributes"].status, "cached")
        self.assertEqual(second["resolved_ini"].status, "ok")
        comp = json.loads(
            (second["resolved_ini"].dir / "comparison.json").read_text(encoding="utf-8")
        )
        self.assertEqual(comp["counts"].get("diferente"), 1)


if __name__ == "__main__":
    unittest.main()
