"""Aerodinámica ``cefaero`` del exportador 40340 (G2.29, 2026-11-11).

    python tools/catalog/add_aero_cefaero_40340_rules.py [--dry-run]

``cefaero.lua`` (el Lua del exportador; mismo archivo en los cinco 40340 y en el Ibiza) es ANALÍTICO: arrastre
``vehicleArea·½ρv²`` en un nodo y carga ``cla·½ρv²`` en cada grupo de nodos, sin ángulo de ataque. No hace
falta banco. El importador lo expresa en los mismos campos del IR que la aerodinámica medida del 200409
(``aero.{cda,cla_front_axle,cla_rear_axle}_vs_aoa``, tablas planas; ``src.extract.aero.axle_equivalent``:
sistema de fuerzas equivalente en los centros de eje), así que las 34 reglas ``aero.*_200409@1`` (tres alas de
1 m²) valen tal cual con otras condiciones: exportador 40340 y ``aero.model == cefaero``.

Cada clon ``aero.*_cefaero_40340@1`` cambia solo: condiciones, estado ``provisional``, clase (``measured`` →
``computed``) y los supuestos que hablaban del banco. Solo añade. El 38022 (Ibiza, Porsche) NO se toca: sus
``aero.ini`` son decisiones de proyecto.
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

RULES_DIR = ROOT / "catalog" / "rules"
REWORD = {
    "aerodinámica medida en BeamNG": (
        "aerodinámica analítica de cefaero.lua (l. 117-142): arrastre vehicleArea·½ρv² y carga cla·½ρv² por "
        "grupo de nodos, sin dependencia del ángulo de ataque (LUT plana); cla con ρ₀ = 1,225 (l. 17, 157-163)"
    ),
    "guiñada no representada": (
        "no representado: arrastre lateral (×1,25) y vertical (×1,5) del Lua, factor |arriba·vertical| de la "
        "carga y arrastre de los triángulos de rueda (dragCoef 1, C++ del juego); sin daño aerodinámico"
    ),
    "ningún 200409 se ha probado en AC": "ningún 40340 se ha probado en AC",
    "la medida reparte la carga": (
        "las fuerzas del Lua (nodo de arrastre y nodos de carga) se trasladan a los centros de eje como "
        "sistema de fuerzas equivalente (misma resultante y mismo momento de cabeceo): "
        "src/extract/aero.py axle_equivalent"
    ),
}


def clone(path: Path) -> S.Rule:
    d = json.loads(path.read_text("utf-8"))
    conds = d["conditions"]
    assert [c["fact"] for c in conds] == [
        "ir.formats.exporter_version",
        "ir.aero.model",
        "ir.aero.cda_vs_aoa",
    ], path.name
    conds[0]["value"], conds[1]["value"] = ["40340"], "cefaero"
    assumptions = []
    for a in d["assumptions"]:
        hit = [new for old, new in REWORD.items() if a.startswith(old)]
        assumptions.append(hit[0] if hit else a.replace("medido", "del origen").replace("medida", "del origen"))
    d["supersedes"] = ""
    d.update(
        id=d["id"].replace("_200409", "_cefaero_40340"),
        version=1,
        status="provisional",
        kind="computed" if d["kind"] == "measured" else d["kind"],
        assumptions=assumptions,
        notes="G2.29",
        source_doc=[
            {"type": "code", "path": "src/extract/aero.py", "locator": "build_aero, axle_equivalent",
             "sha256": "", "note": ""},
            {"type": "code", "path": "samples_beamng/thershow_hyacinth_sage/vehicles/hyacinth_sage/lua/"
             "controller/cefaero.lua", "locator": "l. 117-142, 157-163, 226-270 (md5 2320b788…, igual en "
             "los 5 del 40340 y en el Ibiza)", "sha256": "", "note": ""},
        ],
        evidence=[{"type": "report", "path": "docs/research/g229_consolidacion_2026-11-11.md",
                   "locator": "§2.2", "sha256": "", "note": ""}],
        history=[{"date": "2026-11-11", "author": "claude (G2.29, encargada por el usuario)",
                  "action": "create", "reason": f"clon de {path.stem} para cefaero (40340)",
                  "status_from": "", "status_to": "provisional"}],
    )
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = [clone(p) for p in sorted(RULES_DIR.glob("aero.*_200409@1.json"))]
    assert len(rs) == 34, len(rs)
    for r in rs:
        S.from_dict(S.to_dict(r))
        if (RULES_DIR / f"{r.id}@{r.version}.json").exists():
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
