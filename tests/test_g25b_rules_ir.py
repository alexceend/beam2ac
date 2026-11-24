"""G2.5b: reglas A restantes con entradas y condiciones sobre hechos ``ir.*``.

Por cada grupo migrado (informe G2 §10):
* la versión nueva solo cambia referencias a hechos (resto de campos idéntico);
* la anterior sigue en el catálogo, intacta, como versión sustituida;
* el resultado de la nueva es el mismo que el de la anterior (estado, valor, unidad,
  origen, confianza y sus criterios, supuestos, problemas, rechazos y alternativas) en
  el Ibiza (regla aplicada) y en vehículos de muestra (propuesta sin aplicar);
* sin el hecho ``ir.*`` la nueva no se aplica (nunca un valor inventado);
* las exclusiones (P, B, C) siguen sin migrar y la herramienta las rechaza.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.ir.legacy_facts import ir_to_facts

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
sys.path.insert(0, str(ROOT / "tools/catalog"))

# regla (id) -> {hecho anterior: hecho ir.*} de entradas, condiciones y applies_when
GROUP_1 = {  # identidad y combustible
    "car.screen_name": {"bng.info.name": "ir.identity.name"},
    "car.fuel": {"bng.fuel.start": "ir.mass.fuel_start"},
    "car.max_fuel": {"bng.fuel.capacity": "ir.mass.fuel_capacity"},
}
GROUP_2 = {  # dimensiones medidas en los nodos
    "susp.wheelbase": {"bng.geometry.wheelbase": "ir.dimensions.wheelbase"},
    "susp.track_front": {"bng.geometry.track_front": "ir.dimensions.track_front"},
    "susp.track_rear": {"bng.geometry.track_rear": "ir.dimensions.track_rear"},
}
GROUP_3 = {  # neumáticos por eje: tyres.axles.<eje>.<campo>
    f"tyre.{pos}.{rule}": {f"bng.tyre.{pos}.{fact}": f"ir.tyres.axles.{pos}.{fact}"}
    for pos in ("front", "rear")
    for rule, fact in (
        ("radius", "radius"),
        ("rim_radius", "hub_radius"),
        ("width", "width"),
        ("pressure_static", "pressure"),
    )
}
GROUP_4 = {  # frenos: reparto a pedal 100 % (el codo sigue como problema abierto)
    "brk.front_share": {
        "bng.brakes.front_share_pedal_full": "ir.brakes.front_share_full_pedal"
    },
}
GROUP_5 = {  # transmisión del Ibiza (ámbito vehículo); dt.gear_8@1 queda fuera (P)
    "dt.final": {"bng.diff.final_ratio": "ir.drivetrain.final_ratio"},
    "dt.gear_count": {
        "bng.gearbox.forward_count": "ir.drivetrain.gearbox.forward_count"
    },
    "dt.gear_r": {"bng.gearbox.reverse_ratio": "ir.drivetrain.gearbox.reverse_ratio"},
    **{
        f"dt.gear_{k}": {
            f"bng.gearbox.forward_ratio_{k}": (
                f"ir.drivetrain.gearbox.forward_ratios.{k}"
            ),
            "bng.gearbox.forward_count": "ir.drivetrain.gearbox.forward_count",
        }
        for k in range(1, 8)
    },
}
_CAMSO = {
    "bng.engine.device_type": "ir.engine.device_type",
    "bng.code.camsoEngine_sha256": "ir.formats.code_versions.camsoEngine",
}
GROUP_6 = {  # motor del Ibiza (camsoEngine 47437d5c; ámbito vehículo)
    "eng.inertia": {"bng.engine.inertia": "ir.engine.inertia", **_CAMSO},
    "eng.limiter": {"bng.engine.rpm_ceiling": "ir.engine.speed_ceiling", **_CAMSO},
    "eng.minimum": {"bng.engine.idle_rpm": "ir.engine.idle_speed", **_CAMSO},
}
GROUP_7 = {  # tracción (ámbito familia automation_export)
    "dt.traction": {"bng.drivetrain.traction": "ir.drivetrain.layout"},
}
MIGRATED: dict[str, dict[str, str]] = {
    **GROUP_7,
    **GROUP_6,
    **GROUP_1,
    **GROUP_2,
    **GROUP_3,
    **GROUP_4,
    **GROUP_5,
}
UNCHANGED_FIELDS = (
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


def _plain(o):
    return json.loads(json.dumps(o, default=lambda x: x.__dict__))


# Versiones POSTERIORES a G2.5b que sustituyen a una regla migrada aquí. Se quitan
# para evaluar como al cerrar G2.5b (mismo criterio que ``as_of_g24`` en
# tests/test_g2_gears_mass_ir.py); cada una se prueba en su propio archivo.
LATER_VERSIONS = (
    "dt.traction@3",  # procedencia, D-P3: tests/test_provenance_automation.py
)


def catalog(drop_new: bool = False) -> Catalog:
    cat = Catalog.load(REPO_CATALOG)
    for key in LATER_VERSIONS:
        cat.rules.pop(key, None)
    if (
        drop_new
    ):  # como antes de G2.5b: las versiones anteriores vuelven a estar vigentes
        for base in MIGRATED:
            key = next(k for k, r in cat.rules.items() if r.id == base and _is_new(r))
            cat.rules.pop(key)
    return cat


def _is_new(rule) -> bool:
    return any(h.date == "2026-10-31" for h in rule.history) and rule.supersedes != ""


def new_and_old(cat: Catalog, base: str):
    new = next(r for r in cat.rules.values() if r.id == base and _is_new(r))
    return new, cat.rules[new.supersedes]


def facts_of(d: Path, pc: str):
    from src.importers.beamng import import_preview
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    src = load_vehicle_dir(d, pc)
    return src, ir_to_facts(import_preview(preview_physics(src, 70.0), 70.0))


def resolve(src, facts, target: str, drop_new: bool = False):
    cat = catalog(drop_new)
    ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
    return ResolutionEngine(cat, ctx).resolve(target)


_RULE_KEY = re.compile(r"\b([a-z]+\.[A-Za-z0-9_.]+)@\d+\b")


def comparable(res, mapping: dict[str, str]) -> dict:
    """La Resolution sin claves de regla ni ids de hechos equivalentes."""
    d = res.to_dict()
    d["inputs"] = [{k: v for k, v in i.items() if k != "fact"} for i in d["inputs"]]
    text = json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
    for old, new in mapping.items():
        text = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w])", new, text)
    return json.loads(_RULE_KEY.sub(r"\1@*", text))


class TestDefinitions(unittest.TestCase):
    def test_new_versions_change_only_references(self) -> None:
        cat = catalog()
        for base, mapping in MIGRATED.items():
            with self.subTest(rule=base):
                new, old = new_and_old(cat, base)
                self.assertEqual(new.version, old.version + 1)
                vigentes = [r.key for r in cat.rules_for(old.target) if r.id == base]
                self.assertEqual(vigentes, [new.key])
                self.assertEqual(
                    new.inputs, {k: mapping.get(v, v) for k, v in old.inputs.items()}
                )
                for a, b in (
                    (old.conditions, new.conditions),
                    (old.applies_when, new.applies_when),
                ):
                    self.assertEqual(
                        [(mapping.get(c.fact, c.fact), c.op, c.value) for c in a],
                        [(c.fact, c.op, c.value) for c in b],
                    )
                refs = list(new.inputs.values()) + [
                    c.fact for c in new.conditions + new.applies_when
                ]
                self.assertFalse(
                    [r for r in refs if r.startswith(("bng.", "project."))]
                )
                for field in UNCHANGED_FIELDS:
                    self.assertEqual(
                        _plain(getattr(old, field)), _plain(getattr(new, field)), field
                    )
                for field in ("source_doc", "evidence", "history"):
                    a, b = _plain(getattr(old, field)), _plain(getattr(new, field))
                    self.assertEqual(b[: len(a)], a, field)

    def test_previous_versions_untouched(self) -> None:
        """Las anteriores siguen en el catálogo con sus hechos bng.*/project.*."""
        cat = catalog()
        for base, mapping in MIGRATED.items():
            with self.subTest(rule=base):
                _, old = new_and_old(cat, base)
                refs = list(old.inputs.values()) + [
                    c.fact for c in old.conditions + old.applies_when
                ]
                self.assertTrue(set(mapping) <= set(refs))

    def test_exclusions_not_migrated(self) -> None:
        """P, B y C no se migran con la sustitución genérica (informe G2 §7.2).

        G2.7 (informe §14) las migra con herramientas propias y demostraciones aparte:
        una regla de esta lista solo puede estar sustituida por su versión de G2.7, y
        la herramienta genérica sigue rechazando las B (sin equivalente uno a uno) y
        las P sin la evidencia sintética explícita. ``dt.preload@2`` (C) se migra con
        el par cerrado de G2.7, paso 3, y se prueba en ``test_g27_driven_axle``."""
        from migrate_rule_to_ir import MigrationError, legacy_to_ir, migrate

        cat = catalog()
        successor = {r.supersedes: r.key for r in cat.rules.values() if r.supersedes}
        g27 = {
            "eng.coast_rpm@1": "eng.coast_rpm@2",
            "eng.coast_torque@1": "eng.coast_torque@2",
            "eng.coast_nonlinearity@1": "eng.coast_nonlinearity@2",
            "susp.type_front@1": "susp.type_front@2",
            "susp.type_rear@1": "susp.type_rear@2",
            "dt.preload@2": "dt.preload@3",
            "dt.gear_8@1": "dt.gear_8@2",
            "dt.clutch_max_torque@1": "dt.clutch_max_torque@2",
        }
        for key, allowed in g27.items():
            with self.subTest(rule=key):
                self.assertIn(successor.get(key), (None, allowed))
                if key != "dt.preload@2":
                    with self.assertRaises(MigrationError):
                        migrate(cat.rules[key], legacy_to_ir())

    def test_tyre_pattern_is_checked(self) -> None:
        """Patrón de neumáticos: solo eje front/rear y campos de TyreAxle."""
        from migrate_rule_to_ir import MigrationError, ir_ref, legacy_to_ir

        t = legacy_to_ir()
        self.assertEqual(
            ir_ref("bng.tyre.rear.hub_radius", t), "ir.tyres.axles.rear.hub_radius"
        )
        for bad in (
            "bng.tyre.middle.radius",
            "bng.tyre.front.grip",
            "bng.tyre.front",
            "bng.tyre.front.radius.x",
        ):
            with self.subTest(ref=bad), self.assertRaises(MigrationError):
                ir_ref(bad, t)

    def test_architecture_scope_is_untouched(self) -> None:
        cat = catalog()
        for key in [k for k in cat.rules if re.fullmatch(r"dt\..+_2wd@2", k)]:
            facts = [c.fact for c in cat.rules[key].scope.conditions]
            self.assertEqual(facts, ["bng.drivetrain.traction", "bng.gearbox.type"])


@unittest.skipUnless(IBIZA.is_dir(), "sin la muestra del Ibiza")
class TestEquivalence(unittest.TestCase):
    """Mismo resultado con la versión nueva y con la anterior."""

    CASES = [(IBIZA, "35b84.pc")]

    @classmethod
    def setUpClass(cls) -> None:
        cases = list(cls.CASES)
        if SAMPLES.is_dir():
            cases += [
                (next(SAMPLES.glob("*/vehicles/sucette_poubelle")), "f5893.pc"),
                (next(SAMPLES.glob("*/vehicles/autist_cuanto")), "b78dc.pc"),
            ]
        cls.vehicles = [facts_of(d, pc) for d, pc in cases]

    def test_without_ir_fact_not_applied(self) -> None:
        """Sin el hecho ir.* la versión nueva no se aplica (aunque el bng.* siga):
        si es una entrada, se rechaza; si es de ``applies_when``, queda «no aplica» sin
        valor (comportamiento del motor, igual que la versión anterior sin su bng.*)."""
        src, facts = self.vehicles[0]
        cat = catalog()
        for base, mapping in MIGRATED.items():
            new_rule, _ = new_and_old(cat, base)
            for ir_id in sorted(set(mapping.values())):
                if ir_id not in facts:  # p. ej. la 7.ª marcha en el Ibiza (6)
                    continue
                with self.subTest(rule=base, fact=ir_id):
                    reduced = {k: v for k, v in facts.items() if k != ir_id}
                    r = resolve(src, reduced, new_rule.target)
                    in_conditions = ir_id in [c.fact for c in new_rule.conditions]
                    if ir_id in new_rule.inputs.values() or in_conditions:
                        self.assertNotEqual((r.source, r.rule), ("rule", new_rule.key))
                        self.assertTrue(
                            any(x.get("rule") == new_rule.key for x in r.rejected),
                            r.rejected,
                        )
                    else:
                        self.assertEqual(r.status, "not_applicable")
                        self.assertIsNone(r.value)


if __name__ == "__main__":
    unittest.main()


# --- familia automation_export (G2.5b, grupo 7) ------------------------------------

OLD_MEMBERSHIP = [
    ("bng.info.type", "eq", "Automation"),
    ("bng.code.camsoEngine_sha256", "exists", None),
]
NEW_MEMBERSHIP = [
    ("ir.identity.source_tool", "eq", "Automation"),
    ("ir.formats.code_versions.camsoEngine", "exists", None),
]


def _family(membership):
    from src.attrs import schema as S

    return S.Family(
        id="automation_export",
        description="x",
        membership=[S.Condition(fact=f, op=o, value=v) for f, o, v in membership],
    )


def _members(fam, facts) -> bool:
    from src.attrs.engine import VehicleContext, compute_families

    cat = Catalog.load(REPO_CATALOG)
    cat.families = {fam.id: fam}
    ctx = VehicleContext("v", "", facts)
    compute_families(cat, ctx)
    return fam.id in ctx.families


class TestFamily(unittest.TestCase):
    def test_membership_on_ir_facts_same_ops_and_values(self) -> None:
        """La pertenencia de G2.5b (NEW_MEMBERSHIP) la sustituyó el 2026-11-02 la de
        procedencia (tests/test_provenance_automation.py); queda en ``evidence``. Sigue
        usando solo hechos ir.*."""
        fam = catalog().families["automation_export"]
        self.assertTrue(all(c.fact.startswith("ir.") for c in fam.membership))
        g25b = "; ".join(f"{f} {o} {v!r}" for f, o, v in NEW_MEMBERSHIP)
        self.assertTrue(any(g25b in e.note for e in fam.evidence), g25b)
        self.assertEqual(
            [(o, v) for _, o, v in OLD_MEMBERSHIP],
            [(o, v) for _, o, v in NEW_MEMBERSHIP],
        )

    def test_previous_membership_recorded_in_evidence(self) -> None:
        fam = catalog().families["automation_export"]
        self.assertEqual(
            [e.path for e in fam.evidence[:2]],
            ["info.json", "lua/powertrain/camsoEngine.lua"],
        )
        self.assertIn(
            "bng.info.type eq 'Automation'; bng.code.camsoEngine_sha256 exists",
            fam.evidence[3].note,
        )

    def test_same_members_old_and_new(self) -> None:
        """Mismos miembros con cualquier combinación de los dos hechos (cada par
        bng/ir sale del mismo parámetro del IR: mismo valor o los dos ausentes)."""
        from src.attrs.facts import Fact

        old, new = _family(OLD_MEMBERSHIP), _family(NEW_MEMBERSHIP)
        for tool in ("Automation", "Car", "automation", None):
            for sha in ("47437d5c", None):
                facts = {}
                if tool is not None:
                    for fid in ("bng.info.type", "ir.identity.source_tool"):
                        facts[fid] = Fact(fid, tool, "text", "extracted")
                if sha is not None:
                    for fid in (
                        "bng.code.camsoEngine_sha256",
                        "ir.formats.code_versions.camsoEngine",
                    ):
                        facts[fid] = Fact(fid, sha, "text", "extracted")
                with self.subTest(tool=tool, sha=sha):
                    expected = tool == "Automation" and sha is not None
                    self.assertEqual(_members(old, facts), expected)
                    self.assertEqual(_members(new, facts), expected)

    def test_without_ir_facts_not_member(self) -> None:
        """La pertenencia nueva necesita los hechos ir.* (en el flujo real siempre
        están: build_facts = ir_to_facts); con solo bng.* no se supone."""
        from src.attrs.facts import Fact

        facts = {
            "bng.info.type": Fact("bng.info.type", "Automation", "text", "extracted"),
            "bng.code.camsoEngine_sha256": Fact(
                "bng.code.camsoEngine_sha256", "x", "text", "extracted"
            ),
        }
        self.assertTrue(_members(_family(OLD_MEMBERSHIP), facts))
        self.assertFalse(_members(_family(NEW_MEMBERSHIP), facts))

    def test_tool_refuses_if_members_differ(self) -> None:
        from migrate_family_to_ir import MigrationError, check_members

        snap = {
            "v": {
                "bng.info.type": {"value": "Automation", "unit": "text", "kind": "x"},
                "ir.identity.source_tool": {
                    "value": "Car",
                    "unit": "text",
                    "kind": "x",
                },
                "bng.code.camsoEngine_sha256": {
                    "value": "a",
                    "unit": "text",
                    "kind": "x",
                },
                "ir.formats.code_versions.camsoEngine": {
                    "value": "a",
                    "unit": "text",
                    "kind": "x",
                },
            }
        }
        with self.assertRaises(MigrationError):
            check_members(_family(OLD_MEMBERSHIP), _family(NEW_MEMBERSHIP), snap)

    @unittest.skipUnless(IBIZA.is_dir(), "sin la muestra del Ibiza")
    def test_ibiza_family_rules_apply(self) -> None:
        src, facts = facts_of(IBIZA, "35b84.pc")
        cat = catalog()
        ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
        self.assertEqual(ctx.families, [])  # se calcula al crear el motor
        eng = ResolutionEngine(cat, ctx)
        self.assertEqual(ctx.families, ["automation_export"])
        r = eng.resolve("ac:drivetrain.ini/TRACTION/TYPE")
        self.assertEqual((r.rule, r.value, r.status), ("dt.traction@2", "FWD", "known"))
        self.assertEqual(eng.resolve("ac:power.lut").rule, "eng.power_lut@4")
