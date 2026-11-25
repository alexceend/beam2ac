"""Banco de neumáticos de BeamNG lanzado desde el conversor (G2.45).

Antes (G2.20/G2.29/G2.44) el banco eran scripts sueltos por coche en ``output/``; un
coche
inédito (otra huella de origen) se quedaba sin ``vertical_stiffness``,
``rotational_inertia`` ni
``force_slip_samples`` y se bloqueaba en 12 claves de ``tyres.ini``. Este módulo repite
el MISMO
procedimiento para cualquier vehículo en carpeta:

1. :func:`prepare`: carpeta de usuario AISLADA de BeamNG
(``<work>/userpath_<sesión>/current``)
   con las extensiones Lua del banco (idénticas byte a byte a las de G2.20,
   ``src/bench/lua``),
   una copia SIN CAMBIOS del vehículo y ``beam2ac_tyre/config.json``;
2. :func:`launch`: ``BeamNG.drive.x64.exe -nosteam -level smallgrid -onLevelLoad_ext
   beam2acTyreGE -userpath …`` (nunca dos sesiones a la vez: se niega si BeamNG ya
   corre) y
   comprueba que la instalación del juego no tiene archivos más nuevos que la marca;
3. :func:`collect`: reduce cada sesión con ``src.extract.tyre_bench`` (determinista),
exige que
   la copia cargada sea idéntica al origen y escribe
   ``measurements/beamng_bench/tyre/<id>.json``
   (``beamng_tyre_bench``) con la huella del origen. Nunca sobrescribe.

La primera sesión da los datos y las demás la repetibilidad (como G2.20): hacen falta ≥
2.
Una sesión de un coche ronda 9 minutos reales.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from src.bench.protocol import protocol
from src.extract import tyre_bench as tb
from src.importers.measurements import (
    MEASUREMENTS_ROOT,
    SCHEMA,
    scoped_fingerprint,
    source_fingerprint,
)
from src.safe_paths import ensure_safe_output_path

ROOT = Path(__file__).resolve().parents[2]
LUA_SRC = Path(__file__).resolve().parent / "lua"
SCRIPT = LUA_SRC / "vehicle/extensions/beam2acTyre.lua"
CODE = ROOT / "src/extract/tyre_bench.py"
CHECKED = (".jbeam", ".pc", ".cs")
CHECKED_NAMES = ("info.json",)
BEAMNG_EXE = "Bin64/BeamNG.drive.x64.exe"
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


class BenchError(RuntimeError):
    pass


class BenchCancelled(BenchError):
    """Sesión interrumpida a petición (G2.46): el juego se cerró y no hay resultado."""


def _rel(p: Path) -> str:
    p = Path(p).resolve()
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _copy_tree(src: Path, dst: Path) -> int:
    n = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        t = ensure_safe_output_path(dst / f.relative_to(src))
        if t.exists():
            if not filecmp.cmp(f, t, shallow=False):
                raise BenchError(f"colisión con contenido distinto: {t}")
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, t)
        n += 1
    return n


def pc_of(vehicle_dir: Path, pc: str | None = None) -> str:
    pcs = sorted(p.name for p in Path(vehicle_dir).glob("*.pc"))
    if pc:
        if pc not in pcs:
            raise BenchError(f"la configuración {pc!r} no está en {vehicle_dir}: {pcs}")
        return pc
    if len(pcs) != 1:
        raise BenchError(f"indique la configuración (.pc): {pcs}")
    return pcs[0]


def prepare(
    work: Path,
    session: str,
    vehicle_dir: Path,
    pc: str | None = None,
    kind: str = "completo",
) -> Path:
    """Carpeta de usuario aislada de una sesión (``<work>/userpath_<s>/current``)."""
    vd = Path(vehicle_dir)
    name = vd.name
    pc = pc_of(vd, pc)
    user = Path(work) / f"userpath_{session}" / "current"
    _copy_tree(LUA_SRC, user / "lua")
    _copy_tree(vd, user / "vehicles" / name)
    segs = protocol(kind)
    cfg = ensure_safe_output_path(user / "beam2ac_tyre" / "config.json")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists():
        raise BenchError(f"la sesión ya está preparada: {cfg}")
    cfg.write_text(
        json.dumps(
            {
                "vehicles": [
                    {
                        "name": name,
                        "model": name,
                        "config": f"vehicles/{name}/{pc}",
                        "tag": session,
                        "protocol": kind,
                        "segments": segs,
                        "timeout": sum(x["dur"] for x in segs) * 1.5 + 120,
                    }
                ]
            },
            indent=1,
        ),
        "utf-8",
    )
    return user


def beamng_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq BeamNG.drive.x64.exe"],
            capture_output=True,
            timeout=30,
        ).stdout
    except subprocess.TimeoutExpired:
        return True  # sin respuesta: suponer abierto (lo seguro)
    # bytes: tasklist usa la página de códigos OEM
    return b"BeamNG.drive.x64.exe" in out


def _newer_count(root: Path, ref: float) -> int:
    n = 0
    for dp, _, files in os.walk(root):
        for f in files:
            try:
                if os.stat(os.path.join(dp, f)).st_mtime > ref:
                    n += 1
            except OSError:
                pass
    return n


def _kill_tree(p: subprocess.Popen) -> None:
    """Cierra el juego y sus procesos hijos (Windows: taskkill /T)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pass  # se intenta p.kill() abajo
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()


