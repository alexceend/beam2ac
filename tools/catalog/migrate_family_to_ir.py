"""G2.5b: pertenencia de una familia sobre hechos ``ir.*``.

    python tools/catalog/migrate_family_to_ir.py <familia> --facts <hechos.json>
        [--dry-run]

Misma correspondencia ``bng.*`` → ``ir.*`` que ``migrate_rule_to_ir.py`` (uno a uno
con el MISMO parámetro del IR; se detiene si no la hay). Solo cambian los hechos de
``membership``: operadores y valores se comprueban iguales.

``families.json`` no tiene versiones (``catalog/README.md``: «editar con evidencia»):
la pertenencia anterior queda escrita en una entrada nueva de ``evidence`` y la
familia se reescribe en su sitio; el resto de familias no cambia.

Demostración previa obligatoria: ``--facts`` es una instantánea de hechos por
vehículo (``output/g1_ir_2026-10-30/hechos.py``). Se evalúan la pertenencia anterior y
la nueva con el evaluador del motor (``engine.eval_condition``) en cada vehículo y la
herramienta se niega a escribir si algún resultado de alguna condición difiere.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_rule_to_ir import DOC, MigrationError, ir_ref, legacy_to_ir  # noqa: E402

from src.attrs import schema as S  # noqa: E402
from src.attrs.engine import VehicleContext, eval_condition  # noqa: E402
from src.attrs.facts import Fact  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog  # noqa: E402


def migrate_family(old: S.Family, table: dict[str, str], date: str) -> S.Family:
    new = copy.deepcopy(old)
    new.membership = [
        S.Condition(fact=ir_ref(c.fact, table), op=c.op, value=c.value)
        for c in old.membership
    ]
    if new.membership == old.membership:
        raise MigrationError(f"{old.id}: ya usa hechos ir.*; nada que migrar")
    if [(c.op, c.value) for c in new.membership] != [
        (c.op, c.value) for c in old.membership
    ]:
        raise MigrationError(f"{old.id}: cambiaría operadores o valores")
    before = "; ".join(f"{c.fact} {c.op} {c.value!r}" for c in old.membership)
    changed = [
        c.fact for c in old.membership if c.fact.startswith(("bng.", "project."))
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(
            type="code",
            path="src/ir/legacy_facts.py",
            locator="ir_facts: mismo parámetro que " + ", ".join(changed),
        ),
        S.EvidenceRef(
            type="report",
            path=DOC,
            locator="§10 (G2.5b): pertenencia sobre hechos ir.*",
            note=f"pertenencia anterior hasta {date}: {before}",
        ),
    ]
    if S._to_plain(new.evidence[: len(old.evidence)]) != S._to_plain(old.evidence):
        raise MigrationError(f"{old.id}: evidence no conserva lo anterior")
    for name in ("id", "description"):
        if getattr(old, name) != getattr(new, name):
            raise MigrationError(f"{old.id}: cambiaría {name}")
    return new


def membership_results(fam: S.Family, facts: dict[str, Fact]) -> list[bool | None]:
    ctx = VehicleContext("x", "", facts)
    return [eval_condition(c, ctx)[0] for c in fam.membership]


def check_members(old: S.Family, new: S.Family, snapshot: dict) -> list[str]:
    lines = []
    for veh, raw in snapshot.items():
        facts = {
            fid: Fact(fid, d["value"], d["unit"], d["kind"], [], d.get("note", ""))
            for fid, d in raw.items()
        }
        a, b = membership_results(old, facts), membership_results(new, facts)
        member = bool(a) and all(r is True for r in a)
        if a != b:
            raise MigrationError(f"{old.id}: {veh} cambia de {a} a {b}")
        lines.append(f"{veh}: {'miembro' if member else 'no miembro'} {a} = {b}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("family")
    ap.add_argument("--facts", required=True, type=Path)
    ap.add_argument("--date", default="2026-10-31")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    path = REPO_CATALOG / "families.json"
    items = json.loads(path.read_text("utf-8"))
    fams = [S.from_dict(i, "family") for i in items]
    idx = [i for i, f in enumerate(fams) if f.id == a.family]
    if len(idx) != 1:
        raise MigrationError(f"la familia {a.family} no existe (o está repetida)")
    old = fams[idx[0]]
    new = migrate_family(old, legacy_to_ir(), a.date)
    snapshot = json.loads(a.facts.read_text("utf-8"))
    for line in check_members(old, new, snapshot):
        print(line)
    if a.dry_run:
        print("ok (sin escribir):", [(c.fact, c.op, c.value) for c in new.membership])
        return 0
    fams[idx[0]] = new
    path.write_text(
        json.dumps([S.to_dict(f) for f in fams], indent=1, ensure_ascii=False) + "\n",
        "utf-8",
    )
    Catalog.load(REPO_CATALOG)
    print("escrita:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
