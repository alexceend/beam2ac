"""Atributos de la sección ``[AWD]`` de ``drivetrain.ini`` y su aplicabilidad (2026-10-30).

    python tools/catalog/add_awd_attributes.py [--dry-run]

Evidencia:
* transmisión de AC (AC) lee ``[AWD] FRONT_SHARE`` (×0,01) y
  ``CENTRE_/FRONT_/REAR_DIFF_POWER|COAST|PRELOAD``
  (output/research_frenos_diferencial_transmision_2026-10-26/code_evidence.json) [C].
* ``transmisión 2WD de AC`` solo usa ``[DIFFERENTIAL]`` (mismo archivo) [C]: en FWD/RWD
  las claves ``[AWD]`` no intervienen. el cálculo 4WD de AC no está analizado (E0).
* Comentarios de Kunos (``ks_toyota_celica_st185/data/drivetrain.ini``): POWER/COAST =
  «differential lock … 1.0=100% lock», PRELOAD = «preload torque setting».

Aplicabilidad (genérica, describe AC): las claves solo aplican con
``[TRACTION] TYPE=AWD``. Con TYPE sin decidir el motor da «indeterminada», nunca la
oculta. ``AWD2`` (otro modelo central) no se cataloga aquí: decisión pendiente.
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
DATE = "2026-10-30"
CODE = "output/research_frenos_diferencial_transmision_2026-10-26/code_evidence.json"
KUNOS = (
    "G:/SteamLibrary/steamapps/common/assettocorsa/content/cars/"
    "ks_toyota_celica_st185/data/drivetrain.ini"
)
DOC = "docs/research/catalogo_motor_awd_vmax_2026-10-30.md"
TYPE_ATTR = "ac:drivetrain.ini/TRACTION/TYPE"

KEYS = [("FRONT_SHARE", "%", "par al eje delantero, en % (loadINI ×0,01)")]
for diff in ("CENTRE", "FRONT", "REAR"):
    KEYS += [
        (f"{diff}_DIFF_POWER", "1", f"bloqueo del diferencial {diff} con par (0–1)"),
        (f"{diff}_DIFF_COAST", "1", f"bloqueo del diferencial {diff} en retención"),
        (f"{diff}_DIFF_PRELOAD", "N·m", f"precarga del diferencial {diff}"),
    ]


def attr(key: str, unit: str, desc: str) -> dict:
    return {
        "schema": "beam2ac.attribute/1",
        "id": f"ac:drivetrain.ini/AWD/{key}",
        "game": "assettocorsa",
        "unit": unit,
        "dtype": "number",
        "description": desc + "; uso en transmisión 4WD de AC sin analizar (E0)",
        "doc": [
            {
                "type": "code",
                "path": CODE,
                "locator": "transmisión de AC",
                "sha256": "",
                "note": "",
            },
            {
                "type": "doc",
                "path": KUNOS,
                "locator": "[AWD]",
                "sha256": "",
                "note": "",
            },
        ],
        "required_for_load": False,
    }


def rule(key: str) -> S.Rule:
    ev = [
        S.EvidenceRef(
            type="code", path=CODE, locator="transmisión 2WD de AC"
        ),
        S.EvidenceRef(type="report", path=DOC, locator="§2"),
    ]
    return S.Rule(
        id=f"app.awd.{key.lower()}",
        version=1,
        target=f"ac:drivetrain.ini/AWD/{key}",
        kind="applicability",
        transform=S.Transform(
            id="constant",
            params={"value": None},
            output_unit=dict((k, u) for k, u, _ in KEYS)[key],
        ),
        scope=S.Scope(level="generic"),
        status="validated",
        applies_when=[S.Condition(fact=TYPE_ATTR, op="eq", value="AWD")],
        source_doc=ev,
        target_doc=ev,
        evidence=ev,
        notes=f"con TYPE distinto de AWD, el cálculo 2WD de AC no usa {key}",
        history=[
            S.HistoryEntry(
                date=DATE,
                author="claude (tracción total del autist_cuanto)",
                action="create",
                status_to="validated",
                reason="el cálculo 2WD de AC solo usa [DIFFERENTIAL]; [AWD] solo con TYPE=AWD",
            )
        ],
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    raw = ATTR.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=1, ensure_ascii=False) + "\n" == raw
    existing = {x["id"] for x in data}
    new = [attr(k, u, d) for k, u, d in KEYS]
    clash = [x["id"] for x in new if x["id"] in existing]
    if clash:
        raise SystemExit(f"ya existen: {clash}")
    traw = TARGETS.read_text(encoding="utf-8")
    tg = json.loads(traw)
    assert json.dumps(tg, indent=1, ensure_ascii=False) + "\n" == traw
    tlist = tg["targets"] if isinstance(tg, dict) else tg
    rules = [rule(k) for k, _, _ in KEYS]
    for r in rules:
        S.from_dict(S.to_dict(r))
    if a.dry_run:
        print(f"ok (sin escribir): {len(new)} atributos, {len(rules)} reglas")
        return 0
    data += new
    ATTR.write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    tlist += [x["id"] for x in new]
    TARGETS.write_text(
        json.dumps(tg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for r in rules:
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
