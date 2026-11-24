"""Comentarios de los INI generados sin "[SECCIÓN]" (fase 7.1).

AC toma un "[X]" dentro de un comentario como cabecera de sección y pierde las
claves anteriores de X (docs/research/ensamblado.md §9.3).
"""

import re
import unittest

from src.ac import chassis as ac_chassis
from src.ac.assemble import render_car_ini, render_lods_ini
from src.ac.car_validate import read_ini
from src.ac.drivetrain import analyse as analyse_drivetrain
from src.ac.drivetrain import render_drivetrain_ini
from src.ac.engine import render_engine_ini
from src.ac.ini_format import sanitize_ini_comments
from src.extract.engine_camso import build_engine_model
from src.jbeam import PartIndex, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"
BRACKET = re.compile(r"[\[\]]")


def comment_brackets(text: str) -> list[str]:
    """Líneas cuyo comentario (tras el primer ';') contiene corchetes."""
    return [
        line
        for line in text.splitlines()
        if ";" in line and BRACKET.search(line.partition(";")[2])
    ]


class TestSanitize(unittest.TestCase):
    def test_only_comment_part_changes(self):
        text = (
            "[REAR]\n"
            "TRACK=1.43\t\t; vía [m]\n"
            ";  - [REAR] TYPE: pendiente\n"
            "[FRONT] ; cabecera con comentario [x]\n"
            "VALUE=[1,2] ; lista\n"
        )
        out = sanitize_ini_comments(text)
        self.assertEqual(
            out,
            "[REAR]\n"
            "TRACK=1.43\t\t; vía m\n"
            ";  - REAR TYPE: pendiente\n"
            "[FRONT] ; cabecera con comentario x\n"
            "VALUE=[1,2] ; lista\n",
        )
        self.assertEqual(read_ini_text(text), read_ini_text(out))

    def test_idempotent_and_no_comment(self):
        text = "[A]\nK=1\n"
        self.assertEqual(sanitize_ini_comments(text), text)
        once = sanitize_ini_comments("; [A] x\n")
        self.assertEqual(sanitize_ini_comments(once), once)


def read_ini_text(text: str) -> dict:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ini"
        p.write_text(text, encoding="utf-8")
        return read_ini(p)


class TestGeneratedInis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolved = resolve(PartIndex.from_directories(VEHICLE), load_pc(PC))

    def _check(self, name: str, text: str) -> None:
        self.assertEqual(comment_brackets(text), [], name)

    def test_chassis(self):
        res = ac_chassis.analyse(self.resolved)
        susp = ac_chassis.render_suspensions_ini(res)
        self._check("suspensions.ini", susp)
        self._check("brakes.ini", ac_chassis.render_brakes_ini(res))
        self._check("tyres.ini", ac_chassis.render_tyres_ini(res))
        ini = read_ini_text(susp)
        # Las claves no cambian: [REAR] sigue sin TYPE (decisión pendiente).
        self.assertEqual(ini["FRONT"]["TYPE"], "STRUT")
        self.assertNotIn("TYPE", ini["REAR"])
        self.assertIn("TRACK", ini["REAR"])
        # Ninguna línea de comentario repite el nombre de una sección real.
        for line in susp.splitlines():
            if line.lstrip().startswith(";"):
                self.assertNotRegex(line, r"\[(REAR|FRONT|BASIC|HEADER|ARB)\]")

    def test_engine_and_drivetrain(self):
        self._check(
            "engine.ini",
            render_engine_ini(build_engine_model(self.resolved), None),
        )
        self._check(
            "drivetrain.ini",
            render_drivetrain_ini(analyse_drivetrain(self.resolved)),
        )

    def test_car_and_lods(self):
        car = render_car_ini("Seat", 1296.3, 55, 55, 1226.27, 70)
        self._check("car.ini", car)
        ini = read_ini_text(car)
        self.assertEqual(ini["BASIC"]["TOTALMASS"], "1296.3")
        self.assertEqual(ini["FUEL"]["MAX_FUEL"], "55")
        self._check("lods.ini", render_lods_ini("x.kn5"))


if __name__ == "__main__":
    unittest.main()
