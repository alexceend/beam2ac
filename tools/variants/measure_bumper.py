"""BUMPER_CAMERA_POS del Ibiza a partir de los 4 coches Kunos (solo lectura salvo el JSON).

    python tools/variants/measure_bumper.py --out-root DIR [--base-root DIR]

Usa ``<out-root>/cameras_measurement.json`` (measure_cameras.py) y el KN5 de
``<base-root>/pkg_v17cam``. Escribe ``<out-root>/bumper_measurement.json`` (falla si existe).

Hechos (AC): la cámara del parachoques usa la misma matriz que la del capó (marco del
cuerpo) y los mismos ajustes de render (ajuste de render = 1, como Bonnet y Chase; Dash lo pone a 0):
el coche se dibuja, así que la cámara tiene que quedar fuera de la carrocería.

Regla (convención Kunos, heurística; el S13 es un mod y se excluye):
- x = 0 (las 4 referencias);
- y_cuerpo = mediana de las y de cuerpo Kunos (0,55–0,60);
- z_KN5 = frontal del Ibiza + mediana de (z_KN5 − frontal) de las referencias Kunos.
Comprobación: en todo el rango de z de las referencias la cámara queda por encima de la carrocería.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cam_geom import ROOT, body_triangles, top_y  # noqa: E402

sys.path.insert(0, str(ROOT))
from src.safe_paths import ensure_safe_output_path  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
WORK = ARGS.out_root  # beam2ac fase A
KUNOS = ("ks_toyota_ae86", "ks_abarth500_assetto_corse", "ks_toyota_celica_st185", "ks_audi_r8_lms")
BASE_KN5 = (ARGS.base_root / "pkg_v17cam/beam2ac_test16sn_seat_ibiza_acfr"
            / "seat_ibiza_acfr.kn5")


def main() -> None:
    meas = json.loads((WORK / "cameras_measurement.json").read_text(encoding="utf-8"))
    refs = {n: meas["references"][n] for n in KUNOS}
    ours = meas["ours"]
    go = ours["GRAPHICS_OFFSET"]
    y_body = [r["BUMPER_CAMERA_POS"]["value_body"][1] for r in refs.values()]
    rel_front = [r["BUMPER_CAMERA_POS"]["minus_front_z_m"] for r in refs.values()]
    x_body = [r["BUMPER_CAMERA_POS"]["value_body"][0] for r in refs.values()]
    rule = {"y_body_values": y_body, "y_body_median": statistics.median(y_body),
            "z_minus_front_values": rel_front, "z_minus_front_median": round(statistics.median(rel_front), 3),
            "x_values": x_body}
    front = ours["front_z"]
    yb = rule["y_body_median"]
    y_kn5 = yb - go[1]
    z_kn5 = front + rule["z_minus_front_median"]
    body = [0.0, round(yb, 3), round(z_kn5 + go[2], 3)]
    kn5 = [0.0, round(y_kn5, 3), round(z_kn5, 3)]

    _, tris = body_triangles(BASE_KN5, 0.0)
    sweep = []
    z = front + min(rel_front)
    while z <= front + max(rel_front) + 1e-9:
        sy = top_y(tris, 0.0, z)
        sweep.append({"z_kn5": round(z, 3), "surface_y": None if sy is None else round(sy, 3),
                      "clearance": None if sy is None else round(y_kn5 - sy, 3)})
        z += 0.02
    clear = [s["clearance"] for s in sweep if s["clearance"] is not None]
    sy = top_y(tris, 0.0, z_kn5)
    out = {
        "facts": "cámara del parachoques: marco del cuerpo, ajuste de render = 1 como Bonnet; sin la clave, "
                 "el DrivableCameraDef queda a cero (origen del cuerpo)",
        "previous": "sin BUMPER_CAMERA_POS: (0, 0, 0) en el cuerpo = KN5 " + str([-go[0], -go[1], -go[2]]),
        "rule": rule, "front_z_kn5": front, "GRAPHICS_OFFSET": go,
        "BUMPER_CAMERA_POS": body, "kn5": kn5,
        "surface_y_at_z": None if sy is None else round(sy, 3),
        "min_clearance_over_kunos_z_range": min(clear) if clear else None,
        "sweep": sweep,
    }
    path = ensure_safe_output_path(WORK / "bumper_measurement.json")
    if path.exists():
        sys.exit(f"STOP: ya existe {path}")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "sweep"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
