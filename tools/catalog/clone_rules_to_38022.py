"""Clona al exportador 38022 las reglas vigentes del 40340 (G2.44, 2026-11-14).

    python tools/catalog/clone_rules_to_38022.py [--dry-run]

NO volver a ejecutar tras escribir (reglas inmutables). Informe: ``docs/research/g244_38022_2026-11-14.md``.

Fuente: la ÚLTIMA versión de cada regla cuyo ámbito de versión es exactamente ``["40340"]`` (frenos con codo,
aerodinámica analítica ``cefaero``, suspensión trasera ajustada, neumático SCTM con referencia de κ libre, dirección,
masa, CdG, inercia…), salvo las de tracción total (los cuatro mods 38022 son RWD) y las que 38022 ya tiene propias
(``dt.gearbox_inertia_38022@1``, ``dt.diff_*_2wd_lsd_38022@1``). Cada clon ``<id>_38022@1`` es idéntico salvo la
versión del exportador (``["38022"]``), el estado (``provisional``) y un supuesto que dice qué evidencia tiene.

Alcance: los vehículos 38022 del corpus de mods (porsche_gt3_rs, spicy_mandala, rune_susui, composed_fox) y, por
ámbito, el Ibiza: el Ibiza se verifica aparte (``output/g244_2026-11-14/ibiza_snapshot.py``: sus INI no deben cambiar;
sus decisiones de proyecto prevalecen sobre las reglas).
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

DATE = "2026-11-14"
AUTHOR = "claude (G2.44: cobertura del exportador 38022, misión de desbloqueo)"
DOC = "docs/research/g244_38022_2026-11-14.md"
RULES = ROOT / "catalog" / "rules"
VERSION = "ir.formats.exporter_version"
ASSUMPTION = (
    "clon de la regla del 40340: el 38022 comparte la mecánica de BeamNG; solo hay contraste estático (estructura y masa "
    "frente a la ficha) y las medidas de banco se hicieron con 40340/200409; el neumático necesita el banco propio del coche"
)
SKIP_PARTS = ("_awd_", "dt.awd_", "dt.traction_awd")
OWN = {"dt.gearbox_inertia_40340", "dt.diff_preload_2wd_lsd_40340", "dt.diff_power_2wd_lsd_40340",
       "dt.diff_coast_2wd_lsd_40340"}


def _exporters(d: dict) -> list | None:
    for c in d.get("conditions", []) + d.get("scope", {}).get("conditions", []):
        if c["fact"] == VERSION:
            return c["value"]
    return None


def sources() -> list[dict]:
    latest: dict[str, dict] = {}
    for p in RULES.glob("*.json"):
        d = json.loads(p.read_text("utf-8"))
        if _exporters(d) != ["40340"] or any(x in d["id"] for x in SKIP_PARTS) or d["id"] in OWN:
            continue
        if d["id"] not in latest or d["version"] > latest[d["id"]]["version"]:
            latest[d["id"]] = d
    return [latest[k] for k in sorted(latest)]


def clone(d: dict) -> S.Rule:
    for c in d.get("conditions", []) + d.get("scope", {}).get("conditions", []):
        if c["fact"] == VERSION:
            c["value"] = ["38022"]
    old = f"{d['id']}@{d['version']}"
    if d["id"].startswith("aero."):
        # el aero.ini del Ibiza (referencia protegida) viene de su base física: no cambia
        d["conditions"] = list(d["conditions"]) + [
            {"fact": "ir.identity.vehicle_id", "op": "ne", "value": "seat_ibiza_acfr"}
        ]
    d.update(id=d["id"].replace("_40340", "_38022"), version=1, supersedes="", status="provisional",
             assumptions=list(d.get("assumptions", [])) + [ASSUMPTION], notes="G2.44")
    d["history"] = [{"date": DATE, "author": AUTHOR, "action": "create",
                     "reason": f"clon de {old} para el exportador 38022", "status_from": "",
                     "status_to": "provisional"}]
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "report", "path": DOC, "locator": "", "sha256": "", "note": ""}]
    return S.from_dict(d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    have = {p.stem for p in RULES.glob("*.json")}
    out = []
    for d in sources():
        r = clone(json.loads(json.dumps(d)))
        if r.key in have or r.id == d["id"]:
            print("omitida (ya existe o id sin versión):", r.key)
            continue
        out.append(r)
    for r in out:
        S.from_dict(S.to_dict(r))
    if a.dry_run:
        print(f"ok (sin escribir): {len(out)} reglas")
        return 0
    for r in out:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(out)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
