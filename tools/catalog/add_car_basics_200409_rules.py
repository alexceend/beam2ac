"""``car.ini`` del 200409: combustible, nombre y ``FFMULT`` (G2.27, 2026-11-11).

    python tools/catalog/add_car_basics_200409_rules.py [--dry-run]

* ``[FUEL] FUEL``/``MAX_FUEL`` y ``[INFO] SCREEN_NAME``: las reglas del Ibiza (``car.fuel@2``,
  ``car.max_fuel@2``, ``car.screen_name@2``, identidad sobre el IR) quedaban como propuesta en el
  200409 por ser de ámbito de vehículo. Se clonan como reglas de familia limitadas al 200409.
  - BeamNG [C] (``lua/vehicle/energyStorage/fuelTank.lua`` l. 123-148): ``fuelCapacity`` y
    ``startingFuelCapacity`` en litros; la masa del combustible se SUMA a los nodos en marcha
    (``obj:setNodeMass(k, v + storedEnergy·coef)``): la masa medida en el JBeam no lo incluye.
  - AC [M] (G2.26, cargas en reposo): sin ``[FUEL]`` AC carga ~22 kg de combustible por defecto
    (Σ cargas/g − TOTALMASS − bujes en los 4 coches); la masa de AC = cuerpo + bujes + combustible.
* ``[CONTROLS] FFMULT`` = 1,95: valor de REFERENCIA (mediana de 113 coches de AC,
  ``output/g227_2026-11-10/analisis``; es además el valor que el usuario aprobó con el G29 en el
  Ibiza). Sin la clave no hay fuerza en el volante (observado en el Ibiza v7). No se mide en el origen.

Solo añade; se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-11"
AUTHOR = "claude (G2.27: consolidación y carencias del 200409, encargada por el usuario)"
DOC = "docs/research/g227_consolidacion_b0_2026-11-11.md"
VERSION_COND = {"fact": "ir.formats.exporter_version", "op": "in", "value": ["200409"]}
CLONES = {"car.fuel@2": "car.fuel_200409", "car.max_fuel@2": "car.max_fuel_200409",
          "car.screen_name@2": "car.screen_name_200409"}


def history(reason: str) -> list[dict]:
    return [{"date": DATE, "author": AUTHOR, "action": "create", "reason": reason,
             "status_from": "", "status_to": "provisional"}]


def clone(src: str, new_id: str) -> S.Rule:
    d = json.loads((ROOT / "catalog/rules" / f"{src}.json").read_text("utf-8"))
    d["supersedes"] = ""  # un clon no sustituye a ninguna regla
    d.update(id=new_id, version=1, status="provisional", conditions=[VERSION_COND],
             scope={"level": "family", "family": "automation_export", "vehicle": "", "variant": "",
                    "conditions": []},
             history=history(f"clon de {src} para el exportador 200409 (misma lectura del origen)"),
             notes="G2.27")
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "report", "path": DOC, "locator": "§E", "sha256": "", "note": ""}]
    return S.from_dict(d)


def ffmult() -> S.Rule:
    ev = [S.EvidenceRef(type="report", path=DOC, locator="§E", sha256="", note="")]
    return S.Rule(
        id="ctrl.ffmult_200409", version=1, target="ac:car.ini/CONTROLS/FFMULT", kind="reference",
        transform=S.Transform(id="constant", params={"value": 1.95}, output_unit="1",
                              formula="FFMULT = mediana del corpus de AC"),
        scope=S.Scope(level="family", family="automation_export"), status="provisional",
        conditions=[S.Condition(**VERSION_COND)],
        assumptions=["valor de referencia (mediana de 113 coches de AC; 1,3–4,5), no medido en el origen",
                     "sin la clave AC no da fuerza en el volante (Ibiza v7, prueba del usuario)"],
        source_doc=ev, target_doc=ev, evidence=ev,
        history=[S.HistoryEntry(**history("sin FFMULT el volante no tiene fuerza")[0])], notes="G2.27",
    )


def rules() -> list[S.Rule]:
    return [clone(s, n) for s, n in CLONES.items()] + [ffmult()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = rules()
    have = {p.stem for p in (ROOT / "catalog/rules").glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
