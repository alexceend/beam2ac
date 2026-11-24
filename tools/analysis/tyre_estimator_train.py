"""Entrena y CONGELA el estimador de neumático sin banco (2026-11-16).

    python tools/analysis/tyre_fit_all_benches.py --out output/<nueva>/fits.json
    python tools/analysis/tyre_estimator_train.py --fits output/<nueva>/fits.json \
        --out src/ac/tyre_estimator_coefficients.json

Selecciona, para cada parámetro SCTM, el conjunto de regresores (``src/ac/tyre_estimate.py``)
con menor error dejando UNA FAMILIA de exportador fuera, ajusta con todos los datos y valida
el SCTM estimado contra las muestras MEDIDAS del coche excluido (RMS en μ por barrido,
``tyre_fit._stats``) dejando fuera el coche y la familia. Escribe los coeficientes con la lista y el
sha256 de los registros usados. Se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.tyre_estimate import LIMITS, STRUCT_KEYS, features  # noqa: E402
from src.ac.tyre_fit import INI_FIELDS, _stats, points_from_dataset  # noqa: E402
from src.ac.tyre_sctm import SctmParams  # noqa: E402

INI_OF = {v: k for k, v in INI_FIELDS.items()}
C_GRID = (
    0.30,
    1 / 3,
    0.40,
)  # c = 1/m, m = reparto de carga entre nodos (m = 3: LS_EXP)
PREDICTED = [
    "dy_ref", "dx_ref", "ls_expy", "ls_expx", "friction_limit_angle", "flex_gain",
    "falloff_level", "falloff_speed", "cx_mult", "speed_sensitivity", "combined_factor",
]  # fmt: skip
CONSTANT = ["brake_dx_mod", "camber_gain"]
LOG = {"dy_ref", "dx_ref", "cx_mult"}
SETS = {
    "const": [],
    "lmu": ["lmu"],
    "lmu+dl": ["lmu", "dl"],
    "lmu+ratio": ["lmu", "ratio"],
    "lmu+slope": ["lmu", "slope"],
    "lmu+sl": ["lmu", "sl"],
    "lmu+dl+sl": ["lmu", "dl", "sl"],
    "lmu+sl+se+sv": ["lmu", "sl", "se", "sv"],
    "sv": ["sv"],
    "se+sv": ["se", "sv"],
    "lmu+sv": ["lmu", "sv"],
    "lmu+dl+sv": ["lmu", "dl", "sv"],
    "lmu+dl+sv+psi": ["lmu", "dl", "sv", "psi"],
    "lmu+dl+psi": ["lmu", "dl", "psi"],
    "lmu+w+psi": ["lmu", "width", "psi"],
    "lmu+soft+tread": ["lmu", "soft", "tread"],
    "lmu+dl+w": ["lmu", "dl", "width"],
    "lmu+dl+soft": ["lmu", "dl", "soft"],
    "ltread+width": ["ltread", "width"],
    "lmu+dl+ltread+width": ["lmu", "dl", "ltread", "width"],
    "lmu+dl+ltread": ["lmu", "dl", "ltread"],
    "fla0": ["fla0"],
    "fla0+psi": ["fla0", "psi"],
    "reinf+psi": ["lreinf", "psi"],
    "lmu+dl+reinf": ["lmu", "dl", "lreinf"],
    "lmu+dl+reinf+psi": ["lmu", "dl", "lreinf", "psi"],
    "tz+width": ["ltz", "width"],
    "lmu+dl+tz+width": ["lmu", "dl", "ltz", "width"],
    "trr+psi": ["ltrr", "psi"],
    "lmu+dl+trr": ["lmu", "dl", "ltrr"],
    "nl": ["nl"],
    "nl+fl+slope": ["nl", "fl", "slope"],
}


def car_of(record: str) -> str:
    return re.sub(r"(_c[0-9a-f]{8}|_inedito_g\d+)$", "", record)


def family_of(car: str) -> str:
    if car.startswith(("2020_rpm", "2021_rpm", "rpm_")):
        return "200409"
    if car in ("composed_fox", "porsche_gt3_rs", "spicy_mandala", "susui_espol_vtec"):
        return "38022"
    return "40340"


def design(items: list[dict], names: list[str], c: float) -> np.ndarray:
    rows = []
    for r in items:
        f = features(r["wheel_params"], r["reference_load"], c)
        rows.append([1.0] + [f[n] for n in names])
    return np.array(rows)


def target(items: list[dict], p: str) -> np.ndarray:
    y = np.array([r["fit"][INI_OF[p]] for r in items])
    return np.log(y) if p in LOG else y


def fit_models(items: list[dict], c: float, sets: dict[str, str]) -> dict:
    out = {}
    for p in PREDICTED + CONSTANT:
        names = SETS[sets.get(p, "const")] if p in PREDICTED else []
        coef, *_ = np.linalg.lstsq(
            design(items, names, c), target(items, p), rcond=1e-9
        )
        out[p] = {
            "features": names,
            "intercept": float(coef[0]),
            "coef": {n: float(v) for n, v in zip(names, coef[1:])},
            "log": p in LOG,
        }
    return out


def predict(models: dict, r: dict, c: float) -> SctmParams:
    f = features(r["wheel_params"], r["reference_load"], c)
    vals = {}
    for p, m in models.items():
        v = m["intercept"] + sum(m["coef"][k] * f[k] for k in m["features"])
        v = float(np.exp(v)) if m["log"] else v
        lo, hi = LIMITS[p]
        vals[p] = min(max(v, lo), hi)
    return SctmParams(fz0=r["reference_load"], **vals)


def select_sets(items: list[dict], c: float) -> dict[str, str]:
    """Por parámetro, el conjunto con menor error relativo dejando UN COCHE fuera (los
    regímenes de Stribeck coinciden con las familias: dejar una familia fuera nunca
    contiene su régimen y no representa un coche nuevo de una familia conocida)."""
    fams = sorted({r["car"] for r in items})
    chosen = {}
    for p in PREDICTED:
        best = None
        for sname, names in SETS.items():
            errs = []
            for fam in fams:
                tr = [r for r in items if r["car"] != fam]
                te = [r for r in items if r["car"] == fam]
                coef, *_ = np.linalg.lstsq(
                    design(tr, names, c), target(tr, p), rcond=1e-9
                )
                pred = design(te, names, c) @ coef
                yt = target(te, p)
                if p in LOG:
                    pred, yt = np.exp(pred), np.exp(yt)
                errs += list(np.abs(pred - yt) / (abs(yt) + 1e-9))
            e = float(np.mean(errs))
            # la simplicidad gana los empates (±2 %): conjuntos recorridos de simple a complejo
            if best is None or e < best[0] * 0.98:
                best = (e, sname)
        chosen[p] = best[1]
    return chosen


def evaluate(
    items: list[dict], c: float, sets: dict[str, str] | None, scheme: str
) -> dict:
    """``sets=None``: selección ANIDADA (los regresores se eligen dentro de cada pliegue,
    sin ver el coche/familia excluido)."""
    groups = sorted({r["car"] if scheme == "car" else r["family"] for r in items})
    per = defaultdict(list)
    per_fam = defaultdict(list)
    worst = {}
    for g in groups:
        key = (lambda r: r["car"]) if scheme == "car" else (lambda r: r["family"])
        tr = [r for r in items if key(r) != g]
        models = fit_models(tr, c, sets if sets is not None else select_sets(tr, c))
        for r in [x for x in items if key(x) == g]:
            rms, _, _ = _stats(predict(models, r, c), r["pts"])
            for k, v in rms.items():
                per[k].append(v)
            per_fam[r["family"] + "/lat_y"].append(rms.get("lat_y", 0.0))
            per_fam[r["family"] + "/lon_x"].append(rms.get("lon_x", 0.0))
            worst[f"{r['car']}/{r['axle']}"] = max(rms.values())
    return {
        "mean_rms": {k: float(np.mean(v)) for k, v in per.items()},
        "by_family": {k: float(np.mean(v)) for k, v in sorted(per_fam.items())},
        "max_rms": float(max(worst.values())),
        "worst": sorted(worst.items(), key=lambda kv: -kv[1])[:4],
        "share_below_0.06": float(np.mean([v <= 0.06 for v in worst.values()])),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", type=Path, required=True)
    ap.add_argument("--structure", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.out.exists():
        raise SystemExit(f"ya existe (no se sobrescribe): {a.out}")
    items = [r for r in json.loads(a.fits.read_text("utf-8")) if "fit" in r]
    for r in items:
        r["car"] = car_of(r["record"])
        r["family"] = family_of(r["car"])
        path = ROOT / f"measurements/beamng_bench/tyre/{r['record']}.json"
        rec = json.loads(path.read_text("utf-8"))
        r["pts"] = points_from_dataset(rec["axles"][r["axle"]]["samples"])
        r["sha"] = hashlib.sha256(path.read_bytes()).hexdigest()
    structure = {
        (s["record"], ax): p for s in json.loads(a.structure.read_text("utf-8"))
        for ax, p in (s.get("axles") or {}).items()
    }  # fmt: skip
    for r in items:
        st = structure.get((r["record"], r["axle"]), {})
        for k in STRUCT_KEYS:
            if k in st:
                r["wheel_params"][k] = st[k]
    struct_median = {
        k: float(
            np.median([r["wheel_params"][k] for r in items if k in r["wheel_params"]])
        )
        for k in STRUCT_KEYS
    }
    best = None
    for c in C_GRID:
        sets = select_sets(items, c)
        ev = evaluate(
            items, c, sets, "car"
        )  # selección sobre todos: solo para elegir c
        score = float(np.mean(list(ev["mean_rms"].values())))
        print(f"c={c}: RMS medio coche-fuera {score:.4f}")
        if best is None or score < best[0]:
            best = (score, c, sets)
    _, c, sets = best
    validation = {s: evaluate(items, c, None, s) for s in ("car", "family")}  # anidada
    models = fit_models(items, c, sets)
    # auxiliares: radio libre, inercia, rodadura, rigidez vertical
    frr = float(
        np.mean([r["free_rolling_radius"] / r["wheel_params"]["radius"] for r in items])
    )
    recs = {}
    inert, rr, vy = [], [], []
    for r in items:
        rec = recs.setdefault(
            r["record"],
            json.loads(
                (ROOT / f"measurements/beamng_bench/tyre/{r['record']}.json").read_text(
                    "utf-8"
                )
            ),
        )
        ax = rec["axles"][r["axle"]]
        w = ax["wheel_params"]
        inert.append(
            ax["rotational_inertia"] / (ax["rotating_mass"] * w["radius"] ** 2)
        )
        rr.append([p[1] for p in ax["rolling_resistance"]])
        if (
            ax["vertical_stiffness_static"] > 0
        ):  # el banco da pendiente ≤ 0 en algún eje rígido
            p_pa = w["pressurePSI"] * 6894.76
            kphys = (
                4 * w["radius"] * p_pa**2 * w["tireWidth"] ** 2 / ax["reference_load"]
            )
            vy.append(ax["vertical_stiffness_static"] / kphys)
    vscale = float(np.median(vy))  # RATE medido / (4·r·p²·w²/Fz)
    rr_mean = np.mean(np.array([x for x in rr if len(x) == 3]), axis=0)
    cal_range = {
        k: [
            min(r["wheel_params"][k] for r in items),
            max(r["wheel_params"][k] for r in items),
        ]
        for k in (
            "frictionCoef",
            "noLoadCoef",
            "fullLoadCoef",
            "loadSensitivityslope".replace("slope", "Slope"),
        )
    }
    cal_range["reference_load"] = [
        min(r["reference_load"] for r in items),
        max(r["reference_load"] for r in items),
    ]
    ratios = sorted(
        {
            round(
                r["wheel_params"]["fullLoadCoef"] / r["wheel_params"]["noLoadCoef"], 2
            )
            for r in items
        }
    )
    out = {
        "version": 1,
        "node_load_fraction": c,
        "selected_feature_sets": sets,
        "models": models,
        "aux": {
            "free_rolling_radius_ratio": frr,
            "inertia_factor": float(np.mean(inert)),
            "rolling_resistance": [
                [10.0, float(rr_mean[0])],
                [20.0, float(rr_mean[1])],
                [30.0, float(rr_mean[2])],
            ],
            "vertical_stiffness": {"model": "patch 4*r*p^2*w^2/Fz", "scale": vscale},
        },
        "struct_median": struct_median,
        "calibration_range": cal_range,
        "calibration_ratio_families": [
            float(x) for x in sorted({round(x, 1) for x in ratios})
        ],
        "validation": validation,
        "records": sorted({(r["record"], r["sha"]) for r in items}),
        "trained_with": "tools/analysis/tyre_estimator_train.py",
    }
    a.out.write_text(json.dumps(out, indent=1), "utf-8")
    print("seleccion:", sets)
    for s, v in validation.items():
        print(s, "por familia:", {k: round(x, 3) for k, x in v["by_family"].items()})
        print(
            s,
            {k: round(x, 3) for k, x in v["mean_rms"].items()},
            "max",
            round(v["max_rms"], 3),
            "<=0,06:",
            v["share_below_0.06"],
            v["worst"],
        )
    print("escrito", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
