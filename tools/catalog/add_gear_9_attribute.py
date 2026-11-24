"""Atributo ``[GEARS] GEAR_9`` de ``drivetrain.ini`` y su aplicabilidad (G2.19, 2026-11-06).

    python tools/catalog/add_gear_9_attribute.py [--dry-run]

Evidencia (G2.11, ``output/g211_transmissions_2026-11-02/acs_drivetrain/``, [C]):
transmisión de AC lee ``[GEARS] COUNT`` y después ``GEAR_%d`` de 1 a COUNT con
``getFloat`` (``addGear`` en un bucle, sin límite superior); una clave ausente vale 0.
Por tanto AC admite 9 marchas, y una caja de 9 marchas (``2021_rpm_portland_touring``,
convertidor de par) necesita la clave ``GEAR_9``, que el catálogo no tenía
(``export.validate_package``/``car_validate.gears_problem`` rechaza COUNT 9 sin ella).

Añade:

* el atributo ``ac:drivetrain.ini/GEARS/GEAR_9`` (unidad ``1``, como GEAR_1…8) y el
  objetivo en ``catalog/targets/ac_physics.json``;
* la regla de aplicabilidad genérica ``app.gears.gear_9@1``: solo aplica con
  ``ir.drivetrain.gearbox.forward_count`` ≥ 9 (mismo criterio que ``dt.gear_8@2``).
  Con menos marchas, «no aplica»; sin el dato, el motor da «indeterminada».

NO añade ninguna regla de valor: la única caja de 9 marchas del corpus es automática con
convertidor de par (D-G211-TC, sin equivalente en AC); sus marchas siguen sin aplicarse.
Solo añade; se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

ATTR = ROOT / "catalog/attributes/assettocorsa.json"
TARGETS = ROOT / "catalog/targets/ac_physics.json"
DATE = "2026-11-06"
AUTHOR = "claude (G2.19: desbloqueo integral del 200409, encargada por el usuario)"
CODE = "output/g211_transmissions_2026-11-02/acs_drivetrain/c"
DOC = "docs/research/g219_desbloqueo_integral_200409.md"
TARGET = "ac:drivetrain.ini/GEARS/GEAR_9"


def attribute() -> dict:
    return {
        "schema": "beam2ac.attribute/1",
        "id": TARGET,
        "game": "assettocorsa",
        "unit": "1",
        "dtype": "number",
        "description": "relación de la 9.ª marcha (transmisión de AC: GEAR_%d de 1 a "
        "COUNT, sin límite; ausente = 0)",
        "doc": [
            {
                "type": "code",
                "path": CODE,
                "locator": "transmisión de AC (bucle GEAR_%d hasta COUNT)",
                "sha256": "",
                "note": "",
            },
            {
                "type": "doc",
                "path": "docs/research/g211_transmissions_2026-11-02.md",
                "locator": "COUNT sin límite (addGear)",
                "sha256": "",
                "note": "",
            },
        ],
        "required_for_load": False,
    }


def rule() -> S.Rule:
    ev = [
        S.EvidenceRef(type="code", path=CODE, locator="transmisión de AC"),
        S.EvidenceRef(type="report", path=DOC, locator="§7 nueve marchas"),
    ]
    return S.Rule(
        id="app.gears.gear_9",
        version=1,
        target=TARGET,
        kind="applicability",
        transform=S.Transform(id="constant", params={"value": None}, output_unit="1"),
        scope=S.Scope(level="generic"),
        status="validated",
        applies_when=[
            S.Condition(fact="ir.drivetrain.gearbox.forward_count", op="ge", value=9)
        ],
        source_doc=ev,
        target_doc=ev,
        evidence=ev,
        notes="AC solo lee GEAR_9 si COUNT ≥ 9",
        history=[
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                status_to="validated",
                reason="loadINI lee GEAR_1…COUNT: GEAR_9 solo con 9 o más marchas",
            )
        ],
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    raw = ATTR.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=1, ensure_ascii=False) + "\n" == raw
    if any(x["id"] == TARGET for x in data):
        raise SystemExit(f"ya existe: {TARGET}")
    traw = TARGETS.read_text(encoding="utf-8")
    tg = json.loads(traw)
    assert json.dumps(tg, indent=1, ensure_ascii=False) + "\n" == traw
    tlist = tg["targets"]
    if TARGET in tlist:
        raise SystemExit(f"objetivo ya existe: {TARGET}")
    r = rule()
    S.from_dict(S.to_dict(r))
    if a.dry_run:
        print("ok (sin escribir): 1 atributo, 1 objetivo, 1 regla")
        return 0
    # el atributo y el objetivo van justo después de GEAR_8 (orden de las marchas)
    i = next(k for k, x in enumerate(data) if x["id"].endswith("/GEARS/GEAR_8")) + 1
    data.insert(i, attribute())
    ATTR.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    j = tlist.index("ac:drivetrain.ini/GEARS/GEAR_8") + 1
    tlist.insert(j, TARGET)
    TARGETS.write_text(
        json.dumps(tg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
