"""Frenos con codo de pedal del exportador 40340 (G2.29, 2026-11-11).

    python tools/catalog/add_brake_knee_40340_rules.py [--dry-run]

Los cinco 40340 del corpus llevan el mismo repartidor en el eje trasero (``brakeInputSplit`` 0,5 y
``brakeSplitCoef`` 0,5) y ninguno delante. BeamNG (``wheels.lua`` l. 554-557):

    T(p) = brakeTorque · (min(p, split) + max(p − split, 0) · coef)

AC (frenos de AC [C]) es lineal en el pedal: ``T_eje(p) = p · MAX_TORQUE · reparto``. No puede
representar el codo. Las reglas igualan los dos modelos a PEDAL 1 (par máximo de cada eje exacto, que es donde
se decide el bloqueo y la frenada límite); es la misma convención que la decisión F1 del Ibiza (38022).

Error a pedal parcial (queda escrito en los supuestos): por debajo del codo el eje trasero de AC da el 75 % del
par de BeamNG (0,5 + 0,5·0,5); el eje delantero es exacto. AC frena menos detrás a medio pedal (más estable).

``brk.max_torque_knee_40340@1`` y ``brk.front_share_knee_40340@1`` = ``brk.*_40340@1`` con la condición
«sin codo» sustituida por la estructura observada (delante 1; detrás 0,5/0,5). Solo añade.
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

RULES_DIR = ROOT / "catalog" / "rules"
AX = "ir.brakes.axles"
KNEE = [
    {"fact": f"{AX}.front.input_split", "op": "eq", "value": 1},
    {"fact": f"{AX}.rear.input_split", "op": "eq", "value": 0.5},
    {"fact": f"{AX}.rear.split_coef", "op": "eq", "value": 0.5},
]
ASSUMPTION = (
    "codo de pedal trasero de BeamNG (brakeInputSplit 0,5, brakeSplitCoef 0,5) sin equivalente en AC: "
    "se iguala el par de cada eje a pedal 1; por debajo del codo (pedal ≤ 0,5) el eje trasero de AC da el "
    "75 % del par de BeamNG y el delantero es exacto (reparto delantero de AC mayor que el del origen a "
    "medio pedal)"
)


def rule(base: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{base}_40340@1.json").read_text("utf-8"))
    version = [c for c in d["conditions"] if c["fact"] == "ir.formats.exporter_version"]
    assert len(version) == 1 and version[0]["value"] == ["40340"], base
    assert len(d["conditions"]) == 3, base  # versión + los dos «sin codo»
    d["conditions"] = version + KNEE
    d["supersedes"] = ""
    d.update(
        id=f"{base}_knee_40340",
        version=1,
        status="provisional",
        assumptions=list(d["assumptions"]) + [ASSUMPTION],
        notes="G2.29",
        history=[
            {
                "date": "2026-11-11",
                "author": "claude (G2.29, encargada por el usuario)",
                "action": "create",
                "reason": f"{base}_40340@1 con el codo de pedal trasero 0,5/0,5 (igualdad a pedal 1)",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
    )
    d["evidence"] = list(d.get("evidence", [])) + [
        {
            "type": "report",
            "path": "docs/research/g229_consolidacion_2026-11-11.md",
            "locator": "§2.1",
            "sha256": "",
            "note": "",
        }
    ]
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = [rule("brk.max_torque"), rule("brk.front_share")]
    for r in rs:
        S.from_dict(S.to_dict(r))
        if (RULES_DIR / f"{r.id}@{r.version}.json").exists():
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {[r.id for r in rs]}")
        return 0
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
