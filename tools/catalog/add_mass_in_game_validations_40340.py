"""Masa de los cinco 40340 medida en el juego (G2.29, 2026-11-11).

    python tools/catalog/add_mass_in_game_validations_40340.py [--dry-run]

``info_<config>.json`` (Automation) declara un ``Weight`` MENOR que la masa del vehículo exportado en los
cinco 40340 (+1,7 … +26 kg): la validación ``info_reference`` de la masa falla. La masa que el conversor mide en
los nodos sí es la del juego: el banco de neumáticos de G2.29 registró Σ ``obj:getNodeMass`` en BeamNG 0.38.6
(``meta.totalMass``, idéntica en las dos sesiones) y coincide a +0,48 kg, la masa de los nodos de cámara que el
juego añade (G2.13, ``camera.lua``). Registra una validación ``in_game_value`` por vehículo (cubre
``bng.mass.total``); el motor de reglas ya no rebaja por la ficha cuando hay medida del juego superada
(``engine._vehicle_validations``). Solo añade; no sobrescribe.
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
from src.convert.service import ConversionSession  # noqa: E402

BENCH = ROOT / "output/g229_2026-11-11/banco_neumatico"
CARS = ["autist_cuanto", "hyacinth_sage", "quail_mirabilis", "sucette_poubelle", "sucette_poubelle_turbo"]
TOL = 0.5  # kg; la diferencia esperada son los nodos de cámara (0,48 kg)


def validation(car: str) -> S.Validation:
    vd = ROOT / "samples_beamng" / f"thershow_{car}" / "vehicles" / car
    s = ConversionSession.from_source(vd, None, None, 0.0)
    mass = s.facts()["ir.mass.total"].value
    metas = [BENCH / f"userpath_{x}/current/beam2ac_tyre/{car}_{x}.meta.json" for x in ("s1", "s2")]
    game = [json.loads(m.read_text("utf-8"))["totalMass"] for m in metas]
    assert abs(game[0] - game[1]) < 1e-6, game
    diff = mass - game[0]
    return S.from_dict({
        "schema": "beam2ac.validation/1",
        "id": f"{car}.{s.ir.identity.variant.value}.mass_in_game",
        "vehicle": s.ir.identity.vehicle_id.value,
        "variant": s.ir.identity.variant.value,
        "method": "in_game_value",
        "covers": ["bng.mass.total"],
        "result": "pass" if abs(diff) <= TOL else "fail",
        "expected": round(game[0], 4),
        "observed": round(mass, 4),
        "tolerance": TOL,
        "evidence": [{"type": "file", "path": m.relative_to(ROOT).as_posix(), "locator": "totalMass",
                      "sha256": hashlib.sha256(m.read_bytes()).hexdigest(), "note": ""} for m in metas]
        + [{"type": "report", "path": "docs/research/g229_consolidacion_2026-11-11.md", "locator": "§2.5",
            "sha256": "", "note": ""}],
        "date": "2026-11-11",
        "note": f"esperado: Σ obj:getNodeMass en BeamNG 0.38.6 (dos sesiones idénticas); observado: masa del "
                f"conversor; Δ = {diff:+.2f} kg (nodos de cámara que añade el juego, G2.13)",
    }, "validation")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    for car in CARS:
        v = validation(car)
        target = REPO_CATALOG / "validations" / f"{v.vehicle}.json"
        print(car, v.result, v.expected, v.observed, target.name)
        if target.exists():
            raise SystemExit(f"{target} ya existe: las validaciones no se sobrescriben")
        if not a.dry_run:
            target.write_text(json.dumps([S.to_dict(v)], indent=1, ensure_ascii=False) + "\n", "utf-8")
    if not a.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
