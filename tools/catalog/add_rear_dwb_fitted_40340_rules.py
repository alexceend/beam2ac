"""Suspensión trasera ``dwb_fitted`` del exportador 40340 (G2.30, 2026-11-12).

    python tools/catalog/add_rear_dwb_fitted_40340_rules.py [--dry-run]

Para cada regla trasera de doble triángulo ``susp.*_rear_40340@1`` (condición ``linkage`` ∈ {``dwb_5link``,
``dwb_equivalent``}) crea ``susp.*_rear_fitted_40340@1`` con la condición ``linkage == dwb_fitted``
(``src/extract/linkage.py``: trasera de rango 6 que BeamNG guía por equilibrio elástico del conjunto completo y
que ningún subconjunto de 5 barras reproduce). Las condiciones son excluyentes con las de las reglas originales,
que no se tocan. Los anclajes del DWB son EQUIVALENTES (ajustados), no los nodos físicos, y ``SPRING_RATE`` /
``ROD_LENGTH`` incluyen la rigidez y la fuerza de las barras redundantes (IR ``wheel_rate`` /
``spring_preload_travel``); los supuestos de esas reglas lo dicen. Todas ``provisional``. Solo añade; se niega a
sobrescribir. No volver a ejecutar tras escribir las reglas.
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

DATE = "2026-11-12"
AUTHOR = "claude (G2.30, encargada por el usuario)"
DOC = "docs/research/g230_trasera_sobredeterminada_2026-11-12.md"
RULES_DIR = ROOT / "catalog" / "rules"
LINKAGE = "ir.suspension.axles.rear.linkage"
FITTED_ASSUMPTIONS = [
    "dwb_fitted: la trasera tiene más de 5 barras de rango 6 (longitudes de reposo incompatibles); BeamNG la guía por "
    "el equilibrio elástico del conjunto completo [M, banco cinemático de G2.29: dentro del ruido], no por un "
    "subconjunto de 5 barras (0,35–0,42° de convergencia fuera)",
    "los 5 puntos del chasis del doble triángulo están AJUSTADOS (Gauss-Newton regularizado hacia los nodos reales; "
    "máx. 40 mm de desplazamiento) para reproducir caída, convergencia, vía y avance del centro de rueda del conjunto "
    "completo en ±60 mm a ≤ 0,5 de la tolerancia (0,01° / 0,1 mm): son anclajes equivalentes, no los físicos "
    "(src/extract/linkage.py::_fit_dwb)",
    "el giro de la mangueta alrededor del eje de rueda no se ajusta: la palanca del muelle del DWB ajustado difiere "
    "0,2–0,8 % de la del conjunto completo (≤ 1,6 % en la rigidez en rueda)",
]
STIFFNESS_ASSUMPTIONS = [
    "las barras redundantes almacenan energía elástica ½Σk·ΔL² (longitudes de reposo L·beamPrecompression) en el "
    "equilibrio del conjunto completo: rigidez en rueda d²E/dh² y fuerza −dE/dh en la pose del JBeam se SUMAN a las "
    "del muelle (cálculo; contraste con el banco de BeamNG en docs/research/g230_trasera_sobredeterminada_2026-11-12.md)",
    "contraste [M] con el banco cinemático PARADO de BeamNG (G2.30; V = 0, sin fuerza lateral de rodadura): rigidez en rueda trasera medida 41,6–42,1 N/mm (hyacinth) y 46,8–47,6 N/mm (autist) frente a 40,1 y 44,3 predichas con las barras (+4 %, +6 %) y 23,2 y 31,5 sin ellas (+80 %, +50 %). NO es una validación independiente: en el mismo banco los controles SIN barras redundantes salen también por encima de la predicción (traseras dwb_equivalent: +44 % sucette_poubelle_turbo, +51 % atlanta; delanteras: −1 % autist, +4 % hyacinth, +12 % quail, +17 % sucette_turbo, +22 % atlanta), exceso sin explicar; quail apoya sobre los topes y no se contrasta. La fuerza en reposo de las barras (228–338 N) no se ha medido por separado",
    "SPRING_RATE·ROD_LENGTH = fuerza de APOYO en la pose del JBeam = muelle (beamSpring·compresión·MR) − fuerza de las "
    "barras redundantes sobre la rueda (−dE/dh > 0: empujan a comprimir y restan apoyo); ROD_LENGTH = esa fuerza / "
    "SPRING_RATE total",
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
        if "_fitted_" in p.stem:
            continue
        d = json.loads(p.read_text("utf-8"))
        for c in d.get("conditions", []):
            if c["fact"] == LINKAGE and c["op"] == "in" and "dwb_equivalent" in c["value"]:
                out.append(p.stem)
    return out


def derive(key: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))
    seen = 0
    for c in d["conditions"]:
        if c["fact"] == LINKAGE:
            c["value"] = ["dwb_fitted"]
            seen += 1
    assert seen == 1, (key, seen)
    keep = [a for a in d.get("assumptions", []) if not a.startswith(DROP)]
    extra = FITTED_ASSUMPTIONS + (
        STIFFNESS_ASSUMPTIONS
        if d["target"].endswith(("/SPRING_RATE", "/ROD_LENGTH"))
        else []
    )
    d.update(
        id=d["id"].replace("_40340", "_fitted_40340"),
        version=1,
        status="provisional",
        supersedes="",  # no sustituye a la regla de origen: son excluyentes
        assumptions=keep + extra + ["ningún 40340 se ha probado en AC"],
        history=[
            {
                "date": DATE,
                "author": AUTHOR,
                "action": "create",
                "reason": f"derivada de {key} para la trasera dwb_fitted",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
        notes="G2.30",
    )
    assert "_fitted_" in d["id"], d["id"]
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "report", "path": DOC, "locator": "", "sha256": "", "note": ""}
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
