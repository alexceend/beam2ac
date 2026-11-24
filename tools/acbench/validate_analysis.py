"""G2.25: análisis fuera de línea de la batería de validación (:mod:`tools.acbench.validate`).

    python -m tools.acbench.validate_analysis <runs> --prereg <preregistro.json>
        --kn5 <geometria_kn5.json> --out output/<nueva>/results

Entradas por ejecución (``<runs>/<car_id>/<run_id>/``): ``*.trials.json``, el CSV de la app de
G2.22 (``app/registros/<car>_<run>.csv``), el de geometría (``app/capturas/<car>_<run>_geo.csv``)
y los volcados ``*_geometria.json``. Los umbrales vienen SOLO del pre-registro (escrito antes de
la primera ejecución en AC; su sha256 queda en el resultado).

Métricas (todas desde los CSV del juego, nunca desde lo que el controlador creía):

* A: nulos ``WHEEL_xx`` de la escena en el marco de la carrocería frente a los del KN5;
  posición física de la rueda frente al nulo dibujado; punto más bajo de la carrocería y del
  collider sobre el suelo; asentamiento en reposo; ``graphicsOffset`` frente a ``car.ini``.
* B: contactos carrocería–pista fuera de los muros; extensión frontal, trasera y lateral del
  collider medida en el primer contacto con cada muro frente a la caja de ``collider.kn5``.
* C: ralentí, corte, constante de tiempo de la caída de rpm en punto muerto frente a la ley de
  AC; freno motor en marcha (gas 0 frente a embrague abierto) frente a la predicción.
* D: aceleración, frenada, círculos (radio, volante frente a Ackermann, aceleración lateral,
  balanceo, cargas) y validez de cada ensayo.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics as st
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.safe_paths import ensure_safe_output_path  # noqa: E402
from tools.acbench import track_g225  # noqa: E402

WHEELS = ("LF", "RF", "LR", "RR")


# --- lectura ----------------------------------------------------------------------------------


def _num(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def read_csv(path: Path) -> list[dict[str, float]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        return [{k: _num(v) for k, v in row.items() if k is not None} for row in r]


def join_rows(main: list[dict], geo: list[dict]) -> list[dict]:
    """Une cada fila de G2.22 con la de geometría del MISMO fotograma (``sim_ms`` idéntico)."""
    by = {}
    for g in geo:
        by.setdefault(g["sim_ms"], g)
    out = []
    for m in main:
        g = by.get(m["sim_ms"])
        row = dict(m)
        if g:
            for k, v in g.items():
                row.setdefault(f"g_{k}" if k in row else k, v)
        out.append(row)
    return out


@dataclass
class Run:
    car_id: str
    run_id: str
    folder: Path
    trials: dict
    rows: list[dict]
    dumps: dict[str, dict] = field(default_factory=dict)
    resumen: dict = field(default_factory=dict)

    @classmethod
    def load(cls, folder: Path) -> "Run":
        tj = next(folder.glob("*.trials.json"))
        trials = json.loads(tj.read_text("utf-8"))
        cid, rid = trials["car_id"], trials["run_id"]
        main = read_csv(folder / "app" / "registros" / f"{cid}_{rid}.csv")
        geo = read_csv(folder / "app" / "capturas" / f"{cid}_{rid}_geo.csv")
        dumps = {}
        for p in (folder / "app" / "capturas").glob(f"{cid}_{rid}_*_geometria.json"):
            tag = p.name[len(f"{cid}_{rid}_"):-len("_geometria.json")]
            try:
                dumps[tag] = json.loads(p.read_text("utf-8"))
            except json.JSONDecodeError as e:
                dumps[tag] = {"error": repr(e)}
        res = folder / "resumen.json"
        return cls(cid, rid, folder, trials, join_rows(main, geo), dumps,
                   json.loads(res.read_text("utf-8")) if res.is_file() else {})

    def window_rows(self, trial: dict, name: str) -> list[dict]:
        uid = trial["seg_windows"].get(name)
        if uid is None:
            return []
        return [r for r in self.rows if r.get("seg_uid") == uid and r.get("seg_meas") == 1]


# --- geometría auxiliar ------------------------------------------------------------------------


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    n = math.sqrt(sum(x * x for x in a)) or 1.0
    return tuple(x / n for x in a)


def model_to_world(p, pos, look, up):
    """Marco del modelo de AC (+X izquierda, +Y arriba, +Z delante): x = up × look."""
    look, up = norm(look), norm(up)
    left = norm(cross(up, look))
    return tuple(pos[k] + p[0] * left[k] + p[1] * up[k] + p[2] * look[k] for k in range(3))


def box_corners(lo, hi):
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def linfit(xs, ys):
    n = len(xs)
    if n < 3:
        return math.nan, math.nan
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return math.nan, math.nan
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return b, my - b * mx


def rows3(r: dict, prefix: str) -> tuple[float, float, float]:
    return (r.get(f"{prefix}_x", math.nan), r.get(f"{prefix}_y", math.nan), r.get(f"{prefix}_z", math.nan))


def _finite_min(xs):
    xs = [x for x in xs if math.isfinite(x)]
    return min(xs) if xs else math.nan


def med(xs):
    xs = [x for x in xs if math.isfinite(x)]
    return st.median(xs) if xs else math.nan


# --- métricas por prueba ---------------------------------------------------------------------


def coast_brake(rpm, ref_rpm, torque, minimum):
    return 0.0 if rpm <= minimum else torque * (rpm - minimum) / (ref_rpm - minimum)


def m_static(run: Run, trial: dict, kn5: dict, spec: dict, pre: dict) -> dict:
    rows = run.window_rows(trial, "reposo")
    dump = run.dumps.get(trial["item"], {})
    out: dict = {"filas": len(rows), "volcado": bool(dump) and "error" not in dump,
                 "capturas_ok": trial["info"].get("capturas") == 1}
    go_read = dump.get("graphics_offset") or (trial["info"].get("geo") or {}).get("graphics_offset")
    if go_read:
        out["graphics_offset_leido"] = go_read
        out["graphics_offset_dif_m"] = max(abs(a - b) for a, b in zip(go_read, spec["graphics_offset"]))
    # nulos de rueda (escena) en el marco de la carrocería frente al KN5
    deltas, phys_vs_node = {}, {}
    last2 = [r for r in rows if r["phys_ms"] >= rows[-1]["phys_ms"] - 2000] if rows else []
    for i, w in enumerate(WHEELS):
        kn = kn5["wheels_kn5"].get(f"WHEEL_{w}")
        nb = [rows3(r, f"wnb{i}") for r in last2]
        pb = [rows3(r, f"wpb{i}") for r in last2]
        if kn and nb:
            m = [med([p[k] for p in nb]) for k in range(3)]
            deltas[w] = [m[k] - kn[k] for k in range(3)]
        if nb and pb:
            phys_vs_node[w] = max(abs(med([p[k] for p in pb]) - med([p[k] for p in nb])) for k in range(3))
    out["rueda_dibujada_menos_kn5_m"] = deltas
    out["rueda_fisica_menos_dibujada_m"] = phys_vs_node
    if deltas:
        out["max_abs_dx_dz_m"] = max(max(abs(d[0]), abs(d[2])) for d in deltas.values())
        out["max_abs_dy_m"] = max(abs(d[1]) for d in deltas.values())
    # carrocería y collider sobre el suelo (pose media de los 2 últimos s)
    if last2:
        pos = [med([r.get(f"body_pos_{c}", math.nan) for r in last2]) for c in "xyz"]
        look = [med([r.get(f"body_look_{c}", math.nan) for r in last2]) for c in "xyz"]
        up = [med([r.get(f"body_up_{c}", math.nan) for r in last2]) for c in "xyz"]
        ground = med([r.get(f"wc{i}_y", math.nan) for r in last2 for i in range(4)])
        out["suelo_y_contactos"] = ground
        body = kn5["body_box_no_wheels"]
        col = kn5["collider_box"]
        out["carroceria_y_min_sobre_suelo_m"] = min(model_to_world(c, pos, look, up)[1] for c in
                                                    box_corners(body["min"], body["max"])) - ground
        out["collider_y_min_sobre_suelo_m"] = min(model_to_world(c, pos, look, up)[1] for c in
                                                  box_corners(col["min"], col["max"])) - ground
        lowest = min(kn5["lowest_body_point_by_z_station"].values())
        out["punto_mas_bajo_real_sobre_suelo_m"] = model_to_world((0, lowest, 0), pos, look, up)[1] - ground
        out["cabeceo_deg"] = math.degrees(math.asin(max(-1, min(1, norm(look)[1]))))
        out["balanceo_deg"] = math.degrees(math.asin(max(-1, min(1, norm(cross(up, look))[1]))))
        ys = [r.get("body_pos_y", math.nan) for r in last2]
        fin = [y for y in ys if math.isfinite(y)]
        out["altura_std_2s_mm"] = 1000 * st.pstdev(fin) if len(fin) > 1 else math.nan
    if rows:
        jumps = [math.dist(rows3(a, "body_pos"), rows3(b, "body_pos")) for a, b in zip(rows, rows[1:])]
        out["max_salto_entre_filas_m"] = max(jumps) if jumps else 0.0
        out["cargas_min_N"] = min(min(r.get(f"load{i}", math.nan) for i in range(4)) for r in rows)
        out["colision_filas"] = sum(1 for r in rows if r.get("collided_with") == 0)
        keys = ("rpm", "speed_kmh", "body_pos_y")
        out["no_finitos"] = sum(1 for r in rows if not all(math.isfinite(r.get(k, 0.0)) for k in keys))
    return out


def m_engine(run: Run, trial: dict, spec: dict) -> dict:
    out: dict = {}
    idle = run.window_rows(trial, "ralenti")
    if idle:
        tail = [r["rpm"] for r in idle if r["phys_ms"] >= idle[-1]["phys_ms"] - 3000]
        out["ralenti_media"] = st.fmean(tail)
        out["ralenti_std"] = st.pstdev(tail)
    up = run.window_rows(trial, "subida") + run.window_rows(trial, "corte")
    if up:
        out["rpm_max"] = max(r["rpm"] for r in up)
        cut = run.window_rows(trial, "corte")
        if cut:
            out["corte_rpm_min"] = min(r["rpm"] for r in cut)
            out["corte_rpm_max"] = max(r["rpm"] for r in cut)
            out["limitador_activo_frac"] = st.fmean([1.0 if r.get("limiter_on") == 1 else 0.0 for r in cut])
    fall = run.window_rows(trial, "caida")
    if fall:
        t0 = fall[0]["phys_ms"]
        mn = spec["minimum"]
        lo = mn + 1000.0
        hi = fall[0]["rpm"] - 300.0
        pts = [((r["phys_ms"] - t0) / 1000.0, math.log(r["rpm"] - mn)) for r in fall if lo <= r["rpm"] <= hi]
        slope, _ = linfit([p[0] for p in pts], [p[1] for p in pts])
        out["caida_puntos_ajuste"] = len(pts)
        out["tau_medida_s"] = -1.0 / slope if slope and math.isfinite(slope) and slope < 0 else math.nan
        out["tau_prevista_s"] = spec["tau_pred_s"]
        out["tau_ratio"] = out["tau_medida_s"] / spec["tau_pred_s"]
        reach = [r for r in fall if r["rpm"] <= mn + 300]
        out["t_hasta_MIN_mas_300_s"] = (reach[0]["phys_ms"] - t0) / 1000.0 if reach else math.nan
        tail = [r["rpm"] for r in fall if r["phys_ms"] >= fall[-1]["phys_ms"] - 2000]
        out["rpm_final_media"] = st.fmean(tail)
        out["rpm_final_std"] = st.pstdev(tail)
        incr = [b["rpm"] - a["rpm"] for a, b in zip(fall, fall[1:]) if b["rpm"] > mn + 300]
        out["subidas_rpm_en_caida_max"] = max(incr) if incr else 0.0
    return out


def decel(rows: list[dict]) -> float:
    """Deceleración media (m/s², > 0 frenando) por ajuste lineal de la velocidad."""
    if len(rows) < 10:
        return math.nan
    t0 = rows[0]["phys_ms"]
    b, _ = linfit([(r["phys_ms"] - t0) / 1000.0 for r in rows], [r["speed_kmh"] / 3.6 for r in rows])
    return -b


def m_pass(run: Run, trial: dict, spec: dict, mass: float, mass_total: float | None = None) -> dict:
    out: dict = {}
    acc = run.window_rows(trial, "aceleracion")
    if acc:
        moving = [r for r in acc if r["speed_kmh"] > 0.5]
        t0 = moving[0]["phys_ms"] if moving else acc[0]["phys_ms"]
        for v in (60, 100):
            hit = [r for r in acc if r["speed_kmh"] >= v]
            out[f"t_0_{v}_s"] = (hit[0]["phys_ms"] - t0) / 1000.0 if hit else math.nan
        out["rpm_max"] = max(r["rpm"] for r in acc)
        out["deriva_lateral_max_m"] = max(abs(r.get("pos_x", math.nan) - track_g225.LANE_X) for r in acc)
        out["guinada_max"] = max(abs(r["yaw_rate"]) for r in acc)
        out["carga_min_N"] = min(min(r[f"load{i}"] for i in range(4)) for r in acc)
        gears = [int(r["gear"]) for r in acc if math.isfinite(r["gear"])]
        out["marchas"] = sorted(set(gears))
        out["marchas_monotonas"] = all(b >= a for a, b in zip(gears, gears[1:]))
    for tag in ("altas", "bajas"):
        g = run.window_rows(trial, f"gas0_marcha_{tag}")
        o = run.window_rows(trial, f"embrague_abierto_{tag}")
        if not g:
            continue
        gc = [r for r in g if r["clutch"] >= 0.99 and r["gear"] >= 1]
        out[f"{tag}_frac_embrague_cerrado"] = len(gc) / len(g)
        a_g = decel(gc)
        rpm = med([r["rpm"] for r in gc])
        gear = int(med([r["gear"] for r in gc])) if gc else 0
        r_w = med([r.get(f"lrad{i}", math.nan) for r in gc for i in range(4)]) or spec["tyre_radius"]
        G = spec["gears"][gear - 1] * spec["final"] if gear >= 1 else math.nan
        C = coast_brake(rpm, spec["coast_rpm"], spec["coast_torque"], spec["minimum"])
        k = spec["inertia"] * (G / r_w) ** 2
        # adenda 1: par del motor que publica AC (drivetrainTorque = T de el cálculo 4WD de AC, G2.22) frente a la ley
        torque = med([-r["drivetrain_torque"] for r in gc])
        c_open = [r for r in o if r["clutch"] < 0.05]
        out[f"{tag}_frac_embrague_abierto"] = len(c_open) / len(o) if o else math.nan
        a_o = decel(c_open) if len(c_open) >= 0.9 * max(1, len(o)) else math.nan
        pred = (C * G / r_w - k * a_o) / (mass + k) if math.isfinite(G) else math.nan
        # adenda 4 (G2.27): dinámica completa de AC [C]+[M] con el par MEDIDO: masa total (cargas en reposo),
        # inercia de ruedas y de embrague ([GEARBOX] INERTIA; 1,0 si falta), esta también con el embrague abierto
        full = math.nan
        if mass_total and spec.get("wheel_inertia_sum") and math.isfinite(G):
            m_o = mass_total + (spec["wheel_inertia_sum"] + spec["gearbox_inertia"] * G * G) / r_w**2
            full = (a_g - a_o) / ((torque * G / r_w - k * a_o) / (m_o + k))
        out[tag] = {"par_freno_medido_Nm": torque, "par_ratio": torque / C if C > 0 else math.nan,
                    "marcha": gear, "rpm_mediana": rpm, "decel_marcha": a_g, "decel_embrague_abierto": a_o,
                    "dif_medida": a_g - a_o, "dif_prevista": pred, "freno_motor_Nm_ley": C,
                    "ratio": (a_g - a_o) / pred if pred and math.isfinite(pred) else math.nan,
                    "ratio_completo": full}
    br = run.window_rows(trial, "frenada")
    if br:
        band = [r for r in br if 20 <= r["speed_kmh"] <= 80 and r["brake"] >= 0.79]
        out["frenada_decel_media_80_20"] = decel(band)
        out["frenada_guinada_max"] = max(abs(r["yaw_rate"]) for r in br)
        hd = [math.atan2(r.get("body_look_x", 0.0), r.get("body_look_z", 1.0)) for r in br]
        out["frenada_cambio_rumbo_deg"] = math.degrees(max(hd) - min(hd)) if hd else math.nan
        out["frenada_distancia_m"] = abs(br[-1].get("pos_z", math.nan) - br[0].get("pos_z", math.nan))
        locks = 0
        for r in br:
            v = r["speed_kmh"] / 3.6
            if v > 4.0 and any(abs(r.get(f"ang{i}", math.nan)) * r.get(f"lrad{i}", 0.33) < 0.1 * v for i in range(4)):
                locks += 1
        out["frenada_frac_rueda_bloqueada"] = locks / len(br)
    return out


def m_curve(run: Run, trial: dict, spec: dict, radius: float) -> dict:
    rows = run.window_rows(trial, "circulo")
    if not rows:
        return {}
    v = med([r["speed_kmh"] / 3.6 for r in rows])
    w = med([abs(r["yaw_rate"]) for r in rows])
    steer_deg = med([abs(r["steer"]) for r in rows])
    delta = steer_deg / spec["steer_ratio"]
    ack = math.degrees(math.atan(spec["wheelbase"] / radius))
    roll = med([math.degrees(math.asin(max(-1, min(1, norm(cross(rows3(r, "body_up"), rows3(r, "body_look")))[1]))))
                for r in rows])
    loads = [[r[f"load{i}"] for r in rows] for i in range(4)]
    return {"v_ms": v, "radio_medido_m": v / w if w > 0 else math.nan, "radio_error": (v / w) / radius - 1 if w else math.nan,
            "acel_lateral_g": v * w / 9.81, "acel_lateral_g_ac": med([abs(r.get("acc_x", math.nan)) for r in rows]),
            "volante_deg": steer_deg, "volante_frac_lock": steer_deg / spec["steer_lock"],
            "rueda_deg": delta, "ackermann_deg": ack, "ratio_volante_ackermann": delta / ack,
            "balanceo_deg": abs(roll), "carga_min_N": min(min(x) for x in loads),
            "carga_media_N": [st.fmean(x) for x in loads], "marcha": trial["info"].get("marcha")}


def m_wall(trial: dict, kn5: dict) -> dict:
    c = (trial["info"] or {}).get("contacto")
    if not c:
        return {}
    g = c["geo"]
    look = norm(g["body_look"])
    pos = g["body_pos"]
    ppos = g["phys_pos"]
    col = kn5["collider_box"]
    kind = trial["item"]
    out = {"v_contacto_kmh": c["speed_kmh"], "colision_pos_coche": g.get("collision_pos"),
           "profundidad_m": g.get("collision_depth")}
    if kind == "frontal":
        out["extension_modelo_m"] = (track_g225.WALL_A_Z - pos[2]) / look[2]
        out["extension_fisica_m"] = (track_g225.WALL_A_Z - ppos[2]) / look[2]
        out["collider_m"] = col["max"][2]
    elif kind == "trasero":
        out["extension_modelo_m"] = (track_g225.WALL_A_Z - pos[2]) / -look[2]
        out["extension_fisica_m"] = (track_g225.WALL_A_Z - ppos[2]) / -look[2]
        out["collider_m"] = -col["min"][2]
    else:
        th = math.atan2(look[0], look[2])
        f = col["max"][2]
        out["rumbo_deg"] = math.degrees(th)
        out["extension_modelo_m"] = (track_g225.WALL_B_X - pos[0] - f * math.sin(th)) / math.cos(th)
        out["extension_fisica_m"] = (track_g225.WALL_B_X - ppos[0] - f * math.sin(th)) / math.cos(th)
        out["collider_m"] = col["max"][0]
    out["error_modelo_m"] = out["extension_modelo_m"] - out["collider_m"]
    out["error_fisica_m"] = out["extension_fisica_m"] - out["collider_m"]
    return out


def m_startup(path: Path, mass: float) -> dict:
    """Adenda 3: registro de arranque (``sesion/arranque.jsonl``, solo lectura): estados no finitos y
    estado al terminar (posición y cargas finitas, Σ cargas = masa·g ± 5 %, parado)."""
    if not path.is_file():
        return {"registros": 0, "error": "sin arranque.jsonl"}
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]

    def fin(r):
        vals = list(r.get("pos") or []) + list(r.get("load") or [])
        return bool(vals) and all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals)

    bad = [r for r in rows if not fin(r)]
    out = {"registros": len(rows), "desde_phys_ms": rows[0]["phys_ms"] if rows else None,
           "hasta_phys_ms": rows[-1]["phys_ms"] if rows else None, "no_finitos": len(bad),
           "primer_no_finito_ms": bad[0]["phys_ms"] if bad else None,
           "ultimo_no_finito_ms": bad[-1]["phys_ms"] if bad else None}
    if rows:
        r0, last = rows[0], rows[-1]
        out["primero"] = {"finito": fin(r0), "load": r0.get("load"), "pos": r0.get("pos"),
                          "loaded_radius": (r0.get("geo") or {}).get("loaded_radius"),
                          "body_up": (r0.get("geo") or {}).get("body_up")}
        s = sum(last["load"]) if fin(last) else math.nan
        out["final"] = {"finito": fin(last), "pos": last.get("pos"), "load": last.get("load"), "suma_cargas_N": s,
                        "peso_N": mass * 9.81, "speed_kmh": last.get("speed_kmh"), "up": last.get("up")}
        v = last.get("speed_kmh")
        out["final_valido"] = bool(fin(last) and abs(s / (mass * 9.81) - 1) <= 0.05
                                   and v is not None and abs(v) < 1.0)
    return out


def ground_contacts(run: Run) -> dict:
    """Filas con ``collidedWith = 0`` fuera de las ventanas de los muros (= carrocería/collider
    tocando la pista donde solo hay suelo)."""
    # adenda 1: el ensayo de muro completo (también fuera de su ventana: empuje y frenada contra el muro)
    wall_uids = {uid for t in run.trials["trials"] if t["test"] == "walls"
                 for uid in [t["seg_uid"], *t["seg_windows"].values()]}
    hits = [r for r in run.rows if r.get("collided_with") == 0 and r.get("seg_uid") not in wall_uids]
    return {"filas": len(hits), "filas_total": len(run.rows),
            "posiciones_coche": [[round(r.get("col_x", math.nan), 3), round(r.get("col_y", math.nan), 3),
                                  round(r.get("col_z", math.nan), 3)] for r in hits[:20]]}


# --- criterios ---------------------------------------------------------------------------------


def evaluate(run: Run, kn5: dict, spec: dict, pre: dict) -> dict:
    th = pre["umbrales"]
    trials = run.trials["trials"]
    res: dict = {"car_id": run.car_id, "run_id": run.run_id, "estado_sesion": run.resumen.get("status"),
                 "filas_csv": len(run.rows), "ensayos": len(trials), "validos": sum(t["valid"] for t in trials)}
    mass = next((d.get("mass") for d in run.dumps.values() if d.get("mass")), spec["totalmass"])
    res["masa_ac_kg"] = mass
    rest = [r for t in trials if t["test"] == "static" for r in run.window_rows(t, "reposo")]
    mass_total = st.median(sum(r[f"load{i}"] for i in range(4)) for r in rest) / 9.80665 if rest else None
    res["masa_total_cargas_kg"] = mass_total
    checks: list[dict] = []

    def chk(group, name, ok, value, level="primario"):
        checks.append({"grupo": group, "criterio": name, "ok": bool(ok), "valor": value, "nivel": level})

    # A: carga
    static = [t for t in trials if t["test"] == "static"]
    chk("A_carga", "telemetria_y_sesion", run.resumen.get("status") == "ok" and bool(run.rows), run.resumen.get("status"))
    s = m_static(run, static[0], kn5, spec, pre) if static else {}
    res["static"] = s
    chk("A_carga", "graphics_offset_igual_car_ini", s.get("graphics_offset_dif_m", 9) <= th["graphics_offset_m"],
        s.get("graphics_offset_dif_m"))
    # A: geometría
    chk("A_geometria", "ruedas_dx_dz", s.get("max_abs_dx_dz_m", 9) <= th["rueda_dx_dz_m"], s.get("max_abs_dx_dz_m"))
    chk("A_geometria", "ruedas_dy", s.get("max_abs_dy_m", 9) <= th["rueda_dy_m"], s.get("max_abs_dy_m"))
    pvn = s.get("rueda_fisica_menos_dibujada_m") or {}
    chk("A_geometria", "rueda_fisica_igual_dibujada", bool(pvn) and max(pvn.values()) <= th["rueda_fisica_dibujada_m"],
        max(pvn.values()) if pvn else None)
    chk("A_geometria", "carroceria_sobre_suelo", s.get("punto_mas_bajo_real_sobre_suelo_m", -9) >= th["carroceria_min_sobre_suelo_m"],
        s.get("punto_mas_bajo_real_sobre_suelo_m"))
    chk("A_geometria", "asentamiento", s.get("altura_std_2s_mm", 99) <= th["asentamiento_std_mm"]
        and abs(s.get("cabeceo_deg", 99)) <= th["inclinacion_reposo_deg"]
        and abs(s.get("balanceo_deg", 99)) <= th["inclinacion_reposo_deg"]
        and s.get("max_salto_entre_filas_m", 9) <= th["salto_m"],
        {k: s.get(k) for k in ("altura_std_2s_mm", "cabeceo_deg", "balanceo_deg", "max_salto_entre_filas_m")})
    final = [t for t in trials if t["test"] == "final"]
    if final:
        sf = m_static(run, final[0], kn5, spec, pre)
        res["final"] = sf
        chk("A_geometria", "geometria_final_igual_inicial",
            sf.get("max_abs_dx_dz_m", 9) <= th["rueda_dx_dz_m"] and sf.get("max_abs_dy_m", 9) <= th["rueda_dy_m"],
            {k: sf.get(k) for k in ("max_abs_dx_dz_m", "max_abs_dy_m")})
    # B: collider
    gc = ground_contacts(run)
    res["contactos_carroceria_pista"] = gc
    chk("B_collider", "sin_contacto_carroceria_suelo", gc["filas"] <= th["contactos_suelo_filas_max"], gc["filas"])
    # adenda 3: protocolo mínimo (sin muros ni C20; 1 repetición en el sentido contrario de las curvas)
    minimo = "adenda_3" in pre and run.trials.get("protocol") == "minimo"
    res["protocolo"] = run.trials.get("protocol")
    if minimo:
        res["arranque"] = m_startup(run.folder / "sesion" / "arranque.jsonl", mass)
        a = res["arranque"]
        chk("A_arranque", "sin_estado_no_finito", a.get("registros", 0) > 0 and a.get("no_finitos", 1) == 0,
            {k: a.get(k) for k in ("registros", "no_finitos", "primer_no_finito_ms", "ultimo_no_finito_ms")})
        chk("A_arranque", "estado_final_valido", a.get("final_valido", False), a.get("final"), "informativo")
    walls = [t for t in trials if t["test"] == "walls" and t["valid"]]
    wm = {}
    for kind in (() if minimo else ("frontal", "trasero", "lateral")):
        ms = [m_wall(t, kn5) for t in walls if t["item"] == kind]
        ms = [m for m in ms if m]
        wm[kind] = ms
        lvl = "informativo" if "adenda_2" in pre else "secundario"
        if ms:
            err = med([m["error_modelo_m"] for m in ms])
            chk("B_collider", f"extension_{kind}", abs(err) <= th["collider_extension_m"], err, lvl)
            chk("B_collider", f"contacto_{kind}_suave", max(m["v_contacto_kmh"] for m in ms) <= th["v_contacto_max_kmh"],
                max(m["v_contacto_kmh"] for m in ms), lvl)
        else:
            chk("B_collider", f"extension_{kind}", False, "sin ensayos válidos", lvl)
        if "adenda_2" in pre:  # adenda 2: criterio comparativo con el coche de referencia
            chk("B_collider", f"muro_{kind}_contacto", bool(ms), len(ms), "secundario")
    res["muros"] = wm
    # C: motor
    eng = [m_engine(run, t, spec) for t in trials if t["test"] == "engine" and t["valid"]]
    res["motor"] = eng
    chk("C_motor", "ensayos_validos", len(eng) >= th["repeticiones_min"], len(eng))
    if eng:
        chk("C_motor", "ralenti_estable", max(e.get("ralenti_std", 99) for e in eng) <= th["ralenti_std_rpm"],
            max(e.get("ralenti_std", 99) for e in eng))
        chk("C_motor", "llega_al_corte", min(e.get("rpm_max", 0) for e in eng) >= spec["limiter"] - th["corte_margen_rpm"]
            and max(e.get("rpm_max", 0) for e in eng) <= spec["limiter"] + th["corte_exceso_rpm"],
            [e.get("rpm_max") for e in eng])
        chk("C_motor", "rpm_bajan_al_soltar", all(e.get("t_hasta_MIN_mas_300_s", 99) <= th["caida_t_max_s"] for e in eng),
            [e.get("t_hasta_MIN_mas_300_s") for e in eng])
        chk("C_motor", "sin_oscilacion_en_caida", max(e.get("subidas_rpm_en_caida_max", 99) for e in eng) <= th["caida_subida_max_rpm"],
            max(e.get("subidas_rpm_en_caida_max", 99) for e in eng))
        ratios = [e.get("tau_ratio", math.nan) for e in eng]
        chk("C_motor", "tau_ley_acs_burda", all(th["tau_ratio_burdo"][0] <= r <= th["tau_ratio_burdo"][1] for r in ratios), ratios)
        chk("C_motor", "tau_ley_acs", all(th["tau_ratio"][0] <= r <= th["tau_ratio"][1] for r in ratios), ratios, "secundario")
    # D: conducción
    for test, tag in (("pass_hi", "altas"), ("pass_lo", "bajas")):
        # protocolo mínimo: la pasada combinada «pass» aporta las dos (ventanas *_bajas)
        ps = [m_pass(run, t, spec, mass, mass_total) for t in trials
              if t["test"] in (test, "pass") and t["valid"]]
        res[test] = ps
        chk("D_conduccion", f"{test}_validos", len(ps) >= th["repeticiones_min"], len(ps))
        if not ps:
            continue
        eb = [p[tag]["ratio"] for p in ps if tag in p]
        if tag == "altas":
            chk("D_conduccion", "aceleracion_0_100", all(p.get("t_0_100_s", 99) <= th["t_0_100_max_s"] for p in ps),
                [p.get("t_0_100_s") for p in ps])
        pr = [p[tag]["par_ratio"] for p in ps if tag in p]
        ad = pre.get("adenda_1", {}).get("umbrales", {})
        if ad:
            lo, hi = ad["freno_motor_par_ratio_burdo"]
            chk("C_motor", f"freno_motor_en_marcha_{tag}_par", bool(pr) and all(lo <= r <= hi for r in pr), pr)
            lo, hi = ad["freno_motor_par_ratio"]
            chk("C_motor", f"freno_motor_en_marcha_{tag}_par_ley", bool(pr) and all(lo <= r <= hi for r in pr), pr,
                "secundario")
        ad4 = pre.get("adenda_4", {}).get("umbrales", {})
        full = [p[tag]["ratio_completo"] for p in ps if tag in p]
        if ad4 and any(math.isfinite(r) for r in full):
            lo, hi = ad4["freno_motor_decel_completo"]
            chk("C_motor", f"freno_motor_en_marcha_{tag}_decel_completo", all(lo <= r <= hi for r in full), full,
                "secundario")
        lim = th["freno_motor_marcha_ratio"][tag]
        chk("C_motor", f"freno_motor_en_marcha_{tag}_decel", bool(eb) and all(lim[0] <= r <= lim[1] for r in eb), eb,
            "secundario")
        chk("D_conduccion", f"frenada_{tag}", all(p.get("frenada_decel_media_80_20", 0) >= th["frenada_decel_min"]
                                                  or tag == "bajas" for p in ps)
            and all(p.get("frenada_guinada_max", 9) <= th["frenada_guinada_max"] for p in ps)
            and all(abs(p.get("frenada_cambio_rumbo_deg", 99)) <= th["frenada_rumbo_max_deg"] for p in ps),
            [{k: p.get(k) for k in ("frenada_decel_media_80_20", "frenada_guinada_max", "frenada_cambio_rumbo_deg")}
             for p in ps])
    cv: dict = {}
    for t in trials:
        if t["test"] == "curves" and t["valid"]:
            key = t["item"]
            r = {"G150": 150.0, "A60": 60.0, "C20": 20.0}[key[:-1]]
            cv.setdefault(key, []).append(m_curve(run, t, spec, r))
    res["curvas"] = cv
    if minimo:
        need = {"G150+": th["repeticiones_min"], "G150-": 1, "A60+": th["repeticiones_min"], "A60-": 1}
    else:
        need = {k: th["repeticiones_min"] for k in ("G150+", "G150-", "A60+", "A60-", "C20+", "C20-")}
    for key, n_min in need.items():
        ms = cv.get(key, [])
        chk("D_conduccion", f"curva_{key}_validos", len(ms) >= n_min, len(ms))
        if ms:
            chk("D_conduccion", f"curva_{key}_radio", all(abs(m["radio_error"]) <= th["radio_error_max"] for m in ms),
                [m["radio_error"] for m in ms])
            chk("D_conduccion", f"curva_{key}_cargas", all(m["carga_min_N"] >= th["carga_min_N"] for m in ms),
                [m["carga_min_N"] for m in ms])
    if minimo:  # adenda 3: simetría entre sentidos
        for base in ("G150", "A60"):
            p, m = cv.get(base + "+", []), cv.get(base + "-", [])
            if p and m:
                vp, vm = med([x["volante_deg"] for x in p]), med([x["volante_deg"] for x in m])
                ok = abs(vm - vp) <= max(1.0, 0.10 * vp) and all(
                    abs(x["radio_error"]) <= th["radio_error_max"] for x in p + m)
                chk("D_conduccion", f"curva_{base}_simetria", ok, {"volante_mas": vp, "volante_menos": vm},
                    "secundario")
            else:
                chk("D_conduccion", f"curva_{base}_simetria", False, "falta un sentido", "secundario")
    inv = [t for t in trials if not t["valid"]]
    res["invalidos"] = [{"test": t["test"], "item": t["item"], "intento": t["attempt"], "motivos": t["reasons"]} for t in inv]
    res["criterios"] = checks
    return res


def key_metrics(r: dict) -> dict[str, float]:
    """Métricas comparadas entre ejecuciones independientes (preregistro §reproducibilidad)."""
    k: dict[str, float] = {}
    s = r.get("static") or {}
    for w, d in (s.get("rueda_dibujada_menos_kn5_m") or {}).items():
        k[f"rueda_{w}_dy"] = d[1]
    for name in ("tau_ratio", "ralenti_media"):
        vals = [e.get(name, math.nan) for e in r.get("motor", [])]
        k[name] = med(vals)
    for kind, ms in (r.get("muros") or {}).items():
        k[f"muro_{kind}"] = med([m["extension_modelo_m"] for m in ms])
    ph = r.get("pass_hi") or []
    k["t_0_100_s"] = med([p.get("t_0_100_s", math.nan) for p in ph])
    k["frenada_decel"] = med([p.get("frenada_decel_media_80_20", math.nan) for p in ph])
    for key, ms in (r.get("curvas") or {}).items():
        k[f"curva_{key}_volante_deg"] = med([m["volante_deg"] for m in ms])
    return k


def reproducibility(results: list[dict], tol: dict) -> dict:
    if len(results) < 2:
        return {"comparado": False}
    a, b = key_metrics(results[0]), key_metrics(results[1])
    rows, ok = {}, True
    for name in sorted(set(a) & set(b)):
        x, y = a[name], b[name]
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        rule = next((v for kk, v in tol.items() if name.startswith(kk)), tol["relativa_por_defecto"])
        lim = rule["abs"] if "abs" in rule else rule["rel"] * max(abs(x), abs(y))
        good = abs(x - y) <= lim
        ok &= good
        rows[name] = {"r1": x, "r2": y, "dif": y - x, "limite": lim, "ok": good}
    return {"comparado": True, "ok": ok, "metricas": rows}


def classify(results: list[dict], captures_reviewed: dict | None = None, tol: dict | None = None) -> dict:
    """Clasificación pre-registrada (preregistro §clasificación) a partir de 1–2 ejecuciones."""
    if not results:
        return {"estado": "load_failed", "motivo": "sin ejecuciones"}

    def fails(r, group=None, level=None):
        return [c for c in r["criterios"] if not c["ok"] and c["nivel"] != "informativo"
                and (group is None or c["grupo"].startswith(group)) and (level is None or c["nivel"] == level)]

    if all(fails(r, "A_carga") for r in results):
        return {"estado": "load_failed", "fallos": [c["criterio"] for c in fails(results[0], "A_carga")]}
    loaded = [r for r in results if not fails(r, "A_carga")]
    geo = [c for r in loaded for c in fails(r, "A_geometria", "primario")]
    if geo:
        return {"estado": "geometry_failed", "fallos": sorted({c["criterio"] for c in geo})}
    prim = [c for r in loaded for c in fails(r, None, "primario")]
    sec = [c for r in loaded for c in fails(r, None, "secundario")]
    core_phys = [c for c in prim if c["grupo"] in ("C_motor", "D_conduccion", "B_collider")]
    start = [c for c in prim if c["grupo"] == "A_arranque"]
    if start and not core_phys:  # adenda 3: estado inicial no demostrado seguro ⇒ como mucho partial
        return {"estado": "partial", "fallos": sorted({f"{c['grupo']}:{c['criterio']}" for c in start})}
    if core_phys:
        core_phys += start
        valid_any = any(not fails(r, None, "primario") for r in loaded)
        return {"estado": "partial" if valid_any else "physics_failed",
                "fallos": sorted({f"{c['grupo']}:{c['criterio']}" for c in core_phys})}
    two = len(loaded) >= 2
    rep = reproducibility(loaded, tol) if (two and tol) else {"comparado": False}
    reviewed = bool(captures_reviewed) and all(v == "sin_anomalias" for v in captures_reviewed.values())
    if two and rep.get("ok") and not sec and reviewed:
        return {"estado": "validated_in_ac", "ejecuciones": len(loaded), "reproducibilidad": rep}
    return {"estado": "ready_for_further_testing", "ejecuciones": len(loaded), "reproducibilidad": rep,
            "pendiente": sorted({f"{c['grupo']}:{c['criterio']}" for c in sec})
            + ([] if two else ["una_sola_ejecucion"]) + ([] if rep.get("ok") or not two else ["no_reproducible"])
            + ([] if reviewed else ["capturas_sin_revisar_o_con_anomalias"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", type=Path)
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--kn5", type=Path, required=True)
    ap.add_argument("--specs", type=Path, required=True, help="specs.json (CarSpec por car_id)")
    ap.add_argument("--captures-review", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ref-runs", type=Path, default=None, help="no se usa para clasificar; solo se cita")
    a = ap.parse_args(argv)
    pre = json.loads(a.prereg.read_text("utf-8"))
    adenda = a.prereg.with_name("preregistro_adenda_1.json")
    if adenda.is_file():  # se añade, nunca sustituye a lo pre-registrado
        pre["adenda_1"] = json.loads(adenda.read_text("utf-8"))
    adenda2 = a.prereg.with_name("preregistro_adenda_2.json")
    if adenda2.is_file():
        pre["adenda_2"] = json.loads(adenda2.read_text("utf-8"))
    adenda4 = a.prereg.with_name("preregistro_adenda_4.json")
    if adenda4.is_file():
        pre["adenda_4"] = json.loads(adenda4.read_text("utf-8"))
    adenda3 = a.prereg.with_name("preregistro_adenda_3.json")
    if adenda3.is_file():
        pre["adenda_3"] = json.loads(adenda3.read_text("utf-8"))
    kn5_all = {k["car"]: k for k in json.loads(a.kn5.read_text("utf-8"))}
    specs = json.loads(a.specs.read_text("utf-8"))
    review = json.loads(a.captures_review.read_text("utf-8")) if a.captures_review else {}
    out = ensure_safe_output_path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"prereg_sha256": hashlib.sha256(a.prereg.read_bytes()).hexdigest(),
               "adenda_1_sha256": hashlib.sha256(adenda.read_bytes()).hexdigest() if adenda.is_file() else None,
               "adenda_3_sha256": hashlib.sha256(adenda3.read_bytes()).hexdigest() if adenda3.is_file() else None,
               "adenda_4_sha256": hashlib.sha256(adenda4.read_bytes()).hexdigest() if adenda4.is_file() else None,
               "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "coches": {}}
    for car_dir in sorted(p for p in a.runs.iterdir() if p.is_dir()):
        spec = specs.get(car_dir.name)
        if spec is None:
            continue
        kn5 = kn5_all[spec["source"]]
        results = []
        for run_dir in sorted(p for p in car_dir.iterdir() if p.is_dir()):
            if not list(run_dir.glob("*.trials.json")):
                continue
            r = evaluate(Run.load(run_dir), kn5, spec, pre)
            results.append(r)
            (out / f"{car_dir.name}_{run_dir.name}.json").write_text(
                json.dumps(r, indent=1, ensure_ascii=False, default=str), "utf-8")
        cls = classify(results, review.get(car_dir.name), pre["reproducibilidad"])
        summary["coches"][car_dir.name] = {"ejecuciones": [r["run_id"] for r in results], "clasificacion": cls,
                                           "fallos": {r["run_id"]: [f"{c['grupo']}:{c['criterio']}" for c in r["criterios"]
                                                                    if not c["ok"]] for r in results}}
    (out / "resumen.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), "utf-8")
    print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
