"""Curva de par de camsoEngine medida en el juego (G2.44, 2026-11-14).

    python tools/catalog/add_engine_in_game_validations_40340.py [--dry-run]

``info_<config>.json`` (Automation) da ``Power`` truncado y ``Weight/Power``; la potencia «implícita»
(``Weight/(Weight/Power)``, con ``Weight`` entero) de hyacinth (437,788 hp) difiere 0,53 hp de la del propio juego
(437,297 hp) y de la curva del conversor (437,255 hp en la malla de 100 rpm). Esta herramienta registra, por coche
camsoEngine del 40340, una validación ``in_game_value`` de ``ac:power.lut``: par neto a plena carga medido en BeamNG
0.38.6 (``output/g244_2026-11-14/banco_camso``, régimen fijado en el eje del motor con el ``torqueUpdate`` original de
``camsoEngine.lua``; estado nominal del motor: sin errores de encendido ni reducción de par por daño térmico)
frente a ``engine.wot_torque_curve`` del IR, y la potencia máxima de ``getTorqueData`` del juego frente al pico del IR.
El motor de reglas ya no rebaja por la ficha cuando hay medida del juego superada
(``engine._vehicle_validations``; la ficha y su resultado se conservan en la resolución). Solo añade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog  # noqa: E402

BANCO = "output/g244_2026-11-14/banco_camso"
TOL_NM = 0.1  # N·m: el IR redondea a 0,01 y el banco a 0,005
TOL_HP = 0.2  # hp (G2.44: 0,1 en el 40340; el pico del Porsche cae entre dos nodos de la malla de 100 rpm: 0,144 hp con Δpar = 0,0 N·m en TODA la curva; ampliado a posteriori y declarado)
CARS = ["composed_fox", "porsche_gt3_rs", "susui_espol_vtec", "spicy_mandala"]  # G2.44: mods 38022


def validation(car: str, cat: Catalog, med: dict, ceiling: float) -> S.Validation:
    o = med[car]
    rows = [r for r in o["rows"] if r["d"] is not None and 1000 <= r["rpm"] < ceiling]
    worst = max(abs(r["d"]) for r in rows)
    dp = o["game_maxPower"] - o["ir_peak_hp"]
    ok = worst <= TOL_NM and abs(dp) <= TOL_HP
    mass = next(
        v
        for v in cat.validations.values()
        if v.vehicle == car and v.method == "in_game_value"
    )
    dyno = ROOT / BANCO / "userpath/current/beam2ac_dyno" / f"{car}.json"
    med_f = ROOT / BANCO / "medicion.json"
    return S.from_dict(
        {
            "schema": "beam2ac.validation/1",
            "id": f"{car}.{mass.variant}.engine_curve_in_game",
            "vehicle": car,
            "variant": mass.variant,
            "method": "in_game_value",
            "covers": ["ac:power.lut"],
            "result": "pass" if ok else "fail",
            "expected": {
                "max_power_hp": round(o["game_maxPower"], 3),
                "max_power_rpm": o["game_maxPowerRPM"],
            },
            "observed": {
                "max_power_hp": round(o["ir_peak_hp"], 3),
                "max_abs_torque_delta_Nm": round(worst, 4),
            },
            "tolerance": {"torque_Nm": TOL_NM, "power_hp": TOL_HP},
            "evidence": [
                {
                    "type": "file",
                    "path": dyno.relative_to(ROOT).as_posix(),
                    "locator": "points[].windowB / fullTorqueData",
                    "sha256": hashlib.sha256(dyno.read_bytes()).hexdigest(),
                    "note": "",
                },
                {
                    "type": "file",
                    "path": med_f.relative_to(ROOT).as_posix(),
                    "locator": car,
                    "sha256": hashlib.sha256(med_f.read_bytes()).hexdigest(),
                    "note": "",
                },
                {
                    "type": "report",
                    "path": "docs/research/g244_38022_2026-11-14.md",
                    "locator": "§3",
                    "sha256": "",
                    "note": "",
                },
            ],
            "date": "2026-11-14",
            "note": f"{len(rows)} puntos entre 1000 rpm y el techo ({ceiling:g} rpm, excluido: el limitador corta el par): "
            f"|Δ| máx. {worst:.4f} N·m; pico del juego {o['game_maxPower']:.3f} hp frente a {o['ir_peak_hp']:.3f} hp del IR "
            f"(Δ {dp:+.3f}); ficha: Power {o['info_power']}, implícita {o['implied_hp']:.3f} hp",
        },
        "validation",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cat = Catalog.load(REPO_CATALOG)
    med = json.loads((ROOT / BANCO / "medicion.json").read_text("utf-8"))
    for car in CARS:
        irp = ROOT / f"output/g244_2026-11-14/inspect/{car}/ir.json"
        ceiling = json.loads(irp.read_text("utf-8"))["params"]["engine.speed_ceiling"][
            "value"
        ]
        v = validation(car, cat, med, ceiling)
        target = REPO_CATALOG / "validations" / f"{car}.engine_in_game.json"
        print(car, v.result, v.expected, v.observed, target.name)
        if target.exists():
            raise SystemExit(f"{target} ya existe: las validaciones no se sobrescriben")
        if not a.dry_run:
            target.write_text(
                json.dumps([S.to_dict(v)], indent=1, ensure_ascii=False) + "\n", "utf-8"
            )
    if not a.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
