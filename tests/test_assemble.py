"""Fase 7: ensamblado y validación estructural."""

import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.ac import chassis as ac_chassis
from src.ac.assemble import (
    AssemblyError,
    assemble,
    build_ui,
    check_artifacts,
    read_power_lut,
    render_car_ini,
    render_lods_ini,
    render_setup_ini,
)
from src.ac.car_validate import (
    LOAD_AERO,
    LOAD_REAR_TYPE,
    read_ini,
    validate_structure,
)
from src.ac.drivetrain import analyse as analyse_drivetrain
from src.ac.drivetrain import write_drivetrain_files
from src.ac.engine import write_engine_files
from src.extract.engine_camso import HP_W, build_engine_model
from src.jbeam import PartIndex, load_pc, resolve
from src.mesh.plan import (
    PlanEmpty,
    PlanObject,
    ScenePlan,
    kunos_origin_offset,
    translate_plan,
)
from src.safe_paths import PROJECT_ROOT, ProtectedPathError, ensure_safe_output_path

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"


def _write(path: Path, text: str) -> Path:
    target = ensure_safe_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


class TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestRenderers(unittest.TestCase):
    def test_car_ini_only_backed_keys(self):
        text = render_car_ini("Coche", 1296.27, 40, 55, 1226.27, 70)
        ini = read_ini_text(text)
        self.assertEqual(ini["BASIC"], {"TOTALMASS": "1296.3"})
        self.assertEqual(ini["FUEL"], {"FUEL": "40", "MAX_FUEL": "55"})
        self.assertEqual(ini["INFO"], {"SCREEN_NAME": "Coche"})
        self.assertEqual(set(ini), {"HEADER", "INFO", "BASIC", "FUEL"})
        self.assertIn("GRAPHICS_OFFSET", text)  # solo como pendiente comentado

    def test_lods_ini(self):
        ini = read_ini_text(render_lods_ini("car.kn5"))
        self.assertEqual(ini["LOD_0"], {"FILE": "car.kn5", "IN": "0", "OUT": "1500"})

    def test_setup_ini_minimal(self):
        text = render_setup_ini()
        ini = read_ini_text(text)
        # Solo DISPLAY_METHOD: ningún reglaje ajustable ni valor físico.
        self.assertEqual(ini, {"DISPLAY_METHOD": {"SHOW_CLICKS": "0"}})
        keys = {k for sec in ini.values() for k in sec}
        self.assertFalse(keys & {"MIN", "MAX", "STEP", "DEFAULT", "TAB", "NAME"})
        # Ningún comentario lleva "[SECCIÓN]" (AC lo tomaría como cabecera).
        for line in text.splitlines():
            if line.lstrip().startswith(";"):
                self.assertNotIn("[", line)
        # Mismo contenido útil que la variante verificada en el juego (pkg_v5_setup).
        self.assertIn("[DISPLAY_METHOD]\nSHOW_CLICKS=0\n", text)

    def test_ui(self):
        curve = [(0.0, 0.0), (1000.0, 100.0), (2000.0, 150.0), (3000.0, 120.0)]
        info = {"Name": "X", "Brand": "", "Author": "A"}
        cfg = {
            "Top Speed": 50.0,
            "0-100 km/h": 6.1,
            "Transmission": "Manual",
            "Induction Type": "Turbo",
            "Years": {"min": 2020},
        }
        ui = build_ui(info, cfg, 1000.4, curve, "FWD")
        hp3000 = 120 * 3000 * 2 * math.pi / 60 / HP_W
        self.assertEqual(ui["specs"]["torque"], "150Nm")
        self.assertEqual(ui["specs"]["bhp"], f"{round(hp3000)}bhp")
        self.assertEqual(ui["specs"]["weight"], "1000kg")
        self.assertEqual(ui["specs"]["topspeed"], "180km/h")
        self.assertEqual(ui["specs"]["acceleration"], "6.1s 0-100")
        self.assertEqual(ui["tags"], ["fwd", "manual", "turbo"])
        self.assertEqual(ui["year"], 2020)
        self.assertEqual([p[0] for p in ui["torqueCurve"]], [0, 1000, 2000, 3000])
        self.assertAlmostEqual(ui["powerCurve"][3][1], round(hp3000, 2))


