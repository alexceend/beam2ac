"""Reglas de motor para camsoEngine.lua 91392d70… (exportador 40340), 2026-10-30.

    python tools/catalog/add_camso_91392_engine_rules.py [--dry-run]

Autorizado por el usuario (2026-10-30). Solo añade; nunca sobrescribe (reglas inmutables).

Evidencia común [C]: el ``camsoEngine.lua`` 91392d70… es idéntico línea a línea al
47437d5c… (Ibiza, Porsche) salvo la selección de la fila a gas cerrado:
- líneas 1-723 iguales;
- antiguas 729-2190 = nuevas 726-2187;
- antiguas 724-728 (2 comentarios y ``max(1, …)``) → nuevas 724-725 (``max(0, …)``).
Comprobado por ``tests/test_camso_91392_rules.py::TestLuaEquivalence``.

Reglas:

* ``eng.power_lut@3`` sustituye a ``@2``: misma transformación, entradas y ámbito
  (familia ``automation_export``); la condición de versión admite las dos. La curva a
  plena carga usa la fila 99 en ambas (``torqueCurve = torqueThrottleMap[99]``, l. 2047
  / 2044; ``min(99, …)`` en l. 728 / 725) y la misma fricción (l. 731 / 728). Para el
  Ibiza el valor no cambia; solo la clave de la regla (@2 → @3).
* Reglas PARALELAS de familia, solo para 91392d70… (las del Ibiza, de ámbito vehículo,
  no se tocan):
  - ``eng.inertia_camso91392@1``: INERTIA = ``inertia`` del motor, con la condición de que
    los hijos del motor (embrague) no añadan ``additionalEngineInertia`` (Lua l. 998 /
    1001; frictionClutch.lua l. 242: por defecto 0).
  - ``eng.limiter_camso91392@1``: LIMITER = techo de rpm (l. 1994-2004 / 1997-2007), solo
    si el limitador está activo o el techo no depende del conflicto ``hasRevLimiter``.
  - ``eng.minimum_camso91392@1``: MINIMUM = ``idleRPM`` (misma hipótesis del Ibiza).
* El freno motor (``eng.coast_*``) NO se amplía: el extractor elige la fila 0 para
  esta versión, pero la conversión (COAST_REF lineal de AC) no está validada.
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

OLD = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
NEW = "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"
DATE = "2026-10-30"
AUTHOR = "claude (catálogo de motor para camsoEngine 91392d70, autorizado)"
DOC = "docs/research/catalogo_motor_awd_vmax_2026-10-30.md"
LUA_NEW = (
    "samples_beamng/thershow_sucette_poubelle/vehicles/sucette_poubelle/"
    "lua/powertrain/camsoEngine.lua"
)
LUA_OLD = "sample_beamng/vehicles/seat_ibiza_acfr/lua/powertrain/camsoEngine.lua"


def ev(type_: str, path: str, locator: str = "", sha: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha)


def cond(fact: str, op: str, value) -> S.Condition:
    return S.Condition(fact=fact, op=op, value=value)


def hist(reason: str, status_from: str, status_to: str, action: str) -> S.HistoryEntry:
    return S.HistoryEntry(
        date=DATE,
        author=AUTHOR,
        action=action,
        reason=reason,
        status_from=status_from,
        status_to=status_to,
    )


LUA_EVIDENCE = [
    ev("code", LUA_OLD, "versión 47437d5c", OLD),
    ev("code", LUA_NEW, "versión 91392d70 (diff de 1 línea, l. 725)", NEW),
    ev("report", DOC, "§1"),
]


def power_lut_v3(old: S.Rule) -> S.Rule:
    new = copy.deepcopy(old)
    new.version, new.supersedes = 3, old.key
    sha = [c for c in new.conditions if c.fact == "bng.code.camsoEngine_sha256"]
    if len(sha) != 1 or sha[0].op != "eq" or sha[0].value != OLD:
        raise SystemExit(f"{old.key}: condición de versión inesperada {sha}")
    new.conditions = [
        c if c.fact != "bng.code.camsoEngine_sha256" else cond(c.fact, "in", [OLD, NEW])
        for c in new.conditions
    ]
    new.source_doc = list(old.source_doc) + LUA_EVIDENCE
    new.evidence = list(old.evidence) + [ev("report", DOC, "§1.1 power.lut")]
    new.history = list(old.history) + [
        hist(
            "admite camsoEngine.lua 91392d70: fila 99 y fricción idénticas (diff de 1 "
            "línea, solo gas cerrado); transformación, entradas y ámbito sin cambios",
            old.status,
            new.status,
            "new_version",
        )
    ]
    return new


def parallel(
    old: S.Rule,
    new_id: str,
    extra_conditions: list[S.Condition],
    assumptions: list[str],
    reason: str,
) -> S.Rule:
    """Copia de una regla del Ibiza con ámbito de familia y solo para 91392d70."""
    new = copy.deepcopy(old)
    new.id, new.version, new.supersedes = new_id, 1, ""
    new.scope = S.Scope(level="family", family="automation_export")
    new.status = "provisional"
    new.conditions = [
        c if c.fact != "bng.code.camsoEngine_sha256" else cond(c.fact, "eq", NEW)
        for c in old.conditions
    ] + extra_conditions
    new.assumptions = assumptions
    new.source_doc = list(old.source_doc) + LUA_EVIDENCE
    new.evidence = list(old.evidence) + [ev("report", DOC, f"§1 {old.target}")]
    new.validations = []
    new.history = [hist(reason, "", "provisional", "create")]
    new.notes = f"derivada de {old.key} (Ibiza, ámbito vehículo), que no cambia"
    return new


def build(cat: Catalog) -> list[S.Rule]:
    r = cat.rules
    return [
        power_lut_v3(r["eng.power_lut@2"]),
        parallel(
            r["eng.inertia@1"],
            "eng.inertia_camso91392",
            [cond("bng.engine.clutch_additional_inertia", "eq", 0.0)],
            [
                "INERTIA de AC («engine inertia», comentario de Kunos) = inercia de "
                "rotación del motor de BeamNG (device.inertia, kg·m²); equivalencia de "
                "semántica en AC sin verificar"
            ],
            "Lua idéntico (l. 998/1001, 1006/1009); el supuesto del Ibiza (falta "
            "additionalEngineInertia del embrague) pasa a condición comprobable",
        ),
        parallel(
            r["eng.limiter@1"],
            "eng.limiter_camso91392",
            [
                cond(
                    "bng.engine.rev_limiter",
                    "in",
                    ["activo", "independiente_del_conflicto"],
                )
            ],
            list(r["eng.limiter@1"].assumptions),
            "Lua idéntico (l. 1994-2004 / 1997-2007); sin limitador BeamNG anula el "
            "par por encima de la tabla y AC corta: se excluye ese caso",
        ),
        parallel(
            r["eng.minimum@1"],
            "eng.minimum_camso91392",
            [],
            list(r["eng.minimum@1"].assumptions),
            "Lua idéntico (idleRPM, l. 1760-1761 / 1763-1764); misma hipótesis que el "
            "Ibiza",
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = build(cat)
    for rule in rules:
        S.from_dict(S.to_dict(rule))  # valida
        if args.dry_run:
            print("ok (sin escribir):", rule.key)
        else:
            print("escrita:", write_rule(REPO_CATALOG, rule))
    if not args.dry_run:
        Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
