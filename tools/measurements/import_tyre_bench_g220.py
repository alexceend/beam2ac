"""Registra las medidas del banco de neumáticos de G2.20 como medidas del origen.

    python tools/measurements/import_tyre_bench_g220.py --sessions s2,s3 [--dry-run]
        [--out measurements/beamng_bench/tyre]

Fuente: banco de BeamNG 0.38.6 de G2.20 (``output/g220_2026-11-07/banco/``), una carpeta
de usuario aislada por sesión (``userpath_<s>/current``):

* ``beam2ac_tyre/<id>_<s>.csv`` y ``.meta.json`` (volcado de ``beam2acTyre.lua``);
* la copia de los vehículos que cargó el juego (``vehicles/<id>/``).

Para cada vehículo del 200409 de ``samples_beamng/`` presente en TODAS las sesiones:

1. comprueba que la copia cargada es IDÉNTICA byte a byte a la muestra; si no, se detiene;
2. reduce cada sesión con ``src/extract/tyre_bench.py`` (determinista);
3. la PRIMERA sesión da los datos; las demás, la repetibilidad (mismas magnitudes);
4. escribe ``<out>/<id>.json`` (``beamng_tyre_bench``) con la huella del origen, la versión
   del juego, los sha256 de los volcados, del script y del código de reducción.

Nunca sobrescribe: si el archivo existe, se detiene. El nombre es solo una etiqueta (el
importador asocia por la huella).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.extract import tyre_bench as tb  # noqa: E402
from src.importers.measurements import (  # noqa: E402
    MEASUREMENTS_ROOT,
    SCHEMA,
    source_fingerprint,
)
from src.pipeline.physics_preview import load_vehicle_dir  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402

BENCH = ROOT / "output/g220_2026-11-07/banco"
SCRIPT = BENCH / "lua_src/lua/vehicle/extensions/beam2acTyre.lua"
CODE = ROOT / "src/extract/tyre_bench.py"
CHECKED = (".jbeam", ".pc", ".cs")
CHECKED_NAMES = ("info.json",)
COLUMNS = [
    ["wheel", "text"],
    ["kind", "text"],
    ["condition", "text"],
    ["n", "1"],
    ["fz", "N"],
    ["slip_angle", "deg"],
    ["slip_ratio", "1"],
    ["camber_axis", "deg"],
    ["speed", "m/s"],
    ["fx", "N"],
    ["fy", "N"],
    ["pressure", "Pa"],
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def game_version(log: Path) -> str:
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
        if "Log started - v " in line:
            return line.split("Log started - v ", 1)[1].split()[0]
    return "?"


def check_copy(sample: Path, copy: Path) -> list[str]:
    files = []
    for f in sorted(sample.rglob("*")):
        if not f.is_file() or not (f.suffix in CHECKED or f.name in CHECKED_NAMES):
            continue
        c = copy / f.relative_to(sample)
        if not c.is_file() or sha(c) != sha(f):
            raise SystemExit(f"la copia del banco difiere de la muestra: {c}")
        files.append(f.relative_to(ROOT).as_posix())
    return files


def _mean(xs):
    return sum(xs) / len(xs)


def reduce_session(user: Path, vid: str, session: str) -> dict:
    stem = Path(user).resolve() / "beam2ac_tyre" / f"{vid}_{session}"
    meta = json.loads(Path(str(stem) + ".meta.json").read_text("utf-8"))
    csv_path = Path(str(stem) + ".csv")
    samples = tb.reduce_rows(meta, tb.read_csv_text(csv_path.read_text("utf-8")))
    closure = tb.static_check(meta, samples, "settle")
    rfree = tb.free_radius(samples)
    pts = tb.binned_points(samples, rfree)
    vstat = tb.vertical_static(samples)
    present = {s.seg_name for s in samples}
    vdyn = {seg: tb.vertical_dynamic(meta, samples, seg)
            for seg in ("vosc_3hz", "vosc_12hz", "voscl_8hz", "voscl_12hz",
                        "voscl_12hz_a12", "voscl_3hz") if seg in present}
    rr = tb.rolling_resistance(samples)
    axles: dict[str, dict] = {}
    for w in meta["wheels"]:
        a = axles.setdefault(w["axle"], {"wheels": []})
        a["wheels"].append(w["name"])
    for axle, a in axles.items():
        ws = a["wheels"]
        settle = [s for s in samples if s.seg_name == "settle" and s.axle == axle and s.t > 2]
        a["reference_load"] = _mean([s.fz for s in settle])
        a["reference_load_by_wheel"] = {
            w: _mean([s.fz for s in settle if s.wheel == w]) for w in ws
        }
        a["free_rolling_radius"] = _mean([rfree[(w, "base")] for w in ws])
        a["free_rolling_radius_by_condition"] = {
            tag: _mean([rfree[(w, tag)] for w in ws])
            for (w0, tag) in rfree
            if w0 == ws[0] and all((w, tag) in rfree for w in ws)
        }
        a["vertical_stiffness_static"] = _mean([vstat[w]["stiffness"] for w in ws])
        a["vertical_static_by_wheel"] = {w: vstat[w] for w in ws}
        a["vertical_dynamic"] = {
            seg: {
                "stiffness": _mean([d[w]["stiffness"] for w in ws]),
                "damping": _mean([d[w]["damping"] for w in ws]),
                "by_wheel": {w: d[w] for w in ws},
            }
            for seg, d in vdyn.items()
        }
        wm = [w for w in meta["wheels"] if w["axle"] == axle]
        a["rotational_inertia"] = _mean([float(w["inertia"]) for w in wm])
        a["rotating_mass"] = _mean([float(w["mass"]) for w in wm])
        a["rolling_resistance"] = rr.get(axle, {}).get("points", [])
        a["wheel_params"] = {k: wm[0].get(k) for k in (
            "radius", "hubRadius", "tireWidth", "numRays", "pressurePSI", "frictionCoef",
            "slidingFrictionCoef", "noLoadCoef", "fullLoadCoef", "loadSensitivitySlope",
            "softnessCoef", "treadCoef", "stribeckExponent", "stribeckVelMult")}
        rows = []
        for p in pts:
            if p["axle"] != axle:
                continue
            rows.append([p["wheel"], p["kind"], p["tag"], p["n"], round(p["fz"], 3),
                         round(p["slip_angle_deg"], 5), round(p["slip_ratio"], 6),
                         round(p["camber_axis_deg"], 4), round(p["speed"], 4),
                         round(p["fx"], 3), round(p["fy"], 3), round(p["pressure_pa"], 1)])
        a["samples"] = {"columns": COLUMNS, "rows": rows}
    return {
        "session": session,
        "csv": csv_path.relative_to(ROOT).as_posix(),
        "csv_sha256": sha(csv_path),
        "meta_sha256": sha(Path(str(stem) + ".meta.json")),
        "closure": closure,
        "total_mass": meta["totalMass"],
        "wall_seconds": meta.get("wallSeconds"),
        "axles": axles,
    }


def repeatability(a: dict, b: dict) -> dict:
    """Diferencias entre sesiones: escalares y μ por intervalo común (mismas filas)."""
    out = {}
    for axle in a["axles"]:
        x, y = a["axles"][axle], b["axles"][axle]
        d = {k: y[k] / x[k] - 1 for k in ("reference_load", "free_rolling_radius",
                                           "vertical_stiffness_static")}
        key = {tuple(r[:3]) + (r[1] == "lat" and round(r[5] * 2) or round(r[6] * 100),): r
               for r in x["samples"]["rows"]}
        dmu = []
        for r in y["samples"]["rows"]:
            k = tuple(r[:3]) + (r[1] == "lat" and round(r[5] * 2) or round(r[6] * 100),)
            if k in key:
                q = key[k]
                col = 10 if r[1] == "lat" else 9
                dmu.append(r[col] / r[4] - q[col] / q[4])
        d["mu_rms_diff"] = math.sqrt(_mean([v * v for v in dmu])) if dmu else None
        d["mu_max_abs_diff"] = max(abs(v) for v in dmu) if dmu else None
        d["n_common_bins"] = len(dmu)
        out[axle] = d
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(MEASUREMENTS_ROOT / "beamng_bench/tyre"))
    # G2.29: el mismo banco copiado a otra carpeta (40340); por defecto, el de G2.20
    ap.add_argument("--bench", default=str(BENCH))
    ap.add_argument("--report", default="docs/research/g220_tyre_grip_model_2026-11-07.md")
    args = ap.parse_args(argv)
    bench = Path(args.bench).resolve()
    script = bench / SCRIPT.relative_to(BENCH)
    sessions = args.sessions.split(",")
    out_dir = Path(args.out)
    users = {s: bench / f"userpath_{s}" / "current" for s in sessions}
    ids = sorted(
        p.name[: -len(f"_{sessions[0]}.csv")]
        for p in (users[sessions[0]] / "beam2ac_tyre").glob(f"*_{sessions[0]}.csv")
    )
    written = 0
    for vid in ids:
        if not all((users[s] / "beam2ac_tyre" / f"{vid}_{s}.csv").is_file() for s in sessions):
            print(f"{vid}: falta en alguna sesión, se omite")
            continue
        sample = ROOT / "samples_beamng" / vid / "vehicles" / vid
        if not sample.is_dir():  # exportador 40340: thershow_<id>/vehicles/<id>
            sample = ROOT / "samples_beamng" / f"thershow_{vid}" / "vehicles" / vid
        if not sample.is_dir():  # G2.44: mods 38022 extraídos de los zip
            sample = next(
                (ROOT / "output/g244_2026-11-14/mods_src").glob(f"*/vehicles/{vid}"), sample
            )
        files = []
        for s in sessions:
            files = check_copy(sample, users[s] / "vehicles" / vid)
        src = load_vehicle_dir(sample, sorted(p.name for p in sample.glob("*.pc"))[0])
        fp = source_fingerprint(src)
        red = [reduce_session(users[s], vid, s) for s in sessions]
        rec = {
            "schema": SCHEMA,
            "kind": "beamng_tyre_bench",
            "source_fingerprint": fp,
            "label": vid,
            "game": {"name": "BeamNG.drive",
                     "version": game_version(users[sessions[0]] / "beamng.log")},
            "bench": {
                "script": script.relative_to(ROOT).as_posix(),
                "script_sha256": sha(script),
                "reduction_code": CODE.relative_to(ROOT).as_posix(),
                "reduction_code_sha256": sha(CODE),
                "report": args.report,
                "sessions": [{k: r[k] for k in ("session", "csv", "csv_sha256", "meta_sha256",
                                                "closure", "wall_seconds")} for r in red],
                "vehicle_files": files,
            },
            "axles": red[0]["axles"],
            "repeatability": {r["session"]: repeatability(red[0], r) for r in red[1:]},
        }
        target = out_dir / f"{vid}.json"
        if target.exists():
            raise SystemExit(f"ya existe (no se sobrescribe): {target}")
        print(f"{vid}: huella {fp[:12]}  filas "
              f"{ {a: len(v['samples']['rows']) for a, v in rec['axles'].items()} }  "
              f"cierre {red[0]['closure']['rel_error']:+.4f}")
        if not args.dry_run:
            if target.is_relative_to(MEASUREMENTS_ROOT):
                target.parent.mkdir(parents=True, exist_ok=True)
            else:
                target = ensure_safe_output_path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
            written += 1
    print(f"escritos {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