def launch(
    work: Path,
    session: str,
    beamng_root: Path,
    timeout: float = 4000,
    ext: str = "beam2acTyreGE",
    cancel: Callable[[], bool] | None = None,
    tick: Callable[[float], None] | None = None,
) -> dict:
    """Una sesión de banco. Devuelve ``{exit, seconds, newer_files}``.

    ``cancel()`` (sondeada cada 2 s) cierra el juego y lanza :class:`BenchCancelled`;
    ``tick(segundos)`` se llama en cada sondeo (supervisión desde la interfaz)."""
    exe = Path(beamng_root) / BEAMNG_EXE
    if not exe.is_file():
        raise BenchError(f"no existe {exe}")
    if beamng_running():
        raise BenchError("BeamNG ya está en ejecución: nunca dos sesiones a la vez")
    user = Path(work) / f"userpath_{session}"
    ref = time.time()
    t0 = time.time()
    p = subprocess.Popen(
        [
            str(exe),
            "-nosteam",
            "-level",
            "smallgrid",
            "-onLevelLoad_ext",
            ext,
            "-userpath",
            str(user.resolve()),
        ]
    )
    while True:
        try:
            p.wait(timeout=2)
            break
        except subprocess.TimeoutExpired:
            pass
        if tick:
            tick(time.time() - t0)
        if cancel and cancel():
            _kill_tree(p)
            raise BenchCancelled(f"sesión {session} cancelada")
        if time.time() - t0 > timeout:
            _kill_tree(p)
            raise BenchError(f"sesión {session}: tiempo agotado ({timeout:.0f} s)")
    info = {
        "exit": p.returncode,
        "seconds": round(time.time() - t0, 1),
        "newer_files": _newer_count(Path(beamng_root), ref),
    }
    (Path(work) / f"lanzamiento_{session}.json").write_text(json.dumps(info), "utf-8")
    if info["newer_files"]:
        raise BenchError(f"la instalación de BeamNG cambió durante el banco: {info}")
    return info


def game_version(log: Path) -> str:
    for line in Path(log).read_text("utf-8", errors="replace").splitlines()[:5]:
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
            raise BenchError(f"la copia del banco difiere de la muestra: {c}")
        files.append(_rel(f))
    return files


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


OSC_WINDOW = 25  # filas (0,5 s a 50 Hz) de la media móvil que define la «tendencia»
OSC_LIMIT = 0.06  # índice por encima del cual el segmento se declara inestable


