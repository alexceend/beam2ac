"""Valida la estimación del neumático SIN banco (2026-11-16).

    python tools/analysis/tyre_estimate_validation.py --fits output/tyre_study_2026-11-16/fits.json \
        --out output/tyre_study_2026-11-16/validation.json

Para cada coche (los registros repetidos del mismo coche se agrupan) se estima el modelo SCTM
con una regresión entrenada SIN ese coche (y, en la variante «familia», sin ninguno de su
familia de exportador) y se evalúa el SCTM estimado contra las muestras MEDIDAS del coche
excluido: RMS en μ por barrido (``tyre_fit._stats``), frente al RMS del ajuste a las propias
muestras y al ``max_rms`` de producción (0,06).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.tyre_fit import INI_FIELDS, _stats, points_from_dataset  # noqa: E402
from src.ac.tyre_sctm import SctmParams  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402

INI_OF = {v: k for k, v in INI_FIELDS.items()}  # campo SctmParams -> clave INI
PREDICTED = [
    "dy_ref", "dx_ref", "ls_expy", "ls_expx", "friction_limit_angle", "flex_gain",
    "falloff_level", "falloff_speed", "cx_mult", "speed_sensitivity", "combined_factor",
]  # fmt: skip
CONSTANT = ["brake_dx_mod", "camber_gain"]  # la regresión no mejora a la media
FEATURES = {
    "const": [],
    "noLoad": ["noLoadCoef"],
    "nl+fl+slope": ["noLoadCoef", "fullLoadCoef", "slope"],
    "nl+fl+slope+tread+soft": ["noLoadCoef", "fullLoadCoef", "slope", "treadCoef", "softnessCoef"],
}  # fmt: skip


def car_of(record: str) -> str:
    return re.sub(r"(_c[0-9a-f]{8}|_inedito_g\d+)$", "", record)


def family_of(car: str) -> str:
    if car.startswith(("2020_rpm", "2021_rpm", "rpm_")):
        return "200409"
    if car in ("composed_fox", "porsche_gt3_rs", "spicy_mandala", "susui_espol_vtec"):
        return "38022"
    return "40340"


def feats(item: dict, names: list[str]) -> list[float]:
    w = item["wheel_params"]
    vals = {
        "noLoadCoef": w["noLoadCoef"],
        "fullLoadCoef": w["fullLoadCoef"],
        "slope": w["loadSensitivitySlope"] * 1e4,
        "treadCoef": w["treadCoef"],
        "softnessCoef": w["softnessCoef"],
    }
    return [vals[n] for n in names]


def train(items: list[dict], names: list[str]) -> dict:
    X = np.array([[1.0] + feats(i, names) for i in items])
    out = {}
    for k in PREDICTED + CONSTANT:
        y = np.array([i["fit"][INI_OF[k]] for i in items])
        use = names if k in PREDICTED else []
        A = X[:, : 1 + len(use)]
        coef, *_ = np.linalg.lstsq(A, y, rcond=1e-9)
        out[k] = (len(use), coef)
    return out


def predict(model: dict, item: dict, names: list[str]) -> SctmParams:
    x = np.array([1.0] + feats(item, names))
    vals = {}
    for k, (n, coef) in model.items():
        vals[k] = float(x[: 1 + n] @ coef)
    lim = {
        "dy_ref": (0.2, 4.0), "dx_ref": (0.2, 4.0), "ls_expy": (0.3, 1.2), "ls_expx": (0.3, 1.2),
        "friction_limit_angle": (0.5, 40.0), "flex_gain": (-0.95, 3.0), "falloff_level": (0.2, 1.0),
        "falloff_speed": (0.0, 100.0), "cx_mult": (0.2, 8.0), "speed_sensitivity": (0.0, 0.1),
        "combined_factor": (1.0, 6.0), "brake_dx_mod": (-0.6, 1.0), "camber_gain": (-1.0, 3.0),
    }  # fmt: skip
    vals = {k: min(max(v, lim[k][0]), lim[k][1]) for k, v in vals.items()}
    return SctmParams(fz0=item["reference_load"], **vals)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    fits = json.loads(a.fits.read_text("utf-8"))
    items = [r for r in fits if "fit" in r]
    # cada INI-key del ajuste ya está en r["fit"] (claves de tyres.ini)
    for r in items:
        r["car"] = car_of(r["record"])
        r["family"] = family_of(r["car"])
        rec = json.loads(
            (ROOT / f"measurements/beamng_bench/tyre/{r['record']}.json").read_text(
                "utf-8"
            )
        )
        r["pts"] = points_from_dataset(rec["axles"][r["axle"]]["samples"])
    cars = sorted({r["car"] for r in items})
    result: dict = {"cars": {}, "summary": {}}
    for scheme in ("car", "family"):
        for fname, names in FEATURES.items():
            errs = []
            for car in cars:
                fam = family_of(car)
                tr = [
                    r
                    for r in items
                    if (r["car"] != car if scheme == "car" else r["family"] != fam)
                ]
                model = train(tr, names)
                for r in [x for x in items if x["car"] == car]:
                    p = predict(model, r, names)
                    rms, _, n = _stats(p, r["pts"])
                    key = f"{scheme}/{fname}"
                    result["cars"].setdefault(car, {}).setdefault(key, {})[
                        r["axle"]
                    ] = rms
                    errs.append(rms)
            agg = defaultdict(list)
            for e in errs:
                for k, v in e.items():
                    agg[k].append(v)
            result["summary"][f"{scheme}/{fname}"] = {
                k: {
                    "mean": float(np.mean(v)),
                    "p90": float(np.percentile(v, 90)),
                    "max": float(np.max(v)),
                }
                for k, v in agg.items()
            }
    own = defaultdict(list)
    for r in items:
        for k, v in r["rms"].items():
            own[k].append(v)
    result["own_fit_rms_mean"] = {k: float(np.mean(v)) for k, v in own.items()}
    out = ensure_safe_output_path(a.out)
    out.write_text(json.dumps(result, indent=1), "utf-8")
    print("referencia (ajuste a sus propias muestras):", result["own_fit_rms_mean"])
    for k, v in result["summary"].items():
        print(
            k,
            {kk: round(vv["mean"], 3) for kk, vv in v.items()},
            "max",
            round(max(vv["max"] for vv in v.values()), 3),
        )
    b = result["cars"].get("bisexual_aspiration", {})
    for k in ("car/nl+fl+slope", "family/nl+fl+slope"):
        print(
            "bisexual",
            k,
            {
                ax: {kk: round(vv, 3) for kk, vv in d.items()}
                for ax, d in b.get(k, {}).items()
            },
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
