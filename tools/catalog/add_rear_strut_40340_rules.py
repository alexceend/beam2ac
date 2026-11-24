"""Suspensión trasera MacPherson del exportador 40340 (2026-11-19, eagle_inca_p395).

    python tools/catalog/add_rear_strut_40340_rules.py [--dry-run]

Clona para el 40340 las reglas TRASERAS de puntal con raíl del 200409 (``susp.*_rear_strut_200409``; STRUT_CAR/TYRE,
WBCAR_*/WBTYRE_*, muelle, amortiguadores, topes, caída, ROD_LENGTH, ARB) en su versión vigente, con ``clone()`` de
``clone_200409_rules_to_40340.py``. Sus condiciones exigen la estructura medida (``linkage`` = ``strut_rail``), que
``src/extract/linkage.py`` ya da a la trasera en H del eagle_inca (4 barras, una redundante: ``_drop_redundant_bars``).
Hasta hoy el 40340 solo tenía las reglas delanteras de puntal y TYPE=STRUT trasero (propuesta), así que un 40340 con
trasera MacPherson quedaba con 13 claves sin valor. Solo añade; se niega a sobrescribir.
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

C.DATE = "2026-11-19"
C.AUTHOR = "claude (eagle_inca_p395, encargada por el usuario)"
C.DOC = "docs/research/trasera_macpherson_h_40340_2026-11-19.md"
C.ASSUMPTION = (
    "clon de la regla del 200409 para el exportador 40340: misma extracción por estructura "
    "(src/extract/linkage.py) y misma mecánica del juego; ningún 40340 con trasera MacPherson se ha "
    "probado en AC"
)


def keys() -> list[str]:
    latest: dict[str, int] = {}
    for p in C.RULES_DIR.glob("susp.*_rear_strut_200409@*.json"):
        m = re.fullmatch(r"(susp\.\w+_rear_strut)_200409@(\d+)", p.stem)
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
        d["notes"] = "eagle_inca_p395 2026-11-19"
        rs.append(S.from_dict(d))
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
