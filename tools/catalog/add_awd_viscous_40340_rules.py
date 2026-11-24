"""AWD con diferenciales ``viscous`` en 40340 + camsoEngine (2026-11-22).

    python tools/catalog/add_awd_viscous_40340_rules.py [--dry-run]

NO volver a ejecutar tras escribir las reglas (reglas inmutables, solo se añade).

Muestra: ``agus_1_nrt_1`` (central y ejes ``viscous``, ``viscousCoef`` 19,04, ``viscousTorque`` 190,39,
``diffTorqueSplit`` 0,57). Encargo del usuario: una regla GENERAL para coches con diferenciales viscosos.

Derivación (``differential.lua`` ``viscousLSDUpdateTorque`` l. 68-83, 399-404): salida1 = T·A − τ,
salida2 = T·(1−A) + τ con τ = clamp(viscousCoef·Δω, ±viscousTorque). Saturado, la diferencia entre
salidas es 2·viscousTorque: es la MISMA relación que la del LSD con lsdTorque = viscousTorque (G2.12.1/G2.21,
factor 2), sin término dependiente del par (⇒ POWER = COAST = 0). Por eso se clonan las reglas ``lsd``
sustituyendo ``lsdPreload`` por ``viscousTorque`` y las ``open`` (constante 0) para POWER/COAST.
``TYPE = AWD`` y ``FRONT_SHARE`` clonan las del central open/lsd (reparto fijo ``diffTorqueSplit``).
Es una APROXIMACIÓN (docs/research/diferencial_viscoso_y_electronica_ac_2026-11-21.md §1).
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
DT = "ir.drivetrain."
DATE = "2026-11-22"
BASE_CONDITIONS = [
    {"fact": "ir.formats.exporter_version", "op": "in", "value": ["40340"]},
    {"fact": "ir.engine.device_type", "op": "eq", "value": "camsoEngine"},
    {"fact": DT + "layout", "op": "eq", "value": "AWD"},
    {"fact": DT + "differentials.center.kind", "op": "eq", "value": "viscous"},
]
COMMON = [
    "viscous: salida1 = T·A − τ, salida2 = T·(1−A) + τ, τ = clamp(viscousCoef·Δω, ±viscousTorque) "
    "(differential.lua viscousLSDUpdateTorque l. 68-83, 399-404): saturado, la diferencia entre salidas es "
    "2·viscousTorque, igual que un lsd con lsdTorque = viscousTorque (misma relación y factor 2 que las reglas lsd)",
    "sin término dependiente del par de entrada ⇒ POWER = COAST = 0",
    "APROXIMACIÓN: el viscoso satura a Δω = viscousTorque/viscousCoef (agus: ≈ 10 rad/s) y AC aplica "
    "lim·Δω/(|Δω|+0,1); no es el mismo comportamiento por debajo de la saturación. Una sola muestra "
    "(agus_1_nrt_1), sin probar en AC",
]
DOC = {
    "type": "report",
    "path": "docs/research/diferencial_viscoso_y_electronica_ac_2026-11-21.md",
    "locator": "§1 (viscoso en AC)",
    "sha256": "",
    "note": "",
}


def _base(key: str) -> dict:
    return json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))


def _finish(d: dict, new_id: str, base_key: str, extra: list[str]) -> S.Rule:
    d.update(
        id=new_id,
        version=1,
        supersedes="",
        status="provisional",
        notes="agus_1_nrt_1 viscoso 2026-11-22",
        history=[
            {
                "date": DATE,
                "author": "claude (conversión de agus_1_nrt_1, encargada por el usuario)",
                "action": "create",
                "reason": f"derivada de {base_key} para diferenciales viscous",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
    )
    d["assumptions"] = list(d.get("assumptions", [])) + COMMON + extra
    d["source_doc"] = list(d.get("source_doc", [])) + [DOC]
    return S.from_dict(d)


def make(new_id: str, base_key: str, *, axle: str | None = None, viscous_input: str | None = None,
         formula: str | None = None, extra: list[str] | None = None) -> S.Rule:
    d = _base(base_key)
    d["conditions"] = [dict(c) for c in BASE_CONDITIONS]
    if axle:
        d["conditions"].append({"fact": DT + f"differentials.{axle}.kind", "op": "eq", "value": "viscous"})
    if viscous_input:  # lsdPreload → viscousTorque
        d["inputs"] = {**d["inputs"], "x": viscous_input}
        d["transform"]["input_units"] = {**d["transform"]["input_units"], "x": "N·m"}
    if formula:
        d["transform"]["formula"] = formula
    return _finish(d, new_id, base_key, extra or [])


def rules() -> list[S.Rule]:
    out = [
        make("dt.traction_awd_viscous_40340", "dt.traction_awd_40340@1"),
        make("dt.awd_front_share_viscous_40340", "dt.awd_front_share_40340@1"),
    ]
    for k in ("power", "coast"):
        out.append(make(f"dt.awd_center_diff_{k}_viscous_40340", f"dt.awd_center_diff_{k}_open@1",
                        formula="viscous: sin término dependiente del par ⇒ 0"))
    out.append(make("dt.awd_center_diff_preload_viscous_40340", "dt.awd_center_diff_preload_lsd_40340@1",
                    viscous_input=DT + "differentials.center.viscous_torque",
                    formula="CENTRE_DIFF_PRELOAD = 2 · viscousTorque · FINAL"))
    for ax in ("front", "rear"):
        for k in ("power", "coast"):
            out.append(make(f"dt.awd_{ax}_diff_{k}_viscous_40340", f"dt.awd_{ax}_diff_{k}_openmode_lsdc_40340@1",
                            axle=ax, formula="viscous: sin término dependiente del par ⇒ 0"))
        out.append(make(f"dt.awd_{ax}_diff_preload_viscous_40340", f"dt.awd_{ax}_diff_preload_lsd_40340@1",
                        axle=ax, viscous_input=DT + f"differentials.{ax}.viscous_torque",
                        formula=f"{ax.upper()}_DIFF_PRELOAD = 2 · viscousTorque"))
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