def oscillation_index(samples: list) -> dict[str, dict[str, float]]:
    """Índice de oscilación de la fuerza por segmento y rueda (G2.46).

    ``sd(F − media móvil de 0,5 s) / media(Fz)`` de Fy en los barridos laterales y Fx en
    los longitudinales. En una condición estable vale ≈ 0,015–0,03; en las que el
    neumático entra en un ciclo límite (G2.46: ``km`` delantero, ``v30`` trasero) 0,1–0,3
    y entonces el μ de una caja depende de la fase de la oscilación: no es repetible
    entre sesiones (el coche y el juego son los mismos)."""
    out: dict[str, dict[str, float]] = {}
    bykey: dict[tuple[str, str], list] = {}
    for x in samples:
        if x.seg_name.startswith(("lat_", "lon_")):
            bykey.setdefault((x.seg_name, x.wheel), []).append(x)
    k = OSC_WINDOW
    for (seg, wheel), xs in bykey.items():
        f = [(v.fy if seg.startswith("lat_") else v.fx) for v in xs]
        fz = [v.fz for v in xs]
        if len(f) < 4 * k or _mean(fz) <= 0:
            continue
        r = [f[i] - _mean(f[i - k : i + k + 1]) for i in range(k, len(f) - k)]
        out.setdefault(seg, {})[wheel] = round(
            math.sqrt(_mean([v * v for v in r])) / _mean(fz), 4
        )
    return out


def reduce_session(user: Path, vid: str, session: str) -> dict:
    stem = Path(user).resolve() / "beam2ac_tyre" / f"{vid}_{session}"
    meta_p, csv_p = Path(str(stem) + ".meta.json"), Path(str(stem) + ".csv")
    if not (meta_p.is_file() and csv_p.is_file()):
        raise BenchError(f"la sesión {session} no dejó {csv_p.name}/.meta.json")
    meta = json.loads(meta_p.read_text("utf-8"))
    samples = tb.reduce_rows(meta, tb.read_csv_text(csv_p.read_text("utf-8")))
    closure = tb.static_check(meta, samples, "settle")
    rfree = tb.free_radius(samples)
    pts = tb.binned_points(samples, rfree)
    vstat = tb.vertical_static(samples)
    present = {s.seg_name for s in samples}
    vdyn = {
        seg: tb.vertical_dynamic(meta, samples, seg)
        for seg in (
            "vosc_3hz",
            "vosc_12hz",
            "voscl_8hz",
            "voscl_12hz",
            "voscl_12hz_a12",
            "voscl_3hz",
        )
        if seg in present
    }
    rr = tb.rolling_resistance(samples)
    axles: dict[str, dict] = {}
    for w in meta["wheels"]:
        axles.setdefault(w["axle"], {"wheels": []})["wheels"].append(w["name"])
    for axle, a in axles.items():
        ws = a["wheels"]
        settle = [
            s for s in samples if s.seg_name == "settle" and s.axle == axle and s.t > 2
        ]
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
        a["wheel_params"] = {
            k: wm[0].get(k)
            for k in (
                "radius",
                "hubRadius",
                "tireWidth",
                "numRays",
                "pressurePSI",
                "frictionCoef",
                "slidingFrictionCoef",
                "noLoadCoef",
                "fullLoadCoef",
                "loadSensitivitySlope",
                "softnessCoef",
                "treadCoef",
                "stribeckExponent",
                "stribeckVelMult",
            )
        }
        a["samples"] = {
            "columns": COLUMNS,
            "rows": [
                [
                    p["wheel"],
                    p["kind"],
                    p["tag"],
                    p["n"],
                    round(p["fz"], 3),
                    round(p["slip_angle_deg"], 5),
                    round(p["slip_ratio"], 6),
                    round(p["camber_axis_deg"], 4),
                    round(p["speed"], 4),
                    round(p["fx"], 3),
                    round(p["fy"], 3),
                    round(p["pressure_pa"], 1),
                ]
                for p in pts
                if p["axle"] == axle
            ],
        }
    return {
        "session": session,
        "csv": _rel(csv_p),
        "csv_sha256": sha(csv_p),
        "meta_sha256": sha(meta_p),
        "closure": closure,
        "oscillation_index": oscillation_index(samples),
        "total_mass": meta["totalMass"],
        "wall_seconds": meta.get("wallSeconds"),
        "axles": axles,
    }


