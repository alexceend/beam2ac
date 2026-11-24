"""G2.7, paso 2 (G2.6a): ``susp.type_{front,rear}@2`` sobre la etiqueta del IR.

    python tools/catalog/add_susp_type_ir_rules.py [--dry-run]

Cada @1 lee ``bng.suspension.<eje>.ac_type``: el adaptador de hechos aplica la tabla de
AC «MacPherson/strut → STRUT» a la etiqueta de la pieza (``legacy_facts``,
``AC_SUSPENSION_TYPE_BY_LABEL``), así que el IR dependía del destino. La @2 lee la
etiqueta del ORIGEN, ``ir.suspension.axles.<eje>.label`` (texto libre declarado por el
autor de la pieza), y aplica la tabla en la propia regla con la transformación
genérica ``text_contains_map``. La tabla va en ``transform.params.pairs`` (el catálogo
es la fuente de verdad), copiada de la constante actual sin ampliarla ni reordenarla:
la herramienta se niega si las dos copias del código (``src/extract/suspension.py`` y
``src/ir/legacy_facts.py``) no coinciden.

Ámbito, estado, supuestos, validaciones y documentación se copian. Se niega a escribir
si alguna @1 no es la esperada o si ya existe la @2.
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
from src.extract.suspension import TYPE_BY_LABEL  # noqa: E402
from src.ir.legacy_facts import AC_SUSPENSION_TYPE_BY_LABEL  # noqa: E402

DATE = "2026-11-01"
AUTHOR = "claude (G2.7: implementación de G2.6)"
DOC = "docs/research/g2_reglas_ir_2026-10-30.md"
AXLES = ("front", "rear")


def pairs() -> list[list[str]]:
    if tuple(TYPE_BY_LABEL) != tuple(AC_SUSPENSION_TYPE_BY_LABEL):
        raise SystemExit(
            f"las tablas del código difieren: {TYPE_BY_LABEL} ≠ "
            f"{AC_SUSPENSION_TYPE_BY_LABEL}"
        )
    return [[needle, value] for needle, value in AC_SUSPENSION_TYPE_BY_LABEL]


def type_v2(old: S.Rule, axle: str) -> S.Rule:
    if old.inputs != {"x": f"bng.suspension.{axle}.ac_type"}:
        raise SystemExit(f"{old.key}: entradas inesperadas {old.inputs}")
    if old.transform.id != "identity" or old.transform.params:
        raise SystemExit(f"{old.key}: transformación inesperada {old.transform}")
    if (
        old.transform.input_units != {"x": "enum"}
        or old.transform.output_unit != "enum"
    ):
        raise SystemExit(f"{old.key}: unidades inesperadas {old.transform}")
    if old.conditions or old.applies_when:
        raise SystemExit(f"{old.key}: condiciones inesperadas")
    new = copy.deepcopy(old)
    new.version, new.supersedes = 2, old.key
    new.inputs = {"text": f"ir.suspension.axles.{axle}.label"}
    new.transform = S.Transform(
        id="text_contains_map",
        params={"pairs": pairs()},
        input_units={"text": "text"},
        output_unit="enum",
        formula="primer par [aguja, valor] cuya aguja está en la etiqueta "
        "(sin distinguir mayúsculas); sin coincidencia: sin valor",
    )
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code",
            path="src/importers/beamng.py",
            locator=f"suspension.axles.{axle}.label",
        ),
        S.EvidenceRef(
            type="code", path="src/attrs/transforms.py", locator="t_text_contains_map"
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
            reason="la tabla MacPherson/strut → STRUT pasa a la regla "
            "(text_contains_map) sobre la etiqueta del IR; misma tabla, mismo ámbito, "
            "estado y supuestos",
        )
    ]
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    new_rules = []
    for axle in AXLES:
        if f"susp.type_{axle}@2" in cat.rules:
            raise SystemExit(f"susp.type_{axle}@2 ya existe")
        rule = type_v2(cat.rules[f"susp.type_{axle}@1"], axle)
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
