"""Suspensión trasera torsional ``dwb_coupled`` del exportador 40340 (2026-11-22).

    python tools/catalog/add_rear_dwb_coupled_40340_rules.py [--dry-run]

Para cada regla trasera de doble triángulo ``susp.*_rear_40340@1`` (condición ``linkage`` ∈ {``dwb_5link``,
``dwb_equivalent``}) crea ``susp.*_rear_coupled_40340@1`` con la condición ``linkage == dwb_coupled``
(``src/extract/coupled_axle.py``: eje trasero torsional, cuerpo rígido acoplado de las dos ruedas unido a la
carrocería por una rótula por lado). Las condiciones son excluyentes con las de las reglas originales, que no se
tocan. Los anclajes del DWB son los pivotes reales del brazo (no ajustados): cinemática exacta del giro del
semieje en recorrido simétrico. Sirve para cualquier eje con esa estructura, no para un coche concreto.
Todas ``provisional``. Solo añade; se niega a sobrescribir. No volver a ejecutar tras escribir las reglas.
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

DATE = "2026-11-22"
AUTHOR = "claude (eje trasero torsional, encargada por el usuario)"
DOC = "src/extract/coupled_axle.py"
RULES_DIR = ROOT / "catalog" / "rules"
LINKAGE = "ir.suspension.axles.rear.linkage"
COUPLED_ASSUMPTIONS = [
    "dwb_coupled: eje trasero torsional. Las dos ruedas son un único cuerpo rígido (cierre rígido de nodos, "
    "src/extract/unsprung.py) unido a la carrocería por UNA rótula por lado (nodo con ≥ 3 vigas rígidas a la "
    "carrocería) y suspendido por un muelle y un amortiguador por lado; la torsión de la viga la da una "
    "torsionbar, no una viga rígida (src/extract/coupled_axle.py)",
    "AC no tiene viga de torsión: se traduce cada semieje como una rueda independiente con un DWB de 5 barras "
    "(precedente oficial: Abarth 500 Assetto Corse, viga de torsión real con DWB, docs/PLAN.md). Los 5 puntos del "
    "chasis son los pivotes REALES (rótula propia y rótula del otro lado), no ajustados: el semieje gira "
    "alrededor del eje que los une y las barras reproducen esa trayectoria exacta",
    "recorrido SIMÉTRICO (las dos ruedas suben a la vez): las vigas del semieje a nodos móviles del otro lado se "
    "descartan tras comprobar que conservan su distancia al reflejado (≤ 0,1 mm en ±60 mm; si no, el eje queda "
    "'coupled' y bloquea). La torsión diferencial de la viga no entra en la cinemática: su rigidez va a "
    "[ARB] REAR (torsionbar, misma conversión que una barra estabilizadora); sin contraste en AC",
    "el DWB resultante reproduce el barrido del cuerpo rígido completo a 0,0000° y 0,0000 mm de desviación "
    "(solo un eje en el corpus: ranunculus_alfonsino)",
]
DROP = (
    "modelo de enlaces rígidos de BeamNG",
    "rigidez en rueda = beamSpring·MR²",
    "ROD_LENGTH = compresión del muelle en reposo / MR",
    "clon de la regla del 200409",
)


def sources() -> list[str]:
    out = []
    for p in sorted(RULES_DIR.glob("susp.*_40340@1.json")):
        if "_fitted_" in p.stem or "_coupled_" in p.stem:
            continue
        d = json.loads(p.read_text("utf-8"))
        for c in d.get("conditions", []):
            if (
                c["fact"] == LINKAGE
                and c["op"] == "in"
                and "dwb_equivalent" in c["value"]
            ):
                out.append(p.stem)
    return out


def derive(key: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))
    seen = 0
    for c in d["conditions"]:
        if c["fact"] == LINKAGE:
            c["value"] = ["dwb_coupled"]
            seen += 1
    assert seen == 1, (key, seen)
    keep = [a for a in d.get("assumptions", []) if not a.startswith(DROP)]
    d.update(
        id=d["id"].replace("_40340", "_coupled_40340"),
        version=1,
        status="provisional",
        supersedes="",  # no sustituye a la regla de origen: son excluyentes
        assumptions=keep + COUPLED_ASSUMPTIONS + ["ningún 40340 se ha probado en AC"],
        history=[
            {
                "date": DATE,
                "author": AUTHOR,
                "action": "create",
                "reason": f"derivada de {key} para la trasera torsional dwb_coupled",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
        notes="eje torsional",
    )
    assert "_coupled_" in d["id"], d["id"]
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "code", "path": DOC, "locator": "", "sha256": "", "note": ""}
    ]
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = [derive(k) for k in sources()]
    have = {p.stem for p in RULES_DIR.glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    print([r.id for r in rs])
    if a.dry_run or not rs:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
