"""G2.7, paso 5 (G2.6c, decisión D-G26c): evidencia sintética para las reglas P.

Ningún vehículo local recorre la rama «presente» de ``bng.gearbox.forward_ratio_8`` ni
de ``bng.clutch.lock_torque`` (informe G2 §13.5). Aquí se construye: una COPIA del Ibiza
(solo JBeam, .pc, Lua y JSON; en una carpeta temporal, nunca en ``sample_*``) con UN
cambio por variante en ``35b84/camso_transmission_35b84.jbeam`` (la caja activa):

* ``8 marchas``: dos relaciones más al final de ``gearRatios`` (7.ª y 8.ª);
* ``lockTorque``: ``"lockTorque": 432.1`` en la sección ``clutch``.

Cada variante atraviesa el flujo real: ``load_vehicle_dir`` → ``preview_physics`` →
``import_preview`` → ``ir_to_facts`` → motor de reglas. Se comprueba:

* los hechos ``bng.*`` y ``ir.*`` del par son idénticos (valor, unidad, clase, nota,
  procedencia, clave de origen) en la rama presente y en la ausente;
* la copia sin cambios da los mismos hechos que el Ibiza original (salvo la ruta);
* la regla migrada en memoria con la herramienta (``migrate_rule_to_ir``) resuelve
  igual que la @1 (valor, estado, confianza, aplicabilidad, rechazos) en las tres
  variantes y en el original; y, si ya existe en el catálogo, es esa misma.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.importers.beamng import import_preview
from src.ir.legacy_facts import ir_to_facts
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
GEARBOX = "35b84/camso_transmission_35b84.jbeam"  # caja activa de 35b84.pc
SKIP = {".dds", ".dae", ".png", ".cdae", ".jpg"}
GEAR8 = "ac:drivetrain.ini/GEARS/GEAR_8"
GEAR7 = "ac:drivetrain.ini/GEARS/GEAR_7"
CLUTCH = "ac:drivetrain.ini/CLUTCH/MAX_TORQUE"
PAIRS = {
    "bng.gearbox.forward_ratio_8": "ir.drivetrain.gearbox.forward_ratios.8",
    "bng.gearbox.forward_ratio_7": "ir.drivetrain.gearbox.forward_ratios.7",
    "bng.gearbox.forward_count": "ir.drivetrain.gearbox.forward_count",
    "bng.clutch.lock_torque": "ir.drivetrain.clutch.lock_torque",
    "bng.clutch.lock_torque_default": "ir.drivetrain.clutch.lock_torque_default",
    "bng.engine.device_type": "ir.engine.device_type",
}
OLD_RATIOS = '"$gear_5_35b84", "$gear_6_35b84"]'
NEW_RATIOS = '"$gear_5_35b84", "$gear_6_35b84", 0.71, 0.62]'
OLD_CLUTCH = '"clutch": {\n\t\t\t//"thermalsEnabled": true'
NEW_CLUTCH = '"clutch": {\n\t\t\t"lockTorque": 432.1,\n\t\t\t//"thermalsEnabled": true'
VARIANTS = {
    "copia": [],
    "8 marchas": [(OLD_RATIOS, NEW_RATIOS)],
    "lockTorque": [(OLD_CLUTCH, NEW_CLUTCH)],
}


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "migrate_rule_to_ir", ROOT / "tools/catalog/migrate_rule_to_ir.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _facts_dict(facts: dict, root: Path) -> dict:
    """Hechos como diccionarios, con la ruta del vehículo normalizada."""
    text = json.dumps({k: f.to_dict() for k, f in sorted(facts.items())})
    for form in {str(root), str(root).replace("\\", "\\\\")}:
        text = text.replace(form.replace("\\", "\\\\"), "<VEH>").replace(form, "<VEH>")
    return json.loads(text)


def _fact_body(facts: dict, fid: str) -> dict | None:
    f = facts.get(fid)
    if f is None:
        return None
    d = f.to_dict()
    d.pop("id")
    return d


class SyntheticIbiza(unittest.TestCase):
    tmp: Path
    data: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="beam2ac_g27_p_"))
        cls.data = {}
        for name, edits in [("original", None), *VARIANTS.items()]:
            if edits is None:
                root = IBIZA
            else:
                root = cls.tmp / name.replace(" ", "_") / "seat_ibiza_acfr"
                for p in IBIZA.rglob("*"):
                    if p.is_file() and p.suffix.lower() not in SKIP:
                        dst = root / p.relative_to(IBIZA)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(p, dst)
                gb = root / GEARBOX
                text = gb.read_text(encoding="utf-8").replace("\r\n", "\n")
                for old, new in edits:
                    n = text.count(old)
                    assert n >= 1, (name, old)  # el cambio tiene que aplicarse
                    text = text.replace(old, new)
                gb.write_text(text, encoding="utf-8")
            pv = preview_physics(load_vehicle_dir(root, "35b84.pc"), 70.0)
            ir = import_preview(pv, 70.0)
            cls.data[name] = (root, pv, ir, ir_to_facts(ir))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --- el cambio llega al IR y a los hechos ----------------------------------------
    def test_copy_without_edits_equals_original(self) -> None:
        r0, _pv0, _ir0, f0 = self.data["original"]
        r1, _pv1, _ir1, f1 = self.data["copia"]
        a, b = _facts_dict(f0, r0), _facts_dict(f1, r1)
        # la copia no lleva texturas ni mallas: solo cambian los hechos del inventario
        # de recursos (ninguno lo usa una regla P)
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        self.assertFalse([k for k in diff if not k.startswith("ir.resources")], diff)

    def test_eight_gears_reach_the_ir(self) -> None:
        _r, pv, ir, facts = self.data["8 marchas"]
        self.assertEqual(len(pv.drivetrain.drivetrain.forward), 8)
        self.assertEqual(ir.drivetrain.gearbox.forward_ratios[7].value, 0.62)
        self.assertEqual(facts["bng.gearbox.forward_ratio_8"].value, 0.62)
        self.assertEqual(facts["bng.gearbox.forward_count"].value, 8)
        self.assertNotIn("bng.gearbox.forward_ratio_8", self.data["copia"][3])

    def test_lock_torque_reaches_the_ir(self) -> None:
        _r, _pv, ir, facts = self.data["lockTorque"]
        self.assertEqual(ir.drivetrain.clutch.lock_torque.value, 432.1)
        self.assertEqual(facts["bng.clutch.lock_torque"].value, 432.1)
        # con lockTorque declarado no se calcula el valor por defecto (no se inventa)
        self.assertEqual(ir.drivetrain.clutch.lock_torque_default.status, "missing")
        self.assertNotIn("bng.clutch.lock_torque_default", facts)
        self.assertIn("bng.clutch.lock_torque_default", self.data["copia"][3])
        self.assertNotIn("bng.clutch.lock_torque", self.data["copia"][3])

    def test_legacy_and_ir_facts_are_identical(self) -> None:
        for name, (_r, _pv, _ir, facts) in self.data.items():
            for bng, irf in PAIRS.items():
                with self.subTest(name, fact=bng):
                    self.assertEqual(_fact_body(facts, bng), _fact_body(facts, irf))

    # --- reglas: @1 frente a la migración --------------------------------------------
    def _migrated(self, key: str) -> S.Rule:
        mod = load_tool()
        cat = Catalog.load(REPO_CATALOG)
        new = mod.migrate(
            cat.rules[key], mod.legacy_to_ir(), "2026-11-01", accept_synthetic=True
        )
        stored = cat.rules.get(new.key)
        if stored is not None:  # ya escrita: debe ser exactamente la migración
            self.assertEqual(S.to_dict(stored), S.to_dict(new))
        return new

    def _resolve(self, facts: dict, target: str, rules: dict) -> object:
        cat = Catalog.load(REPO_CATALOG)
        for k in [
            k for k in cat.rules if k.split("@")[0] in {r.id for r in rules.values()}
        ]:
            cat.rules.pop(k)
        cat.rules.update(rules)
        # las decisiones del Ibiza prevalecen sobre las reglas: aquí se comparan
        # solo las reglas
        cat.decisions = [d for d in cat.decisions if d.target != target]
        ctx = context_from_facts("seat_ibiza_acfr", "35b84", facts, cat)
        return ResolutionEngine(cat, ctx).resolve(target)

    def _compare(self, key: str, target: str) -> dict:
        v1 = Catalog.load(REPO_CATALOG).rules[key]
        v2 = self._migrated(key)
        out = {}
        for name, (_r, _pv, _ir, facts) in self.data.items():
            a = self._resolve(facts, target, {v1.key: copy.deepcopy(v1)})
            b = self._resolve(facts, target, {v2.key: v2})
            with self.subTest(name, rule=key):
                for f in (
                    "value",
                    "unit",
                    "status",
                    "confidence",
                    "source",
                    "assumptions",
                    "open_issues",
                    "requires_approval",
                    "confidence_criteria",
                ):
                    self.assertEqual(getattr(a, f), getattr(b, f), f)
                self.assertEqual(
                    [(x["rule"].split("@")[0]) for x in a.rejected],
                    [(x["rule"].split("@")[0]) for x in b.rejected],
                )
            out[name] = (a, b)
        return out

    def test_gear_8(self) -> None:
        out = self._compare("dt.gear_8@1", GEAR8)
        a, b = out["8 marchas"]
        self.assertEqual((a.value, b.value), (0.62, 0.62))
        self.assertEqual(b.rule, "dt.gear_8@2")
        self.assertEqual(b.inputs[0]["fact"], "ir.drivetrain.gearbox.forward_ratios.8")
        a, b = out["copia"]  # 6 marchas: no aplica en las dos
        self.assertEqual((a.status, b.status), ("not_applicable", "not_applicable"))

    def test_clutch_max_torque(self) -> None:
        out = self._compare("dt.clutch_max_torque@1", CLUTCH)
        a, b = out["copia"]  # sin lockTorque: valor por defecto de frictionClutch.lua
        self.assertIsNotNone(b.value)
        self.assertEqual(b.rule, "dt.clutch_max_torque@2")
        a, b = out["lockTorque"]  # declarado: la regla no se aplica en ninguna
        self.assertNotEqual(a.rule, "dt.clutch_max_torque@1")
        self.assertNotEqual(b.rule, "dt.clutch_max_torque@2")

    def test_gear_7_unchanged_by_eight_gears(self) -> None:
        _r, _pv, _ir, facts = self.data["8 marchas"]
        self.assertIn("bng.gearbox.forward_ratio_7", facts)
        cat = Catalog.load(REPO_CATALOG)
        ctx = context_from_facts("seat_ibiza_acfr", "35b84", facts, cat)
        self.assertEqual(ResolutionEngine(cat, ctx).resolve(GEAR7).value, 0.71)


if __name__ == "__main__":
    unittest.main()
