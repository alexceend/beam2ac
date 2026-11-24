"""Ejes lsd / open / torqueVectoring con central ``viscous`` en 40340 + camsoEngine (2026-11-22).

    python tools/catalog/add_awd_viscous_center_axles_40340_rules.py [--dry-run]

NO volver a ejecutar tras escribir las reglas (reglas inmutables, solo se añade).

Muestra: ``autist_quantic`` (central ``viscous``, ejes delantero y trasero ``lsd``). Las reglas ``*_lsd_40340`` y
``*_openmode_lsdc_40340`` exigían central open/lsd; el modo de un eje no depende del central (differential.lua
l. 126-134; mismo argumento que ``add_awd_lsd_center_tv_40340_rules.py``), así que son clones con la condición
del central cambiada a ``center.kind == viscous``. Valores y transformaciones idénticos a los de las reglas de origen.
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
CENTER = "ir.drivetrain.differentials.center.kind"
NOTE = (
    "el modo del eje no depende del diferencial central (differential.lua l. 126-134); central viscous: una "
    "sola muestra (autist_quantic), sin probar en AC"
)


def clone(base_key: str, new_id: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{base_key}.json").read_text("utf-8"))
    d["conditions"] = [c for c in d["conditions"] if c["fact"] != CENTER] + [
        {"fact": CENTER, "op": "eq", "value": "viscous"}
    ]
    d.update(
        id=new_id,
        version=1,
        supersedes="",
        status="provisional",
        notes="autist_quantic central viscoso 2026-11-22",
        history=[
            {
                "date": "2026-11-22",
                "author": "claude (conversión de autist_quantic, encargada por el usuario)",
                "action": "create",
                "reason": f"clon de {base_key} con central viscous",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
    )
    d["assumptions"] = list(d.get("assumptions", [])) + [NOTE]
    return S.from_dict(d)


def rules() -> list[S.Rule]:
    out = []
    for ax in ("front", "rear"):
        for k in ("power", "coast", "preload"):
            out.append(
                clone(f"dt.awd_{ax}_diff_{k}_lsd_40340@1", f"dt.awd_{ax}_diff_{k}_lsd_viscc_40340")
            )
            out.append(
                clone(
                    f"dt.awd_{ax}_diff_{k}_openmode_lsdc_40340@1",
                    f"dt.awd_{ax}_diff_{k}_openmode_viscc_40340",
                )
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    rs = rules()
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
