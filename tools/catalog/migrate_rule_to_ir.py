"""G2: versión nueva de una regla con entradas y condiciones sobre hechos ``ir.*``.

    python tools/catalog/migrate_rule_to_ir.py <id@versión> [<id@versión> …] [--dry-run]
    python tools/catalog/migrate_rule_to_ir.py --scope <id@versión> … [--dry-run]

Genérica (no por regla). La correspondencia ``bng.*`` → ``ir.*`` se deriva del propio
adaptador ``src/ir/legacy_facts.py``, y solo se admite cuando es uno a uno con el MISMO
parámetro del IR (mismo valor, unidad, clase y procedencia):

* ``STATIC``: ruta del IR → hecho ``bng.*`` (inyectiva; se comprueba);
* ``bng.code.<nombre>_sha256`` → ``formats.code_versions.<nombre>``;
* ``bng.gearbox.forward_ratio_<k>`` → ``drivetrain.gearbox.forward_ratios.<k>``
  (comprobado con ``legacy_facts.legacy_id``);
* ``bng.tyre.<eje>.<campo>`` → ``tyres.axles.<eje>.<campo>`` (G2.5b; eje
  ``front``/``rear`` y campo de ``TyreAxle``; comprobado con
  ``legacy_facts.legacy_id``).

Se traducen ``inputs``, ``conditions`` y ``applies_when`` (mismos operadores y
valores). ``scope`` NO se toca: las condiciones de un ámbito de arquitectura siguen
con sus hechos (cambiarlas es cambiar el ámbito y requiere una decisión aparte).

Si una entrada o condición no tiene correspondencia (p. ej. ``bng.engine.coast_ref_*``,
que es un ajuste del destino, o ``bng.diff.*``, que depende de la tracción), la
herramienta se DETIENE: no inventa equivalencias. La versión nueva sustituye a la
anterior (``supersedes``), que no se modifica. Solo cambian ``inputs``, ``conditions``
y se añaden referencias en ``source_doc``, ``evidence`` e ``history``; el resto se
comprueba campo a campo antes de escribir.

**Modo ``--scope`` (G2.5d), explícito y separado.** Migra SOLO las condiciones de un
ámbito de arquitectura (``scope.conditions``) y nada más:

* lista CERRADA de hechos de ámbito (``SCOPE_FACTS``), cada uno comprobado contra
  ``STATIC`` (el adaptador produce ese ``bng.*`` desde ese parámetro del IR). Cualquier
  otro hecho del ámbito detiene la herramienta: no hay sustitución genérica;
* mismo nivel (``architecture``), mismo número y orden de condiciones, mismos
  operadores y valores; ``vehicle``/``variant``/``family`` intactos;
* ``inputs``, ``conditions``, ``applies_when`` y el resto de campos NO cambian (deben
  estar ya sobre ``ir.*``; si no, se detiene: primero el modo normal); las excepciones
  (``exceptions[].scope``) tampoco se tocan;
* si un hecho ``ir.*`` del ámbito falta en un vehículo, la condición queda sin
  cumplir («falta el hecho …», ``engine.eval_condition``): no se recurre al ``bng.*``.

Sin ``--scope`` el comportamiento es el de antes: ``scope`` nunca cambia.

**Diferencial del eje motriz (G2.7, paso 3).** Par CERRADO ``bng.diff.type`` /
``bng.diff.lsd_preload`` → ``drivetrain.driven_axle_differential.{kind,preload}``
(:data:`DRIVEN_AXLE_DIFF`). No está en ``STATIC`` porque ``bng.diff.*`` depende de la
tracción: el adaptador lo toma de ``differentials.<eje motriz>`` solo fuera de AWD, y
el importador enlaza ``driven_axle_differential`` al MISMO objeto de ese eje. Antes de
usarlo se comprueba por construcción (:func:`check_driven_axle_pairs`) que, en FWD y
RWD, ``legacy_id`` produce ese ``bng.*`` desde el mismo campo del eje motriz y que en
AWD no produce ninguno. Ningún otro ``bng.diff.*`` tiene par.

**Evidencia sintética de las P (G2.7, paso 5; decisión D-G26c).** Las referencias de
:data:`PENDING_EVIDENCE` siguen bloqueadas por defecto. Con
``--accept-synthetic-evidence`` (``accept_synthetic=True``) se admiten SOLO las de
:data:`SYNTHETIC_EVIDENCE`, cuya rama «presente» demuestra una prueba que pasa por el
importador real con una copia modificada del Ibiza; la entrada de historial y la
evidencia de la regla nueva lo dicen expresamente.
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
from src.ir.legacy_facts import STATIC, legacy_id  # noqa: E402
from src.ir.param import Param  # noqa: E402
from src.ir.vehicle import Differential, TyreAxle, VehicleIR  # noqa: E402

DATE = "2026-10-30"
AUTHOR = "claude (G2: reglas sobre el IR)"
DOC = "docs/research/g2_reglas_ir_2026-10-30.md"
FORWARD_RATIO = "bng.gearbox.forward_ratio_"
# Correspondencia estática uno a uno pero SIN evidencia en los vehículos de referencia
# (auditoría G2 §7.2, categoría P): no se migran sin un vehículo que lo demuestre.
PENDING_EVIDENCE = {
    "bng.clutch.lock_torque": "ningún vehículo de referencia declara lockTorque",
    "bng.gearbox.forward_ratio_8": "ningún vehículo de referencia tiene 8 marchas",
}
# G2.7, paso 5 (D-G26c): referencias P con evidencia sintética por el importador real
SYNTHETIC_EVIDENCE = {
    "bng.clutch.lock_torque": "tests/test_g27_p_rules.py (variante lockTorque)",
    "bng.gearbox.forward_ratio_8": "tests/test_g27_p_rules.py (variante 8 marchas)",
}
# G2.5d: hechos de ámbito migrables (lista cerrada) -> parámetro del IR. Equivalencia
# demostrada en los 14 proyectos legibles (output/g25c_2026-10-31/
# equivalencia_ambito_marchas.txt); ninguna validación cubre estos hechos.
SCOPE_FACTS = {
    "bng.drivetrain.traction": "drivetrain.layout",
    "bng.gearbox.type": "drivetrain.gearbox.kind",
}
SCOPE_DOC_LOCATOR = "§12"
# G2.7, paso 3: par cerrado (comprobado por check_driven_axle_pairs)
DRIVEN_AXLE_DIFF = {
    "bng.diff.type": "drivetrain.driven_axle_differential.kind",
    "bng.diff.lsd_preload": "drivetrain.driven_axle_differential.preload",
}
TYRE = "bng.tyre."
TYRE_AXLES = ("front", "rear")
UNCHANGED = (
    "id",
    "target",
    "kind",
    "transform",
    "scope",
    "status",
    "exceptions",
    "incompatible_with",
    "assumptions",
    "open_issues",
    "target_doc",
    "validations",
    "notes",
)


class MigrationError(SystemExit):
    pass


def legacy_to_ir() -> dict[str, str]:
    inverse: dict[str, str] = {}
    for path, fid in STATIC.items():
        if fid in inverse:
            raise MigrationError(f"{fid} no es uno a uno: {inverse[fid]} y {path}")
        inverse[fid] = f"ir.{path}"
    return inverse


def check_driven_axle_pairs() -> None:
    """Cada par de :data:`DRIVEN_AXLE_DIFF`: en FWD (eje delantero) y RWD (trasero),
    ``legacy_id`` da ese ``bng.*`` desde ``differentials.<eje>.<campo>``, el campo
    existe en ``Differential`` y es el mismo que el de destino; en AWD, ninguno."""
    for ref, path in DRIVEN_AXLE_DIFF.items():
        field = path.rsplit(".", 1)[1]
        if field not in Differential.__dataclass_fields__:
            raise MigrationError(f"{path}: campo inexistente en Differential")
        for layout, role in (("FWD", "front"), ("RWD", "rear"), ("AWD", None)):
            ir = VehicleIR()
            ir.drivetrain.layout = Param.of(layout, "enum", "computed")
            for r in ("front", "rear"):
                got = legacy_id(f"drivetrain.differentials.{r}.{field}", ir)
                want = ref if r == role else None
                if layout == "AWD":
                    ok = got is None or not got.startswith("bng.diff.")
                else:
                    ok = r != role or got == want
                if not ok:
                    raise MigrationError(
                        f"{ref}: el adaptador no lo produce solo desde el eje motriz "
                        f"({layout}, {r}: {got})"
                    )


def ir_ref(ref: str, table: dict[str, str], accept_synthetic: bool = False) -> str:
    if ref in PENDING_EVIDENCE and not (accept_synthetic and ref in SYNTHETIC_EVIDENCE):
        raise MigrationError(f"{ref}: pendiente de evidencia ({PENDING_EVIDENCE[ref]})")
    if ref in table:
        return table[ref]
    if ref in DRIVEN_AXLE_DIFF:
        check_driven_axle_pairs()
        return f"ir.{DRIVEN_AXLE_DIFF[ref]}"
    if ref.startswith("bng.code.") and ref.endswith("_sha256"):
        name = ref[len("bng.code.") : -len("_sha256")]
        return f"ir.formats.code_versions.{name}"
    if ref.startswith(FORWARD_RATIO) and ref[len(FORWARD_RATIO) :].isdigit():
        # marcha k: drivetrain.gearbox.forward_ratios.<k>; uno a uno e independiente
        # de la tracción (se comprueba contra legacy_facts.legacy_id)
        k = ref[len(FORWARD_RATIO) :]
        path = f"drivetrain.gearbox.forward_ratios.{k}"
        if legacy_id(path, VehicleIR()) != ref:
            raise MigrationError(f"{ref}: el adaptador no lo produce desde {path}")
        return f"ir.{path}"
    if ref.startswith(TYRE):
        # neumático por eje: tyres.axles.<eje>.<campo>; uno a uno e independiente de la
        # tracción (se comprueba contra legacy_facts.legacy_id)
        parts = ref[len(TYRE) :].split(".")
        if (
            len(parts) == 2
            and parts[0] in TYRE_AXLES
            and parts[1] in TyreAxle.__dataclass_fields__
        ):
            path = f"tyres.axles.{parts[0]}.{parts[1]}"
            if legacy_id(path, VehicleIR()) != ref:
                raise MigrationError(f"{ref}: el adaptador no lo produce desde {path}")
            return f"ir.{path}"
    if ref.startswith(("ir.", "ac:")):
        return ref  # ya nativo, o atributo de destino
    raise MigrationError(f"sin correspondencia demostrable en el IR para {ref!r}")


def scope_fact(fact: str) -> str:
    """Hecho de ámbito ``bng.*`` -> ``ir.*``, solo para la lista cerrada."""
    if fact not in SCOPE_FACTS:
        raise MigrationError(f"hecho de ámbito sin migración admitida: {fact!r}")
    path = SCOPE_FACTS[fact]
    if STATIC.get(path) != fact:
        raise MigrationError(f"{fact}: el adaptador no lo produce desde {path}")
    return f"ir.{path}"


def _scope_target(fact: str) -> str:
    return fact if fact.startswith("ir.") else scope_fact(fact)


def migrate_scope(old: S.Rule, date: str = DATE) -> S.Rule:
    """Versión nueva con SOLO las condiciones del ámbito sobre hechos ``ir.*``."""
    if old.scope.level != "architecture":
        raise MigrationError(
            f"{old.key}: ámbito {old.scope.level!r}, no de arquitectura"
        )
    if not old.scope.conditions:
        raise MigrationError(f"{old.key}: ámbito sin condiciones")
    refs = list(old.inputs.values()) + [
        c.fact for c in old.conditions + old.applies_when
    ]
    for ref in refs:
        if ref.startswith(("bng.", "project.")):
            raise MigrationError(
                f"{old.key}: {ref} aún no es ir.*; migrar antes las entradas"
            )
    if all(c.fact.startswith("ir.") for c in old.scope.conditions):
        raise MigrationError(
            f"{old.key}: el ámbito ya usa hechos ir.*; nada que migrar"
        )
    new = copy.deepcopy(old)
    new.version, new.supersedes = old.version + 1, old.key
    new.scope.conditions = [
        S.Condition(fact=_scope_target(c.fact), op=c.op, value=copy.deepcopy(c.value))
        for c in old.scope.conditions
    ]
    changed = [c.fact for c in old.scope.conditions if not c.fact.startswith("ir.")]
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code",
            path="src/ir/legacy_facts.py",
            locator="STATIC (ámbito): mismo parámetro que " + ", ".join(changed),
        )
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(type="report", path=DOC, locator=SCOPE_DOC_LOCATOR)
    ]
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=date,
            author=AUTHOR,
            action="new_version",
            status_from=old.status,
            status_to=new.status,
            reason="condiciones del ámbito sobre hechos nativos del IR (ir.*): mismos "
            "operadores, valores y orden; entradas, transformación, supuestos y "
            "validaciones sin cambios",
        )
    ]
    check_scope(old, new)
    return new


def check_scope(old: S.Rule, new: S.Rule) -> None:
    """Modo ``--scope``: solo pueden diferir los hechos del ámbito (lista cerrada) y
    los añadidos de trazabilidad."""
    for name in UNCHANGED + ("inputs", "conditions", "applies_when"):
        if name == "scope":
            continue
        if S._to_plain(getattr(old, name)) != S._to_plain(getattr(new, name)):
            raise MigrationError(f"{old.key}: cambiaría {name}")
    for name in ("source_doc", "evidence", "history"):
        a, b = S._to_plain(getattr(old, name)), S._to_plain(getattr(new, name))
        if b[: len(a)] != a or len(b) != len(a) + 1:
            raise MigrationError(f"{old.key}: {name} no conserva lo anterior")
    so, sn = old.scope, new.scope
    for name in ("level", "vehicle", "variant", "family"):
        if getattr(so, name) != getattr(sn, name):
            raise MigrationError(f"{old.key}: cambiaría scope.{name}")
    if len(so.conditions) != len(sn.conditions):
        raise MigrationError(f"{old.key}: cambia el número de condiciones del ámbito")
    for c_old, c_new in zip(so.conditions, sn.conditions):
        if (c_old.op, S._to_plain(c_old.value)) != (c_new.op, S._to_plain(c_new.value)):
            raise MigrationError(f"{old.key}: cambia el operador o el valor de {c_old}")
        if c_new.fact != _scope_target(c_old.fact):
            raise MigrationError(f"{old.key}: {c_old.fact} -> {c_new.fact} no admitido")


def migrate(
    old: S.Rule,
    table: dict[str, str],
    date: str = DATE,
    accept_synthetic: bool = False,
) -> S.Rule:
    new = copy.deepcopy(old)
    new.version, new.supersedes = old.version + 1, old.key

    def ref(r: str) -> str:
        return ir_ref(r, table, accept_synthetic)

    new.inputs = {k: ref(v) for k, v in old.inputs.items()}
    new.conditions = [
        S.Condition(fact=ref(c.fact), op=c.op, value=c.value) for c in old.conditions
    ]
    new.applies_when = [
        S.Condition(fact=ref(c.fact), op=c.op, value=c.value) for c in old.applies_when
    ]
    if (
        new.inputs == old.inputs
        and new.conditions == old.conditions
        and new.applies_when == old.applies_when
    ):
        raise MigrationError(f"{old.key}: ya usa hechos ir.*; nada que migrar")
    changed = sorted(
        {v for v in old.inputs.values() if v.startswith("bng.")}
        | {
            c.fact
            for c in old.conditions + old.applies_when
            if c.fact.startswith(("bng.", "project."))
        }
    )
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code",
            path="src/ir/legacy_facts.py",
            locator="ir_facts: mismo parámetro que " + ", ".join(changed),
        )
    ]
    synthetic = sorted(r for r in changed if r in SYNTHETIC_EVIDENCE)
    if synthetic and not accept_synthetic:  # ir_ref ya lo habría impedido
        raise MigrationError(f"{old.key}: {synthetic} sin evidencia aceptada")
    new.evidence = list(old.evidence) + [S.EvidenceRef(type="report", path=DOC)]
    new.evidence += [
        S.EvidenceRef(
            type="code",
            path=SYNTHETIC_EVIDENCE[r].split(" ")[0],
            locator=SYNTHETIC_EVIDENCE[r],
            note="evidencia SINTÉTICA (D-G26c): copia modificada del Ibiza por el "
            "importador real; ningún vehículo real recorre la rama presente",
        )
        for r in synthetic
    ]
    reason = (
        "entradas y condiciones sobre hechos nativos del IR (ir.*); "
        "transformación, ámbito, supuestos y validaciones sin cambios"
    )
    if synthetic:
        reason += (
            f"; equivalencia de {', '.join(synthetic)} demostrada con evidencia "
            "SINTÉTICA (D-G26c, informe G2 §14)"
        )
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=date,
            author=AUTHOR,
            action="new_version",
            status_from=old.status,
            status_to=new.status,
            reason=reason,
        )
    ]
    check(old, new)
    return new


def check(old: S.Rule, new: S.Rule) -> None:
    """Solo pueden diferir las referencias a hechos y los añadidos de trazabilidad."""
    for name in UNCHANGED:
        if S._to_plain(getattr(old, name)) != S._to_plain(getattr(new, name)):
            raise MigrationError(f"{old.key}: cambiaría {name}")
    for name in ("source_doc", "evidence", "history"):
        a, b = S._to_plain(getattr(old, name)), S._to_plain(getattr(new, name))
        if b[: len(a)] != a:
            raise MigrationError(f"{old.key}: {name} no conserva lo anterior")
    if list(old.inputs) != list(new.inputs):
        raise MigrationError(f"{old.key}: cambian los nombres de las entradas")
    for c_old, c_new in zip(old.conditions, new.conditions):
        if (c_old.op, c_old.value) != (c_new.op, c_new.value):
            raise MigrationError(f"{old.key}: cambia el operador o el valor de {c_old}")
    if len(old.conditions) != len(new.conditions):
        raise MigrationError(f"{old.key}: cambia el número de condiciones")
    if len(old.applies_when) != len(new.applies_when):
        raise MigrationError(
            f"{old.key}: cambia el número de condiciones de aplicación"
        )
    for c_old, c_new in zip(old.applies_when, new.applies_when):
        if (c_old.op, c_old.value) != (c_new.op, c_new.value):
            raise MigrationError(f"{old.key}: cambia el operador o el valor de {c_old}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rules", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--scope",
        action="store_true",
        help="G2.5d: migrar SOLO las condiciones del ámbito (lista SCOPE_FACTS)",
    )
    ap.add_argument("--date", default=DATE, help="fecha de la entrada de historial")
    ap.add_argument(
        "--accept-synthetic-evidence",
        action="store_true",
        help="G2.7 (D-G26c): admitir las P de SYNTHETIC_EVIDENCE",
    )
    a = ap.parse_args(argv)
    cat = Catalog.load(REPO_CATALOG)
    table = legacy_to_ir()
    new_rules = []
    for key in a.rules:
        if key not in cat.rules:
            raise MigrationError(f"la regla {key} no existe en el catálogo")
        superseded = {r.supersedes for r in cat.rules.values()}
        if key in superseded:
            raise MigrationError(f"{key} ya está sustituida por otra versión")
        if a.scope:
            new = migrate_scope(cat.rules[key], a.date)
        else:
            new = migrate(cat.rules[key], table, a.date, a.accept_synthetic_evidence)
        S.from_dict(S.to_dict(new))
        new_rules.append(new)
    for r in new_rules:
        if a.dry_run:
            print(
                f"ok (sin escribir): {r.key} {r.inputs} "
                f"{[(c.fact, c.op) for c in r.conditions]} "
                f"ámbito {[(c.fact, c.op, c.value) for c in r.scope.conditions]}"
            )
        else:
            print("escrita:", write_rule(REPO_CATALOG, r))
    if not a.dry_run:
        Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
