"""Procedencia (2026-11-02, D-P1 y D-P2): pertenencia de ``automation_export``.

    python tools/catalog/migrate_family_provenance.py --snapshot <instantanea.json>
        [--dry-run]

Cambia la pertenencia de la familia de «Automation **y** ``camsoEngine``» a la
procedencia declarada en ``info.json`` (D-P1), el MISMO criterio que
``src.importers.automation.is_automation_info``:

* ``ir.identity.source_tool == "Automation"``;
* ``ir.identity.author == "Camshaft Software LTD"``;
* ``ir.formats.exporter_version in KNOWN_EXPORTER_VERSIONS`` (identificador opaco,
  nunca un rango).

Las señales estructurales (``license.txt``, ``*.car``, piezas ``Camso_``, sonido…) NO
entran (D-P2: solo corroboran). ``families.json`` no tiene versiones: la pertenencia
anterior queda en una entrada nueva de ``evidence`` (como en G2.5b).

Controles obligatorios antes de escribir (la herramienta se niega si alguno falla):

1. **Protección previa de las reglas** (informe §5.2, paso 2): toda regla VIGENTE con
   ámbito ``family: automation_export`` tiene al menos una condición sobre
   ``ir.formats.exporter_version``, ``ir.engine.device_type`` o
   ``ir.formats.code_versions.*``. Así ampliar la familia no amplía ninguna regla.
2. **Lista cerrada de altas** (``EXPECTED_NEW_MEMBERS``, los 7 del exportador 200409):
   en la instantanea (``output/procedencia_impl_2026-11-02/instantanea.py``) se evalúan
   la pertenencia anterior y la nueva con el evaluador del motor; ningún miembro puede
   salir, solo esos 7 pueden entrar y los 7 tienen que estar y entrar. La lista es un
   control de ESTA migración, no el criterio de procedencia.
3. Los valores de la pertenencia coinciden con las constantes del importador.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.engine import VehicleContext, eval_condition  # noqa: E402
from src.attrs.facts import Fact  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog  # noqa: E402
from src.importers import automation  # noqa: E402

FAMILY = "automation_export"
DATE = "2026-11-02"
DOC = "docs/research/procedencia_automation_2026-11-01.md"
IMPL_DOC = "docs/research/procedencia_implementacion_2026-11-02.md"
# Altas esperadas de esta migración: ir.identity.vehicle_id de los 7 coches del
# exportador 200409 de samples_beamng/ (alcance_automation_2026-11-01.md §3).
EXPECTED_NEW_MEMBERS = frozenset(
    {
        "2020_rpm_atacama_sport__4x4",
        "2020_rpm_atlanta_gt_3_6t",
        "2020_rpm_legion_grand_touring",
        "2020_rpm_patagonia_xt_v8",
        "2020_rpm_prothean_s",
        "2021_rpm_portland_touring",
        "rpm_omega_one_concept",
    }
)
PROTECTING_FACTS = ("ir.formats.exporter_version", "ir.engine.device_type")
PROTECTING_PREFIXES = ("ir.formats.code_versions.",)
DESCRIPTION = (
    "Procedencia: vehículos exportados por Automation (Camshaft Software) a BeamNG, "
    "según info.json (Type, Author y ExporterVersion reconocido). No implica ningún "
    "adaptador (camsoEngine, combustionEngine, cajas): cada regla de la familia limita "
    "con condiciones propias las versiones del exportador o el adaptador en los que "
    "está validada"
)


class FamilyMigrationError(Exception):
    pass


def new_membership() -> list[S.Condition]:
    return [
        S.Condition(fact="ir.identity.source_tool", op="eq", value=automation.TYPE),
        S.Condition(fact="ir.identity.author", op="eq", value=automation.AUTHOR),
        S.Condition(
            fact="ir.formats.exporter_version",
            op="in",
            value=list(automation.KNOWN_EXPORTER_VERSIONS),
        ),
    ]


def protects(rule: S.Rule) -> bool:
    facts = [c.fact for c in rule.conditions]
    return any(
        f in PROTECTING_FACTS or f.startswith(PROTECTING_PREFIXES) for f in facts
    )


def unprotected_rules(cat: Catalog) -> list[str]:
    """Reglas vigentes de la familia sin condición de versión ni de adaptador."""
    current = {
        r.key for t in {r.target for r in cat.rules.values()} for r in cat.rules_for(t)
    }
    return sorted(
        k
        for k in current
        if cat.rules[k].scope.level == "family"
        and cat.rules[k].scope.family == FAMILY
        and not protects(cat.rules[k])
    )


def migrate(old: S.Family) -> S.Family:
    if old.id != FAMILY:
        raise FamilyMigrationError(f"familia inesperada {old.id}")
    new = copy.deepcopy(old)
    new.membership = new_membership()
    if S._to_plain(new.membership) == S._to_plain(old.membership):
        raise FamilyMigrationError("la pertenencia ya es la de procedencia")
    new.description = DESCRIPTION
    before = "; ".join(f"{c.fact} {c.op} {c.value!r}" for c in old.membership)
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(
            type="code",
            path="src/importers/automation.py",
            locator="is_automation_info: TYPE, AUTHOR, KNOWN_EXPORTER_VERSIONS",
        ),
        S.EvidenceRef(
            type="report",
            path=DOC,
            locator="§1.1 (marcadores 17/17 y 0/123), §5.2 capa 1",
        ),
        S.EvidenceRef(
            type="report",
            path=IMPL_DOC,
            locator="pertenencia por procedencia (D-P1, D-P2)",
            note=(
                f"pertenencia anterior hasta {DATE}: {before}; descripción anterior: "
                f"{old.description}"
            ),
        ),
    ]
    return new


def _member(fam: S.Family, facts: dict[str, Fact]) -> bool:
    ctx = VehicleContext("x", "", facts)
    res = [eval_condition(c, ctx)[0] for c in fam.membership]
    return bool(res) and all(r is True for r in res)


def vehicles_of(snapshot: dict) -> dict[str, dict[str, Fact]]:
    """{nombre: hechos} de los bloques ``projects`` y ``sources`` (sin errores)."""
    out: dict[str, dict[str, Fact]] = {}
    for block in ("projects", "sources"):
        for name, rec in snapshot.get(block, {}).items():
            if "error" in rec:
                continue
            out[f"{block}/{name}"] = {
                fid: Fact(fid, d["value"], d["unit"], d["kind"], [], d.get("note", ""))
                for fid, d in rec["facts"].items()
            }
    return out


def check_members(old: S.Family, new: S.Family, snapshot: dict) -> list[str]:
    lines, entered = [], set()
    for name, facts in sorted(vehicles_of(snapshot).items()):
        a, b = _member(old, facts), _member(new, facts)
        vid = facts.get("ir.identity.vehicle_id")
        vid = vid.value if vid else ""
        if a and not b:
            raise FamilyMigrationError(f"{name}: dejaría de ser miembro")
        if b and not a:
            if vid not in EXPECTED_NEW_MEMBERS:
                raise FamilyMigrationError(f"{name} ({vid}): alta no prevista")
            entered.add(vid)
        lines.append(f"{name}: {a} -> {b}")
    missing = EXPECTED_NEW_MEMBERS - entered
    if missing:
        raise FamilyMigrationError(f"altas esperadas que no entran: {sorted(missing)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cat = Catalog.load(REPO_CATALOG)
    bad = unprotected_rules(cat)
    if bad:
        raise FamilyMigrationError(f"reglas de familia sin protección: {bad}")
    path = REPO_CATALOG / "families.json"
    items = json.loads(path.read_text("utf-8"))
    fams = [S.from_dict(i, "family") for i in items]
    idx = [i for i, f in enumerate(fams) if f.id == FAMILY]
    if len(idx) != 1:
        raise FamilyMigrationError(f"la familia {FAMILY} no existe (o está repetida)")
    old = fams[idx[0]]
    new = migrate(old)
    snapshot = json.loads(a.snapshot.read_text("utf-8"))
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
