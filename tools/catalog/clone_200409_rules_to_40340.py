"""Clona al exportador 40340 las reglas del 200409 que ya resuelven con sus datos (G2.27, 2026-11-11).

    python tools/catalog/clone_200409_rules_to_40340.py [--dry-run]

Lista CERRADA (``RULES``): las reglas del 200409 que, con la condición de versión ampliada en una COPIA del
catálogo, dan valor en los cinco 40340 del corpus (``output/g227_paralelo_2026-11-11/hipotesis_40340.py``):
suspensión delantera por estructura (DWB de 5 barras y puntal con raíl), masas no suspendidas, ``TOTALMASS``,
CdG e inercia del cuerpo (piloto 0), dirección, freno de mano, presión del neumático, ``FFMULT``, combustible y
nombre. Cada clon ``<id>_40340@1`` es idéntico salvo la versión del exportador (``["40340"]``), el estado
(``provisional``) y un supuesto que dice qué evidencia tiene:

* la extracción es la misma (mismas condiciones estructurales, que el 40340 cumple);
* la mecánica es la del juego (BeamNG), no la del exportador; las medidas de banco se hicieron con 200409;
* en 38022/40340 solo hay contraste estático: masa del conversor frente a ``Weight`` de ``info_<config>.json``
  y ``HUB_MASS`` del Ibiza/Porsche reproducidos (G2.14).

Además ``[GEARBOX] INERTIA``: ``dt.gearbox_inertia_40340@1`` (cajas ``manualGearbox``/``sequentialGearbox``, que
tampoco tienen inercia propia en BeamNG: ``calculateInertia`` [C]) y ``app.gearbox.inertia@2`` (aplica a 200409 y
40340; el 38022 sigue «no aplica»: D-INV-GBI).

No toca las reglas del 200409 ni las del Ibiza. Los 40340 siguen sin ser exportables (neumático y aerodinámica
sin medir, suspensión trasera sobredeterminada). Solo añade; se niega a sobrescribir.
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
AUTHOR = "claude (G2.27, trabajo paralelo: cobertura del 40340, encargada por el usuario)"
DOC = "docs/research/g227_consolidacion_b0_2026-11-11.md"
RULES_DIR = ROOT / "catalog" / "rules"
LIST = ROOT / "output" / "g227_paralelo_2026-11-11" / "reglas_a_clonar.json"
EXTRA = ["car.fuel_200409@1", "car.max_fuel_200409@1", "car.screen_name_200409@1"]
# Segunda tanda (``--batch 2``): claves que en el 40340 solo tenían la PROPUESTA de la regla del Ibiza
# (de ámbito de vehículo, nunca aplicada): batalla, vías, dimensiones y presión del neumático, reparto
# y par de freno y TYPE del puntal. ``dt.clutch_max_torque_40340`` se clona de la regla del Ibiza
# (``frictionClutch``; el 200409 usa DCT) limitada al 40340.
BATCH2 = [
    "susp.wheelbase_200409@1", "susp.track_front_200409@1", "susp.track_rear_200409@1",
    "susp.type_strut_front_200409@1", "brk.front_share_200409@1", "brk.max_torque_200409@1",
] + [f"tyre.{a}.{k}_200409@1" for a in ("front", "rear")
     for k in ("radius", "rim_radius", "width", "pressure_static")]
ASSUMPTION = ("clon de la regla del 200409: en el 40340 solo hay contraste estático (estructura y masa frente "
              "a la ficha); las medidas en el juego se hicieron con vehículos 200409")
VERSION = "ir.formats.exporter_version"


def history(reason: str, status: str = "provisional") -> list[dict]:
    return [{"date": DATE, "author": AUTHOR, "action": "create", "reason": reason,
             "status_from": "", "status_to": status}]


def clone(key: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))
    assert "_200409" in d["id"], key
    seen = 0
    for c in d.get("conditions", []) + d.get("scope", {}).get("conditions", []):
        if c["fact"] == VERSION:
            assert c["value"] == ["200409"], (key, c)
            c["value"] = ["40340"]
            seen += 1
    assert seen == 1, (key, seen)
    d["supersedes"] = ""  # un clon no sustituye a ninguna regla
    d.update(id=d["id"].replace("_200409", "_40340"), version=1, status="provisional",
             assumptions=list(d.get("assumptions", [])) + [ASSUMPTION],
             history=history(f"clon de {key} para el exportador 40340"), notes="G2.27 (paralelo)")
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "report", "path": DOC, "locator": "§P", "sha256": "", "note": ""}]
    return S.from_dict(d)


def gearbox() -> list[S.Rule]:
    val = json.loads((RULES_DIR / "dt.gearbox_inertia_200409@1.json").read_text("utf-8"))
    for c in val["conditions"]:
        if c["fact"] == VERSION:
            c["value"] = ["40340"]
        if c["fact"] == "ir.drivetrain.gearbox.kind":
            c["value"] = ["manualGearbox", "sequentialGearbox"]
    val.update(id="dt.gearbox_inertia_40340", version=1,
               history=history("la caja manual/secuencial de BeamNG tampoco tiene inercia propia"),
               notes="G2.27 (paralelo)")
    val["source_doc"] = [{"type": "code", "path": "BeamNG.drive 0.38.6 lua/vehicle/powertrain", "sha256": "",
                          "locator": "manualGearbox.lua l. 372-384 (calculateInertia)", "note": ""}]
    app = json.loads((RULES_DIR / "app.gearbox.inertia@1.json").read_text("utf-8"))
    assert [c["value"] for c in app["applies_when"]] == [["200409"]]
    app["applies_when"][0]["value"] = ["200409", "40340"]
    app.update(version=2, history=history("aplica también al 40340 (regla de valor nueva)", "validated"))
    return [S.from_dict(val), S.from_dict(app)]


def clutch() -> S.Rule:
    d = json.loads((RULES_DIR / "dt.clutch_max_torque@2.json").read_text("utf-8"))
    d["supersedes"] = ""
    d.update(id="dt.clutch_max_torque_40340", version=1, status="provisional",
             scope={"level": "family", "family": "automation_export", "vehicle": "", "variant": "",
                    "conditions": []},
             conditions=list(d.get("conditions", [])) + [{"fact": VERSION, "op": "in", "value": ["40340"]}],
             assumptions=list(d.get("assumptions", [])) + [ASSUMPTION.replace("del 200409", "del Ibiza (38022)")],
             history=history("clon de dt.clutch_max_torque@2 (Ibiza) para el exportador 40340"),
             notes="G2.27 (paralelo)")
    return S.from_dict(d)


def rules(batch: int = 1) -> list[S.Rule]:
    if batch == 2:
        return [clone(k) for k in BATCH2] + [clutch()]
    keys = json.loads(LIST.read_text("utf-8")) + EXTRA
    return [clone(k) for k in sorted(set(keys))] + gearbox()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, choices=(1, 2), default=1)
    a = ap.parse_args(argv)
    rs = rules(a.batch)
    have = {p.stem for p in RULES_DIR.glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
