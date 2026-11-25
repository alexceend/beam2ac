"""G2.5c: tracción y diferenciales en ``src.extract.traction`` y su paso al IR.

Casos: FWD, RWD, AWD con diferencial central, RWD con diferencial trasero
``torqueVectoring``; datos ausentes, estructuras desconocidas y configuraciones
ambiguas; compatibilidad de ``src.ac.drivetrain`` (mismo resultado que antes) y capas
(la lógica de origen ya no está en ``src/ac``)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.ac import drivetrain as ac_dt
from src.extract.drivetrain import DrivetrainDataError, build_drivetrain
from src.extract.mass_geometry import compute_mass_geometry
from src.extract.traction import (
    DIFF_FIELDS,
    DiffSetup,
    awd_axle_roles,
    classify_traction,
    traction_type,
)
from src.importers.beamng import _import_drivetrain
from src.ir.legacy_facts import ir_facts, ir_to_facts
from src.ir.param import Param
from src.ir.vehicle import VehicleIR

from .jbeam_fixtures import resolve_parts
from .test_drivetrain import ENGINE, rwd_car
from .test_drivetrain_awd import awd_car

ROOT = Path(__file__).resolve().parents[1]


def fwd_car(diff_opts=None):
    """rwd_car() reflejado: el diferencial mueve FL/FR; RL cuelga de un semieje
    inexistente (no motriz)."""
    parts = rwd_car(diff_row_opts=diff_opts)
    pt = parts["car"]["powertrain"]
    for row in pt[1:]:
        if row[1] == "rearDiff":
            row[1] = "frontDiff"
        if len(row) > 2 and row[2] == "rearDiff":
            row[2] = "frontDiff"
    pt[6][1], pt[6][4] = "axleFL", {"connectedWheel": "FL"}
    pt[7][1], pt[7][4] = "axleFR", {"connectedWheel": "FR"}
    pt[8][1], pt[8][2], pt[8][4] = "axleRL", "halfshaftRL", {"connectedWheel": "RL"}
    parts["car"]["frontDiff"] = parts["car"].pop("rearDiff")
    return parts


def layout_for(parts):
    r = resolve_parts(parts)
    return classify_traction(build_drivetrain(r), compute_mass_geometry(r))


def result_for(parts):
    r = resolve_parts(parts)
    return ac_dt.analyse(r, compute_mass_geometry(r), ENGINE)


def ir_for(parts) -> VehicleIR:
    ir = VehicleIR()

    def put(path, value, unit, kind, *refs, note="", key=""):
        ir.set(path, Param.of(value, unit, kind, list(refs), note, key))

    _import_drivetrain(ir, SimpleNamespace(drivetrain=result_for(parts)), put, None)
    return ir


class TestClassification(unittest.TestCase):
    def test_fwd(self):
        lay = layout_for(fwd_car())
        self.assertEqual(lay.layout, "FWD")
        self.assertEqual(lay.driven_wheels, ("FL", "FR"))
        self.assertEqual(sorted(lay.axles), ["front"])
        ax = lay.axles["front"]
        self.assertEqual(
            (ax.diff.device, ax.final, ax.wheels), ("frontDiff", 4.0, ("FL", "FR"))
        )
        self.assertEqual(
            ax.diff.values, {"kind": "lsd", "preload": 50, "lock_coef": 0.2}
        )
        self.assertIsNone(ax.torque_share)
        self.assertIsNone(lay.center)
        self.assertIsNone(lay.front_torque_share)
        self.assertIsNone(lay.awd_summary())

    def test_rwd(self):
        lay = layout_for(rwd_car())
        self.assertEqual(lay.layout, "RWD")
        self.assertEqual(sorted(lay.axles), ["rear"])
        self.assertEqual(lay.axles["rear"].diff.device, "rearDiff")
        self.assertEqual(
            lay.driven_radius,
            compute_mass_geometry(resolve_parts(rwd_car())).wheels[0].radius,
        )

    def test_awd_with_center(self):
        lay = layout_for(awd_car({"diffType": "lsd", "diffTorqueSplit": 0.45}))
        self.assertEqual(lay.layout, "AWD")
        self.assertEqual(lay.center.diff.device, "centerDiff")
        self.assertEqual(lay.center.split_source, "diffTorqueSplit")
        rear, front = lay.axles["rear"], lay.axles["front"]
        self.assertEqual((rear.center_output, front.center_output), (1, 2))
        self.assertAlmostEqual(rear.torque_share, 0.45)
        self.assertAlmostEqual(front.torque_share, 0.55)
        self.assertAlmostEqual(lay.front_torque_share, 0.55)
        self.assertEqual(front.diff.get("kind"), "open")

    def test_rwd_with_torque_vectoring_is_not_awd(self):
        lay = layout_for(rwd_car(diff_row_opts={"diffType": "torqueVectoring"}))
        self.assertEqual(lay.layout, "RWD")
        self.assertIsNone(lay.center)
        self.assertEqual(sorted(lay.axles), ["rear"])
        self.assertEqual(lay.axles["rear"].diff.values, {"kind": "torqueVectoring"})


class TestMissingUnknownAmbiguous(unittest.TestCase):
    def test_absent_fields_are_not_invented(self):
        lay = layout_for(rwd_car(diff_row_opts={"lsdLockCoef": 0.2}))
        d = lay.axles["rear"].diff
        self.assertFalse(d.has("kind"))
        self.assertIsNone(d.get("kind"))
        self.assertEqual(d.values, {"lock_coef": 0.2})

    def test_absent_split_uses_documented_default(self):
        lay = layout_for(awd_car({"diffType": "lsd"}))
        self.assertEqual(lay.center.split_a, 0.5)
        self.assertIn("por defecto", lay.center.split_source)

    def test_unknown_diff_type_is_kept_raw(self):
        lay = layout_for(rwd_car(diff_row_opts={"diffType": "quantumDiff", "x": 1}))
        d = lay.axles["rear"].diff
        self.assertEqual(d.get("kind"), "quantumDiff")
        self.assertEqual(d.params["x"], 1)  # opciones desconocidas: conservadas
        self.assertNotIn("x", d.values)

    def test_structural_errors(self):
        cases = {
            "central con relación propia": awd_car({"diffType": "lsd", "gearRatio": 2}),
            "finales distintos": awd_car(front_final=4.0, rear_final=3.5),
        }
        for name, parts in cases.items():
            with self.subTest(name), self.assertRaises(DrivetrainDataError):
                layout_for(parts)

    def test_axle_diff_spanning_both_axles(self):
        dt = build_drivetrain(resolve_parts(awd_car()))
        # ejes geométricos cruzados respecto de las ruedas de cada diferencial
        with self.assertRaises(DrivetrainDataError):
            awd_axle_roles(dt, {"FL", "RL"}, {"FR", "RR"})
        with self.assertRaises(DrivetrainDataError):
            awd_axle_roles(dt, {"FL", "FR", "RL", "RR"}, set())

    def test_wheel_without_axle_and_axle_count(self):
        with self.assertRaises(DrivetrainDataError):
            traction_type(["X"], {"FL"}, {"RL"})
        r = resolve_parts(rwd_car())
        geo = compute_mass_geometry(r)
        with self.assertRaises(DrivetrainDataError):
            classify_traction(
                build_drivetrain(r),
                SimpleNamespace(axles=geo.axles[:1], wheels=geo.wheels),
            )

    def test_different_driven_radii(self):
        r = resolve_parts(rwd_car())
        geo = compute_mass_geometry(r)
        wheels = [
            SimpleNamespace(
                name=w.name,
                radius=w.radius
                * (1.2 if w.name == "RR" else 1),  # > 5 %: DRIVEN_RADIUS_SPREAD_MAX
            )
            for w in geo.wheels
        ]
        with self.assertRaises(DrivetrainDataError):
            classify_traction(
                build_drivetrain(r), SimpleNamespace(axles=geo.axles, wheels=wheels)
            )

    def test_both_axles_driven_without_center_assigns_no_roles(self):
        # Un único diferencial que mueve RL (trasera) y FL (delantera): tracción a
        # los dos ejes sin diferencial central. No se supone ninguna estructura.
        parts = rwd_car()
        pt = parts["car"]["powertrain"]
        pt[7] = ["shaft", "axleFL2", "rearDiff", 2, {"connectedWheel": "FL"}]
        pt[8] = ["shaft", "axleRR", "halfshaftRR", 1, {"connectedWheel": "RR"}]
        lay = layout_for(parts)
        self.assertEqual(lay.layout, "AWD")
        self.assertEqual((lay.axles, lay.center), ({}, None))
        with self.assertRaises(DrivetrainDataError):
            ac_dt.render_drivetrain_ini(result_for(parts))
        ir = ir_for(parts)
        self.assertEqual(ir.drivetrain.layout.value, "AWD")
        # G2.8: el reparto existe pero no se ha interpretado: ausente, no «no aplica»
        self.assertEqual(ir.drivetrain.front_torque_share.status, "missing")
        self.assertIn(
            "sin diferencial central reconocido",
            ir.drivetrain.front_torque_share.reason,
        )
        self.assertEqual(sorted(ir.drivetrain.differentials), ["center"])
        self.assertEqual(len(lay.unassigned_diffs), 1)
        self.assertEqual(ir.drivetrain.differentials["center"].kind.status, "missing")
        self.assertTrue(any(u.raw is not None for u in ir.unknown))


class TestIR(unittest.TestCase):
    def test_fwd_ir(self):
        ir = ir_for(fwd_car())
        dtr = ir.drivetrain
        self.assertEqual(dtr.driven_wheels.value, ["FL", "FR"])
        f = dtr.differentials["front"]
        self.assertEqual(
            (f.kind.value, f.kind.source_key), ("lsd", "frontDiff.diffType")
        )
        self.assertEqual((f.preload.value, f.preload.unit), (50, "N·m"))
        self.assertEqual(f.rev_lock_coef.status, "missing")  # ausente en el origen
        self.assertEqual(f.torque_share.status, "not_applicable")
        self.assertEqual(dtr.differentials["rear"].kind.status, "not_applicable")
        self.assertEqual(dtr.differentials["center"].kind.status, "not_applicable")
        facts = ir_to_facts(ir)
        self.assertEqual(facts["bng.drivetrain.traction"].value, "FWD")
        self.assertEqual(facts["bng.diff.type"].value, "lsd")
        self.assertNotIn("bng.diff.torque_share", facts)

    def test_awd_ir(self):
        ir = ir_for(
            awd_car({"diffType": "lsd", "lsdPreload": 10, "diffTorqueSplit": 0.45})
        )
        dtr = ir.drivetrain
        self.assertAlmostEqual(dtr.front_torque_share.value, 0.55)
        self.assertAlmostEqual(dtr.differentials["front"].torque_share.value, 0.55)
        self.assertAlmostEqual(dtr.differentials["rear"].torque_share.value, 0.45)
        self.assertIn("salida 1", dtr.differentials["rear"].torque_share.note)
        self.assertEqual(dtr.differentials["center"].preload.value, 10)
        self.assertEqual(dtr.differentials["front"].preload.status, "missing")  # open
        facts = ir_to_facts(ir)
        self.assertAlmostEqual(facts["bng.awd.front_share"].value, 0.55)
        self.assertEqual(facts["bng.awd.center_diff_type"].value, "lsd")
        refs = {(r.path, r.locator) for r in dtr.layout.refs}
        self.assertIn(
            ("src/extract/traction.py", "classify_traction/traction_type"), refs
        )

    def test_torque_vectoring_ir(self):
        ir = ir_for(rwd_car(diff_row_opts={"diffType": "torqueVectoring"}))
        dtr = ir.drivetrain
        self.assertEqual(dtr.layout.value, "RWD")
        r = dtr.differentials["rear"]
        self.assertEqual(r.kind.value, "torqueVectoring")
        self.assertEqual(r.preload.status, "missing")  # no es lsd: no se lee
        self.assertEqual(dtr.front_torque_share.status, "not_applicable")
        self.assertEqual(dtr.differentials["front"].kind.status, "not_applicable")
        facts = ir_facts(ir)
        self.assertEqual(
            facts["ir.drivetrain.differentials.rear.kind"].value, "torqueVectoring"
        )
        self.assertNotIn("ir.drivetrain.differentials.rear.preload", facts)

    def test_absent_diff_type_ir(self):
        ir = ir_for(rwd_car(diff_row_opts={"lsdPreload": 50}))
        r = ir.drivetrain.differentials["rear"]
        self.assertEqual(r.kind.status, "missing")
        # sin diffType no se supone lsd; G2.8: el valor del origen se conserva
        self.assertEqual(r.preload.status, "unparsed")
        self.assertEqual(r.preload.original, 50)


class TestCompatibility(unittest.TestCase):
    def test_result_fields_derive_from_layout(self):
        for parts in (
            fwd_car(),
            rwd_car(),
            awd_car({"diffType": "lsd", "diffTorqueSplit": 0.3}),
        ):
            res = result_for(parts)
            self.assertEqual(res.traction, res.layout.layout)
            self.assertEqual(res.driven_radius, res.layout.driven_radius)
            self.assertEqual(res.awd, res.layout.awd_summary())

    def test_awd_axles_wrapper_matches_previous_format(self):
        res = result_for(awd_car({"diffType": "lsd", "diffTorqueSplit": 0.45}))
        lay = res.layout
        old = ac_dt.awd_axles(
            res.drivetrain, set(lay.front_wheels), set(lay.rear_wheels)
        )
        self.assertEqual(old, res.awd)
        self.assertEqual(
            list(old),
            [
                "center",
                "split_a",
                "split_source",
                "center_params",
                "rear",
                "front",
                "front_share_percent",
            ],
        )
        self.assertAlmostEqual(old["front_share_percent"], 55.0)
        self.assertIs(ac_dt.traction_type, traction_type)

    def test_diff_fields_order_is_the_comment_order(self):
        # El comentario de drivetrain.ini enumera las claves en este orden.
        self.assertEqual(
            [k for _, k in DIFF_FIELDS],
            ["diffType", "lsdPreload", "lsdLockCoef", "lsdRevLockCoef", "friction"],
        )
        d = DiffSetup("d", {"kind": "lsd", "friction": 2}, {})
        self.assertEqual(ac_dt._diff_desc("x", d), "x: diffType='lsd', friction=2")

    def test_ac_module_no_longer_interprets_axles(self):
        tree = ast.parse((ROOT / "src/ac/drivetrain.py").read_text("utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        # ni ejes geométricos ni el dispositivo diferencial/AWD del grafo
        self.assertFalse(_uses_geometry_axles(tree))
        self.assertNotIn("differential", names)
        self.assertNotIn("awd", names)
        self.assertNotIn(
            "diffTorqueSplit", (ROOT / "src/ac/drivetrain.py").read_text("utf-8")
        )

    def test_importer_reads_layout_not_ac_logic(self):
        text = (ROOT / "src/importers/beamng.py").read_text("utf-8")
        self.assertNotIn('"analyse/traction_type"', text)
        self.assertNotIn("awd_axles", text.split('"""', 2)[2])
        self.assertNotIn("dt.differential", text)


def _uses_geometry_axles(tree: ast.AST) -> bool:
    """¿Lee ``geometry.axles``? (la lógica de ejes ya no debe estar en src/ac)."""
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Attribute)
            and n.attr == "axles"
            and isinstance(n.value, ast.Name)
            and n.value.id == "geometry"
        ):
            return True
    return False


if __name__ == "__main__":
    unittest.main()
