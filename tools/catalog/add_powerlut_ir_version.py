"""G2, primera acción: ``eng.power_lut@4`` con entradas del IR (2026-10-30).

    python tools/catalog/add_powerlut_ir_version.py [--dry-run]

Sustituye a ``eng.power_lut@3`` sin cambiar su semántica. Solo cambian las referencias:

* entrada ``x``: ``bng.engine.net_wot_curve`` → ``ir.engine.wot_torque_curve`` (el mismo
  parámetro del IR, con la misma unidad ``table:rpm->N·m`` y la misma procedencia);
* condiciones: ``bng.engine.device_type`` → ``ir.engine.device_type`` y
  ``bng.code.camsoEngine_sha256`` → ``ir.formats.code_versions.camsoEngine``, con los
  mismos operadores y valores.

Transformación, unidades, ámbito (familia ``automation_export``), estado, supuestos,
validaciones y documentación de origen y destino se copian. Se niega a escribir si la
@3 no es la esperada o si la @4 ya existe. Comprobación: resoluciones idénticas salvo la
clave de regla (``output/g1_ir_2026-10-30/resolucion.py --diff``).
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-10-30"
AUTHOR = "claude (G2, primera acción: reglas sobre el IR)"
DOC = "docs/research/g2_reglas_ir_2026-10-30.md"
INPUTS = {"bng.engine.net_wot_curve": "ir.engine.wot_torque_curve"}
CONDITIONS = {
    "bng.engine.device_type": "ir.engine.device_type",
    "bng.code.camsoEngine_sha256": "ir.formats.code_versions.camsoEngine",
}


def power_lut_v4(old: S.Rule) -> S.Rule:
    if old.inputs != {"x": "bng.engine.net_wot_curve"}:
        raise SystemExit(f"{old.key}: entradas inesperadas {old.inputs}")
    if sorted(c.fact for c in old.conditions) != sorted(CONDITIONS):
        raise SystemExit(f"{old.key}: condiciones inesperadas {old.conditions}")
    new = copy.deepcopy(old)
    new.version, new.supersedes = 4, old.key
    new.inputs = {k: INPUTS[v] for k, v in old.inputs.items()}
    new.conditions = [
        S.Condition(fact=CONDITIONS[c.fact], op=c.op, value=c.value)
        for c in old.conditions
    ]
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code",
            path="src/ir/legacy_facts.py",
            locator="ir_facts: ir.engine.wot_torque_curve = bng.engine.net_wot_curve",
        )
    ]
    new.evidence = list(old.evidence) + [S.EvidenceRef(type="report", path=DOC)]
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="new_version",
            status_from=old.status,
            status_to=new.status,
            reason="entradas y condiciones sobre hechos nativos del IR (ir.*); "
            "transformación, ámbito, supuestos y validaciones sin cambios",
        )
    ]
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rule = power_lut_v4(cat.rules["eng.power_lut@3"])
    S.from_dict(S.to_dict(rule))
    if args.dry_run:
        print("ok (sin escribir):", rule.key, rule.inputs, rule.conditions)
        return 0
    print("escrita:", write_rule(REPO_CATALOG, rule))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
