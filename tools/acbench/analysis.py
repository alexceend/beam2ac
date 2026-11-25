"""G2.22: análisis corregido de los registros del banco LSD de AC (G2.21 y G2.22).

    python -m tools.acbench.analysis <carpeta_csv> [<carpeta_csv> …] --out output/<nueva>

Mismo método que ``output/g221_2026-11-07/banco_ac/analizar_ac.py`` (estimador de
``src/lab/ac_step4wd.py``; ρ = ΣX·P/ΣP²; |Δω| ≥ 0,5 rad/s en AWD y ≥ 0,1 en 2WD;
TOL 0,10; p10–p90 en [0,8; 1,2]; 5 bloques como mínimo) con las correcciones de la
auditoría de los CSV de G2.21 (``docs/research/g222_banco_lsd_autonomo_2026-11-07.md`` §2):

1. **Embrague.** En CSP ``ac.StateCar.clutch`` = 1 es embrague CERRADO (pedal suelto):
   ``rpm = r·ω`` con 1,000 en los 8 CSV con ``clutch = 1``. El analizador de G2.21 exigía
   ``clutch ≤ 0,01`` y descartaba todas las muestras en marcha.
2. **Par del motor.** ``data/script.lua`` nunca se ejecutó (``cphys_alive = 0``; los
   scripts de física del coche exigen física extendida). ``ac.StateCar.drivetrainTorque``
   ya estaba en los CSV y es el par ``T`` de el cálculo 4WD de AC: coincide con el balance de las 4
   ruedas ``r·T = (v20/4)·Σdω/dt − Σfb`` (los bloqueos se cancelan al sumar) a 0,2–7 N·m.
   Se usa ``drivetrain_torque`` y se comprueba ese balance por archivo (``torque_check``).
3. **Base de tiempos.** ``ac.StateCar.timestamp`` (último paso de física, ms) si el CSV lo
   trae (registrador G2.22, columna ``phys_ms``); si no, ``sim_ms``. Filas repetidas (mismo
   estado de física) fuera.
4. **2WD.** Con ``TYPE=FWD`` CSP da ``shaftVelocity = shaftInertia = 0``: se usan
   ``angularSpeed`` y ``angularInertia`` [H] (en régimen saturado el término inercial es
   pequeño; se publica la sensibilidad con I = 0).
5. **Bloques independientes.** G2.21 partía en segundos enteros de tiempo: segundos
   consecutivos de UNA maniobra no son independientes. Ahora un bloque es una repetición
   de maniobra (``seg_uid`` del controlador G2.22, que restablece la condición inicial) o,
   en registros manuales, un episodio saturado separado del siguiente por ≥ 1 s sin
   muestras válidas. Bloque válido: ≥ 10 muestras y ≥ 0,2 s.
6. **Validez del bloque** del controlador (``seg_valid``): solo cuentan repeticiones que el
   controlador cerró como válidas (sin colisión, freno, pérdida de contacto ni lectura
   inválida). Las inválidas se cuentan como descartadas.

Clases de datos (``data_class``): ``ac_in_game`` (CSV del juego), ``sintetico`` (doble de
``simac.py`` o ``simular.py``; la herramienta de reglas lo rechaza), y por parámetro
``descartado`` (motivo) o ``insuficiente``. Umbrales sin cambios (no se rebajan).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.project.export import default_ac_root  # noqa: E402
from src.safe_paths import claim_output_dir, ensure_safe_output_path  # noqa: E402

G221_BANCO = ROOT / "output" / "g221_2026-11-07" / "banco_ac"
ACS_EXE = (default_ac_root() or Path("assettocorsa")) / "acs.exe"
ACS_SHA_G211 = "0df569c8"

MIN_DW = {"AWD": 0.5, "FWD": 0.1}
WINDOW = {"AWD": 0.1000000014901161, "FWD": 0.009999999776482582}
TOL = 0.10
MIN_BLOCKS = 5
MIN_BLOCK_SAMPLES = 10
MIN_BLOCK_SECONDS = 0.2
EPISODE_GAP_S = 1.0
CLUTCH_ENGAGED = 0.99  # CSP: 1 = cerrado
# Saturación ESTABLE PASO A PASO (pre-registrada el 2026-11-07, ``preregistro.json``): además del
# umbral de G2.21, |Δω| ≥ STEP_MARGIN × cambio de Δω que el bloqueo produce en UN paso de física
# (eje y 2WD: dt·lim/I; central: dt·lim/(2I); lectura [C], ``src/lab/ac_step4wd.py``). Con lim
# grande el bloqueo invierte el signo de Δω de un paso a otro y el registro por fotograma (≈2–3
# pasos) ve una «saturación» que no existe (reanálisis de G2.21, ``auditoria/criterio_paso.py``).
DT_PHYS = 1.0 / 333.0
STEP_MARGIN = 2.0
TORQUE_CHECK_TOL = 0.05  # |mediana(T_balance − T)| ≤ 5 % de mediana|T| (y ≥ 3 N·m de margen)


def _load_variants():
    """Carga las variantes de G2.21; sin esa evidencia local devuelve ``None`` (BY_ID vacío)."""
    if not (G221_BANCO / "variantes.py").is_file():
        return None
    spec = importlib.util.spec_from_file_location("g221_variantes", G221_BANCO / "variantes.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["g221_variantes"] = mod
    spec.loader.exec_module(mod)
    return mod


VARIANTS = _load_variants()
BY_ID = VARIANTS.BY_ID if VARIANTS is not None else {}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fnum(x: str | None) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


# --- lectura -------------------------------------------------------------------------


@dataclass
class Log:
    path: Path
    car_id: str
    rows: list[dict[str, float]]
    time_base: str
    duplicates: int
    synthetic: bool
    run_id: str = ""
    segments: dict[int, dict] = field(default_factory=dict)  # seg_uid → metadatos del controlador


def car_id_of(name: str) -> str | None:
    return next((cid for cid in BY_ID if name.startswith(cid + "_")), None)


def load_log(path: Path, synthetic: bool = False) -> Log | None:
    car_id = car_id_of(path.name)
    if car_id is None:
        return None
    with path.open(newline="", encoding="utf-8") as f:
        raw = [{k: fnum(v) for k, v in r.items()} for r in csv.DictReader(f)]
    has_phys = bool(raw) and "phys_ms" in raw[0] and not all(math.isnan(r["phys_ms"]) for r in raw)
    key_cols = ["phys_ms"] if has_phys else [f"{p}{i}" for p in ("shaftv", "ang", "fbt") for i in range(4)]
    rows: list[dict[str, float]] = []
    dup = 0
    for r in raw:
        if rows and all(r.get(k) == rows[-1].get(k) for k in key_cols):
            dup += 1
            continue
        r["t"] = (r["phys_ms"] if has_phys else r["sim_ms"]) / 1000.0
        rows.append(r)
    log = Log(path, car_id, rows, "phys_ms" if has_phys else "sim_ms", dup, synthetic)
    meta = path.with_suffix(".segments.json")
    if meta.is_file():
        m = json.loads(meta.read_text("utf-8"))
        log.run_id = m.get("run_id", "")
        log.segments = {int(s["seg_uid"]): s for s in m.get("segments", [])}
    return log


# --- física ----------------------------------------------------------------------------


def sat(x: float, window: float) -> float:
    return x / (abs(x) + window)


def limit(d, t_ref: float, r: float) -> float:
    if t_ref > 0.0:
        return d.preload + d.power * t_ref * abs(r)
    return d.preload + abs(d.coast * t_ref * abs(r))


def total_ratio(row: dict[str, float]) -> float:
    if row["gear"] == 0:  # ac.StateCar.gear: 0 = punto muerto
        return 0.0
    return abs(row["gear_ratio"] * row["final_ratio"])


def wheel_cols(traction: str, row: dict[str, float]) -> tuple[str, str]:
    """Columnas de velocidad e inercia del semieje. FWD: CSP da 0 en shaft* [corrección 4]."""
    if traction == "AWD" or (row.get("shafti0", 0.0) or 0.0) > 0.0:
        return "shaftv", "shafti"
    return "ang", "angi"


def torque_check(log: Log) -> dict:
    """Balance de las 4 ruedas frente a ``drivetrain_torque`` (embrague cerrado, en marcha).
    Solo en AWD: en 2WD las ruedas no motrices no están en el árbol y el balance no aplica."""
    if BY_ID[log.car_id].traction != "AWD":
        return {"n": 0, "status": "no_aplica_2wd"}
    diffs, ts = [], []
    rows = log.rows
    for a, b in zip(rows, rows[1:]):
        if a["gear"] < 1 or a["clutch"] < CLUTCH_ENGAGED or b["gear"] != a["gear"]:
            continue
        r = total_ratio(a)
        if r == 0.0 or b["gear_ratio"] != a["gear_ratio"] or a["speed_kmh"] < 8.0:
            continue
        if max(a["brake"], b["brake"], a["handbrake"], b["handbrake"]) > 0.01:
            continue
        dt = b["t"] - a["t"]
        if not 0.0 < dt <= 0.05:
            continue
        vcol, icol = wheel_cols("AWD", a)
        if vcol != "shaftv":
            continue  # el balance de 4 ruedas solo vale en AWD
        inertia = a["shafti0"]
        v20 = r * r * (a["clutch_inertia"] + a["engine_inertia"]) + a["drivetrain_inertia"] + 4 * inertia
        sdw = sum(b[f"shaftv{i}"] - a[f"shaftv{i}"] for i in range(4)) / dt
        sfb = sum((a[f"fbt{i}"] + b[f"fbt{i}"]) / 2 for i in range(4))
        t_bal = (v20 / 4 * sdw - sfb) / r
        diffs.append(t_bal - a["drivetrain_torque"])
        ts.append(abs(a["drivetrain_torque"]))
    if len(diffs) < 50:
        return {"n": len(diffs), "status": "sin_datos"}
    med = statistics.median(diffs)
    scale = statistics.median(ts)
    ok = abs(med) <= max(TORQUE_CHECK_TOL * scale, 3.0)
    q = statistics.quantiles(diffs, n=4)
    return {"n": len(diffs), "median_diff": med, "iqr": [q[0], q[2]], "median_abs_T": scale,
            "status": "coincide" if ok else "no_coincide"}


def step_threshold(lim: float, inertia: float, centre: bool = False) -> float:
    if inertia <= 0.0:
        return math.inf
    return STEP_MARGIN * DT_PHYS * lim / ((2.0 if centre else 1.0) * inertia)


def analyse_log(log: Log, step_stable: bool = True) -> dict:
    v = BY_ID[log.car_id]
    win = WINDOW[v.traction]
    min_dw = MIN_DW[v.traction]
    s = v.front_share / 100.0
    samples: dict[str, list[dict]] = defaultdict(list)
    rejected: dict[str, int] = defaultdict(int)
    rows = log.rows
    for k in range(len(rows) - 1):
        a, b = rows[k], rows[k + 1]
        dt = b["t"] - a["t"]
        if not (0.0 < dt <= 0.1):
            rejected["dt"] += 1
            continue
        seg_uid = int(a["seg_uid"]) if not math.isnan(a.get("seg_uid", math.nan)) else -1
        if log.segments:
            in_window = a.get("seg_meas", 0.0) == 1.0 and b.get("seg_meas", 0.0) == 1.0
            if seg_uid < 0 or seg_uid != int(b.get("seg_uid", -1)) or not in_window:
                rejected["fuera_de_ventana_de_medida"] += 1
                continue
            meta = log.segments.get(seg_uid)
            if meta is None or not meta.get("valid", False):
                rejected["repeticion_invalida"] += 1
                continue
        if max(a["brake"], b["brake"], a["handbrake"], b["handbrake"]) > 0.01:
            rejected["freno"] += 1
            continue
        if min(a[f"load{i}"] for i in range(4)) < 200.0 or a["speed_kmh"] < 8.0:
            rejected["carga_o_velocidad"] += 1
            continue
        r = total_ratio(a)
        T = a.get("drivetrain_torque", math.nan)
        in_gear = r > 0.0
        vcol, icol = wheel_cols(v.traction, a)
        if in_gear:
            if a["clutch"] < CLUTCH_ENGAGED or b["clutch"] < CLUTCH_ENGAGED or b["gear"] != a["gear"]:
                rejected["embrague_abierto_o_cambio"] += 1
                continue
            if math.isnan(T):
                rejected["par_desconocido"] += 1
                continue
            w_eng = a["rpm"] * 2.0 * math.pi / 60.0
            driven = range(4) if v.traction == "AWD" else (range(2) if v.traction == "FWD" else range(2, 4))
            w_drv = sum(abs(a[f"{vcol}{i}"]) for i in driven) / len(driven)
            if w_drv <= 0.0 or abs(w_eng / (r * w_drv) - 1.0) > 0.10:
                rejected["embrague_patina"] += 1
                continue
        else:
            T = 0.0
        inertia = a[f"{icol}0"]
        regime = "neutral" if not in_gear else ("power" if T > 0 else "coast")
        base = {"t": a["t"], "regime": regime, "T": T, "r": r, "seg": seg_uid}
        pairs = []
        if v.traction == "AWD":
            pairs.append(("front", 0, 1, v.front, s * T))
            pairs.append(("rear", 2, 3, v.rear, (1.0 - s) * T))
        else:
            pairs.append(("axle2wd", 0, 1, v.diff2wd, T))
        for name, i, j, d, t_ref in pairs:
            dw_a = a[f"{vcol}{i}"] - a[f"{vcol}{j}"]
            dw_b = b[f"{vcol}{i}"] - b[f"{vcol}{j}"]
            dw = 0.5 * (dw_a + dw_b)
            dfb = 0.5 * (a[f"fbt{i}"] + b[f"fbt{i}"]) - 0.5 * (a[f"fbt{j}"] + b[f"fbt{j}"])
            x = dfb - inertia * (dw_b - dw_a) / dt
            lim = limit(d, t_ref, r)
            rec = dict(base, dw=dw, x=x, x_no_inertia=dfb, lim=lim, p=lim * sat(dw, win))
            thr = max(min_dw, step_threshold(lim, inertia) if step_stable else 0.0)
            ok = abs(dw_a) >= thr and abs(dw_b) >= thr and dw_a * dw_b > 0
            samples[name if ok else name + ".bajo"].append(rec)
        if v.traction == "AWD" and (abs(s - 0.5) < 1e-9 or not in_gear):
            S_a = (a["shaftv2"] + a["shaftv3"]) - (a["shaftv0"] + a["shaftv1"])
            S_b = (b["shaftv2"] + b["shaftv3"]) - (b["shaftv0"] + b["shaftv1"])
            fbs = [0.5 * (a[f"fbt{i}"] + b[f"fbt{i}"]) for i in range(4)]
            dfb = fbs[2] + fbs[3] - fbs[0] - fbs[1]
            x = dfb - inertia * (S_b - S_a) / dt
            dw = 0.25 * (S_a + S_b)
            lim = limit(v.centre, T, r)
            rec = dict(base, dw=dw, x=x, x_no_inertia=dfb, lim=lim, p=lim * sat(dw, win))
            thr = max(min_dw, step_threshold(lim, inertia, centre=True) if step_stable else 0.0)
            ok = abs(S_a / 2) >= thr and abs(S_b / 2) >= thr and S_a * S_b > 0
            samples["centre" if ok else "centre.bajo"].append(rec)
    return {"samples": samples, "rejected": dict(rejected)}


# --- bloques y ajuste ----------------------------------------------------------------


def blocks_of(recs: list[dict], by_segment: bool) -> list[list[dict]]:
    """Bloques independientes [corrección 5]."""
    if by_segment:
        g: dict[int, list[dict]] = defaultdict(list)
        for q in recs:
            g[q["seg"]].append(q)
        return [g[k] for k in sorted(g)]
    out: list[list[dict]] = []
    for q in sorted(recs, key=lambda q: q["t"]):
        if out and q["t"] - out[-1][-1]["t"] <= EPISODE_GAP_S:
            out[-1].append(q)
        else:
            out.append([q])
    return out


def rho(qs: list[dict], key: str = "x") -> float | None:
    spp = sum(q["p"] ** 2 for q in qs)
    return sum(q[key] * q["p"] for q in qs) / spp if spp > 0 else None


def fit(recs: list[dict], by_segment: bool) -> dict:
    use = [q for q in recs if abs(q["p"]) > 1.0]
    out: dict = {"n": len(recs), "n_with_prediction": len(use)}
    if not use:
        return out
    out["rho"] = rho(use)
    out["rho_sin_inercia"] = rho(use, "x_no_inertia")
    rb = []
    for qs in blocks_of(use, by_segment):
        if len(qs) >= MIN_BLOCK_SAMPLES and qs[-1]["t"] - qs[0]["t"] >= MIN_BLOCK_SECONDS:
            val = rho(qs)
            if val is not None:
                rb.append(val)
    out["blocks"] = len(rb)
    out["block_rhos"] = rb
    if rb:
        srt = sorted(rb)
        out["rho_median"] = statistics.median(srt)
        out["rho_p10"] = srt[int(0.1 * (len(srt) - 1))]
        out["rho_p90"] = srt[int(0.9 * (len(srt) - 1))]
    out["residual_rms"] = math.sqrt(statistics.fmean((q["x"] - out["rho"] * q["p"]) ** 2 for q in use))
    return out


def classify(f: dict) -> str:
    r = f.get("rho_median")
    if r is None or f.get("blocks", 0) < MIN_BLOCKS:
        return "insuficiente"
    if abs(r - 1.0) <= TOL and f["rho_p10"] >= 1.0 - 2 * TOL and f["rho_p90"] <= 1.0 + 2 * TOL:
        return "coincide_lectura_C"
    for name, target in (("diferencia_lim_medios", 0.5), ("diferencia_2lim", 2.0)):
        if abs(r / target - 1.0) <= TOL:
            return name
    return "no_concluyente"


PARAM_REGIME = {"preload": None, "power": "power", "coast": "coast", "combined": None}


def summarise(per_car: dict[str, dict], by_segment: dict[str, bool]) -> dict[str, dict]:
    params: dict[str, dict] = {}
    for car_id, res in per_car.items():
        v = BY_ID[car_id]
        for m in v.measures:
            diff, param = m.split(".")
            if param == "none":
                continue
            regime = PARAM_REGIME.get(param)
            d = getattr(v, diff, None) if diff != "axle2wd" else v.diff2wd
            if param == "preload" and d is not None and (d.power or d.coast):
                regime = "neutral"
            recs = [q for q in res["samples"].get(diff, []) if regime is None or q["regime"] == regime]
            f = fit(recs, by_segment[car_id])
            f["status"] = classify(f)
            f["k_equivalente"] = (2.0 / f["rho_median"]) if f.get("rho_median") else None
            params[f"{v.key}.{diff}.{param}"] = f
    return params


# --- CLI ---------------------------------------------------------------------------------


def analyse_folders(folders: list[Path], step_stable: bool = True) -> dict:
    files = sorted(p for fo in folders for p in fo.glob("*.csv"))
    synthetic = any((fo / "SINTETICO.txt").exists() for fo in folders) or any("SINTETICO" in f.name for f in files)
    inputs, logs = [], []
    for f in files:
        log = load_log(f, synthetic)
        inputs.append({"file": f.name, "folder": f.parent.name, "sha256": sha256(f),
                       "car_id": log.car_id if log else None,
                       "time_base": log.time_base if log else None,
                       "duplicates": log.duplicates if log else None,
                       "rows": len(log.rows) if log else None})
        if log is not None:
            logs.append(log)
    per_car: dict[str, dict] = {}
    by_segment: dict[str, bool] = {}
    checks = {}
    for idx, log in enumerate(logs):
        res = analyse_log(log, step_stable)
        for recs in res["samples"].values():
            for q in recs:  # bloque = (archivo, repetición): seg_uid solo es único dentro de un CSV
                q["seg"] = idx * 1_000_000 + q["seg"]
        checks[log.path.name] = torque_check(log)
        acc = per_car.setdefault(log.car_id, {"samples": defaultdict(list), "rejected": defaultdict(int)})
        for k, recs in res["samples"].items():
            acc["samples"][k].extend(recs)  # seg_uid es único en todo el banco
        for k, n in res["rejected"].items():
            acc["rejected"][k] += n
        by_segment[log.car_id] = by_segment.get(log.car_id, True) and bool(log.segments)
    params = summarise(per_car, by_segment)
    controls = {}
    for cid, res in per_car.items():
        if BY_ID[cid].key == "A0":  # bloqueos 0: el par transferido medido debe ser ≈ 0
            for diff, recs in res["samples"].items():
                if recs:
                    xs = [q["x"] for q in recs]
                    controls[diff] = {"n": len(xs), "x_mean": statistics.fmean(xs),
                                      "x_rms": math.sqrt(statistics.fmean(x * x for x in xs)),
                                      "fb_diff_rms": math.sqrt(statistics.fmean(q["x_no_inertia"] ** 2 for q in recs))}
    # puerta del par por COCHE: si algún CSV de ese coche no pasa el balance, su POWER/COAST no concluye
    bad_cars = {lg.car_id for lg in logs if checks[lg.path.name]["status"] == "no_coincide"}
    bad_keys = {BY_ID[c].key for c in bad_cars}
    for k, p in params.items():
        if k.split(".")[0] in bad_keys and p["status"] != "insuficiente" and not k.endswith(".preload"):
            p["status_before_torque_gate"] = p["status"]
            p["status"] = "no_concluyente"  # el par de referencia no está validado en algún archivo
    return {"synthetic": synthetic, "step_stable": step_stable, "inputs": inputs, "params": params,
            "controls": controls,
            "torque_check": checks,
            "rejected": {cid: dict(r["rejected"]) for cid, r in per_car.items()},
            "blocks_by_segment": by_segment}


def apply_sensitivity_gate(strict: dict, loose: dict) -> None:
    """Adenda al pre-registro (2026-11-07, antes de analizar F4): un parámetro solo queda en
    ``coincide_lectura_C`` si coincide CON y SIN el criterio paso a paso. El criterio no se pudo
    justificar con el port (``auditoria/aliasing_port.txt``) y descarta datos: si el resultado
    depende de él, es ``no_concluyente``."""
    for k, p in strict["params"].items():
        q = loose["params"].get(k, {})
        p["status_sin_criterio_paso"] = q.get("status")
        p["rho_median_sin_criterio_paso"] = q.get("rho_median")
        p["blocks_sin_criterio_paso"] = q.get("blocks", 0)
        if p["status"] == "coincide_lectura_C" and q.get("status") != "coincide_lectura_C":
            p["status_antes_de_sensibilidad"] = p["status"]
            p["status"] = "no_concluyente"
    strict["sensitivity_gate"] = True


def verdict(result: dict) -> dict:
    acs = {"path": str(ACS_EXE), "sha256": sha256(ACS_EXE) if ACS_EXE.is_file() else None}
    statuses = {k: p["status"] for k, p in result["params"].items()}
    return {
        "schema": "beam2ac.g221.lsd_ac_verdict/1",
        "analyzer": "tools/acbench/analysis.py (G2.22)",
        "source": "sintetico" if result["synthetic"] else "ac_in_game",
        "acs_exe": acs,
        "acs_matches_g211_analysis": bool(acs["sha256"] and acs["sha256"].startswith(ACS_SHA_G211)),
        "inputs": result["inputs"],
        "params": statuses,
        "independent_blocks": {k: p.get("blocks", 0) for k, p in result["params"].items()},
        "criteria": {"min_dw": MIN_DW, "step_stable": result["step_stable"], "step_margin": STEP_MARGIN,
                     "tol": TOL, "min_blocks": MIN_BLOCKS, "min_block_samples": MIN_BLOCK_SAMPLES,
                     "block": "repetición del controlador" if all(result["blocks_by_segment"].values())
                     else "episodio separado ≥ 1 s (registro manual: NO independiente por construcción)"},
        "torque_check": {k: c["status"] for k, c in result["torque_check"].items()},
        "all_measured_match_reading": bool(statuses) and all(s == "coincide_lectura_C" for s in statuses.values()),
    }


def report_md(result: dict, v: dict) -> str:
    lines = ["# Banco LSD de AC — análisis G2.22", "",
             f"Fuente: **{v['source']}**. CSV: {len(result['inputs'])}. "
             f"`acs.exe`: `{(v['acs_exe']['sha256'] or '?')[:12]}…`. "
             f"Bloques por repetición del controlador: {result['blocks_by_segment']}.", "",
             "| Parámetro | n | bloques | ρ global | ρ sin inercia | ρ mediana [p10, p90] | sin criterio paso: bloques · ρ med · estado | estado |",
             "|---|---|---|---|---|---|---|---|"]
    for k, p in result["params"].items():
        g = f"{p['rho']:.4f}" if "rho" in p else "—"
        g0 = f"{p['rho_sin_inercia']:.4f}" if p.get("rho_sin_inercia") is not None else "—"
        med = f"{p['rho_median']:.4f} [{p['rho_p10']:.3f}, {p['rho_p90']:.3f}]" if "rho_median" in p else "—"
        alt = "—"
        if "status_sin_criterio_paso" in p:
            rm = p.get("rho_median_sin_criterio_paso")
            alt = (f"{p['blocks_sin_criterio_paso']} · {rm:.4f} · {p['status_sin_criterio_paso']}" if rm is not None
                   else f"{p['blocks_sin_criterio_paso']} · — · {p['status_sin_criterio_paso']}")
        lines.append(f"| {k} | {p['n']} | {p.get('blocks', 0)} | {g} | {g0} | {med} | {alt} | {p['status']} |")
    if result.get("controls"):
        lines += ["", "Control A0 (bloqueos 0): par transferido estimado X (debe ser ≈ 0)", "",
                  "| Diferencial | n | media X (N·m) | rms X (N·m) | rms (fb_L − fb_R) (N·m) |", "|---|---|---|---|---|"]
        for d, c in result["controls"].items():
            lines.append(f"| {d} | {c['n']} | {c['x_mean']:.2f} | {c['x_rms']:.2f} | {c['fb_diff_rms']:.1f} |")
    lines += ["", "Par de referencia: balance de 4 ruedas frente a `drivetrain_torque`", "",
              "| CSV | n | mediana dif. (N·m) | IQR | mediana abs(T) | estado |", "|---|---|---|---|---|---|"]
    for n, c in result["torque_check"].items():
        if c["status"] in ("sin_datos", "no_aplica_2wd"):
            lines.append(f"| {n} | {c['n']} | — | — | — | {c['status']} |")
        else:
            lines.append(f"| {n} | {c['n']} | {c['median_diff']:.2f} | {c['iqr'][0]:.1f}…{c['iqr'][1]:.1f} | "
                         f"{c['median_abs_T']:.1f} | {c['status']} |")
    lines += ["", "Muestras descartadas por motivo", ""]
    for cid, r in result["rejected"].items():
        lines.append(f"- `{cid}`: " + ", ".join(f"{k} {n}" for k, n in sorted(r.items())))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", type=Path, nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sin-criterio-paso", action="store_true",
                    help="desactiva la saturación estable paso a paso (solo para comparar con G2.21)")
    a = ap.parse_args(argv)
    result = analyse_folders(a.logs, step_stable=not a.sin_criterio_paso)
    if not a.sin_criterio_paso:
        apply_sensitivity_gate(result, analyse_folders(a.logs, step_stable=False))
    if not result["inputs"]:
        print("sin CSV")
        return 2
    v = verdict(result)
    out = claim_output_dir(ensure_safe_output_path(a.out))
    (out / "resultados.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), "utf-8")
    (out / "veredicto.json").write_text(json.dumps(v, indent=1, ensure_ascii=False), "utf-8")
    md = report_md(result, v)
    (out / "resultados.md").write_text(md, "utf-8")
    sys.stdout.buffer.write(md.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
