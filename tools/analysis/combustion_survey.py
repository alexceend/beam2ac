"""Contraste del adaptador ``combustionEngine`` con los ``info_*.json`` oficiales.

    python tools/analysis/combustion_survey.py
        --inventory output/reference_audit_2026-10-25/bng_inventory.json
        --vehicles <BeamNG>/content/vehicles --out output/<nueva>

Recorre las configuraciones de coches y camiones con ``Induction Type == NA`` y
combustible gasolina/diésel, lee el motor con ``src.extract.engine_combustion`` (zip sin
extraer) y compara el par y la potencia máximos con los declarados. NO escribe física:
solo ``survey.json`` y ``summary.json``. Índice de piezas: uno por zip.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.extract.engine_combustion import (  # noqa: E402
    HP_W,
    read_combustion_engine,
    zip_resource_reader,
)
from src.jbeam import (  # noqa: E402
    PartIndex,
    ZipSource,
    load_pc,
    resolve,
    vehicle_dirs_in_zip,
)
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402


def _stats(d: list[float]) -> dict[str, Any]:
    if not d:
        return {"n": 0}
    s = sorted(d)
    return {
        "n": len(d),
        "abs_le_0_5": sum(abs(x) <= 0.5 for x in d),
        "abs_le_1": sum(abs(x) <= 1.0 for x in d),
        "abs_le_5pct": None,
        "median": s[len(s) // 2],
        "min": s[0],
        "max": s[-1],
    }


def survey(inventory: dict[str, Any], vehicles: Path) -> list[dict[str, Any]]:
    common = ZipSource(vehicles / "common.zip", "vehicles/common/")
    rows: list[dict[str, Any]] = []
    for v in inventory["zips"]:
        if not any(
            m.get("Type") in ("Car", "Truck") for m in v.get("model_meta", {}).values()
        ):
            continue
        cfgs = [
            c
            for c in v.get("configs", [])
            if c.get("Induction Type") == "NA"
            and c.get("Power") is not None
            and c.get("Fuel Type") in ("Gasoline", "Diesel")
        ]
        if not cfgs:
            continue
        zp = vehicles / v["zip"]
        try:
            prefixes = vehicle_dirs_in_zip(zp)
            src = ZipSource(zp, prefixes[0])
            index = PartIndex.from_sources(src, common)
            pcs = {m.name: m for m in src.members(".pc")}
        except (
            Exception
        ) as ex:  # el inventario incluye zips que no son de un solo vehículo
            rows.append(
                {
                    "zip": v["zip"],
                    "error": f"índice: {type(ex).__name__}: {str(ex)[:90]}",
                }
            )
            continue
        for cfg in cfgs:
            pc = cfg["file"].split("/")[-1].replace("info_", "").replace(".json", ".pc")
            row: dict[str, Any] = {
                "zip": v["zip"],
                "pc": pc,
                "T_info": cfg.get("Torque"),
                "P_info": cfg.get("Power"),
            }
            try:
                reader = zip_resource_reader(zp, vehicles / "common.zip")
                e = read_combustion_engine(resolve(index, load_pc(pcs[pc])), reader)
            except Exception as ex:
                row["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
            else:
                row.update(
                    T=round(e.peak_torque[1], 2),
                    P=round(e.peak_power[1] / HP_W, 2),
                    forced=e.forced_induction,
                )
            rows.append(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    out: dict[str, Any] = {
        "configs": len(rows),
        "read": len(ok),
        "errors": len(rows) - len(ok),
        "errors_by_kind": Counter(
            r["error"][:60] for r in rows if "error" in r
        ).most_common(10),
    }
    for name, key, ref in (("power_hp", "P", "P_info"), ("torque_nm", "T", "T_info")):
        d = [r[key] - r[ref] for r in ok if isinstance(r.get(ref), (int, float))]
        rel = [
            (r[key] - r[ref]) / r[ref]
            for r in ok
            if isinstance(r.get(ref), (int, float)) and r[ref]
        ]
        st = _stats(d)
        st["abs_le_5pct"] = sum(abs(x) <= 0.05 for x in rel)
        out[name] = st
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", type=Path, required=True)
    ap.add_argument("--vehicles", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    rows = survey(inventory, args.vehicles)
    summary = summarize(rows)
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "survey.json").write_text(
        json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    ensure_safe_output_path(out / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
