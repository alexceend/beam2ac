"""Casos sintéticos para el validador de coches de AC (solo carpetas temporales).

    python tools/analysis/validator_cases.py --out output/<nueva>

Construye en una carpeta TEMPORAL un coche mínimo que el validador acepta y variantes de
``brakes.ini``/``drivetrain.ini`` (claves ausentes, cero, negativas, no numéricas,
infinitas, NaN, formato distinto, secciones repetidas…). Registra para cada caso
``ok``/errores de ``validate_structure`` y qué leería AC según su ``INIReader``. No
escribe en paquetes reales.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac import car_validate as cv  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

BRAKES_OK = (
    "[DATA]\nMAX_TORQUE=2536.2\t\t; par\nFRONT_SHARE=0.857\nHANDBRAKE_TORQUE=1208.9\n"
)
DRIVETRAIN_OK = (
    "[TRACTION]\nTYPE=FWD\n[GEARS]\nCOUNT=2\nGEAR_R=-3.3\nGEAR_1=3.14\nGEAR_2=1.87\n"
    "FINAL=5.98\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n"
)

# (nombre, brakes.ini o None, drivetrain.ini o None, requeridos)
CASES: list[tuple[str, str | None, str | None]] = [
    ("válido completo", BRAKES_OK, DRIVETRAIN_OK),
    ("sin brakes.ini ni drivetrain.ini (fixture incompleto por diseño)", None, None),
    ("falta MAX_TORQUE", "[DATA]\nFRONT_SHARE=0.857\n", DRIVETRAIN_OK),
    ("falta FRONT_SHARE", "[DATA]\nMAX_TORQUE=2536.2\n", DRIVETRAIN_OK),
    ("falta FINAL", BRAKES_OK, DRIVETRAIN_OK.replace("FINAL=5.98\n", "")),
    ("falta [DATA]", "[OTRA]\nMAX_TORQUE=2536.2\nFRONT_SHARE=0.857\n", DRIVETRAIN_OK),
    (
        "falta [GEARS]",
        BRAKES_OK,
        "[TRACTION]\nTYPE=FWD\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n",
    ),
    (
        "MAX_TORQUE=0",
        BRAKES_OK.replace("MAX_TORQUE=2536.2", "MAX_TORQUE=0"),
        DRIVETRAIN_OK,
    ),
    (
        "FRONT_SHARE=0 (todo atrás; dominio)",
        BRAKES_OK.replace("0.857", "0"),
        DRIVETRAIN_OK,
    ),
    (
        "FRONT_SHARE=1 (todo delante; dominio)",
        BRAKES_OK.replace("0.857", "1"),
        DRIVETRAIN_OK,
    ),
    ("FINAL=0", BRAKES_OK, DRIVETRAIN_OK.replace("FINAL=5.98", "FINAL=0")),
    ("MAX_TORQUE negativo", BRAKES_OK.replace("2536.2", "-2536.2"), DRIVETRAIN_OK),
    ("FRONT_SHARE negativo", BRAKES_OK.replace("0.857", "-0.2"), DRIVETRAIN_OK),
    ("FRONT_SHARE > 1", BRAKES_OK.replace("0.857", "1.3"), DRIVETRAIN_OK),
    (
        "FRONT_SHARE en porcentaje (85.7)",
        BRAKES_OK.replace("0.857", "85.7"),
        DRIVETRAIN_OK,
    ),
    ("FINAL negativo", BRAKES_OK, DRIVETRAIN_OK.replace("FINAL=5.98", "FINAL=-5.98")),
    ("MAX_TORQUE no numérico", BRAKES_OK.replace("2536.2", "abc"), DRIVETRAIN_OK),
    ("MAX_TORQUE coma decimal", BRAKES_OK.replace("2536.2", "2536,2"), DRIVETRAIN_OK),
    ("MAX_TORQUE vacío", BRAKES_OK.replace("2536.2", ""), DRIVETRAIN_OK),
    ("MAX_TORQUE inf", BRAKES_OK.replace("2536.2", "inf"), DRIVETRAIN_OK),
    ("FRONT_SHARE nan", BRAKES_OK.replace("0.857", "nan"), DRIVETRAIN_OK),
    ("FINAL 1e999", BRAKES_OK, DRIVETRAIN_OK.replace("FINAL=5.98", "FINAL=1e999")),
    (
        "MAX_TORQUE con espacios: 'MAX_TORQUE = 2536.2'",
        BRAKES_OK.replace("MAX_TORQUE=", "MAX_TORQUE = "),
        DRIVETRAIN_OK,
    ),
    (
        "minúsculas: 'max_torque=2536.2'",
        BRAKES_OK.replace("MAX_TORQUE", "max_torque"),
        DRIVETRAIN_OK,
    ),
    (
        "[DATA] repetida (la segunda vacía la primera en AC)",
        BRAKES_OK + "[DATA]\nCOCKPIT_ADJUSTABLE=0\n",
        DRIVETRAIN_OK,
    ),
    (
        "clave duplicada (AC usa la primera)",
        BRAKES_OK + "MAX_TORQUE=0\n",
        DRIVETRAIN_OK,
    ),
    (
        "orden distinto y comentarios",
        "; frenos\n[DATA]\nHANDBRAKE_TORQUE=1208.9\nFRONT_SHARE=0.857 ; r\n"
        "MAX_TORQUE=2536.2\t; t\n",
        "[GEARS]\nFINAL=5.98\nCOUNT=2\nGEAR_1=3.14\nGEAR_2=1.87\nGEAR_R=-3.3\n"
        "[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n[TRACTION]\nTYPE=FWD\n",
    ),
    (
        "CRLF y tabuladores",
        BRAKES_OK.replace("\n", "\r\n"),
        DRIVETRAIN_OK.replace("\n", "\r\n"),
    ),
]


def build_car(root: Path, brakes: str | None, drivetrain: str | None) -> Path:
    car = root / "car"
    data = car / "data"
    data.mkdir(parents=True)
    (data / "engine.ini").write_text(
        "[HEADER]\nPOWER_CURVE=power.lut\n", encoding="utf-8"
    )
    (data / "power.lut").write_text("0|0\n", encoding="utf-8")
    (data / "lods.ini").write_text(
        "[LOD_0]\nFILE=car.kn5\nIN=0\nOUT=1\n", encoding="utf-8"
    )
    (data / "aero.ini").write_text("[WING_0]\nNAME=BODY\n", encoding="utf-8")
    (data / "suspensions.ini").write_text(
        "[FRONT]\nTYPE=STRUT\n[REAR]\nTYPE=DWB\n", encoding="utf-8"
    )
    if brakes is not None:
        (data / "brakes.ini").write_bytes(brakes.encode("utf-8"))
    if drivetrain is not None:
        (data / "drivetrain.ini").write_bytes(drivetrain.encode("utf-8"))
    (car / "car.kn5").write_text("x", encoding="utf-8")
    (car / "ui").mkdir()
    (car / "ui" / "ui_car.json").write_text("{}", encoding="utf-8")
    (car / "skins" / "s").mkdir(parents=True)
    (car / "skins" / "s" / "ui_skin.json").write_text("{}", encoding="utf-8")
    return car


def run_cases() -> list[dict[str, Any]]:
    rows = []
    for name, brakes, drivetrain in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            car = build_car(Path(tmp), brakes, drivetrain)
            v = cv.validate_structure(car, ("engine.ini", "lods.ini"))
            ac_view = {}
            if hasattr(cv, "read_ini_ac"):
                for ini in ("brakes.ini", "drivetrain.ini"):
                    p = car / "data" / ini
                    if p.is_file():
                        ac_view[ini] = cv.read_ini_ac(p).sections
            rows.append(
                {"caso": name, "ok": v.ok, "errores": v.errors, "ac_lee": ac_view}
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    rows = run_cases()
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "validator_cases.json").write_text(
        json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for r in rows:
        print(f"{'OK ' if r['ok'] else 'ERR'} {r['caso']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
