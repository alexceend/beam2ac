"""Reglas de marchas y FINAL para la CVT (``cvtGearbox`` + ``torqueConverter``) del
exportador 40340 de Automation (2026-11-19; muestra ``agus_1_nrt_1``).

    python tools/catalog/add_cvt_gearbox_rules.py [--dry-run]

Solo añade reglas (inmutables; no volver a ejecutar). Condiciones en lista cerrada:
``gearbox.kind == cvtGearbox``, exportador ``40340`` (la única versión con CVT vista) y
``camsoEngine``. Sin condición de tracción (``transmisión 2WD de AC``/el cálculo 4WD de AC aplican
``FINAL·marcha`` igual; ``[TRACTION] TYPE`` y ``[AWD]`` NO se resuelven aquí).

Representación en AC (decisión de diseño; ver ``docs/research/cvt_generalizacion_2026-11-19.md``):
AC no tiene CVT. La caja queda con UNA marcha de relación 1,0 y ``FINAL`` = grupo final de los
diferenciales; el script de CSP (``src/ac/cvt_script.py``) fija en ejecución
``FINAL·relación CVT``. Sin el script, el coche circula con una relación total fija igual al
grupo final.

* ``GEARS/COUNT`` = 1 y ``GEARS/GEAR_1`` = 1,0: convención de la emulación (formato).
* ``GEARS/GEAR_R`` = −``maxGearRatio``: BeamNG usa la misma relación variable con signo −1
  (cvtGearbox.lua l. 116) y parado el controlador fija ``maxGearRatio`` (shiftLogic l. 239).
  [I] La relación total en marcha atrás varía en BeamNG; esta es la estática equivalente a la
  salida desde parado.
* ``GEARS/FINAL`` = ``final_ratio`` (gearRatio común de los diferenciales de eje).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import add_dct_gearbox_rules as D  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402
from src.extract.automatic_transmission import (  # noqa: E402
    CVT_LUA,
    GAME_LUA,
    SHIFT_CVT_LUA,
)

D.DATE = "2026-11-19"
D.AUTHOR = "claude (soporte CVT con agus_1_nrt_1, encargado por el usuario)"
D.DOC = "docs/research/cvt_generalizacion_2026-11-19.md"
D.BASE_CONDITIONS = [
    D.cond("ir.drivetrain.gearbox.kind", "eq", "cvtGearbox"),
    D.cond("ir.formats.exporter_version", "in", ["40340"]),
    D.cond("ir.engine.device_type", "eq", "camsoEngine"),
    D.cond("ir.drivetrain.torque_converter.stall_torque_ratio", "exists", None),
]
D.SOURCE_DOC = [
    D.ev("code", "src/extract/drivetrain.py", "build_drivetrain, _cvt_range"),
    D.ev("code", "src/extract/automatic_transmission.py", "read_cvt"),
    D.ev(
        "code",
        D.GAME + CVT_LUA,
        "l. 116-124 (signo de la marcha atrás, rango)",
        GAME_LUA[CVT_LUA],
    ),
    D.ev(
        "code",
        D.GAME + SHIFT_CVT_LUA,
        "l. 229-240 (rpm objetivo y relación; parado = maxGearRatio)",
        GAME_LUA[SHIFT_CVT_LUA],
    ),
]
D.ASSUMPTION = (
    "APROXIMACIÓN: AC no tiene CVT ni convertidor de par; la caja se emula con una marcha 1,0 y "
    "FINAL = grupo final, y un script de CSP (sin verificar en AC: data/script.lua solo corre con "
    "física extendida, G2.22) fija en ejecución FINAL·relación CVT. Sin script, la relación total "
    "es fija e igual al grupo final"
)
D.EVID = "output/_diag_agus_cvt_2026-11-19c"


def _make(rule, rid, target, kind, transform, inputs, reason, notes, assumptions=None):
    r = D.rule(rid, target, inputs, [], [], reason, kind=kind, assumptions=assumptions)
    r.transform = transform
    r.notes = notes
    r.evidence = [D.ev("report", D.DOC, "§2.2-§2.3")]
    return r


def build() -> list[S.Rule]:
    T = S.Transform
    note = "CVT (40340, agus_1_nrt_1); no amplía dt.*_atc/_dct"
    return [
        _make(
            D.rule,
            "dt.gear_count_cvt",
            "ac:drivetrain.ini/GEARS/COUNT",
            "format",
            T(id="constant", params={"value": 1}, input_units={}, output_unit="1"),
            {},
            "CVT: una marcha de AC (la variación la hace el script de CSP)",
            note,
        ),
        _make(
            D.rule,
            "dt.gear_1_cvt",
            "ac:drivetrain.ini/GEARS/GEAR_1",
            "format",
            T(id="constant", params={"value": 1.0}, input_units={}, output_unit="1"),
            {},
            "CVT: relación 1,0 de la única marcha; FINAL lleva el grupo final",
            note,
        ),
        _make(
            D.rule,
            "dt.gear_r_cvt",
            "ac:drivetrain.ini/GEARS/GEAR_R",
            "direct",
            T(
                id="scale",
                params={"factor": -1.0},
                input_units={"x": "1"},
                output_unit="1",
            ),
            {"x": "ir.drivetrain.gearbox.cvt_ratio_max"},
            "CVT: marcha atrás = −maxGearRatio (BeamNG reutiliza el rango con signo −1; "
            "parado, el controlador fija maxGearRatio)",
            note,
            assumptions=[
                D.ASSUMPTION,
                "[I] en BeamNG la relación en marcha atrás varía como en marcha adelante; "
                "aquí se fija la de salida desde parado (maxGearRatio)",
            ],
        ),
        _make(
            D.rule,
            "dt.final_cvt",
            "ac:drivetrain.ini/GEARS/FINAL",
            "direct",
            T(id="identity", params={}, input_units={"x": "1"}, output_unit="1"),
            {"x": "ir.drivetrain.final_ratio"},
            "CVT: grupo final común de los diferenciales de eje (central 1:1)",
            note,
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))
        if r.key in cat.rules:
            print("ya existe:", r.key)
            return 1
    for r in rules:
        print(
            r.key if args.dry_run else f"escrita: {write_rule(REPO_CATALOG, r)}",
        )
    if not args.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