# Parámetros del ajuste con identificabilidad insuficiente en el banco (G2.46:
# ``brake_dx_mod`` ≈ 0,001 y diferencias entre sesiones ≤ 0,001 absolutas: la diferencia
# relativa no es un error; ``falloff_level`` está en su cota)
FIT_WEAK_KEYS = ("brake_dx_mod", "falloff_level")


def _rms(xs: list[float]) -> float | None:
    return math.sqrt(_mean([v * v for v in xs])) if xs else None


def unstable_conditions(*sessions: dict) -> dict[str, list[str]]:
    """``{eje: [condición, …]}`` con índice de oscilación > :data:`OSC_LIMIT` en ALGUNA
    sesión (p. ej. ``lat_km``): su μ por caja no es una medida repetible."""
    out: dict[str, list[str]] = {}
    for sess in sessions:
        osc = sess.get("oscillation_index", {})
        for axle, a in sess["axles"].items():
            wheels = set(a["wheels"])
            for seg, per in osc.items():
                if any(v > OSC_LIMIT for w, v in per.items() if w in wheels):
                    cond = seg
                    if cond not in out.setdefault(axle, []):
                        out[axle].append(cond)
    return {k: sorted(v) for k, v in out.items()}


def repeatability(a: dict, b: dict) -> dict:
    """Diferencias entre sesiones: escalares y μ por intervalo común.

    ``mu_rms_diff`` (todas las cajas) se conserva por compatibilidad: NO es la precisión
    del banco. Lo dominan unas pocas condiciones en las que el neumático oscila
    (:func:`unstable_conditions`; G2.46: ``lat_km`` delantero, ``lat_v30`` trasero,
    Δμ 0,01–0,15 con la misma configuración). La precisión de las condiciones
    estables es ``mu_rms_diff_stable`` (G2.46: 0,002 RMS, mediana 0,001; G2.20 midió
    0,001–0,002 solo en ellas) y por condición ``mu_rms_diff_by_condition``."""
    unstable = unstable_conditions(a, b)
    out = {}
    for axle in a["axles"]:
        x, y = a["axles"][axle], b["axles"][axle]
        d = {
            k: y[k] / x[k] - 1
            for k in (
                "reference_load",
                "free_rolling_radius",
                "vertical_stiffness_static",
            )
        }

        def key(r):
            return tuple(r[:3]) + (
                r[1] == "lat" and round(r[5] * 2) or round(r[6] * 100),
            )

        base = {key(r): r for r in x["samples"]["rows"]}
        dmu, stable, cond = [], [], {}
        for r in y["samples"]["rows"]:
            if key(r) in base:
                q = base[key(r)]
                col = 10 if r[1] == "lat" else 9
                v = r[col] / r[4] - q[col] / q[4]
                dmu.append(v)
                name = f"{r[1]}_{r[2]}"
                cond.setdefault(name, []).append(v)
                if name not in unstable.get(axle, []):
                    stable.append(v)
        d["mu_rms_diff"] = _rms(dmu)
        d["mu_max_abs_diff"] = max(abs(v) for v in dmu) if dmu else None
        d["n_common_bins"] = len(dmu)
        d["mu_rms_diff_stable"] = _rms(stable)
        d["mu_median_abs_diff_stable"] = (
            sorted(abs(v) for v in stable)[len(stable) // 2] if stable else None
        )
        d["n_common_bins_stable"] = len(stable)
        d["unstable_conditions"] = unstable.get(axle, [])
        d["mu_rms_diff_by_condition"] = {k: _rms(v) for k, v in sorted(cond.items())}
        out[axle] = d
    return out


def fit_repeatability(a: dict, b: dict, free_reference: bool = False) -> dict:
    """Repetibilidad al nivel que importa: el ajuste SCTM de cada sesión por separado.

    Devuelve, por eje, la diferencia relativa de cada parámetro entre sesiones
    (``rel``), la absoluta (``abs``) y la lista de parámetros poco identificables
    (:data:`FIT_WEAK_KEYS`, para los que la diferencia relativa carece de sentido).
    Tarda ≈ 45 s por eje y sesión (memorizado por huella de las muestras)."""
    import dataclasses

    from src.ac import tyre_fit as tf

    out = {}
    for axle in a["axles"]:
        fa = tf.fit_dataset(
            a["axles"][axle]["samples"],
            a["axles"][axle]["reference_load"],
            free_reference,
        )
        fb = tf.fit_dataset(
            b["axles"][axle]["samples"],
            b["axles"][axle]["reference_load"],
            free_reference,
        )
        pa, pb = dataclasses.asdict(fa.params), dataclasses.asdict(fb.params)
        keys = [
            k
            for k in pa
            if isinstance(pa[k], (int, float)) and k != "fz0" and abs(pa[k]) > 1e-12
        ]
        out[axle] = {
            "rel": {k: pb[k] / pa[k] - 1 for k in keys},
            "abs": {k: pb[k] - pa[k] for k in keys},
            "weak": [k for k in FIT_WEAK_KEYS if k in keys],
            "rms": {"a": fa.rms, "b": fb.rms},
        }
    return out


def collect(
    work: Path,
    sessions: list[str],
    vehicle_dir: Path,
    pc: str | None = None,
    out_dir: Path | None = None,
    dry_run: bool = False,
    label: str | None = None,
) -> dict:
    """Reduce las sesiones y escribe la medida del origen (``beamng_tyre_bench``)."""
    from src.pipeline.physics_preview import load_vehicle_dir

    if len(sessions) < 2:
        raise BenchError("hacen falta ≥ 2 sesiones (la 2.ª da la repetibilidad)")
    vd = Path(vehicle_dir)
    vid = vd.name
    users = {s: Path(work) / f"userpath_{s}" / "current" for s in sessions}
    files: list[str] = []
    for s in sessions:
        files = check_copy(vd, users[s] / "vehicles" / vid)
    src = load_vehicle_dir(vd, pc_of(vd, pc))
    fp = source_fingerprint(src)
    red = [reduce_session(users[s], vid, s) for s in sessions]
    rec = {
        "schema": SCHEMA,
        "kind": "beamng_tyre_bench",
        "source_fingerprint": fp,
        "scoped_fingerprint": scoped_fingerprint(src, "beamng_tyre_bench"),
        "label": label or vid,
        "game": {
            "name": "BeamNG.drive",
            "version": game_version(users[sessions[0]] / "beamng.log"),
        },
        "bench": {
            "script": _rel(SCRIPT),
            "script_sha256": sha(SCRIPT),
            "reduction_code": _rel(CODE),
            "reduction_code_sha256": sha(CODE),
            "report": "docs/research/g245_piloto_2026-11-14.md",
            "sessions": [
                {
                    k: r[k]
                    for k in (
                        "session",
                        "csv",
                        "csv_sha256",
                        "meta_sha256",
                        "closure",
                        "wall_seconds",
                    )
                }
                for r in red
            ],
            "vehicle_files": files,
        },
        "axles": red[0]["axles"],
        "repeatability": {r["session"]: repeatability(red[0], r) for r in red[1:]},
        # G2.46: condiciones con el neumático oscilando (μ por caja no repetible); el
        # ajuste NO las excluye (cambiaría coches ya validados): informe, no filtro
        "unstable_conditions": unstable_conditions(*red),
    }
    target = (
        Path(out_dir or MEASUREMENTS_ROOT / "beamng_bench/tyre")
        / f"{label or vid}.json"
    )
    if target.exists():
        raise BenchError(f"ya existe (no se sobrescribe): {target}")
    if not dry_run:
        if not target.resolve().is_relative_to(Path(MEASUREMENTS_ROOT).resolve()):
            target = ensure_safe_output_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    rec["_target"] = str(target)
    return rec


def measure(
    vehicle_dir: Path,
    work: Path,
    beamng_root: Path,
    pc: str | None = None,
    sessions: tuple[str, ...] = ("s1", "s2"),
) -> dict[str, Any]:
    """Todo el banco de un vehículo: prepara, lanza cada sesión en serie y recoge."""
    for s in sessions:
        prepare(work, s, vehicle_dir, pc)
    for s in sessions:
        launch(work, s, beamng_root)
    return collect(work, list(sessions), vehicle_dir, pc)
