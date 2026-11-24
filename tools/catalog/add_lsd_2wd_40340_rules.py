"""Diferencial autoblocante 2WD (``[DIFFERENTIAL] PRELOAD/POWER/COAST``) para el 40340 (G2.27, 2026-11-11).

    python tools/catalog/add_lsd_2wd_40340_rules.py [--dry-run]

* BeamNG [M] (G2.12.1, ``differential.lua``): al saturar, bloqueo = ``lsdPreload + coef·|T|`` a cada
  salida con signo opuesto ⇒ diferencia entre salidas = 2·lsdTorque.
* AC 2WD [C] (``transmisión 2WD de AC``): ``lim = PRELOAD + |ratio|·RAMP·|T|``; bloqueo estático mientras
  ``|fbt_D − fbt_I| ≤ lim``; al deslizar, diferencia entre ruedas = ``lim``.
* AC 2WD [M] (G2.27, B0 en agarre partido, ``output/g227_2026-11-10/b0/``): cociente medido/previsto
  POWER 1,002 (9 bloques), COAST 0,98–1,00 (51 bloques), PRELOAD 1,00–1,01 (45 bloques; 0,91 en la
  variante de inercia de caja 0,01). Antes, G2.22: PRELOAD 0,9985.

⇒ misma conversión que en los ejes del AWD (G2.23): ×2 en los tres parámetros.

Añade los atributos y objetivos ``[DIFFERENTIAL] POWER`` y ``COAST``, su aplicabilidad (exportador 40340
con diferencial ``lsd`` en el eje motriz) y tres reglas de valor ``provisional`` (40340, FWD/RWD, ``lsd``,
coef ≤ 1). El 38022 queda fuera A PROPÓSITO: el Ibiza (lsd 100/0,25/0,025 en el origen, exportado con
PRELOAD 100 y sin POWER/COAST) es un coche aprobado por el usuario; cambiarlo es la decisión D-G227-IBIZA.
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

ATTR = ROOT / "catalog/attributes/assettocorsa.json"
TARGETS = ROOT / "catalog/targets/ac_physics.json"
DATE = "2026-11-11"
AUTHOR = "claude (G2.27: B0 en agarre partido, encargada por el usuario)"
DOC = "docs/research/g227_consolidacion_b0_2026-11-11.md"
CODE = "output/g211_transmissions_2026-11-02/acs_drivetrain/c"
SEC = "ac:drivetrain.ini/DIFFERENTIAL/"
DIFF = "ir.drivetrain.driven_axle_differential"
VERSION = S.Condition(fact="ir.formats.exporter_version", op="in", value=["40340"])
IS_LSD = S.Condition(fact=f"{DIFF}.kind", op="eq", value="lsd")
PARAMS = {  # clave de AC -> (campo del IR, unidad)
    "PRELOAD": ("preload", "N·m"), "POWER": ("lock_coef", "1"), "COAST": ("rev_lock_coef", "1")}


def ev(type_: str, path: str, locator: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note="")


def hist(status: str, reason: str) -> list[S.HistoryEntry]:
    return [S.HistoryEntry(date=DATE, author=AUTHOR, action="create", reason=reason,
                           status_from="", status_to=status)]


def attribute(key: str) -> dict:
    return {
        "schema": "beam2ac.attribute/1", "id": SEC + key, "game": "assettocorsa", "unit": "1",
        "dtype": "number",
        "description": f"rampa de bloqueo del diferencial 2WD con par {'motor' if key == 'POWER' else 'de retención'}: "
        "lim = PRELOAD + |relación total|·RAMPA·|par del motor| (transmisión 2WD de AC; ausente = 0)",
        "doc": [{"type": "code", "path": CODE, "locator": "transmisión 2WD de AC", "sha256": "",
                 "note": ""},
                {"type": "doc", "path": DOC, "locator": "§B0", "sha256": "", "note": ""}],
        "required_for_load": False,
    }


def rules() -> list[S.Rule]:
    code = [ev("code", CODE, "transmisión 2WD de AC")]
    report = [ev("report", DOC, "§B0"), ev("file", "output/g227_2026-11-10/b0/b0_resumen.md"),
              ev("report", "docs/research/g2121_lsd_validation_2026-11-02.md", "mitad BeamNG")]
    out = []
    for key in ("POWER", "COAST"):
        out.append(S.Rule(
            id=f"app.diff2wd.{key.lower()}", version=1, target=SEC + key, kind="applicability",
            transform=S.Transform(id="constant", params={"value": None}, output_unit="1"),
            scope=S.Scope(level="generic"), status="validated", applies_when=[VERSION, IS_LSD],
            source_doc=code, target_doc=code, evidence=report,
            notes="límite deliberado al 40340: en el 38022 (Ibiza) el origen también tiene rampas, pero "
            "escribirlas cambia un coche aprobado (D-G227-IBIZA); sin diferencial lsd AC lee 0 = abierto",
            history=hist("validated", "objetivo nuevo sin cambiar los proyectos 38022"),
        ))
    for key, (field, unit) in PARAMS.items():
        conds = [VERSION, S.Condition(fact="ir.drivetrain.layout", op="in", value=["FWD", "RWD"]), IS_LSD]
        if key != "PRELOAD":  # BeamNG usa min(coef, 1)
            conds.append(S.Condition(fact=f"{DIFF}.{field}", op="le", value=1.0))
        out.append(S.Rule(
            id=f"dt.diff_{key.lower()}_2wd_lsd_40340", version=1, target=SEC + key, kind="computed",
            transform=S.Transform(id="scale", params={"factor": 2.0}, input_units={"x": unit}, output_unit=unit,
                                  formula=f"{key} = 2 · {field}"),
            inputs={"x": f"{DIFF}.{field}"},
            scope=S.Scope(level="family", family="automation_export"), status="provisional", conditions=conds,
            assumptions=[
                "BeamNG (medido, G2.12.1): diferencia entre salidas al saturar = 2·(lsdPreload + coef·|T|)",
                "AC 2WD (el cálculo 2WD de AC [C]; medido en agarre partido, G2.27): diferencia entre ruedas = lim",
                "solo iguala el par SATURADO; por debajo AC bloquea estáticamente y BeamNG con un muelle angular",
                "medido en AC con un coche de diagnóstico 38022 (FWD); ningún 40340 probado en AC",
            ],
            source_doc=[ev("report", "docs/research/g2121_lsd_validation_2026-11-02.md")],
            target_doc=code, evidence=report,
            history=hist("provisional", "B0: semántica 2WD de AC medida; factor 2 como en el AWD (G2.23)"),
            notes="G2.27",
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    raw = ATTR.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=1, ensure_ascii=False) + "\n" == raw
    traw = TARGETS.read_text(encoding="utf-8")
    tg = json.loads(traw)
    assert json.dumps(tg, indent=1, ensure_ascii=False) + "\n" == traw
    new = [SEC + "POWER", SEC + "COAST"]
    if any(x["id"] in new for x in data) or any(t in tg["targets"] for t in new):
        raise SystemExit("ya existen los objetivos POWER/COAST")
    rs = rules()
    have = {p.stem for p in (ROOT / "catalog/rules").glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): 2 atributos, 2 objetivos, {len(rs)} reglas")
        return 0
    i = next(k for k, x in enumerate(data) if x["id"] == SEC + "PRELOAD") + 1
    data[i:i] = [attribute("POWER"), attribute("COAST")]
    ATTR.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    j = tg["targets"].index(SEC + "PRELOAD") + 1
    tg["targets"][j:j] = new
    TARGETS.write_text(json.dumps(tg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritos 2 atributos, 2 objetivos y {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
