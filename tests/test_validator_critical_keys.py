"""Validador: claves sin las que AC deja el coche sin frenos o sin tracción.

Añadido el 2026-10-26.

Semántica de AC (AC): lectura de INI de AC,
getString, getFloat, frenos de AC,
neumático de AC, transmisión 2WD de AC. Todo en carpetas temporales.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import unittest
from pathlib import Path

from src.ac import car_validate as cv

BRAKES_OK = (
    "[DATA]\nMAX_TORQUE=2536.2\t\t; par\nFRONT_SHARE=0.857\nHANDBRAKE_TORQUE=1208.9\n"
)
DT_OK = (
    "[TRACTION]\nTYPE=FWD\n[GEARS]\nCOUNT=1\nGEAR_R=-3.3\nGEAR_1=3.14\nFINAL=5.98\n"
    "[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n"
)
REAL_BASE = Path(
    "output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25/seat_ibiza_acfr/data"
)


def write(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


class TestReadIniAC(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def ini(self, text: str) -> cv.ACIni:
        return cv.read_ini_ac(write(self.tmp / "x.ini", text))

    def test_case_and_untrimmed_key(self) -> None:
        ini = self.ini("[DATA]\nmax_torque=1\nMAX_TORQUE =2\n FRONT_SHARE=0.5\n")
        self.assertEqual(
            ini.sections["DATA"],
            {"max_torque": "1", "MAX_TORQUE ": "2", " FRONT_SHARE": "0.5"},
        )
        self.assertEqual(cv.ac_get_float(ini, "DATA", "MAX_TORQUE").status, "ausente")

    def test_value_cut_at_semicolon_and_right_trimmed(self) -> None:
        ini = self.ini("[A]\nX=  12.5 \t;c\r\nY=3\r\n")
        self.assertEqual(ini.sections["A"], {"X": "  12.5", "Y": "3"})
        self.assertEqual(cv.ac_get_float(ini, "A", "X").value, 12.5)

    def test_semicolon_before_equals_is_comment(self) -> None:
        self.assertEqual(self.ini("[A]\n;X=1\nY;=2\n").sections["A"], {})

    def test_first_duplicate_wins_and_section_reset(self) -> None:
        ini = self.ini("[A]\nX=1\nX=2\n[B]\nZ=1\n[A]\nY=3\n")
        self.assertEqual(
            ini.sections["A"], {"Y": "3"}
        )  # la segunda [A] vacía la primera
        self.assertEqual(ini.reset_sections, ["A"])
        ini2 = self.ini("[A]\nX=1\nX=2\n")
        self.assertEqual(ini2.sections["A"]["X"], "1")
        self.assertEqual(ini2.duplicate_keys, ["[A] X"])

    def test_bracket_anywhere_opens_section(self) -> None:
        # coincide con ensamblado.md §9.3: «; […]» en un comentario abre sección en AC
        ini = self.ini("[DATA]\nMAX_TORQUE=1\n; ver [OTRA]\nFRONT_SHARE=0.5\n")
        self.assertEqual(ini.sections["DATA"], {"MAX_TORQUE": "1"})
        self.assertEqual(ini.sections["OTRA"], {"FRONT_SHARE": "0.5"})

    def test_keys_outside_section_ignored(self) -> None:
        self.assertEqual(self.ini("X=1\n[A]\nY=2\n").sections, {"A": {"Y": "2"}})


class TestGetFloat(unittest.TestCase):
    def f(self, raw: str | None) -> cv.ACFloat:
        ini = cv.ACIni({"S": {} if raw is None else {"K": raw}})
        return cv.ac_get_float(ini, "S", "K")

    def test_statuses(self) -> None:
        self.assertEqual((self.f(None).status, self.f(None).value), ("ausente", 0.0))
        self.assertEqual((self.f("").status, self.f("").value), ("vacío", 0.0))
        self.assertEqual((self.f(" ").status, self.f(" ").value), ("vacío", 0.0))
        self.assertEqual(self.f("2536.2").value, 2536.2)
        self.assertEqual(self.f("-3").status, "número")
        p = self.f("2536,2")  # coma decimal: wcstod lee el prefijo
        self.assertEqual((p.status, p.value), ("prefijo", 2536.0))
        self.assertEqual(self.f("abc").status, "excepción")
        self.assertEqual(self.f("1e999").status, "excepción")  # ERANGE
        self.assertEqual(
            self.f("1e-400").status, "excepción"
        )  # ERANGE (subdesbordamiento)
        self.assertEqual(self.f("inf").status, "no_finito")
        self.assertTrue(math.isnan(self.f("nan").value))


class TestCriticalKeys(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def car(self, brakes: str | None = BRAKES_OK, dt: str | None = DT_OK) -> Path:
        car = self.tmp / "car"
        if car.exists():
            shutil.rmtree(car)
        write(car / "data/engine.ini", "[HEADER]\nPOWER_CURVE=power.lut\n")
        write(car / "data/power.lut", "0|0\n")
        write(car / "data/lods.ini", "[LOD_0]\nFILE=car.kn5\nIN=0\nOUT=1\n")
        write(car / "data/aero.ini", "[WING_0]\nNAME=BODY\n")
        write(car / "data/suspensions.ini", "[FRONT]\nTYPE=STRUT\n[REAR]\nTYPE=DWB\n")
        if brakes is not None:
            write(car / "data/brakes.ini", brakes)
        if dt is not None:
            write(car / "data/drivetrain.ini", dt)
        write(car / "car.kn5", "x")
        write(car / "ui/ui_car.json", "{}")
        write(car / "skins/s/ui_skin.json", "{}")
        return car

    def errors(self, **kw: str | None) -> list[str]:
        return cv.validate_structure(self.car(**kw), ("engine.ini", "lods.ini")).errors

    # --- positivos ---

    def test_valid_and_incomplete_by_design(self) -> None:
        self.assertEqual(self.errors(), [])
        # sin brakes.ini ni drivetrain.ini: su existencia la exige required_data
        self.assertEqual(self.errors(brakes=None, dt=None), [])

    def test_valid_domain_edges_and_formats(self) -> None:
        for share in ("0", "1", "0.0", "1.0"):
            with self.subTest(share=share):
                self.assertEqual(
                    self.errors(brakes=BRAKES_OK.replace("0.857", share)), []
                )
        self.assertEqual(self.errors(dt=DT_OK.replace("FINAL=5.98", "FINAL=-5.98")), [])
        self.assertEqual(
            self.errors(
                brakes=BRAKES_OK.replace("\n", "\r\n"), dt=DT_OK.replace("\n", "\r\n")
            ),
            [],
        )
        reordered = "; c\n[DATA]\nFRONT_SHARE=0.857 ; r\nMAX_TORQUE=2536.2\t; t\n"
        self.assertEqual(self.errors(brakes=reordered), [])
        # clave repetida: AC usa la primera (válida)
        self.assertEqual(self.errors(brakes=BRAKES_OK + "MAX_TORQUE=0\n"), [])

    @unittest.skipUnless(REAL_BASE.is_dir(), "base congelada no disponible")
    def test_real_frozen_base_passes(self) -> None:
        # solo lectura de la base aprobada
        problems = cv.critical_key_problems(REAL_BASE)
        self.assertEqual(len(problems), 3)
        self.assertTrue(all(ok for _, ok, _ in problems), problems)

    # --- negativos ---

    def assert_fails(self, key: str, needle: str, **kw: str | None) -> None:
        errs = self.errors(**kw)
        self.assertTrue(any(e.startswith(key) and needle in e for e in errs), errs)

    def test_missing_keys_sections_files(self) -> None:
        mt, fs, fin = (
            "brakes.ini [DATA] MAX_TORQUE",
            "brakes.ini [DATA] FRONT_SHARE",
            "drivetrain.ini [GEARS] FINAL",
        )
        self.assert_fails(mt, "falta", brakes="[DATA]\nFRONT_SHARE=0.857\n")
        self.assert_fails(fs, "falta", brakes="[DATA]\nMAX_TORQUE=2536.2\n")
        self.assert_fails(fin, "falta", dt=DT_OK.replace("FINAL=5.98\n", ""))
        self.assert_fails(mt, "no hay sección [DATA]", brakes="[X]\nMAX_TORQUE=1\n")
        self.assert_fails(
            fin, "no hay sección [GEARS]", dt="[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n"
        )
        self.assert_fails(mt, "vacía", brakes=BRAKES_OK.replace("2536.2", ""))

    def test_zero_negative_and_range(self) -> None:
        self.assert_fails(
            "brakes.ini [DATA] MAX_TORQUE",
            "0 ⇒",
            brakes=BRAKES_OK.replace("MAX_TORQUE=2536.2", "MAX_TORQUE=0"),
        )
        self.assert_fails(
            "brakes.ini [DATA] MAX_TORQUE",
            "negativo",
            brakes=BRAKES_OK.replace("2536.2", "-2536.2"),
        )
        self.assert_fails(
            "drivetrain.ini [GEARS] FINAL",
            "0 ⇒",
            dt=DT_OK.replace("FINAL=5.98", "FINAL=0"),
        )
        for share, clipped in (("-0.2", "0"), ("1.3", "1"), ("85.7", "1")):
            with self.subTest(share=share):
                self.assert_fails(
                    "brakes.ini [DATA] FRONT_SHARE",
                    f"recortaría a {clipped}",
                    brakes=BRAKES_OK.replace("0.857", share),
                )

    def test_non_numeric_nonfinite(self) -> None:
        mt = "brakes.ini [DATA] MAX_TORQUE"
        self.assert_fails(mt, "excepción", brakes=BRAKES_OK.replace("2536.2", "abc"))
        self.assert_fails(
            mt, "prefijo 2536", brakes=BRAKES_OK.replace("2536.2", "2536,2")
        )
        self.assert_fails(mt, "no finito", brakes=BRAKES_OK.replace("2536.2", "inf"))
        self.assert_fails(
            "brakes.ini [DATA] FRONT_SHARE",
            "no finito",
            brakes=BRAKES_OK.replace("0.857", "nan"),
        )
        self.assert_fails(
            "drivetrain.ini [GEARS] FINAL",
            "excepción",
            dt=DT_OK.replace("FINAL=5.98", "FINAL=1e999"),
        )

    def test_lenient_formats_that_ac_does_not_read(self) -> None:
        mt = "brakes.ini [DATA] MAX_TORQUE"
        self.assert_fails(
            mt,
            "AC distingue mayúsculas",
            brakes=BRAKES_OK.replace("MAX_TORQUE=", "MAX_TORQUE = "),
        )
        self.assert_fails(
            mt,
            "AC distingue mayúsculas",
            brakes=BRAKES_OK.replace("MAX_TORQUE", "max_torque"),
        )
        self.assert_fails(
            mt, "repetida", brakes=BRAKES_OK + "[DATA]\nCOCKPIT_ADJUSTABLE=0\n"
        )

    def test_base_without_brakes_or_final_is_rejected(self) -> None:
        # Caso que cubrían (sin pretenderlo) los fixtures de paquete aprobado anteriores
        # a 2026-10-26: antes pasaba la validación; ahora debe fallar.
        errs = self.errors(
            brakes="[DATA]\nFRONT_SHARE=0.857\n",
            dt="[TRACTION]\nTYPE=FWD\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n",
        )
        self.assertTrue(any(e.startswith("brakes.ini [DATA] MAX_TORQUE") for e in errs))
        self.assertTrue(any(e.startswith("drivetrain.ini [GEARS] FINAL") for e in errs))

    def test_messages_name_file_section_key_and_fix(self) -> None:
        errs = self.errors(brakes="[DATA]\nFRONT_SHARE=0.857\n")
        msg = next(e for e in errs if "MAX_TORQUE" in e)
        for part in (
            "data/brakes.ini",
            "[DATA]",
            "MAX_TORQUE",
            "AC leería 0",
            "Solución",
        ):
            self.assertIn(part, msg)


if __name__ == "__main__":
    unittest.main()
