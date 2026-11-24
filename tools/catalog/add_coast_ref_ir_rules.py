"""G2.7, paso 1 (G2.6a): ``eng.coast_{rpm,torque,nonlinearity}@2`` sobre el IR.

    python tools/catalog/add_coast_ref_ir_rules.py [--dry-run]

Cada @1 lee un hecho ``bng.engine.coast_ref_*`` que calcula el adaptador de hechos
con el ajuste de AC (``src.ac.engine.fit_coast_ref``): el IR dependía del destino. La
@2 hace el ajuste en la propia regla, con la transformación ``ac_coast_ref``
(``src/attrs/transforms.py`` → ``src/ac/coast_ref.py``), sobre datos del ORIGEN que ya
están en el IR:

* entradas: ``curve`` = ``ir.engine.coast_curve`` (``table:rpm->N·m``) y ``ceiling`` =
  ``ir.engine.speed_ceiling`` (``rpm``);
* ``params.output``: ``rpm``, ``torque`` o ``non_linearity``; ``params.round`` = 1 solo
  en el par (el hecho @1 se redondeaba con ``round(fit.torque, 1)``);
* condiciones: ``bng.engine.device_type`` → ``ir.engine.device_type`` y
  ``bng.code.camsoEngine_sha256`` → ``ir.formats.code_versions.camsoEngine``, con los
  mismos operadores y valores (equivalencia ya demostrada en ``eng.inertia@2``).

Ámbito, estado, supuestos declarados, validaciones y documentación se copian. El error
máximo del ajuste, que en @1 iba en la nota del hecho, lo devuelve ahora la
transformación como supuesto dependiente de los valores (D-G26a, opción b). Se niega a
escribir si alguna @1 no es la esperada o si ya existe la @2.
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

DATE = "2026-11-01"
AUTHOR = "claude (G2.7: implementación de G2.6)"
DOC = "docs/research/g2_reglas_ir_2026-10-30.md"
CONDITIONS = {
    "bng.engine.device_type": "ir.engine.device_type",
    "bng.code.camsoEngine_sha256": "ir.formats.code_versions.camsoEngine",
}
# regla → (hecho @1, salida de ac_coast_ref, unidad, params extra)
RULES = {
    "eng.coast_rpm@1": ("bng.engine.coast_ref_rpm", "rpm", "rpm", {}),
    "eng.coast_torque@1": (
        "bng.engine.coast_ref_torque",
        "torque",
        "N·m",
        {"round": 1},
    ),
    "eng.coast_nonlinearity@1": (
        "bng.engine.coast_ref_nonlinearity",
        "non_linearity",
        "1",
        {},
    ),
}
FORMULA = (
    "ac_coast_ref (src/ac/coast_ref.py): RPM, TORQUE = último punto (r, t) de la curva "
    "con r ≤ techo; NON_LINEARITY = 0; freno(rpm) = TORQUE·rpm/RPM"
)


def coast_v2(old: S.Rule, fact: str, output: str, unit: str, extra: dict) -> S.Rule:
    if old.inputs != {"x": fact}:
        raise SystemExit(f"{old.key}: entradas inesperadas {old.inputs}")
    if old.transform.id != "identity" or old.transform.params:
        raise SystemExit(f"{old.key}: transformación inesperada {old.transform}")
    if old.transform.input_units != {"x": unit} or old.transform.output_unit != unit:
        raise SystemExit(f"{old.key}: unidades inesperadas {old.transform}")
    if sorted(c.fact for c in old.conditions) != sorted(CONDITIONS) or old.applies_when:
        raise SystemExit(f"{old.key}: condiciones inesperadas {old.conditions}")
    new = copy.deepcopy(old)
    new.version, new.supersedes = 2, old.key
    new.inputs = {
        "curve": "ir.engine.coast_curve",
        "ceiling": "ir.engine.speed_ceiling",
    }
    new.transform = S.Transform(
        id="ac_coast_ref",
        params={"output": output, **extra},
        input_units={"curve": "table:rpm->N·m", "ceiling": "rpm"},
        output_unit=unit,
        formula=FORMULA,
    )
    new.conditions = [
        S.Condition(fact=CONDITIONS[c.fact], op=c.op, value=c.value)
        for c in old.conditions
    ]
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code", path="src/ac/coast_ref.py", locator="coast_ref_linear"
        ),
        S.EvidenceRef(
            type="code", path="src/attrs/transforms.py", locator="t_ac_coast_ref"
        ),
        S.EvidenceRef(
            type="code",
            path="src/importers/beamng.py",
            locator="engine.coast_curve, engine.speed_ceiling",
        ),
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(type="report", path=DOC, locator="§13.3, §14"),
        S.EvidenceRef(type="report", path="output/g26_2026-11-01/evidencia.md"),
    ]
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="new_version",
            status_from=old.status,
            status_to=new.status,
            reason="ajuste de COAST_REF en la regla (transformación ac_coast_ref) "
            "sobre ir.engine.coast_curve e ir.engine.speed_ceiling; condiciones sobre "
            "ir.*; ámbito, estado, supuestos y validaciones sin cambios",
        )
    ]
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    new_rules = []
    for key, (fact, output, unit, extra) in RULES.items():
        new_key = key.replace("@1", "@2")
        if new_key in cat.rules:
            raise SystemExit(f"{new_key} ya existe")
        rule = coast_v2(cat.rules[key], fact, output, unit, extra)
        S.from_dict(S.to_dict(rule))
        new_rules.append(rule)
    for rule in new_rules:
        if args.dry_run:
            print("ok (sin escribir):", rule.key, rule.inputs, rule.transform.params)
        else:
            print("escrita:", write_rule(REPO_CATALOG, rule))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
