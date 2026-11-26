"""Uso de parámetros de freno en los JBeam oficiales y en los mods disponibles (solo lectura).

    python tools/analysis/brake_params_survey.py --out output/<nueva>

Recorre las tablas ``pressureWheels`` de todas las piezas (nivel de pieza, sin resolver
configuraciones) y cuenta las filas modificadoras o filas con opciones que declaran las
claves de freno que ``wheels.lua`` lee. Sirve para saber si el codo (``brakeInputSplit``),
el ABS o el modelo térmico son excepciones de Automation o usos generales. No sustituye
a una resolución por configuración.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.steam_paths import find_game  # noqa: E402
from src.jbeam import PartIndex, ZipSource  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

VEH = (find_game("BeamNG.drive") or Path("BeamNG.drive")) / "content" / "vehicles"
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
KEYS = (
    "brakeTorque",
    "brakeInputSplit",
    "brakeSplitCoef",
    "parkingTorque",
    "enableABS",
    "enableABSactuator",
    "absSlipRatioTarget",
    "enableBrakeThermals",
    "padMaterial",
    "brakeType",
)


def scan(index: PartIndex) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = defaultdict(list)
    for part in index.parts.values():
        table = part.data.get("pressureWheels")
        if not isinstance(table, list):
            continue
        for row in table:
            if isinstance(row, dict):
                opts = row
            elif isinstance(row, list) and row and isinstance(row[-1], dict):
                opts = row[-1]
            else:
                continue
            for k in KEYS:
                if k in opts:
                    found[k].append(opts[k])
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    per_origin: dict[str, dict[str, Any]] = {}
    sources: list[tuple[str, Any]] = [
        ("common.zip", ZipSource(VEH / "common.zip", "vehicles/common/"))
    ]
    sources += [
        (z.name, z) for z in sorted(VEH.glob("*.zip")) if z.name != "common.zip"
    ]
    sources += [(z.name, z) for z in sorted(MODS.glob("*.zip"))]
    for name, src in sources:
        try:
            found = scan(PartIndex.from_sources(src))
        except Exception as exc:  # noqa: BLE001 - se registra y se sigue
            per_origin[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        per_origin[name] = {
            k: {"n": len(v), "values": Counter(map(repr, v)).most_common(6)}
            for k, v in found.items()
        }
    summary: dict[str, Any] = {}
    for k in KEYS:
        origins = [o for o, d in per_origin.items() if isinstance(d, dict) and k in d]
        values: Counter = Counter()
        for o in origins:
            for val, n in per_origin[o][k]["values"]:
                values[val] += n
        summary[k] = {"origins": len(origins), "top_values": values.most_common(8)}
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "brake_params_survey.json").write_text(
        json.dumps(
            {"summary": summary, "per_origin": per_origin}, indent=1, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