def read_ini_text(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        return read_ini(_write(Path(tmp) / "x.ini", text))


class TestIniAndValidation(TmpDir):
    def test_read_ini(self):
        ini = read_ini(
            _write(self.tmp / "a.ini", "[a]\nX=1 ; c\n; nada\n[A]\nY = 2\nZ\n")
        )
        self.assertEqual(ini, {"A": {"X": "1", "Y": "2"}})

    def _car(self, turbo=False, lut=True, kn5=True, aero=True, rear_type=True):
        car = self.tmp / "car"
        engine = "[HEADER]\nPOWER_CURVE=power.lut\n" + (
            "[TURBO_0]\nMAX_BOOST=1\n" if turbo else ""
        )
        _write(car / "data" / "engine.ini", engine)
        _write(car / "data" / "lods.ini", "[LOD_0]\nFILE=car.kn5\nIN=0\nOUT=1\n")
        if aero:
            _write(car / "data" / "aero.ini", "[WING_0]\nNAME=BODY\n")
        _write(
            car / "data" / "suspensions.ini",
            "[FRONT]\nTYPE=STRUT\n[REAR]\n" + ("TYPE=DWB\n" if rear_type else ""),
        )
        if lut:
            _write(car / "data" / "power.lut", "0|0\n")
        if kn5:
            _write(car / "car.kn5", "x")
        _write(car / "ui" / "ui_car.json", "{}")
        _write(car / "skins" / "s" / "ui_skin.json", "{}")
        return car

    def test_valid_structure(self):
        v = validate_structure(
            self._car(), ("engine.ini", "lods.ini"), ["engine.ini", "ai.ini"]
        )
        self.assertTrue(v.ok, v.errors)
        self.assertEqual(len(v.blockers), 1)  # falta ai.ini respecto a la referencia
        self.assertIn("ai.ini", v.blockers[0])

    def test_load_blockers_make_validation_fail(self):
        """Sin aero.ini o sin [REAR] TYPE, AC aborta la carga: nunca ok=True."""
        for kwargs, expected in (
            ({"aero": False}, LOAD_AERO),
            ({"rear_type": False}, LOAD_REAR_TYPE),
        ):
            with self.subTest(**kwargs):
                car = self._car(**kwargs)
                v = validate_structure(car, ("engine.ini", "lods.ini"))
                self.assertFalse(v.ok)
                self.assertEqual(
                    [e for e in v.errors if e.startswith(expected)], v.errors
                )
                shutil.rmtree(car)
        car = self._car()
        (car / "data" / "aero.ini").write_text(
            "[HEADER]\nVERSION=1\n", encoding="utf-8"
        )
        v = validate_structure(car, ())
        self.assertFalse(v.ok)
        self.assertTrue(any(e.startswith(LOAD_AERO) for e in v.errors))
        (car / "data" / "suspensions.ini").unlink()
        v = validate_structure(car, ())
        self.assertTrue(any("falta data/suspensions.ini" in e for e in v.errors))

    def test_broken_reference_and_turbo(self):
        v = validate_structure(
            self._car(turbo=True, lut=False, kn5=False), ("engine.ini",)
        )
        joined = " ".join(v.errors)
        self.assertIn("power.lut", joined)
        self.assertIn("car.kn5", joined)
        self.assertIn("TURBO", joined)

    def test_missing_artifacts(self):
        with self.assertRaises(AssemblyError):
            check_artifacts(self.tmp / "nada")

    def test_read_power_lut(self):
        path = _write(self.tmp / "p.lut", "0|0.00\n100|-3.10\n\n")
        self.assertEqual(read_power_lut(path), [(0.0, 0.0), (100.0, -3.1)])


class TestOrigin(unittest.TestCase):
    def _plan(self):
        ident = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
        body = PlanObject(
            "b",
            "b",
            "d",
            "p",
            [],
            ident,
            None,
            "",
            8,
            ((-1, -2, 0.4), (1, 3, 1.5)),
            ((0,) * 3,) * 2,
            {},
        )
        tyre = PlanObject(
            "t",
            "t",
            "d",
            "p",
            [],
            ident,
            "WHEEL_LF",
            "",
            8,
            ((0.6, -1.3, 0.3), (0.8, -0.7, 0.9)),
            ((0,) * 3,) * 2,
            {},
        )
        frame = ((1, 0, 0, 0.7), (0, 1, 0, -1.0), (0, 0, 1, 0.6), (0, 0, 0, 1))
        return ScenePlan(
            1,
            {},
            {},
            [body, tyre],
            [PlanEmpty("WHEEL_LF", "FL", frame)],
            {"WHEEL_LF": (0.7, -1.0, 0.6)},
        )

    def test_offset_and_translation(self):
        plan = self._plan()
        offset = kunos_origin_offset(plan)
        self.assertEqual(
            offset, (0.0, -0.5, -0.3)
        )  # centro x, y; suelo = z mín. de rueda
        moved = translate_plan(plan, offset)
        self.assertEqual(moved.objects[1].bbox_beamng[0], (0.6, -1.8, 0.0))
        self.assertAlmostEqual(moved.empties[0].matrix[2][3], 0.3)
        self.assertEqual(moved.objects[0].matrix[1][3], -0.5)
        self.assertEqual(
            plan.objects[1].bbox_beamng[0], (0.6, -1.3, 0.3)
        )  # original intacto


class TestSampleAssemblyWithoutBlender(TmpDir):
    def test_assemble_reuses_artifacts(self):
        resolved = resolve(PartIndex.from_directories(VEHICLE), load_pc(PC))
        art = self.tmp / "art"
        write_engine_files(build_engine_model(resolved), art / "engine")
        write_drivetrain_files(analyse_drivetrain(resolved), art / "drivetrain")
        ac_chassis.write_chassis_files(ac_chassis.analyse(resolved), art / "chassis")
        report = assemble(
            VEHICLE, PC, "seat_ibiza_acfr", art, self.tmp / "out", blender=None
        )
        car = Path(report["car_dir"])
        ini = read_ini(car / "data" / "car.ini")
        self.assertEqual(ini["BASIC"]["TOTALMASS"], "1296.3")
        self.assertEqual((ini["FUEL"]["FUEL"], ini["FUEL"]["MAX_FUEL"]), ("55", "55"))
        for name, sub in (("power.lut", "engine"), ("tyres.ini", "chassis")):
            self.assertEqual(
                (car / "data" / name).read_bytes(), (art / sub / name).read_bytes()
            )
        ui = json.loads((car / "ui" / "ui_car.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ui["specs"],
            {
                "bhp": "339bhp",
                "torque": "308Nm",
                "weight": "1226kg",
                "pwratio": "3.61kg/hp",
                "topspeed": "296km/h",
                "acceleration": "5.5s 0-100",
            },
        )
        self.assertTrue((car / "skins" / "default1" / "ui_skin.json").is_file())
        setup = car / "data" / "setup.ini"
        self.assertEqual(setup.read_text(encoding="utf-8"), render_setup_ini())
        self.assertIn("data/setup.ini", report["generated"])
        rows = {r["item"]: r for r in report["classification"]}
        self.assertEqual(rows["data/setup.ini"]["group"], "cargar")
        self.assertTrue(rows["data/setup.ini"]["present"])
        checks = {c["check"]: c for c in report["validation"]["checks"]}
        self.assertTrue(
            checks["setup.ini mínimo: sin reglajes ni valores físicos (§9.12)"]["ok"]
        )
        self.assertTrue((car / "collider.kn5").is_file())
        self.assertEqual(report["collider"]["source_object"], "35b84_body0")
        self.assertGreater(report["collider"]["box_ac_min"][1], 0)  # sobre el suelo
        groups = {r["group"] for r in report["classification"]}
        self.assertEqual(groups, {"cargar", "conducir", "opcional"})
        off = report["kn5"]["offset_beamng"]
        self.assertAlmostEqual(off[1], 0.02787, places=4)
        self.assertAlmostEqual(off[2], -0.29680, places=4)
        self.assertIn("error", report["kn5"])  # sin Blender no hay KN5…
        self.assertFalse(report["ok"])  # …y el ensamblado no se da por bueno
        self.assertTrue(any("KN5" in e for e in report["validation"]["errors"]))
        names = [c["check"] for c in report["validation"]["checks"]]
        self.assertIn("TOTALMASS − piloto = masa BeamNG (fase 2)", names)
        failed = [c for c in report["validation"]["checks"] if not c["ok"]]
        # Fallan las comprobaciones que dependen del KN5 (no generado), los dos
        # requisitos de carga que la física de las fases 3–5 aún no escribe y, desde
        # 2026-10-26, MAX_TORQUE: el borrador de brakes.ini solo escribe FRONT_SHARE y
        # AC leería 0 (coche sin frenos). FINAL sí lo escribe el borrador y debe pasar.
        max_torque = next(
            n for n in names if n.startswith("brakes.ini [DATA] MAX_TORQUE")
        )
        final = next(n for n in names if n.startswith("drivetrain.ini [GEARS] FINAL"))
        self.assertNotIn(final, {c["check"] for c in failed})
        load = {LOAD_AERO, LOAD_REAR_TYPE, max_torque}
        self.assertEqual({c["check"] for c in failed} & load, load)
        self.assertTrue(
            all(
                c["check"] in load or "kn5" in (c["check"] + str(c["detail"])).lower()
                for c in failed
            )
        )
        # Ningún INI lleva "[SECCIÓN]" en comentarios (fase 7.1).
        comment_checks = [
            c for c in report["validation"]["checks"] if "en comentarios" in c["check"]
        ]
        self.assertTrue(comment_checks)
        self.assertTrue(all(c["ok"] for c in comment_checks))

    def test_refuses_samples(self):
        with self.assertRaises(ProtectedPathError):
            assemble(
                VEHICLE, PC, "x", self.tmp, PROJECT_ROOT / "sample_assetto" / "x", None
            )


@unittest.skipUnless(
    os.environ.get("BEAM2AC_BLENDER_TESTS") == "1" and shutil.which("blender"),
    "construcción con Blender desactivada (BEAM2AC_BLENDER_TESTS=1 para activarla)",
)
class TestSampleAssemblyWithBlender(TmpDir):
    def test_full_assembly(self):
        resolved = resolve(PartIndex.from_directories(VEHICLE), load_pc(PC))
        art = self.tmp / "art"
        write_engine_files(build_engine_model(resolved), art / "engine")
        write_drivetrain_files(analyse_drivetrain(resolved), art / "drivetrain")
        ac_chassis.write_chassis_files(ac_chassis.analyse(resolved), art / "chassis")
        report = assemble(
            VEHICLE,
            PC,
            "seat_ibiza_acfr",
            art,
            self.tmp / "out",
            shutil.which("blender"),
        )
        # La física de las fases 3–5 aún no escribe [REAR] TYPE ni aero.ini: el coche
        # no cargaría (ensamblado.md §9.4, §9.6) y el ensamblado no se da por bueno.
        errors = report["validation"]["errors"]
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(any(e.startswith(LOAD_AERO) for e in errors))
        self.assertTrue(any(e.startswith(LOAD_REAR_TYPE) for e in errors))
        self.assertFalse(report["ok"])
        loading = [r for r in report["classification"] if r["group"] == "cargar"]
        missing = [r["item"] for r in loading if not r["present"]]
        self.assertEqual(missing, ["sfx/<id>.bank"])
        checks = {c["check"]: c for c in report["validation"]["checks"]}
        self.assertTrue(
            checks["STEER_LR hijo de COCKPIT_LR (como en los KN5 oficiales)"]["ok"]
        )
        self.assertTrue(
            checks["STEER_HR hijo de COCKPIT_HR (como en 126/128 coches ks_*)"]["ok"]
        )
        self.assertTrue(
            all(c["ok"] for name, c in checks.items() if name.startswith("collider"))
        )


if __name__ == "__main__":
    unittest.main()
