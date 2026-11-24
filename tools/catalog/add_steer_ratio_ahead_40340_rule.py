"""STEER_RATIO con la barra de dirección DELANTE del eje, exportador 40340 (2026-11-16).

    python tools/catalog/add_steer_ratio_ahead_40340_rule.py [--dry-run]

NO volver a ejecutar tras escribir la regla (reglas inmutables, solo se añade).

``ctrl.steer_ratio_rod_ahead_200409@1`` (signo +1) no tenía clon para 40340 (los cinco
40340 de G2.30 llevan la barra detrás del eje). ``bisexual_aspiration`` (eje rígido delantero
con dirección, DWB equivalente: ``src/extract/steered_axle.py``) la lleva delante. Es la
misma regla con la versión del exportador cambiada; provisional, una sola muestra.
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

DATE = "2026-11-16"
AUTHOR = "claude (conversión de bisexual_aspiration, encargada por el usuario)"
SRC = ROOT / "catalog" / "rules" / "ctrl.steer_ratio_rod_ahead_200409@1.json"


def rule() -> S.Rule:
    d = json.loads(SRC.read_text("utf-8"))
    n = 0
    for c in d["conditions"]:
        if c["fact"] == "ir.formats.exporter_version":
            assert c["value"] == ["200409"]
            c["value"] = ["40340"]
            n += 1
    assert n == 1
    d["supersedes"] = ""
    d.update(
        id="ctrl.steer_ratio_rod_ahead_40340",
        version=1,
        status="provisional",
        assumptions=list(d["assumptions"])
        + [
            "clon de la regla del 200409 para el exportador 40340: una sola muestra "
            "(bisexual_aspiration), sin probar en AC"
        ],
        history=[
            {
                "date": DATE,
                "author": AUTHOR,
                "action": "create",
                "reason": "clon de ctrl.steer_ratio_rod_ahead_200409@1 para 40340",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
        notes="bisexual_aspiration 2026-11-16",
    )
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    r = rule()
    if (REPO_CATALOG / "rules" / f"{r.id}@{r.version}.json").exists():
        raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print("ok (sin escribir)")
        return 0
    write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print("escrita 1 regla")
    return 0


if __name__ == "__main__":
    sys.exit(main())
