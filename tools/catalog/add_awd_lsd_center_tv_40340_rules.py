"""Ejes open/torqueVectoring con central ``lsd`` en 40340 (2026-11-17).

    python tools/catalog/add_awd_lsd_center_tv_40340_rules.py [--dry-run]

NO volver a ejecutar tras escribir las reglas (reglas inmutables, solo se añade).

Muestra: ``bisexual_aspiration_awd_v2.zip`` (central ``lsd`` con ``camso_advawd``, ejes
``torqueVectoring``). Las ``*_openmode_40340`` exigían central ``locked``; el modo de un eje
no depende del central (differential.lua l. 126-134: ``open`` y ``torqueVectoring`` usan la
misma actualización abierta ⇒ POWER = COAST = PRELOAD = 0). Clon con la condición del central
cambiada a ``center.kind == lsd`` (las ``*_lsd_40340`` ya cubren central open/lsd con eje lsd).
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
CENTER = "ir.drivetrain.differentials.center."
KEYS = [
    f"dt.awd_{r}_diff_{k}_openmode_40340@1"
    for r in ("front", "rear")
    for k in ("power", "coast", "preload")
]


def clone(key: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))
    d["conditions"] = [
        c for c in d["conditions"] if not c["fact"].startswith(CENTER)
    ] + [{"fact": CENTER + "kind", "op": "eq", "value": "lsd"}]
    d.update(
        id=d["id"].replace("_openmode_40340", "_openmode_lsdc_40340"),
        version=1,
        supersedes="",
        status="provisional",
        notes="bisexual_aspiration_awd_v2 2026-11-17",
        history=[
            {
                "date": "2026-11-17",
                "author": "claude (conversión de bisexual_aspiration_awd_v2, encargada por el usuario)",
                "action": "create",
                "reason": f"clon de {key} con central lsd",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
    )
    d["assumptions"] = list(d.get("assumptions", [])) + [
        "el modo del eje no depende del diferencial central; una sola muestra, sin probar en AC"
    ]
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    rs = [clone(k) for k in KEYS]
    have = {p.stem for p in RULES_DIR.glob("*.json")}
    for r in rs:
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if ap.parse_args(argv).dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
