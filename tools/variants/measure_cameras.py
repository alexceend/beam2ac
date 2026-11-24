"""Mide las cámaras de 5 coches de referencia y deduce las del Ibiza (solo lectura salvo el JSON).

    python tools/variants/measure_cameras.py --out-root DIR [--base-root DIR]

Escribe ``cameras_measurement.json`` en ``--out-root`` (falla si ya existe). No toca paquetes ni referencias.

Regla BONNET (heurística medida en las referencias, no una norma de AC):
- x = 0 (todas las referencias);
- z = borde trasero del capó − mediana de las referencias (distancia por detrás del borde);
- y = superficie en esa z + mediana de las referencias (altura sobre la superficie);
- se pasa al marco del cuerpo: cuerpo = KN5 + GRAPHICS_OFFSET.
BUMPER: se mide y se informa, pero no se deduce: las referencias no siguen una regla común.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cam_geom import ROOT, body_triangles, f3, hood_rear_edge, profile, top_y, y_at  # noqa: E402

sys.path.insert(0, str(ROOT))
from src.ac.car_validate import read_ini  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402
from _common import new_output_dir, parse  # noqa: E402

ARGS = parse(__doc__)
WORK = ARGS.out_root  # beam2ac fase A
AC = Path(r"G:\SteamLibrary\steamapps\common\assettocorsa\content\cars")
BASE = ARGS.base_root / "pkg_v16sn_sound/beam2ac_test16sn_seat_ibiza_acfr"
REFS = {
    "ks_toyota_ae86": (ROOT / "sample_assetto_unpacked/ks_toyota_ae86", "toyota_ae86.kn5"),
    "ks_abarth500_assetto_corse": (ROOT / "sample_assetto_unpacked/ks_abarth500_assetto_corse",
                                   "abarth500_assetto_corse.kn5"),
    "ks_toyota_celica_st185": (AC / "ks_toyota_celica_st185", "toyota_celica.kn5"),
    "ks_audi_r8_lms": (AC / "ks_audi_r8_lms", "Audi-R8-LMS.kn5"),
    "sample_assetto (S13, mod)": (ROOT / "sample_assetto", "bw_ps13.kn5"),
}


def measure(folder: Path, kn5: str) -> dict:
    ini = read_ini(folder / "data/car.ini")
    g = ini.get("GRAPHICS", {})
    go = f3(ini["BASIC"].get("GRAPHICS_OFFSET", "0,0,0"))
    pitch_rot = float(ini["BASIC"].get("GRAPHICS_PITCH_ROTATION", "0").split(";")[0])
    eyes = f3(g["DRIVEREYES"])
    front, tris0 = body_triangles(folder / kn5, 0.0)
    p = profile(tris0, 0.0, -1.0, front)
    edge = hood_rear_edge(p, eyes[2])
    r: dict = {"kn5": str(folder / kn5), "GRAPHICS_OFFSET": go, "GRAPHICS_PITCH_ROTATION": pitch_rot,
               "DRIVEREYES": eyes, "front_z": round(front, 4), "hood_rear_edge_z": edge}
    _, tris_e = body_triangles(folder / kn5, eyes[0])
    r["roof_above_eyes_m"] = round(top_y(tris_e, eyes[0], eyes[2]) - eyes[1], 3)
    dash = folder / "data/dash_cam.ini"
    if dash.exists():
        d = read_ini(dash)["DASH_CAM"]
        r["DASH_CAM"] = {"POS": f3(d["POS"]), "EXP": d.get("EXP"),
                         "POS_minus_DRIVEREYES": [round(a - b, 3) for a, b in zip(f3(d["POS"]), eyes)]}
    for key in ("BONNET_CAMERA_POS", "BUMPER_CAMERA_POS"):
        if key not in g:
            continue
        b = f3(g[key])
        k = [b[i] - go[i] for i in range(3)]
        sy = y_at(p, k[2])
        e = {"value_body": b, "kn5": [round(v, 3) for v in k],
             "above_surface_m": None if sy is None else round(k[1] - sy, 3),
             "minus_front_z_m": round(k[2] - front, 3)}
        if key == "BONNET_CAMERA_POS":
            e["behind_hood_edge_m"] = round(edge - k[2], 3)
        r[key] = e
    for key in ("ON_BOARD_PITCH_ANGLE", "BONNET_CAMERA_PITCH", "BUMPER_CAMERA_PITCH"):
        if key in g:
            r[key] = float(g[key].split(";")[0])
    return r, p


def main() -> None:
    refs = {name: measure(folder, kn5)[0] for name, (folder, kn5) in REFS.items()}
    behind = [r["BONNET_CAMERA_POS"]["behind_hood_edge_m"] for r in refs.values()]
    above = [r["BONNET_CAMERA_POS"]["above_surface_m"] for r in refs.values()]
    rule = {"behind_hood_edge_median": statistics.median(behind), "behind_range": [min(behind), max(behind)],
            "above_surface_median": statistics.median(above), "above_range": [min(above), max(above)]}

    ours, p = measure(BASE, "seat_ibiza_acfr.kn5")
    go = ours["GRAPHICS_OFFSET"]
    z = ours["hood_rear_edge_z"] - rule["behind_hood_edge_median"]
    y = y_at(p, z) + rule["above_surface_median"]
    kn5 = [0.0, round(y, 3), round(z, 3)]
    body = [round(kn5[i] + go[i], 3) for i in range(3)]
    corners = []
    for bh in rule["behind_range"]:
        for ab in rule["above_range"]:
            zz = ours["hood_rear_edge_z"] - bh
            corners.append([round(y_at(p, zz) + ab + go[1], 3), round(zz + go[2], 3)])
    eyes_dz = [round(r["BONNET_CAMERA_POS"]["kn5"][2] - r["DRIVEREYES"][2], 3) for r in refs.values()]
    ours["BONNET_derived"] = {
        "rule": rule, "kn5": kn5, "BONNET_CAMERA_POS": body,
        "surface_y_at_z": round(y_at(p, z), 3), "inside_front": z < ours["front_z"],
        "range_body_y_z_over_reference_ranges": corners,
        "cross_check_eyes_anchor": {"reference_dz_bonnet_minus_eyes": eyes_dz,
                                    "median": statistics.median(eyes_dz),
                                    "ours_z_kn5": round(ours["DRIVEREYES"][2] + statistics.median(eyes_dz), 3)},
    }
    bump = {n: {k: r["BUMPER_CAMERA_POS"][k] for k in ("kn5", "above_surface_m", "minus_front_z_m")}
            for n, r in refs.items()}
    out = {"references": refs, "ours": ours, "bumper_reference_spread": bump}
    path = ensure_safe_output_path(WORK / "cameras_measurement.json")
    if path.exists():
        sys.exit(f"STOP: ya existe {path}")
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"rule": rule, "ours": ours["BONNET_derived"], "bumper": bump,
                      "ours_roof_above_eyes": ours["roof_above_eyes_m"]}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
