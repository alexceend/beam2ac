"""Compara la entrada BeamNG, un coche de referencia de AC y un paquete exportado.

    python tools/analysis/compare_reference_car.py --export output/projects/<p>/exports/<NNN>
        --ac-car <AC>/content/cars/<id> --out output/<nueva>

Solo lectura del coche de AC (KN5 y ``ui/ui_car.json``; ``data.acd`` está cifrado y no se
lee). Magnitudes medibles:

* batalla y vías: nulos ``WHEEL_xx`` de los KN5 (eje Z = avance, X = lateral de AC);
* dimensiones: caja de todas las mallas del KN5 principal de cada coche;
* potencia, par, masa y velocidad punta: ``ui_car.json`` de AC, ``info_<pc>.json`` de BeamNG
  y el ``ui_car.json`` exportado.

Una referencia de AC con nombre parecido NO es el mismo coche: el informe solo mide
diferencias; no ajusta ni copia valores.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.kn5_inspect import read_kn5  # noqa: E402
from src.safe_paths import new_output_dir  # noqa: E402


def wheel_geometry(kn5: Path) -> dict[str, float] | None:
    nodes = {
        n.name: n.translation
        for n in read_kn5(kn5, keep_vertices=False).nodes
        if n.name in ("WHEEL_LF", "WHEEL_RF", "WHEEL_LR", "WHEEL_RR")
    }
    if len(nodes) != 4:
        return None
    return {
        "wheelbase_m": abs(nodes["WHEEL_LF"][2] - nodes["WHEEL_LR"][2]),
        "track_front_m": abs(nodes["WHEEL_LF"][0] - nodes["WHEEL_RF"][0]),
        "track_rear_m": abs(nodes["WHEEL_LR"][0] - nodes["WHEEL_RR"][0]),
        "wheel_centre_height_front_m": (nodes["WHEEL_LF"][1] + nodes["WHEEL_RF"][1]) / 2,
    }


def box(kn5: Path) -> dict[str, float] | None:
    k = read_kn5(kn5)
    pts = [p for n in k.nodes if n.type == 2 for p in k.mesh_world_vertices(n)]
    if not pts:
        return None
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return {
        "width_m": hi[0] - lo[0],
        "height_m": hi[1] - lo[1],
        "length_m": hi[2] - lo[2],
    }


def num(text: Any) -> float | None:
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(text or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def ui_specs(path: Path) -> dict[str, Any]:
    # los ui_car.json de Kunos llevan saltos de línea literales en las cadenas
    d = json.loads(path.read_text(encoding="utf-8", errors="replace"), strict=False)
    s = d.get("specs", {})
    return {
        "name": d.get("name"),
        "power_hp": num(s.get("bhp")),
        "torque_nm": num(s.get("torque")),
        "weight_kg": num(s.get("weight")),
        "top_speed_kmh": num(s.get("topspeed")),
        "tags": d.get("tags", []),
    }


def beamng_info(attempt: Path) -> dict[str, Any] | None:
    """Potencia, par, masa y velocidad punta declaradas en ``info_<pc>.json``."""
    from src.project.export import source_info
    from src.project.manifest import load_project

    project = load_project(attempt.parents[1])
    src = sorted((project.root / "stages" / "source").glob("*/source.json"))
    if not src:
        return None
    variant = json.loads(src[-1].read_text(encoding="utf-8"))["variant"]
    _, cfg = source_info(project, variant)
    top = cfg.get("Top Speed")
    return {
        "power_hp": cfg.get("Power"),
        "torque_nm": cfg.get("Torque"),
        "weight_kg": cfg.get("Weight"),
        # unidad m/s inferida en la fase 4 (src.ac.assemble.build_ui)
        "top_speed_kmh": round(top * 3.6, 1) if isinstance(top, (int, float)) else None,
    }


def pct(a: float | None, b: float | None) -> str:
    if a is None or b is None or b == 0:
        return "—"
    return f"{100 * (a - b) / b:+.1f} %"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", type=Path, required=True)
    ap.add_argument("--ac-car", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    man = json.loads((a.export / "manifest.json").read_text(encoding="utf-8"))
    car = a.export / man["car_id"]
    ac = a.ac_car
    ac_kn5 = sorted(p for p in ac.glob("*.kn5") if "_lod_" not in p.name and p.name != "collider.kn5")
    ours = {
        "ui": ui_specs(car / "ui" / "ui_car.json"),
        "wheels": wheel_geometry(car / f"{man['car_id']}.kn5"),
        "kn5_box": box(car / f"{man['car_id']}.kn5"),
    }
    bng = beamng_info(a.export)
    if bng:
        ours["ui"].update({k: v for k, v in bng.items() if v is not None})
        ours["ui_source"] = "info_<pc>.json de BeamNG (declarado por Automation)"
    ref = {
        "ui": ui_specs(ac / "ui" / "ui_car.json"),
        "wheels": wheel_geometry(ac_kn5[0]) if ac_kn5 else None,
        "kn5_box": box(ac_kn5[0]) if ac_kn5 else None,
        "kn5": ac_kn5[0].name if ac_kn5 else None,
    }
    rows = []
    for group, key in (
        ("wheels", "wheelbase_m"),
        ("wheels", "track_front_m"),
        ("wheels", "track_rear_m"),
        ("kn5_box", "length_m"),
        ("kn5_box", "width_m"),
        ("kn5_box", "height_m"),
        ("ui", "power_hp"),
        ("ui", "torque_nm"),
        ("ui", "weight_kg"),
        ("ui", "top_speed_kmh"),
    ):
        o = (ours.get(group) or {}).get(key)
        r = (ref.get(group) or {}).get(key)
        rows.append({"quantity": f"{group}.{key}", "export": o, "ac_reference": r,
                     "difference": pct(o, r)})
    out = new_output_dir(a.out)
    report = {"export": str(a.export), "ac_car": str(ac), "ours": ours, "reference": ref,
              "rows": rows,
              "note": "referencia de nombre parecido; recreación de Automation: solo medidas"}
    (out / "comparison.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
    md = [f"# {man['car_id']} frente a {ac.name}", "",
          "| Magnitud | Exportado | Referencia AC | Diferencia |", "|---|---|---|---|"]
    for r in rows:
        f = (lambda v: "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v)))
        md.append(f"| {r['quantity']} | {f(r['export'])} | {f(r['ac_reference'])} | {r['difference']} |")
    (out / "comparison.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
