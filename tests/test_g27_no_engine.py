"""G2.7, paso 4 (G2.6d): tracción y transmisión sin el modelo de motor Camso.

* ``src.extract.traction.analyse_source_drivetrain``: grafo, tracción y
  ``vehicleController`` sin motor.
* ``src.ac.drivetrain.analyse(..., build_engine=False)`` sin motor: sin velocidades,
  ``rpm_ceiling = None``, sin comprobaciones de velocidad; el borrador
  ``drivetrain.ini`` es el mismo que con motor.
* El CLI y la llamada por defecto siguen construyendo el modelo Camso.
* Vista previa con un motor que no se puede modelar (Ibiza con el adaptador Camso
  forzado a fallar, como un ``combustionEngine``): la etapa ``drivetrain`` se ejecuta,
  el IR tiene tracción y marchas, no hay ``lock_torque_default`` y las reglas de marchas
  por arquitectura se aplican; ``dt.clutch_max_torque`` no (condición Camso).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from src.ac import drivetrain as ac_dt
from src.attrs.engine import ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract.mass_geometry import compute_mass_geometry
from src.extract.traction import analyse_source_drivetrain
from src.importers.beamng import import_preview
from src.ir.legacy_facts import ir_to_facts
from src.pipeline import physics_preview as PP

from .jbeam_fixtures import resolve_parts
from .test_drivetrain import ENGINE, rwd_car

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
INFO_TOP = {"Top Speed": 60.0, "Drivetrain": "RWD", "Transmission": "Manual"}


class TestSourceDrivetrain(unittest.TestCase):
    def test_without_engine(self) -> None:
        r = resolve_parts(rwd_car())
        geo = compute_mass_geometry(r)
        src = analyse_source_drivetrain(r, geo)
        self.assertEqual(src.layout.layout, "RWD")
        self.assertEqual(
            src.drivetrain.forward, ac_dt.analyse(r, geo, ENGINE).drivetrain.forward
        )

    def test_analyse_without_engine(self) -> None:
        r = resolve_parts(rwd_car())
        geo = compute_mass_geometry(r)
        with_engine = ac_dt.analyse(r, geo, ENGINE, INFO_TOP)
        without = ac_dt.analyse(r, geo, None, INFO_TOP, build_engine=False)
        self.assertEqual(without.speeds, [])
        self.assertIsNone(without.rpm_ceiling)
        self.assertEqual(
            [c.quantity for c in without.checks], ["Drivetrain", "Transmission"]
        )  # sin «Top Speed»: no hay velocidades con las que comparar
        self.assertIn("Top Speed", [c.quantity for c in with_engine.checks])
        for f in ("traction", "driven_radius", "shift_controller_raw", "awd", "layout"):
            self.assertEqual(getattr(with_engine, f), getattr(without, f), f)
        self.assertEqual(
            ac_dt.render_drivetrain_ini(with_engine),
            ac_dt.render_drivetrain_ini(without),
        )
        rep = ac_dt.drivetrain_report(without)
        self.assertIsNone(rep["derived"]["rpm_ceiling"])
        self.assertEqual(rep["derived"]["gear_speeds"], [])

    def test_default_still_builds_the_engine(self) -> None:
        r = resolve_parts(rwd_car())
        geo = compute_mass_geometry(r)
        with mock.patch.object(ac_dt, "build_engine_model", return_value=ENGINE) as m:
            res = ac_dt.analyse(r, geo)
        m.assert_called_once()
        self.assertEqual(res.rpm_ceiling, 6000.0)
        self.assertTrue(res.speeds)
        with mock.patch.object(ac_dt, "build_engine_model") as m:
            ac_dt.analyse(r, geo, build_engine=False)
        m.assert_not_called()

    def test_graph_error_before_engine_error(self) -> None:
        """Mismo orden que antes: un grafo no soportado falla antes que el motor."""
        parts = rwd_car()
        pt = parts["car"]["powertrain"]
        assert pt[2][0] == "frictionClutch"
        pt[2][0] = "torqueConverter"  # no soportado en el camino motriz
        r = resolve_parts(parts)
        with mock.patch.object(
            ac_dt, "build_engine_model", side_effect=AssertionError("no debía llamarse")
        ):
            with self.assertRaises(Exception) as cm:
                ac_dt.analyse(r, compute_mass_geometry(r))
        self.assertNotIsInstance(cm.exception, AssertionError)


def _no_camso(*_a, **_k):
    raise ValueError("motor 'combustionEngine': adaptador no disponible (prueba)")


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestPreviewWithoutEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        src = PP.load_vehicle_dir(IBIZA, "35b84.pc")
        cls.ref = PP.preview_physics(src, 70.0)
        with mock.patch.object(PP, "build_engine_model", _no_camso):
            cls.pv = PP.preview_physics(PP.load_vehicle_dir(IBIZA, "35b84.pc"), 70.0)
        cls.ir = import_preview(cls.pv, 70.0)
        cls.facts = ir_to_facts(cls.ir)

    def test_stage_runs_without_engine(self) -> None:
        self.assertIsNone(self.pv.engine)
        self.assertFalse(self.pv.stages["engine_model"]["ok"])
        self.assertTrue(self.pv.stages["drivetrain"]["ok"])
        self.assertEqual(self.pv.drivetrain.speeds, [])
        self.assertIsNone(self.pv.drivetrain.rpm_ceiling)
        self.assertEqual(
            self.pv.rendered["drivetrain.ini"], self.ref.rendered["drivetrain.ini"]
        )
        self.assertNotIn("engine.ini", self.pv.rendered)

    def test_ir_has_traction_and_gears_but_no_engine_values(self) -> None:
        dtr = self.ir.drivetrain
        self.assertEqual(dtr.layout.value, "FWD")
        self.assertEqual(dtr.gearbox.forward_count.value, 6)
        self.assertEqual(dtr.driven_axle.value, "front")
        self.assertEqual(dtr.clutch.lock_torque_default.status, "missing")
        self.assertNotIn("ir.drivetrain.clutch.lock_torque_default", self.facts)
        self.assertNotIn("ir.engine.speed_ceiling", self.facts)
        ref_facts = ir_to_facts(import_preview(self.ref, 70.0))
        for k in [k for k in ref_facts if k.startswith("ir.drivetrain.")]:
            if k != "ir.drivetrain.clutch.lock_torque_default":
                self.assertEqual(self.facts[k].to_dict(), ref_facts[k].to_dict(), k)

    def test_gear_rules_apply_and_clutch_rule_does_not(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        ctx = context_from_facts("otro_vehiculo", "v", self.facts, cat)
        eng = ResolutionEngine(cat, ctx)
        g1 = eng.resolve("ac:drivetrain.ini/GEARS/GEAR_1")
        self.assertEqual(g1.rule, "dt.gear_1_2wd@3")
        self.assertIsNotNone(g1.value)
        cl = eng.resolve("ac:drivetrain.ini/CLUTCH/MAX_TORQUE")
        self.assertIsNone(cl.value)


if __name__ == "__main__":
    unittest.main()
