"""Proyectos y orquestador (src/project): caché, reanudación, inmutabilidad."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.project.manifest import (
    ProjectError,
    SourceSpec,
    create_project,
    list_zip_configs,
    load_project,
)
from src.project.orchestrator import Stage, project_status, run_project
from src.safe_paths import ProtectedPathError

IBIZA = Path("sample_beamng/vehicles/seat_ibiza_acfr")


def quiet(_: str) -> None:
    pass


class Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.vehicle = self.tmp / "veh"
        self.vehicle.mkdir()
        (self.vehicle / "a.pc").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, name: str = "p"):
        return create_project(
            self.tmp / name, SourceSpec("dir", str(self.vehicle), "a.pc")
        )


class TestManifest(Tmp):
    def test_create_load_and_refuse_overwrite(self) -> None:
        p = self.project()
        self.assertEqual(load_project(p.root).source.pc, "a.pc")
        self.assertTrue((p.root / "decisions").is_dir())  # misma disposición que la UI
        with self.assertRaises(ProtectedPathError):
            create_project(p.root, SourceSpec("dir", str(self.vehicle), "a.pc"))

    def test_source_validation(self) -> None:
        with self.assertRaises(ProjectError):
            SourceSpec("dir", str(self.vehicle), "no.pc").validate()
        with self.assertRaises(ProjectError):
            SourceSpec("zip", str(self.tmp / "no.zip")).validate()
        with self.assertRaises(ProjectError):
            SourceSpec("otro", str(self.vehicle)).validate()

    def test_zip_configs_checked_at_creation(self) -> None:
        z = self.tmp / "car.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("vehicles/car/info.json", "{}")
            zf.writestr("vehicles/car/a.pc", "{}")
            zf.writestr("vehicles/car/b.pc", "{}")
        self.assertEqual(list_zip_configs(z), ["a.pc", "b.pc"])
        with self.assertRaisesRegex(ProjectError, "configuraciones"):
            SourceSpec("zip", str(z), "c.pc").validate()
        with self.assertRaisesRegex(ProjectError, "indica --pc"):
            SourceSpec("zip", str(z)).validate()
        SourceSpec("zip", str(z), "b.pc").validate()
        self.assertFalse((self.tmp / "nunca").exists())


class TestOrchestrator(Tmp):
    def stages(self, calls: list[str], fail: set[str] = frozenset(), version: int = 1):
        def make(name: str):
            def run(ctx, out: Path):
                calls.append(name)
                if name in fail:
                    raise RuntimeError(f"fallo de {name}")
                (out / f"{name}.txt").write_text(name, encoding="utf-8")
                return {"name": name}

            return run

        return (
            Stage("a", version, make("a")),
            Stage("b", 1, make("b"), after=("a",)),
            Stage("c", 1, make("c"), after=("b",)),
            Stage("r", 1, make("r"), after=("a",), optional=("b", "c")),
        )

    def test_cache_and_resume(self) -> None:
        p = self.project()
        calls: list[str] = []
        res = run_project(p, self.stages(calls), log=quiet)
        self.assertEqual([r.status for r in res], ["ok"] * 4)
        self.assertEqual(calls, ["a", "b", "c", "r"])
        calls.clear()
        res = run_project(p, self.stages(calls), log=quiet)
        self.assertEqual([r.status for r in res], ["cached"] * 4)
        self.assertEqual(calls, [])
        # cambia una entrada (versión de «a»): se rehacen «a» y lo que depende de ella
        res = run_project(p, self.stages(calls, version=2), log=quiet)
        self.assertEqual(calls, ["a", "b", "c", "r"])
        self.assertEqual(
            len(list((p.root / "stages" / "a").iterdir())), 2
        )  # se conservan

    def test_source_change_invalidates(self) -> None:
        p = self.project()
        calls: list[str] = []
        run_project(p, self.stages(calls), log=quiet)
        calls.clear()
        (self.vehicle / "x.jbeam").write_text("{}", encoding="utf-8")
        run_project(p, self.stages(calls), log=quiet)
        self.assertEqual(calls, ["a", "b", "c", "r"])

    def test_failure_kept_downstream_skipped_optional_runs(self) -> None:
        p = self.project()
        calls: list[str] = []
        res = {r.name: r for r in run_project(p, self.stages(calls, {"b"}), log=quiet)}
        self.assertEqual(res["b"].status, "failed")
        self.assertIn("fallo de b", res["b"].error)
        self.assertEqual(res["c"].status, "skipped")
        self.assertEqual(res["r"].status, "ok")  # opcional: se ejecuta igual
        failed = [
            d for d in (p.root / "stages" / "b").iterdir() if ".failed-" in d.name
        ]
        self.assertEqual(len(failed), 1)
        meta = json.loads((failed[0] / "stage.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "failed")
        # reanudar sin el fallo: «a» viene de la caché; «b», «c» y «r» se hacen
        calls.clear()
        res = {r.name: r for r in run_project(p, self.stages(calls), log=quiet)}
        self.assertEqual(res["a"].status, "cached")
        self.assertEqual(
            calls, ["b", "c", "r"]
        )  # «r» cambia de clave: b ya tiene resultado
        status = project_status(p)
        self.assertEqual({e["status"] for e in status["b"]}, {"failed", "ok"})

    def test_only_runs_dependencies(self) -> None:
        p = self.project()
        calls: list[str] = []
        run_project(p, self.stages(calls), only=("b",), log=quiet)
        self.assertEqual(calls, ["a", "b"])
        with self.assertRaises(ProjectError):
            run_project(p, self.stages(calls), only=("zzz",), log=quiet)

    def test_runs_journal_appended(self) -> None:
        p = self.project()
        run_project(p, self.stages([]), log=quiet)
        run_project(p, self.stages([]), log=quiet)
        lines = (p.root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["stages"]["a"]["status"], "cached")


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestIbizaFlow(unittest.TestCase):
    """Flujo real sobre la muestra (solo lectura del origen; proyecto en temporal)."""

    def test_full_flow_and_cache(self) -> None:
        from src.project.stages import STAGES

        tmp = Path(tempfile.mkdtemp())
        try:
            p = create_project(tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc"))
            res = {r.name: r for r in run_project(p, STAGES, log=quiet)}
            self.assertEqual({r.status for r in res.values()}, {"ok"})
            comp = json.loads(
                (res["report"].dir / "compatibility.json").read_text(encoding="utf-8")
            )
            self.assertTrue(comp["physics_ok"])
            # 6 + 4 tablas LUT sin decisión en un proyecto nuevo (fase B4)
            # fase C5: +33 atributos de alas de aero.ini sin decisión
            self.assertEqual(comp["attributes"]["manual_required"], 43)
            self.assertFalse(comp["installable_package"])
            self.assertIn(
                "borrador: brakes.ini [DATA] MAX_TORQUE (> 0)", comp["blockers"]
            )
            again = run_project(p, STAGES, log=quiet)
            self.assertEqual({r.status for r in again}, {"cached"})
            # una decisión del proyecto cambia la huella de reglas: solo se rehacen
            # attributes y report
            (p.decisions_dir / "seat_ibiza_acfr.jsonl").write_text("", encoding="utf-8")
            third = {r.name: r.status for r in run_project(p, STAGES, log=quiet)}
            self.assertEqual(third["physics"], "cached")
            self.assertEqual(third["attributes"], "ok")
            self.assertEqual(third["report"], "ok")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_attribute_editor_decision_feeds_the_flow(self) -> None:
        """Una decisión guardada con la interfaz de atributos (ReviewService) sobre la
        carpeta del proyecto se ve en la siguiente ejecución del orquestador."""
        from src.app.service import ReviewService
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics
        from src.project.stages import STAGES

        tmp = Path(tempfile.mkdtemp())
        try:
            p = create_project(tmp / "ibz", SourceSpec("dir", str(IBIZA), "35b84.pc"))
            first = {r.name: r for r in run_project(p, STAGES, log=quiet)}
            report = json.loads(
                (first["attributes"].dir / "report.json").read_text(encoding="utf-8")
            )
            target = next(
                r["target"]
                for r in report["results"]
                if r["status"] == "manual_required"
            )
            svc = ReviewService(
                preview_physics(load_vehicle_dir(IBIZA, "35b84.pc")), p.root
            )
            svc.decide(
                {
                    "action": "defer",
                    "target": target,
                    "justification": "prueba: dejar pendiente",
                    "scope": "vehicle",
                    "status": "pending",
                }
            )
            self.assertTrue(any(p.decisions_dir.glob("*.jsonl")))
            second = {r.name: r for r in run_project(p, STAGES, log=quiet)}
            self.assertEqual(second["physics"].status, "cached")
            self.assertEqual(second["attributes"].status, "ok")
            self.assertEqual(second["attributes"].summary["project_decisions"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
