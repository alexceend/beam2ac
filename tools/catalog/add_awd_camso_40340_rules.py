"""Tracción total (AWD) con caja manual y ``camsoEngine`` del exportador 40340 (G2.30, 2026-11-12).

    python tools/catalog/add_awd_camso_40340_rules.py [--dry-run]

``autist_cuanto`` (40340, ``camsoEngine``, caja manual, AWD con central ``lsd``) solo tenía la propuesta de
vehículo de las marchas y ninguna regla de ``[TRACTION] TYPE`` ni de ``[AWD]``: las reglas AWD de G2.12/G2.23 se
limitan a 200409 + ``combustionEngine``. Se derivan para ``40340`` + ``camsoEngine``:

* ``dt.traction_awd``, ``dt.awd_front_share`` y las 9 ``dt.awd_<rol>_diff_<clave>_lsd`` (misma transformación; la
  mitad de AC está demostrada en G2.22 y no depende del exportador; la de BeamNG es ``differential.lua`` del
  juego, igual en todos los vehículos): solo cambian la versión del exportador y el tipo de motor;
* ``dt.gear_N`` / ``dt.gear_r`` / ``dt.gear_count`` / ``dt.final`` de la caja ``manualGearbox`` con tracción AWD
  (el cálculo 4WD de AC: relación = FINAL·marcha como en el cálculo 2WD de AC, G2.11); las reglas de arquitectura ``*_2wd@3`` se
  limitan a FWD/RWD y las del DCT a ``dctGearbox``.

Todas ``provisional``. No toca las reglas de origen. Solo añade; se niega a sobrescribir. No volver a ejecutar.
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
VERSION = "ir.formats.exporter_version"
ENGINE = "ir.engine.device_type"
KIND = "ir.drivetrain.gearbox.kind"
LAYOUT = "ir.drivetrain.layout"
AWD = [
    "dt.traction_awd@1",
    "dt.awd_front_share@1",
] + [
    f"dt.awd_{r}_diff_{k}_lsd_200409@1"
    for r in ("center", "front", "rear")
    for k in ("preload", "power", "coast")
]
GEARS = (
    [f"dt.gear_{n}_dct@1" for n in range(1, 9)]
    + ["dt.gear_r_dct@1", "dt.gear_count_dct@1", "dt.final_dct@1"]
)
ASSUMPTION = (
    "derivada de la regla del 200409/DCT para el exportador 40340 con camsoEngine y caja manual: la mecánica de AC "
    "(el cálculo 4WD de AC, G2.12/G2.22) y la del juego (differential.lua, G2.12.1) no dependen del exportador ni del motor; "
    "solo hay contraste estático con autist_cuanto (no probado en AC)"
)


def derive(key: str, gears: bool) -> S.Rule:
    d = json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))
    seen = 0
    for c in d.get("conditions", []) + d.get("scope", {}).get("conditions", []):
        if c["fact"] == VERSION:
            assert c["value"] == ["200409"], (key, c)
            c["value"] = ["40340"]
            seen += 1
        elif c["fact"] == ENGINE:
            assert c["value"] == "combustionEngine", (key, c)
            c["value"] = "camsoEngine"
        elif c["fact"] == KIND:
            assert c["value"] == "dctGearbox", (key, c)
            c["value"] = "manualGearbox"
    assert seen == 1, (key, seen)
    if gears:  # las de arquitectura (FWD/RWD) no cubren el AWD: se excluyen entre sí
        d["conditions"].append({"fact": LAYOUT, "op": "eq", "value": "AWD"})
    new = (
        key.split("@")[0].replace("_200409", "_40340")
        if "_200409" in key
        else key.split("@")[0].replace("_dct", "_awd_manual_40340")
        if gears
        else key.split("@")[0] + "_40340"
    )
    d.update(
        id=new,
        version=1,
        status="provisional",
        supersedes="",
        assumptions=list(d.get("assumptions", [])) + [ASSUMPTION],
        history=[
            {
                "date": DATE,
                "author": AUTHOR,
                "action": "create",
                "reason": f"derivada de {key} para 40340 + camsoEngine + AWD",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
        notes="G2.30",
    )
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "report", "path": DOC, "locator": "", "sha256": "", "note": ""}
    ]
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = [derive(k, False) for k in AWD] + [derive(k, True) for k in GEARS]
    have = {p.stem for p in RULES_DIR.glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    print([r.id for r in rs])
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
