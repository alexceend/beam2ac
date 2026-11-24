"""Reglas del piloto en ``CG_LOCATION``/``BASEY``/``INERTIA`` (G2.45, 2026-11-14).

    python tools/catalog/add_driver_cg_rules.py [--dry-run] [--catalog DIR]

Encargo del usuario (G2.45: con los 70 kg predeterminados los 16 vehículos se bloqueaban
por ``CG_LOCATION``, ``BASEY`` delantero/trasero e ``INERTIA`` explícita; revisar D-G217-CG).
Para cada regla vigente ``*_nodriver`` (16 familias: exportadores 200409/38022/40340 y eje
rígido) crea la regla hermana ``*_driver@1`` con ``ir.mass.driver > 0``: la misma
transformación con ``params.driver`` y dos entradas más (``ir.mass.driver`` y
``ir.mass.driver_position``, ESTIMADA: ``src/extract/driver_position.py``). Con 0 kg siguen
mandando las reglas ``*_nodriver`` (resultados históricos intactos); las dos familias son
excluyentes. Solo añade; nunca sobrescribe. No volver a ejecutarla."""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-14"
AUTHOR = "claude (G2.45: piloto en el CdG y la inercia, encargado por el usuario)"
DOC = "docs/research/g245_piloto_2026-11-14.md"


def build(cat: Catalog) -> list[S.Rule]:
    out = []
    for r in cat.current_rules() if hasattr(cat, "current_rules") else _current(cat):
        if not r.id.endswith("_nodriver"):
            continue
        n = copy.deepcopy(r)
        n.id = r.id[: -len("nodriver")] + "driver"
        n.version = 1
        n.supersedes = ""
        n.status = "provisional"
        n.conditions = [
            (
                S.Condition(fact=c.fact, op="gt", value=0)
                if (c.fact == "ir.mass.driver" and c.op == "eq")
                else c
            )
            for c in r.conditions
        ]
        assert any(c.fact == "ir.mass.driver" and c.op == "gt" for c in n.conditions)
        n.inputs = dict(r.inputs)
        n.inputs["driver_mass"] = "ir.mass.driver"
        n.inputs["driver_position"] = "ir.mass.driver_position"
        n.transform.input_units = dict(r.transform.input_units)
        n.transform.input_units.update({"driver_mass": "kg", "driver_position": "m"})
        if r.target.endswith("CG_LOCATION") and "wheelbase" not in n.inputs:
            n.inputs["wheelbase"] = "ir.dimensions.wheelbase"
            n.transform.input_units["wheelbase"] = "m"
        n.transform.params = {**r.transform.params, "driver": True}
        n.transform.formula += (
            "  [G2.45: + masa puntual del piloto m_d en r_d (ir.mass.driver_position); "
            "con m_d = 0 coincide con la forma sin piloto]"
        )
        n.assumptions = [
            "piloto = masa puntual m_d = ir.mass.driver en una posición ESTIMADA "
            "(src/extract/driver_position.py: cámara de cabina plausible o posición por "
            "defecto de las referencias validadas + hipótesis antropométrica, ± 0,15 m "
            "por eje); no es una medida (el origen no declara al piloto)",
            "la masa del piloto entra exactamente una vez: en TOTALMASS (car.totalmass_*); "
            "aquí solo reparte esa masa en CdG e inercia del cuerpo",
            "inercia propia del cuerpo del piloto ignorada (≈ 4 kg·m²)",
        ] + [a for a in r.assumptions if "piloto 0 kg" not in a]
        # un open_issue dejaría el atributo «ambiguous» (engine.py): la limitación va
        # en assumptions y en el informe
        n.open_issues = list(r.open_issues)
        n.source_doc = list(r.source_doc) + [
            S.EvidenceRef(
                type="code",
                path="src/extract/driver_position.py",
                locator="estimate",
                sha256="",
                note="",
            )
        ]
        n.evidence = list(r.evidence) + [
            S.EvidenceRef(type="report", path=DOC, locator="§2-§4", sha256="", note="")
        ]
        n.history = [
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                reason="G2.45: variante con piloto de la regla " + r.key,
                status_from="",
                status_to="provisional",
            )
        ]
        n.notes = "G2.45: hermana con piloto de " + r.key + "; excluyente con ella"
        out.append(n)
    return out


def _current(cat: Catalog):
    superseded = {r.supersedes for r in cat.rules.values() if r.supersedes}
    return [
        r
        for k, r in sorted(cat.rules.items())
        if r.status not in ("rejected", "superseded") and k not in superseded
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--catalog", type=Path, default=REPO_CATALOG)
    args = ap.parse_args()
    rules = build(Catalog.load(args.catalog))
    for r in rules:
        S.from_dict(S.to_dict(r))
    print(len(rules), "reglas")
    for r in rules:
        print(("ok (sin escribir): " if args.dry_run else "escrita: ") + r.key)
        if not args.dry_run:
            write_rule(args.catalog, r)
    if not args.dry_run:
        Catalog.load(args.catalog)
    return 0


if __name__ == "__main__":
    sys.exit(main())
