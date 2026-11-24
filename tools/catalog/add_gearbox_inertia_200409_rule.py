"""``[GEARBOX] INERTIA`` de ``drivetrain.ini`` para el 200409 (investigación 2026-11-10).

    python tools/catalog/add_gearbox_inertia_200409_rule.py [--dry-run]

* AC [C] (``output/g211_transmissions_2026-11-02/acs_drivetrain/c``): el constructor de
  ``Drivetrain`` pone ``clutchInertia = 1.0``; ``loadINI`` solo la cambia (junto con
  ``drive.inertia``) si ``[GEARBOX] INERTIA`` ≠ 0. la inercia desde las ruedas la refleja por la
  marcha: ``ratio²·(clutchInertia + engine.inertia)`` con el embrague cerrado y
  ``ratio²·clutchInertia`` con el embrague abierto y una marcha metida.
* AC [M] (telemetría de las 8 sesiones de G2.26, ``output/investigacion_2026-11-10/
  freno_motor/``): sin la clave, la aceleración a gas a fondo de los 4 coches en cada marcha
  coincide a 1–3 % con el modelo de 1,0 kg·m² (en 1.ª equivale a 1 850–5 700 kg añadidos) y
  el balance de pares por rueda cierra a 0,6 %.
* Corpus de AC (``output/investigacion_2026-11-10/corpus_ac.json``): los 113 coches de
  referencia con ``drivetrain.ini`` legible tienen la clave, entre 0,01 y 0,045 (0,01 es el
  valor más frecuente y el mínimo).
* BeamNG [C] (0.38.6, ``lua/vehicle/powertrain``): ``dctGearbox``/``automaticGearbox`` no
  tienen inercia propia (``calculateInertia`` solo refleja la de los hijos): origen = 0.
  AC no puede representar 0 (0 ⇒ 1,0): se escribe el menor valor del corpus, 0,01.

Es un valor de referencia, no una medida. Añade el atributo, el objetivo, una regla de
aplicabilidad limitada al 200409 y la regla de valor. La aplicabilidad está limitada a
propósito: en 38022/40340 la clave también falta y AC usa 1,0 (Ibiza, Porsche), pero darles
un valor cambia coches aprobados por el usuario y queda como decisión (D-INV-GBI).
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
DATE = "2026-11-10"
AUTHOR = (
    "claude (investigación 2026-11-10: sesgo del freno motor, encargada por el usuario)"
)
CODE = "output/g211_transmissions_2026-11-02/acs_drivetrain/c"
EVID = "output/investigacion_2026-11-10"
DOC = "docs/research/investigacion_capacidades_2026-11-10.md"
TARGET = "ac:drivetrain.ini/GEARBOX/INERTIA"
AFTER = "ac:drivetrain.ini/GEARBOX/VALID_SHIFT_RPM_WINDOW"
VALUE = 0.01
VERSION_COND = S.Condition(
    fact="ir.formats.exporter_version", op="in", value=["200409"]
)


def ev(type_: str, path: str, locator: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note="")


def attribute() -> dict:
    return {
        "schema": "beam2ac.attribute/1",
        "id": TARGET,
        "game": "assettocorsa",
        "unit": "kg·m²",
        "dtype": "number",
        "description": "inercia de embrague y caja en el eje del motor (transmisión de AC: "
        "clutchInertia = drive.inertia = valor; ausente o 0 = 1,0 kg·m²)",
        "doc": [
            {
                "type": "code",
                "path": CODE,
                "sha256": "",
                "note": "",
                "locator": "transmisión de AC (clutchInertia = 1.0) "
                "loadINI ([GEARBOX] INERTIA) la inercia desde las ruedas",
            },
            {"type": "doc", "path": DOC, "locator": "§A", "sha256": "", "note": ""},
        ],
        "required_for_load": False,
    }


def rules() -> list[S.Rule]:
    code = [ev("code", CODE, "transmisión de AC, loadINI, la inercia desde las ruedas")]
    report = [ev("report", DOC, "§A")]
    hist = lambda status, reason: [  # noqa: E731
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="create",
            reason=reason,
            status_from="",
            status_to=status,
        )
    ]
    app = S.Rule(
        id="app.gearbox.inertia",
        version=1,
        target=TARGET,
        kind="applicability",
        transform=S.Transform(
            id="constant", params={"value": None}, output_unit="kg·m²"
        ),
        scope=S.Scope(level="generic"),
        status="validated",
        applies_when=[VERSION_COND],
        source_doc=code,
        target_doc=code,
        evidence=report,
        notes="límite deliberado: AC usa 1,0 kg·m² sin la clave en cualquier coche; fuera del "
        "200409 no se exige para no cambiar proyectos aprobados sin decisión (D-INV-GBI)",
        history=hist(
            "validated", "objetivo nuevo sin cambiar los proyectos 38022/40340"
        ),
    )
    value = S.Rule(
        id="dt.gearbox_inertia_200409",
        version=1,
        target=TARGET,
        kind="reference",
        transform=S.Transform(
            id="constant",
            params={"value": VALUE},
            output_unit="kg·m²",
            formula="INERTIA = mínimo del corpus de AC (origen: 0)",
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        conditions=[
            VERSION_COND,
            S.Condition(
                fact="ir.drivetrain.gearbox.kind",
                op="in",
                value=["dctGearbox", "automaticGearbox"],
            ),
        ],
        assumptions=[
            "valor de referencia (mínimo y moda de 113 coches de AC), no medido en el origen",
            "la caja de BeamNG no tiene inercia propia (calculateInertia solo refleja los hijos); "
            "AC no admite 0: ausente o 0 = 1,0 kg·m²",
            "sin probar en AC con este valor (la validación de G2.25/G2.26 se hizo con 1,0)",
        ],
        source_doc=[
            ev(
                "code",
                "BeamNG.drive 0.38.6 lua/vehicle/powertrain",
                "dctGearbox.lua l. 233-262; automaticGearbox.lua l. 257-273",
            )
        ],
        target_doc=code + [ev("file", f"{EVID}/corpus_ac.json", "gearbox.INERTIA")],
        evidence=report
        + [
            ev("file", f"{EVID}/freno_motor/aceleracion_inercia_caja.txt"),
            ev("file", f"{EVID}/freno_motor/descomposicion.json"),
        ],
        history=hist(
            "provisional", "sin la clave AC añade 1,0 kg·m² al motor (medido en AC)"
        ),
        notes="investigación 2026-11-10",
    )
    return [app, value]


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
    if any(x["id"] == TARGET for x in data) or TARGET in tg["targets"]:
        raise SystemExit(f"ya existe: {TARGET}")
    rs = rules()
    have = {p.stem for p in (ROOT / "catalog/rules").glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print("ok (sin escribir): 1 atributo, 1 objetivo, 2 reglas")
        return 0
    data.insert(
        next(k for k, x in enumerate(data) if x["id"] == AFTER) + 1, attribute()
    )
    ATTR.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tg["targets"].insert(tg["targets"].index(AFTER) + 1, TARGET)
    TARGETS.write_text(
        json.dumps(tg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for r in rs:
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
