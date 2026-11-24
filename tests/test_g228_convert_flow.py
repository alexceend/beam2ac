"""G2.28: flujo de un paso de la aplicación de escritorio (``Workspace.convert``).

El análisis y el exportador reales se sustituyen por dobles (Blender tarda minutos);
lo que se prueba es el encadenado, los bloqueos y que nada se sobrescribe.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.app.desktop import make_desktop_server
from src.app.workspace import Workspace, WorkspaceError
from src.project.export import Issue
from tests.test_app_desktop import IBIZA, wait

OK = {
    "ok": True,
    "exportable_today": True,
    "blockers": [],
    "scope_note": "",
    "driver_mass": 0.0,
}


def fake_export(verdict="completo_sin_probar_en_el_juego", errors=()):
    def export_project(proj, car_id, log=print, car_id_check_root=None, **_):
        car = proj.root / "exports" / "001" / car_id
        (car / "data").mkdir(parents=True)
        (car / "data" / "car.ini").write_text("x", encoding="utf-8")
        log("[hecha] exportación falsa")
        return SimpleNamespace(
            dir=car.parent,
            car_dir=car,
            verdict=verdict,
            errors=list(errors),
            warnings=[],
            build=None,  # G2.38: promoción a builds/
        )

    return mock.Mock(side_effect=export_project)


@unittest.skipUnless((IBIZA / "35b84.pc").is_file(), "muestra del Ibiza no disponible")
class TestConvert(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "entregas"
        self.dest.mkdir()
        self.ws = Workspace(self.tmp / "projects", [], [], None)
        p = mock.patch("src.project.export.default_ac_root", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        # la ruta de AC (de donde sale el piloto) es obligatoria: aquí, una falsa que existe
        q = mock.patch("src.app.workspace._require_ac_root", return_value=str(self.tmp))
        q.start()
        self.addCleanup(q.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def body(self, **extra) -> dict:
        return {
            "path": str(IBIZA),
            "pc": "35b84.pc",
            "car_id": "beam2ac_t",
            **extra,
        }

    def run_convert(self, inspect=OK, export=None, **extra) -> tuple[dict, mock.Mock]:
        export = export or fake_export()
        with (
            mock.patch("src.app.diagnostics.inspect_vehicle", return_value=inspect),
            mock.patch("src.project.export.export_project", export),
        ):
            return wait(self.ws, self.ws.convert(self.body(**extra))["id"]), export

    def test_full_flow_delivers_to_destination(self) -> None:
        job, export = self.run_convert(dest=str(self.dest))
        self.assertEqual(job["status"], "hecha", job)
        r = job["result"]
        self.assertTrue(r["delivered"])
        self.assertEqual(Path(r["output"]), (self.dest / "beam2ac_t").resolve())
        self.assertTrue((self.dest / "beam2ac_t" / "data" / "car.ini").is_file())
        self.assertEqual(r["project"], "seat_ibiza_acfr")
        self.assertTrue((self.ws.root / r["project"] / "project.json").is_file())
        self.assertTrue(any(line.startswith("4/4") for line in job["log"]))
        opened: list[str] = []
        self.ws.open_output(job["id"], opened.append)
        self.assertEqual(opened, [r["output"]])
        # segunda conversión: el destino no se pisa; el proyecto recibe otro nombre
        with self.assertRaisesRegex(WorkspaceError, "no se sobrescribe"):
            self.ws.convert(self.body(dest=str(self.dest)))
        job2, _ = self.run_convert()
        self.assertEqual(job2["result"]["project"], "seat_ibiza_acfr_2")
        self.assertFalse(job2["result"]["delivered"])  # sin destino: queda en exports/

    def test_blocking_analysis_creates_nothing(self) -> None:
        blocked = {
            **OK,
            "exportable_today": False,
            "blockers": [{"target": "ac:tyres.ini/FRONT/DY0"}],
        }
        job, export = self.run_convert(inspect=blocked)
        self.assertEqual(job["status"], "fallida")
        self.assertIn("bloqueada", job["error"])
        self.assertIn("DY0", job["error"])
        export.assert_not_called()
        self.assertFalse((self.ws.root / "seat_ibiza_acfr").exists())
        job, export = self.run_convert(inspect={"ok": False, "error": "ilegible"})
        self.assertIn("formato no reconocido", job["error"])
        export.assert_not_called()

    def test_blocked_or_failed_export_is_not_delivered(self) -> None:
        job, _ = self.run_convert(
            export=fake_export("bloqueado", [Issue("error", "x.y", "falta", "ac:a")]),
            dest=str(self.dest),
        )
        self.assertEqual(job["status"], "hecha")
        self.assertFalse(job["result"]["delivered"])
        self.assertEqual(job["result"]["errors"][0]["code"], "x.y")
        self.assertEqual(list(self.dest.iterdir()), [])
        job, _ = self.run_convert(export=mock.Mock(side_effect=RuntimeError("Blender")))
        self.assertEqual(job["status"], "fallida")
        self.assertIn("Blender", job["error"])

    def test_bad_requests_refused_before_any_work(self) -> None:
        cases = [
            ({"path": str(self.tmp / "nada")}, "no es una carpeta"),
            ({"path": str(self.dest)}, "no parece un vehículo"),
            ({"pc": "otra.pc"}, "elige una configuración"),
            ({"car_id": "ks_ferrari"}, "prefijo"),
            ({"dest": str(self.tmp / "no_existe")}, "destino no es una carpeta"),
            ({"dest": str(IBIZA.parent)}, "no puede estar dentro"),
            ({"driver_mass": "x"}, "no numérica"),
        ]
        for extra, msg in cases:
            with self.subTest(extra), self.assertRaisesRegex(WorkspaceError, msg):
                self.ws.convert(self.body(**extra))
        self.assertEqual(self.ws.jobs, {})
        self.assertFalse(self.ws.root.is_dir() and any(self.ws.root.iterdir()))

    # ------------------------------------------------ G2.29.2: masa del piloto
    def convert_with_mass(self, analysed=None, **extra) -> tuple[dict, mock.Mock]:
        """Análisis doble que devuelve la masa que recibe (o ``analysed``)."""
        inspect = mock.Mock(
            side_effect=lambda path, pc, mass, **kw: {
                **OK,
                "driver_mass": mass if analysed is None else analysed,
            }
        )
        with (
            mock.patch("src.app.diagnostics.inspect_vehicle", inspect),
            mock.patch("src.project.export.export_project", fake_export()),
        ):
            job = wait(self.ws, self.ws.convert(self.body(**extra))["id"])
        return job, inspect

    def test_driver_mass_same_in_analysis_and_export(self) -> None:
        cases = [
            ({}, 0.0),
            ({"driver_mass": 70}, 70.0),
            ({"driver_mass": "82.5"}, 82.5),
        ]
        for extra, mass in cases:
            with self.subTest(extra):
                job, inspect = self.convert_with_mass(
                    **extra, analysis_driver_mass=mass
                )
                self.assertEqual(job["status"], "hecha", job)
                self.assertEqual(inspect.call_args.args[2], mass)  # análisis
                proj = self.ws.root / job["result"]["project"] / "project.json"
                stored = json.loads(proj.read_text(encoding="utf-8"))
                self.assertEqual(stored["driver_mass_kg"], mass)  # exportador
                self.assertEqual(job["result"]["driver_mass"], mass)

    def test_invalid_driver_mass_refused_before_any_work(self) -> None:
        from src.app import diagnostics

        for bad in ("x", -1, 150.1, "nan", "inf", True, [70]):
            with self.subTest(bad):
                with self.assertRaisesRegex(WorkspaceError, "masa del piloto"):
                    self.ws.convert(self.body(driver_mass=bad))
                with self.assertRaisesRegex(WorkspaceError, "masa del piloto"):
                    diagnostics.inspect_vehicle(str(IBIZA), "35b84.pc", bad)
                with self.assertRaisesRegex(WorkspaceError, "masa del piloto"):
                    self.ws.create(
                        {"name": "m", "kind": "dir", "path": str(IBIZA),
                         "pc": "35b84.pc", "driver_mass": bad}
                    )  # fmt: skip
        self.assertEqual(self.ws.jobs, {})
        self.assertFalse(self.ws.root.is_dir() and any(self.ws.root.iterdir()))

    def test_driver_mass_mismatch_blocks_conversion(self) -> None:
        # la pantalla mostraba el análisis de otra masa: ni siquiera empieza
        with self.assertRaisesRegex(WorkspaceError, "vuelve a analizar"):
            self.ws.convert(self.body(driver_mass=70, analysis_driver_mass=0))
        self.assertEqual(self.ws.jobs, {})
        # el análisis no trabajó con la masa pedida: no se crea ni exporta nada
        job, _ = self.convert_with_mass(analysed=0.0, driver_mass=70)
        self.assertEqual(job["status"], "fallida")
        self.assertIn("el análisis usó un piloto de 0.0 kg", job["error"])
        self.assertFalse(self.ws.root.is_dir() and any(self.ws.root.iterdir()))

    def test_real_analysis_reports_the_mass_it_used(self) -> None:
        from src.app import diagnostics

        for arg, mass in ((), 0.0), ((70,), 70.0), (("",), 0.0):
            with self.subTest(arg):
                r = diagnostics.inspect_vehicle(str(IBIZA), "35b84.pc", *arg)
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["driver_mass"], mass)

    def test_driver_only_blockers_say_so(self) -> None:
        """G2.29.3: un bloqueo causado solo por la masa del piloto lo explica."""
        from src.app import diagnostics

        atlanta = IBIZA.parents[2] / "samples_beamng" / "2020_rpm_atlanta_gt_3_6t"
        # G2.45: con piloto ya no hay bloqueo (reglas *_driver con posición estimada);
        # el mensaje DRIVER_STEP queda para futuras reglas solo de piloto 0
        r = diagnostics.inspect_vehicle(str(atlanta), None, 70)
        self.assertEqual(r["blockers"], [])
        self.assertFalse(diagnostics.inspect_vehicle(str(atlanta))["blockers"])

    def test_cancel_stops_at_a_stage_boundary(self) -> None:
        """G2.29: cancelar se atiende en el siguiente ``log`` (entre etapas)."""
        ws = self.ws

        def export_project(proj, car_id, log=print, car_id_check_root=None, **_):
            (jid,) = ws.jobs
            self.assertTrue(ws.cancel(jid)["cancel_requested"])
            log("[hecha]  source")  # frontera de etapa: aquí se detiene
            raise AssertionError("siguió tras la cancelación")

        job, _ = self.run_convert(
            export=mock.Mock(side_effect=export_project), dest=str(self.dest)
        )
        self.assertEqual(job["status"], "cancelada", job)
        self.assertIsNone(job["error"])
        self.assertFalse(any(self.dest.iterdir()))
        self.assertFalse((ws.root / "seat_ibiza_acfr" / "exports").exists())
        with self.assertRaisesRegex(WorkspaceError, "ya terminó"):
            ws.cancel(job["id"])

    def test_installed_car_id_refused(self) -> None:
        ac = self.tmp / "ac"
        (ac / "content" / "cars" / "beam2ac_t").mkdir(parents=True)
        with mock.patch("src.project.export.default_ac_root", return_value=ac):
            with self.assertRaisesRegex(WorkspaceError, "ya está instalado"):
                self.ws.convert(self.body())

    def test_car_id_status_during_analysis(self) -> None:
        """G2.29.1: el conflicto se conoce en el análisis, antes de «Convertir»."""
        from src.app import diagnostics

        ac = self.tmp / "ac"
        cars = ac / "content" / "cars"
        (cars / "Beam2ac_T").mkdir(parents=True)  # sin distinguir mayúsculas
        (cars / "beam2ac_t_2").mkdir()
        before = sorted(x.name for x in cars.iterdir())
        with mock.patch("src.project.export.default_ac_root", return_value=ac):
            hit = diagnostics.car_id_status("beam2ac_t")
            free = diagnostics.car_id_status("beam2ac_libre")
            bad = diagnostics.car_id_status("ks_ferrari")
            server = make_desktop_server(self.ws, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                url = "http://127.0.0.1:%d/api/car_id?car_id=beam2ac_t"
                with urllib.request.urlopen(url % server.server_address[1]) as r:
                    self.assertEqual(json.loads(r.read()), hit)
            finally:
                server.shutdown()
                server.server_close()
        self.assertFalse(hit["ok"])
        self.assertEqual(Path(hit["installed_at"]), cars / "Beam2ac_T")
        self.assertIn(str(cars / "Beam2ac_T"), hit["reason"])
        self.assertEqual(hit["suggestion"], "beam2ac_t_3")  # _2 también instalado
        self.assertEqual(
            free,
            {
                "car_id": "beam2ac_libre",
                "ok": True,
                "checked": True,
                "ac_root": str(ac),
                "installed_at": None,
                "reason": "",
                "suggestion": None,
            },
        )
        self.assertFalse(bad["ok"])
        self.assertIn("prefijo", bad["reason"])
        self.assertIsNone(bad["installed_at"])
        self.assertEqual(sorted(x.name for x in cars.iterdir()), before)  # solo lectura
        # sin instalación localizada: no se afirma que esté libre («sin comprobar»)
        none = diagnostics.car_id_status("beam2ac_t")
        self.assertTrue(none["ok"])
        self.assertFalse(none["checked"])

    def test_project_export_refused_with_blocking_errors(self) -> None:
        self.ws.create(
            {"name": "ibz", "kind": "dir", "path": str(IBIZA), "pc": "35b84.pc"}
        )
        pre = {"available": True, "errors": [{"target": "ac:brakes.ini/DATA/X"}]}
        with mock.patch.object(self.ws, "precheck", return_value=pre):
            with self.assertRaisesRegex(WorkspaceError, "errores bloqueantes"):
                self.ws.export("ibz", {"confirm": True})
        self.assertEqual(self.ws.exports("ibz"), [])

    def test_http_route(self) -> None:
        server = make_desktop_server(self.ws, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d" % server.server_address[1]
            req = urllib.request.Request(
                url + "/api/convert",
                json.dumps({"path": str(self.tmp / "nada")}).encode(),
                {"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 400)
            with urllib.request.urlopen(url + "/static/desktop.js") as r:
                self.assertIn("/api/convert", r.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
