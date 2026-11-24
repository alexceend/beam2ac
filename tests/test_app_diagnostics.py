"""Diagnóstico de la aplicación de escritorio: análisis previo y entorno.

Solo lectura; la interfaz no contiene reglas: todo sale del servicio del conversor.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from src.app import diagnostics as D
from src.app.desktop import make_desktop_server
from src.app.workspace import Workspace, WorkspaceError
from src.attrs import coverage

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PROTHEAN_MOD = ROOT / "samples_beamng" / "2020_rpm_prothean_s"


class TestFindVehicleDir(unittest.TestCase):
    def test_invalid_paths_are_explained(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.zip").write_bytes(b"PK")
            for bad, word in (
                ("", "indica"),
                (str(Path(d) / "no_existe"), "no es una carpeta"),
                (d, ".pc"),
                (str(Path(d) / "x.zip"), "zip"),
            ):
                with self.assertRaises(WorkspaceError) as cm:
                    D.find_vehicle_dir(bad)
                self.assertIn(word, str(cm.exception))

    def test_ambiguous_mod_root_lists_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for n in ("a", "b"):
                v = Path(d) / "vehicles" / n
                v.mkdir(parents=True)
                (v / "c.pc").write_text("{}", "utf-8")
            with self.assertRaises(WorkspaceError) as cm:
                D.find_vehicle_dir(d)
            self.assertIn("a, b", str(cm.exception))
            self.assertEqual(D.find_vehicle_dir(Path(d) / "vehicles" / "a").name, "a")

    @unittest.skipUnless(PROTHEAN_MOD.is_dir(), "sin samples_beamng")
    def test_mod_root_descends_to_the_vehicle(self) -> None:
        self.assertEqual(D.find_vehicle_dir(PROTHEAN_MOD).name, "2020_rpm_prothean_s")


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestInspect(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = D.inspect_vehicle(str(IBIZA), "35b84.pc")

    def test_identity_components_and_scope(self) -> None:
        r = self.r
        self.assertTrue(r["ok"] and r["in_scope"])
        self.assertEqual(r["identity"]["exporter_version"], "38022")
        comp = {c["label"]: c["value"] for c in r["components"]}
        self.assertEqual((comp["Motor"], comp["Tracción"]), ("camsoEngine", "FWD"))
        self.assertTrue(r["capabilities"])
        self.assertIn(
            r["capabilities"][0]["level"],
            coverage.CLASSES
            + (
                "direct",
                "approximation",
                "fidelity_loss",
                "not_representable",
                "pending_research",
            ),
        )

    def test_coverage_counts_every_target_once_and_is_not_a_fidelity_score(
        self,
    ) -> None:
        r = self.r
        total = sum(r["coverage_totals"].values())
        self.assertEqual(total, sum(sum(c.values()) for c in r["coverage"].values()))
        self.assertGreater(r["coverage_totals"]["medida"], 100)
        self.assertNotIn("%", json.dumps(r["coverage"]))
        self.assertIn("no es una medida de fidelidad", r["note"])
        # exportable = sin claves críticas sin valor; nunca «validado»
        self.assertEqual(r["exportable_today"], not r["blockers"])

    def test_unreadable_configuration_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "roto.pc").write_text("esto no es una configuración", "utf-8")
            r = D.inspect_vehicle(d)
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"] and r["hint"])

    @unittest.skipUnless(PROTHEAN_MOD.is_dir(), "sin samples_beamng")
    def test_blockers_match_the_exporter_and_say_what_is_needed(self) -> None:
        # G2.44: Patagonia (central bloqueado) ya es exportable; el caso negativo es un coche
        # oficial de BeamNG fuera del alcance (sin reglas) o, si no hay, una carpeta sin vehículo
        pat = D.inspect_vehicle(str(PROTHEAN_MOD.parent / "2020_rpm_patagonia_xt_v8"))
        self.assertTrue(pat["exportable_today"])
        self.assertEqual(pat["blockers"], [])
        r = D.inspect_vehicle(str(IBIZA))  # FRONT_SHARE: lo resuelve su decisión de proyecto, no el catálogo
        self.assertFalse(r["exportable_today"])
        b = {x["target"]: x for x in r["blockers"]}
        self.assertIn("ac:brakes.ini/DATA/FRONT_SHARE", b)
        self.assertTrue(all(x["next"] for x in r["blockers"]))
        ok = D.inspect_vehicle(str(PROTHEAN_MOD))
        self.assertTrue(ok["exportable_today"])
        self.assertEqual(ok["blockers"], [])

    def test_unknown_config_refused(self) -> None:
        with self.assertRaises(WorkspaceError):
            D.inspect_vehicle(str(IBIZA), "no_existe.pc")


class TestEnvironmentAndHttp(unittest.TestCase):
    def test_environment_is_read_only_summary(self) -> None:
        e = D.environment()
        self.assertTrue(e["catalog"]["ok"])
        self.assertGreater(e["catalog"]["rules"], 400)
        self.assertIn("found", e["assetto_corsa"])
        self.assertIsInstance(e["version"], str)

    def test_routes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            server = make_desktop_server(
                Workspace(Path(d) / "projects", [], [], None), 0
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://%s:%d" % server.server_address[:2]
            try:
                with urllib.request.urlopen(base + "/api/environment") as r:
                    self.assertIn("catalog", json.loads(r.read()))
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(
                        base + "/api/inspect?path=" + urllib.parse.quote(d + "/nada")
                    )
                self.assertEqual(cm.exception.code, 400)
                self.assertIn(
                    "no es una carpeta", json.loads(cm.exception.read())["error"]
                )
                with urllib.request.urlopen(base + "/") as r:
                    page = r.read().decode("utf-8")
                for needle in ('id="analyze"', 'id="env"', 'data-pane="diagnostico"'):
                    self.assertIn(needle, page)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
