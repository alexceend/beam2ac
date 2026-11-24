"""Procedencia (2026-11-02, decisión D-P3): ``dt.traction@3``.

    python tools/catalog/add_traction_exporter_rule.py [--dry-run]

Solo añade (reglas inmutables): ``dt.traction@3`` = ``dt.traction@2`` + la condición
``ir.formats.exporter_version in ["38022", "40340"]``.

Motivo (``docs/research/procedencia_automation_2026-11-01.md`` §1.5, §3.2): la única
protección de ``dt.traction@2`` frente a otras versiones del exportador era que la
familia ``automation_export`` exigía ``camsoEngine``. Al pasar la familia a
«procedencia» (los tres campos de ``info.json``), la regla quedaría aplicada en el
exportador 200409, donde no está validada (sin ``info_<cfg>.json`` no hay ni siquiera
la validación automática ``traction``). La versión del exportador es un identificador
OPACO: lista cerrada con ``in``, nunca un rango.

Versiones admitidas (D-P3): 38022 (validación del catálogo: ``seat_ibiza_acfr``,
``spicy_mandala``) y 40340 (validación automática ``traction`` superada en los 4 coches
FWD/RWD de ``samples_beamng/thershow_*``). 200409 queda fuera.

Comprobaciones antes de escribir: la regla anterior tiene exactamente las condiciones
esperadas y la nueva solo difiere en ``version``, ``supersedes``, ``conditions``
(añadida la de versión, al final), ``evidence``, ``history`` y ``notes``.
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

OLD_KEY = "dt.traction@2"
VERSIONS = ["38022", "40340"]  # identificadores opacos (D-P3); 200409 NO
VERSION_FACT = "ir.formats.exporter_version"
DATE = "2026-11-02"
AUTHOR = "claude (procedencia de Automation, D-P3 aprobada por el usuario)"
DOC = "docs/research/procedencia_automation_2026-11-01.md"
IMPL_DOC = "docs/research/procedencia_implementacion_2026-11-02.md"
EXPECTED_OLD_CONDITIONS = [("ir.drivetrain.layout", "in", ["FWD", "RWD"])]
CHANGED_FIELDS = {"version", "supersedes", "conditions", "evidence", "history", "notes"}


class RuleBuildError(Exception):
    pass


def traction_v3(old: S.Rule) -> S.Rule:
    got = [(c.fact, c.op, c.value) for c in old.conditions]
    if got != EXPECTED_OLD_CONDITIONS:
        raise RuleBuildError(f"{old.key}: condiciones inesperadas {got}")
    if old.scope.level != "family" or old.scope.family != "automation_export":
        raise RuleBuildError(f"{old.key}: ámbito inesperado {S._to_plain(old.scope)}")
    new = copy.deepcopy(old)
    new.version, new.supersedes = 3, old.key
    new.conditions = list(old.conditions) + [
        S.Condition(fact=VERSION_FACT, op="in", value=list(VERSIONS))
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(type="report", path=DOC, locator="§1.5, §3.2, §5.2 paso 1"),
        S.EvidenceRef(type="report", path=IMPL_DOC, locator="dt.traction@3"),
    ]
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="new_version",
            reason=(
                "condición explícita de versión del exportador (38022, 40340; "
                "identificador opaco, lista cerrada): la familia automation_export "
                "pasa a ser solo procedencia y deja de implicar camsoEngine. "
                "Transformación, entradas, ámbito, estado, supuestos y validaciones "
                "sin cambios"
            ),
            status_from=old.status,
            status_to=old.status,
        )
    ]
    new.notes = (
        old.notes + "; validada solo en los exportadores 38022 (catálogo) y 40340 "
        "(validación automática traction); 200409 excluido (D-P3)"
    )
    a, b = S.to_dict(old), S.to_dict(new)
    changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    if not changed <= CHANGED_FIELDS:
        raise RuleBuildError(
            f"cambiarían campos no previstos: {changed - CHANGED_FIELDS}"
        )
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cat = Catalog.load(REPO_CATALOG)
    rule = traction_v3(cat.rules[OLD_KEY])
    S.from_dict(S.to_dict(rule))  # valida
    if args.dry_run:
        print("ok (sin escribir):", rule.key, [S._to_plain(c) for c in rule.conditions])
        return 0
    print("escrita:", write_rule(REPO_CATALOG, rule))
    Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
