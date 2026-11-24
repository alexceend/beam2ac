"""Añade al catálogo los atributos de las alas de ``aero.ini`` (fase C5, opción A1 aprobada).

    python tools/catalog/add_aero_wing_attributes.py [--dry-run]

Esquema genérico (no depende de un coche): ``ac:aero.ini/HEADER/VERSION`` y, para
``WING_0``…``WING_2`` (convención de los coches oficiales: BODY, FRONT, REAR), las claves
``NAME``, ``CHORD``, ``SPAN``, ``POSITION``, ``LUT_AOA_CL``, ``LUT_AOA_CD``, ``CL_GAIN``,
``CD_GAIN``, ``ANGLE`` y las tablas ``…/LUT_AOA_CL/table`` y ``…/LUT_AOA_CD/table`` (mecanismo
L1 de resolved_ini). Solo añade: no toca atributos existentes. Procedencia por clave.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ATTR = ROOT / "catalog/attributes/assettocorsa.json"
TARGETS = ROOT / "catalog/targets/ac_physics.json"
DOC = "docs/research/segundo_coche_porsche_2026-10-26.md"
CONV = "docs/research/convenciones_fisicas_2026-10-26.md"

KEYS = [
    (
        "NAME",
        "text",
        "text",
        "nombre del ala (BODY/FRONT/REAR en los oficiales)",
        "§6.5",
    ),
    (
        "CHORD",
        "m",
        "number",
        "cuerda; con SPAN da el área (comentario de los aero.ini oficiales; ala de AC sin analizar [H])",
        "§6.5",
    ),
    ("SPAN", "m", "number", "envergadura; área = CHORD·SPAN [H]", "§6.5"),
    (
        "POSITION",
        "m",
        "vector",
        "punto de aplicación respecto al CdG, marco del cuerpo de AC [C: ala de AC, addLocalForceAtLocalPos]",
        "§6.5",
    ),
    (
        "LUT_AOA_CL",
        "text",
        "text",
        "LUT del coeficiente de sustentación frente al ángulo de ataque",
        "§6.5",
    ),
    (
        "LUT_AOA_CD",
        "text",
        "text",
        "LUT del coeficiente de arrastre frente al ángulo de ataque",
        "§6.5",
    ),
    (
        "CL_GAIN",
        "1",
        "number",
        "multiplicador de CL [C: cl = LUT_AOA_CL(angle+aoa)·CL_GAIN]; CL > 0 = carga [C]",
        "§6.5",
    ),
    ("CD_GAIN", "1", "number", "multiplicador de CD", "§6.5"),
    ("ANGLE", "deg", "number", "ángulo del ala que se suma al aoa [C]", "§6.5"),
]


def attr(aid: str, unit: str, dtype: str, desc: str, loc: str) -> dict:
    return {
        "schema": "beam2ac.attribute/1",
        "id": aid,
        "game": "assettocorsa",
        "unit": unit,
        "dtype": dtype,
        "description": desc,
        "doc": [
            {"type": "doc", "path": DOC, "locator": loc, "sha256": "", "note": ""},
            {"type": "doc", "path": CONV, "locator": "§3", "sha256": "", "note": ""},
        ],
        "required_for_load": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    raw = ATTR.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=1, ensure_ascii=False) + "\n" == raw
    ids = {x["id"] for x in data}
    new = [
        attr(
            "ac:aero.ini/HEADER/VERSION",
            "1",
            "integer",
            "versión del formato (2 en los aero.ini oficiales legibles)",
            "§6.5",
        )
    ]
    for n in range(3):
        for key, unit, dtype, desc, loc in KEYS:
            aid = f"ac:aero.ini/WING_{n}/{key}"
            if aid not in ids:
                new.append(attr(aid, unit, dtype, desc, loc))
        for key in ("LUT_AOA_CL", "LUT_AOA_CD"):
            new.append(
                attr(
                    f"ac:aero.ini/WING_{n}/{key}/table",
                    "table:deg->1",
                    "table",
                    f"contenido de la LUT que nombra {key}: x = ángulo (°), y = coeficiente",
                    "§6.5",
                )
            )
    new = [x for x in new if x["id"] not in ids]
    traw = TARGETS.read_text(encoding="utf-8")
    tg = json.loads(traw)
    assert json.dumps(tg, indent=1, ensure_ascii=False) + "\n" == traw
    t = tg["targets"]
    at = t.index("ac:aero.ini/WING_0/NAME")
    order = ["ac:aero.ini/HEADER/VERSION"] + [
        x["id"] for x in new if x["id"] != "ac:aero.ini/HEADER/VERSION"
    ]
    print(f"atributos nuevos: {len(new)}")
    if a.dry_run:
        return 0
    data += new
    ATTR.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    t[at:at] = [order[0]]
    pos = t.index("ac:aero.ini/WING_0/NAME") + 1
    t[pos:pos] = [x for x in order[1:] if x not in t]
    TARGETS.write_text(
        json.dumps(tg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
