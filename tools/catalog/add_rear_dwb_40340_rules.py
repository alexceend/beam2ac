"""Suspensión trasera por estructura del exportador 40340 (G2.29, 2026-11-11).

    python tools/catalog/add_rear_dwb_40340_rules.py [--dry-run]

Clona para el 40340 las reglas TRASERAS de doble triángulo del 200409 que no tenían clon (TYPE=DWB, 8 puntos,
muelle, amortiguadores, topes, caída, ROD_LENGTH, ARB), en su versión vigente, con ``clone()`` de
``clone_200409_rules_to_40340.py``. Sus condiciones exigen la estructura medida
(``linkage`` ∈ {``dwb_5link``, ``dwb_equivalent``}): hoy las cumple la trasera de brazo semitirado de los
``sucette`` (desde G2.29 ``linkage.py`` no la da por «acoplada»: sus pivotes van fijos al chasis); la trasera
``overconstrained`` de autist/hyacinth/quail sigue sin regla. Solo añade.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clone_200409_rules_to_40340 as C  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

C.AUTHOR = "claude (G2.29, encargada por el usuario)"
C.DOC = "docs/research/g229_consolidacion_2026-11-11.md"
C.ASSUMPTION = (
    "clon de la regla del 200409 para el exportador 40340: misma extracción por estructura "
    "(src/extract/linkage.py) y misma mecánica del juego; un brazo semitirado se exporta como el doble "
    "triángulo equivalente (pivotes del chasis en el eje de giro del brazo; desviación ≤ 0,01° en ±60 mm); "
    "ningún 40340 se ha probado en AC"
)


def keys() -> list[str]:
    latest: dict[str, int] = {}
    for p in C.RULES_DIR.glob("susp.*_200409@*.json"):
        m = re.fullmatch(r"(susp\.\w+_rear)_200409@(\d+)", p.stem)
        if m and not (C.RULES_DIR / f"{m[1]}_40340@1.json").exists():
            latest[m[1]] = max(latest.get(m[1], 0), int(m[2]))
    return [f"{k}_200409@{v}" for k, v in sorted(latest.items())]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = []
    for k in keys():
        d = S.to_dict(C.clone(k))
        d["notes"] = "G2.29"
        rs.append(S.from_dict(d))
    print([f"{r.id}" for r in rs])
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
