"""Inercia de caja y LSD 2WD ×2 para el exportador 38022 (G2.39, 2026-11-13). Decisión del usuario: D-INV-GBI / D-G227-IBIZA.

    python tools/catalog/add_gearbox_lsd_38022_rules.py [--dry-run]

Mismo mapeo ya validado en AC para 200409/40340 (G2.27: ``[GEARBOX] INERTIA`` 0,01 con aceleración a ≤ 1,5 %
por marcha; B0: ``[DIFFERENTIAL] PRELOAD/POWER/COAST`` ×2, ρ 1,00) y variante ``ib2`` del Ibiza (32/32):

* ``dt.gearbox_inertia_38022@1`` (clon de la de 40340) + ``app.gearbox.inertia@3`` (añade el 38022);
* ``dt.diff_{preload,power,coast}_2wd_lsd_38022@1`` (clones de las de 40340) + ``app.diff2wd.{power,coast}@2``;
* ``dt.preload@4`` (Ibiza, ámbito de vehículo): factor 2 en lugar de 1; sin ella la regla de vehículo @3 ganaría
  a la de familia y el Ibiza quedaría con PRELOAD ×1 y POWER/COAST ×2 (incoherente con ``ib2``).

Todas ``provisional``; clones con ``supersedes = ""``. Solo añade; se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

RULES = ROOT / "catalog/rules"
DATE = "2026-11-13"
AUTHOR = "claude (G2.39: decisión del usuario sobre D-INV-GBI y D-G227-IBIZA)"
DOC = "docs/research/g239_integracion_fisica_ida_2026-11-13.md"
NOTE = (
    "clon de la regla de 40340 para el exportador 38022: misma extracción (camsoEngine, caja manual/secuencial, "
    "diferencial lsd) y mismo destino; la conversión está medida en AC con coches de diagnóstico 38022 (B0, ib2)"
)


def load(key: str) -> dict:
    return json.loads((RULES / f"{key}.json").read_text(encoding="utf-8"))


def hist(status: str, reason: str) -> list[dict]:
    return [
        {
            "date": DATE,
            "author": AUTHOR,
            "action": "create",
            "reason": reason,
            "status_from": "",
            "status_to": status,
        }
    ]


def clone(key: str, new_id: str) -> dict:
    d = load(key)
    conds = copy.deepcopy(d["conditions"])
    for c in conds:
        if c["fact"] == "ir.formats.exporter_version":
            c["value"] = ["38022"]
    d.update(
        id=new_id,
        version=1,
        status="provisional",
        supersedes="",
        notes="G2.39",
        conditions=conds,
        history=hist("provisional", NOTE),
    )
    d["assumptions"] = list(d["assumptions"]) + [NOTE]
    d["evidence"] = list(d["evidence"]) + [
        {"type": "report", "path": DOC, "locator": "", "sha256": "", "note": ""}
    ]
    return d


def widened(key: str, new_version: int) -> dict:
    d = load(key)
    d.update(
        version=new_version,
        supersedes=key,
        notes="G2.39: añade el 38022",
        history=d["history"]
        + hist("validated", "aplica también al 38022 (decisión del usuario)"),
    )
    for c in d["applies_when"]:
        if c["fact"] == "ir.formats.exporter_version":
            c["value"] = sorted(set(c["value"]) | {"38022"})
    return d


def ibiza_preload_x2() -> dict:
    d = load("dt.preload@3")
    d.update(
        version=4,
        supersedes="dt.preload@3",
        notes="G2.39: factor 2 (D-G227-IBIZA, variante ib2)",
        history=d["history"]
        + hist(
            "provisional",
            "BeamNG: diferencia = 2·lsdTorque; AC 2WD: diferencia = lim "
            "(medido, G2.22/B0); ib2 32/32 en AC",
        ),
    )
    d["transform"] = {
        "id": "scale",
        "params": {"factor": 2.0},
        "input_units": {"x": "N·m"},
        "output_unit": "N·m",
        "formula": "PRELOAD = 2 · lsdPreload",
    }
    d["assumptions"] = [
        a for a in d["assumptions"] if not a.startswith("FACTOR DE CONVERSI")
    ] + [
        "factor 2 medido: BeamNG (G2.12.1) 2·lsdTorque entre salidas; AC 2WD (el cálculo 2WD de AC [C]; B0 G2.27) diferencia = lim"
    ]
    return d


def build() -> list[dict]:
    out = [
        clone("dt.gearbox_inertia_40340@1", "dt.gearbox_inertia_38022"),
        widened("app.gearbox.inertia@2", 3),
    ]
    out += [
        clone(f"dt.diff_{k}_2wd_lsd_40340@1", f"dt.diff_{k}_2wd_lsd_38022")
        for k in ("preload", "power", "coast")
    ]
    out += [widened(f"app.diff2wd.{k}@1", 2) for k in ("power", "coast")]
    out.append(ibiza_preload_x2())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    have = {p.stem for p in RULES.glob("*.json")}
    rules = [S.from_dict(d) for d in build()]
    for r in rules:
        if r.key in have:
            raise SystemExit(f"ya existe: {r.key}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rules)} reglas: {[r.key for r in rules]}")
        return 0
    for r in rules:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rules)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
