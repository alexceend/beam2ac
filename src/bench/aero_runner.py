"""Banco aerodinámico de BeamNG lanzado desde el conversor (G2.46).

La aerodinámica por triángulos (``aero.model = triangles``, exportaciones 200409) la
calcula el C++ del juego (G2.15 §5.3): sin una medida del propio coche el IR no tiene
``aero.cda_vs_aoa`` y las 34 reglas ``aero.*_200409@1`` no se aplican (``aero.ini
[WING_0] NAME`` sin resolver). Este módulo repite, para cualquier vehículo en carpeta, la
medida de G2.15 (sesión 4) con las mismas funciones del juego:

1. :func:`prepare`: carpeta de usuario AISLADA (``<work>/userpath_<s>/current``) con las
   extensiones ``src/bench/lua_aero`` y una copia SIN CAMBIOS del vehículo;
2. :func:`launch`: ``-onLevelLoad_ext beam2acAeroGE`` (una sesión ≈ 2 min; nunca dos a la
   vez; la instalación del juego no puede cambiar);
3. :func:`collect`: exige copia idéntica, reduce los casos (v > 0), calcula la
   repetibilidad entre sesiones y escribe ``measurements/beamng_bench/aero/<id>.json``
   (``beamng_aero_bench``) con la huella del origen. Nunca sobrescribe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.bench import tyre_runner as tr
from src.bench.tyre_runner import BenchError
from src.importers.measurements import (
    MEASUREMENTS_ROOT,
    SCHEMA,
    scoped_fingerprint,
    source_fingerprint,
)
from src.safe_paths import ensure_safe_output_path

LUA_SRC = Path(__file__).resolve().parent / "lua_aero"
SCRIPT = LUA_SRC / "vehicle/extensions/beam2acAero.lua"
GE = "beam2acAeroGE"
AERO_DIR = "beam2ac_aero"
# casos con v > 0 de ``beam2acAero.lua`` (14 de 16)
MIN_CASES = 10


def prepare(work: Path, session: str, vehicle_dir: Path, pc: str | None = None) -> Path:
    vd = Path(vehicle_dir)
    pc = tr.pc_of(vd, pc)
    user = Path(work) / f"userpath_{session}" / "current"
    tr._copy_tree(LUA_SRC, user / "lua")
    tr._copy_tree(vd, user / "vehicles" / vd.name)
    cfg = ensure_safe_output_path(user / AERO_DIR / "config.json")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists():
        raise BenchError(f"la sesión ya está preparada: {cfg}")
    cfg.write_text(
        json.dumps(
            {
                "vehicles": [
                    {
                        "name": vd.name,
                        "model": vd.name,
                        "config": f"vehicles/{vd.name}/{pc}",
                        "timeout": 600,
                    }
                ]
            },
            indent=1,
        ),
        "utf-8",
    )
    return user


def launch(
    work: Path,
    session: str,
    beamng_root: Path,
    timeout: float = 900,
    cancel=None,
    tick=None,
) -> dict:
    return tr.launch(
        work, session, beamng_root, timeout=timeout, ext=GE, cancel=cancel, tick=tick
    )


def reduce_session(user: Path, vid: str) -> list[dict]:
    p = Path(user) / AERO_DIR / f"{vid}.json"
    if not p.is_file():
        raise BenchError(f"la sesión no dejó {p.name}")
    data = json.loads(p.read_text("utf-8"))
    cases = [c for c in data.get("aero", []) if c.get("v", 0) > 0]
    if len(cases) < MIN_CASES:
        raise BenchError(f"{p.name}: solo {len(cases)} casos aerodinámicos")
    return cases


def repeatability(a: list[dict], b: list[dict]) -> dict:
    """Diferencia entre dos sesiones de la fuerza aerodinámica total por caso.

    ``max_rel`` = máx |F_b − F_a| / |F_a| (norma de la fuerza); un caso sin fuerza
    (|F| < 1 N) no entra. No se afirma una precisión: se informa lo medido."""
    rel = []
    for x, y in zip(a, b):
        if (x["v"], x["pitch"], x["yaw"]) != (y["v"], y["pitch"], y["yaw"]):
            raise BenchError("los casos de las dos sesiones no coinciden")
        fa = sum(c * c for c in x["F"]) ** 0.5
        if fa < 1.0:
            continue
        d = sum((p - q) ** 2 for p, q in zip(x["F"], y["F"])) ** 0.5
        rel.append(d / fa)
    return {"n_cases": len(rel), "max_rel": max(rel, default=None)}


def collect(
    work: Path,
    sessions: list[str],
    vehicle_dir: Path,
    pc: str | None = None,
    out_dir: Path | None = None,
    dry_run: bool = False,
    label: str | None = None,
) -> dict:
    from src.pipeline.physics_preview import load_vehicle_dir

    vd = Path(vehicle_dir)
    vid = vd.name
    users = {s: Path(work) / f"userpath_{s}" / "current" for s in sessions}
    for s in sessions:
        tr.check_copy(vd, users[s] / "vehicles" / vid)
    pcn = tr.pc_of(vd, pc)
    src = load_vehicle_dir(vd, pcn)
    fp = source_fingerprint(src)
    cases = [reduce_session(users[s], vid) for s in sessions]
    rec = {
        "schema": SCHEMA,
        "kind": "beamng_aero_bench",
        "source_fingerprint": fp,
        "scoped_fingerprint": scoped_fingerprint(src, "beamng_aero_bench"),
        "label": {"vehicle_id": label or vid, "pc": pcn},
        "game": {
            "name": "BeamNG.drive",
            "version": tr.game_version(users[sessions[0]] / "beamng.log"),
        },
        "bench": {
            "report": "docs/research/g246_aero_repetibilidad_2026-11-14.md",
            "session": sessions[0],
            "method": "coche parado y apoyado, freno de mano; obj:setWind(-v·dir); "
            "3 s de reposo y 1 s de muestras por caso (media); "
            "obj:calcTotalAeroForces(), obj:calcTotalAeroTorque(centro de cada "
            "rueda), obj:getAirDensity()",
            "script": tr._rel(SCRIPT),
            "script_sha256": tr.sha(SCRIPT),
            "sessions": list(sessions),
            "loaded_copy_identical_to_source": True,
        },
        "source_files_sha256": {
            p.relative_to(vd).as_posix(): tr.sha(p)
            for p in sorted(vd.rglob("*"))
            if p.is_file() and (p.suffix in tr.CHECKED or p.name in tr.CHECKED_NAMES)
        },
        "frame": "carrocería (adelante, izquierda, arriba) de refNodes; posiciones en m "
        "respecto a refNodes.ref; F en N; torque[w] = calcTotalAeroTorque(centro de "
        "la rueda w) = F × (r − P) (signo opuesto al momento usual); pitch p ⇒ "
        "avance en adelante·cos p + arriba·sin p; yaw y ⇒ avance hacia la izquierda",
        "limits": [
            "ruedas sin girar (viento sobre el coche parado)",
            "actitud de reposo; sin medir otras alturas",
            "ángulos de ataque −4…+4° a 50 m/s; velocidades 20–70 m/s a 0°",
        ],
        "repeatability": {
            s: repeatability(cases[0], c) for s, c in zip(sessions[1:], cases[1:])
        },
        "cases": cases[0],
    }
    target = (
        Path(out_dir or MEASUREMENTS_ROOT / "beamng_bench/aero")
        / f"{label or vid}.json"
    )
    if target.exists():
        raise BenchError(f"ya existe (no se sobrescribe): {target}")
    if not dry_run:
        if not target.resolve().is_relative_to(Path(MEASUREMENTS_ROOT).resolve()):
            target = ensure_safe_output_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", "utf-8")
    rec["_target"] = str(target)
    return rec


def measure(
    vehicle_dir: Path,
    work: Path,
    beamng_root: Path,
    pc: str | None = None,
    sessions: tuple[str, ...] = ("s1", "s2"),
) -> dict[str, Any]:
    for s in sessions:
        prepare(work, s, vehicle_dir, pc)
    for s in sessions:
        launch(work, s, beamng_root)
    return collect(work, list(sessions), vehicle_dir, pc)
