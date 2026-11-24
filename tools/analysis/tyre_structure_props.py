"""Extrae las propiedades ESTRUCTURALES del neumático (muelles de la banda, costados,
periferia, refuerzos) de los vehículos con medida del banco (2026-11-16).

    python tools/analysis/tyre_structure_props.py --out output/<nueva>/structure.json

Para cada registro de ``measurements/beamng_bench/tyre`` carga el vehículo que midió
(``bench.vehicle_files``), resuelve la configuración con el ``.pc`` cuyo neumático coincide
con el de la medida y guarda las propiedades numéricas de ``pressureWheels`` de cada eje
(expresiones ``$=`` evaluadas con las variables de la configuración). Solo lectura.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.extract.components import collect_wheel_props  # noqa: E402
from src.jbeam import evaluate  # noqa: E402
from src.pipeline.physics_preview import load_vehicle_dir  # noqa: E402
from src.pipeline.physics_preview import preview_physics  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402


def vehicle_dir_of(rec: dict) -> Path | None:
    for f in rec["bench"].get("vehicle_files", []):
        m = re.match(r"(.*?vehicles/[^/]+)/", f.replace("\\", "/"))
        if m and (ROOT / m.group(1)).is_dir():
            return ROOT / m.group(1)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    out = ensure_safe_output_path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(glob.glob(str(ROOT / "measurements/beamng_bench/tyre/*.json"))):
        rec = json.loads(Path(f).read_text("utf-8"))
        vd = vehicle_dir_of(rec)
        item = {"record": Path(f).stem, "vehicle_dir": str(vd) if vd else None}
        rows.append(item)
        if vd is None:
            item["error"] = "sin carpeta de vehículo"
            continue
        want = {ax: rec["axles"][ax]["wheel_params"] for ax in rec["axles"]}
        best = None
        for pc in sorted(vd.glob("*.pc")):
            try:
                pv = preview_physics(load_vehicle_dir(vd, pc.name), 0.0)
                variables = pv.resolved.variable_values()
                props = {wp.name: wp.props for wp in collect_wheel_props(pv.resolved)}
                ok = True
                axles = {}
                for ax, names in (("front", ("FL", "FR")), ("rear", ("RL", "RR"))):
                    p = props.get(names[0]) or props.get(names[1])
                    if p is None:
                        ok = False
                        break
                    ev = {}
                    for k, v in p.items():
                        try:
                            x = evaluate(v, variables)
                        except Exception:  # noqa: BLE001
                            continue
                        if isinstance(x, (int, float)) and not isinstance(x, bool):
                            ev[k] = float(x)
                    w = want[ax]
                    if abs(ev.get("noLoadCoef", -1) - w["noLoadCoef"]) > 1e-3:
                        ok = False
                    axles[ax] = ev
                if ok:
                    best = (pc.name, axles)
                    break
            except Exception as exc:  # noqa: BLE001
                item.setdefault("errors", []).append(f"{pc.name}: {type(exc).__name__}: {exc}")
        if best:
            item["pc"], item["axles"] = best
        else:
            item["error"] = "ningún .pc coincide con la medida"
        print(item["record"], item.get("pc"), item.get("error", ""), flush=True)
    out.write_text(json.dumps(rows, indent=1), "utf-8")
    print("escrito", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
