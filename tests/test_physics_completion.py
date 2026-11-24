"""Fase C5: cinemática, pérdidas de transmisión, puertas de validación, correspondencia
de ejes DWB y esquema de alas de aero.ini."""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from src.lab import kinematics as K
from src.lab.powerlut_losses import (
    corrected_curve,
    engine_side_loss,
    wheel_torque_error,
)

TOOL = (
    Path(__file__).resolve().parents[1] / "tools/analysis/derive_physics_completion.py"
)


def load_tool():
    spec = importlib.util.spec_from_file_location("derive_physics_completion", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def parallel_dwb(top_len: float = 0.35, bot_len: float = 0.35) -> K.Knuckle:
    """DWB de rueda derecha (x < 0) con brazos horizontales paralelos y bieleta."""
    c = (-0.8, 0.30, 0.0)
    pts = {
        "C": c,
        "out": (-0.9, 0.30, 0.0),
        "in": (-0.7, 0.30, 0.0),
        "T": (-0.75, 0.45, 0.0),
        "B": (-0.75, 0.15, 0.0),
        "S": (-0.75, 0.15, -0.15),
    }
    links = [
        ((-0.75 + top_len, 0.45, 0.2), "T"),
        ((-0.75 + top_len, 0.45, -0.2), "T"),
        ((-0.75 + bot_len, 0.15, 0.2), "B"),
        ((-0.75 + bot_len, 0.15, -0.2), "B"),
        ((-0.75 + bot_len, 0.15, -0.15), "S"),
    ]
    return K.Knuckle(pts, "C", ("out", "in"), links)


class TestKinematics(unittest.TestCase):
    def test_parallel_equal_arms_keep_camber_and_toe(self) -> None:
        k = parallel_dwb()
        for row in K.sweep(k, [-0.03, 0.0, 0.03]):
            self.assertAlmostEqual(row["dcamber_deg"], 0.0, places=6)
            self.assertAlmostEqual(row["dtoe_deg"], 0.0, places=6)
            self.assertLess(row["residual_m"], 1e-9)

    def test_shorter_top_arm_gives_negative_camber_in_bump(self) -> None:
        k = parallel_dwb(top_len=0.25, bot_len=0.40)
        up = next(r for r in K.sweep(k, [0.0, 0.04]) if r["dy"] == 0.04)
        self.assertLess(up["dcamber_deg"], 0.0)  # parte alta hacia dentro al comprimir

    def test_motion_ratio_of_vertical_spring_at_knuckle_is_one(self) -> None:
        k = parallel_dwb()
        spring = ((-0.75, 0.80, 0.0), "B")
        self.assertAlmostEqual(K.motion_ratio(k, spring), 1.0, places=3)

    def test_overconstrained_consistent_links(self) -> None:
        k = parallel_dwb()
        k2 = K.Knuckle(k.points, "C", k.axis, k.links + [((-0.40, 0.45, 0.0), "T")])
        _, res = K.solve(k2, 0.02)
        self.assertLess(res, 1e-6)  # el enlace extra es compatible (mismo arco)


class TestLossRule(unittest.TestCase):
    def test_constant_loss_and_reference_gear_exact(self) -> None:
        self.assertAlmostEqual(engine_side_loss(5000, 10.0, 6.0, 2.0), 13.0)
        rows = wheel_torque_error(400.0, 10.0, 6.0, [2.0, 1.0], 2.0, 4.0)
        self.assertAlmostEqual(rows[0]["err_corrected_pct"], 0.0, places=9)
        self.assertGreater(rows[0]["err_current_pct"], 0)  # sin pérdidas AC da más par
        self.assertEqual(
            corrected_curve([[0, -20.0], [5000, 400.0]], 10, 6, 2.0),
            [[0, -20.0], [5000, 387.0]],
        )


class TestAxleMapping(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = load_tool()

    def test_classic_and_trapezoid(self) -> None:
        P = {
            "t": (0, 0.2, 0),
            "b": (0, -0.2, 0),
            "s": (0, -0.2, -0.1),
            "c1": (0.3, 0.2, 0.2),
            "c2": (0.3, 0.2, -0.2),
            "c3": (0.3, -0.2, 0.2),
            "c4": (0.3, -0.2, -0.2),
            "c5": (0.3, -0.2, -0.1),
        }
        s = {"links": {"t": {"c1", "c2"}, "b": {"c3", "c4"}, "s": {"c5"}}}
        m, links, kind = self.tool.ac_mapping(s, dict(P))
        self.assertIn("clásico", kind)
        self.assertEqual(
            (m["WBTYRE_TOP"], m["WBTYRE_STEER"], m["WBCAR_STEER"]), ("t", "s", "c5")
        )
        Q = {
            "t1": (0, 0.2, 0.1),
            "t2": (0, 0.2, -0.1),
            "b1": (0, -0.2, 0.1),
            "b2": (0, -0.2, -0.1),
            "c1": (0.3, 0.2, 0.2),
            "c2": (0.3, 0.2, -0.2),
            "c3": (0.3, -0.2, 0.2),
            "c4": (0.3, -0.2, -0.2),
        }
        s2 = {
            "links": {
                "t1": {"c1", "c2"},
                "t2": {"c1", "c2"},
                "b1": {"c3", "c4"},
                "b2": {"c3", "c4"},
            }
        }
        m2, links2, kind2 = self.tool.ac_mapping(s2, Q)
        self.assertIn("R1", kind2)
        self.assertEqual(m2["WBTYRE_TOP"], "_MT")
        self.assertEqual(Q["_MT"], (0.0, 0.2, 0.0))
        # inferior: vértice en el nudo delantero; bieleta del pivote trasero al nudo
        # trasero
        self.assertEqual(
            (m2["WBTYRE_BOTTOM"], m2["WBTYRE_STEER"], m2["WBCAR_STEER"]),
            ("b1", "b2", "c4"),
        )
        self.assertEqual(len(links2), 5)


class TestGatesAndAeroSchema(unittest.TestCase):
    def test_aero_wing_attributes(self) -> None:
        from src.attrs.store import Catalog

        cat = Catalog.load("catalog")
        for n in range(3):
            p = f"ac:aero.ini/WING_{n}/"
            self.assertEqual(cat.attributes[p + "POSITION"].unit, "m")
            self.assertEqual(cat.attributes[p + "ANGLE"].unit, "deg")
            self.assertEqual(
                cat.attributes[p + "LUT_AOA_CL/table"].unit, "table:deg->1"
            )
            self.assertIn(p + "LUT_AOA_CD/table", cat.targets["ac_physics"])

    def test_pending_gate_blocks_and_validated_does_not(self) -> None:
        from src.project import export as E
        from tests.test_export import MIN_FILES, FakeProject

        tmp = Path(tempfile.mkdtemp())
        try:
            proj = FakeProject(tmp / "p")
            proj.root.mkdir()
            src = tmp / "src"
            src.mkdir()
            (src / "source.json").write_text(
                '{"vehicle_id": "v", "variant": "c"}', encoding="utf-8"
            )
            proj.catalog_path = Path("catalog")
            proj.source = type("S", (), {"kind": "dir", "path": str(tmp), "pc": "c.pc"})
            gates = {
                "gates": {
                    "neumatico": {"status": "pendiente", "reason": "x", "how": "y"},
                    "masas": {"status": "validada", "reason": "x", "how": "y"},
                }
            }
            (proj.root / "validation_gates.json").write_text(
                json.dumps(gates), encoding="utf-8"
            )
            _, issues, ctx = E.collect(proj, {"source": src}, {}, "beam2ac_v")
            codes = {i.code for i in issues if i.severity == "error"}
            self.assertIn("validacion_pendiente.neumatico", codes)
            self.assertNotIn("validacion_pendiente.masas", codes)
            res = E.write_export(proj, list(MIN_FILES), issues, ctx, "beam2ac_v", {})
            man = json.loads((res.dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                man["validation_gates"], {"masas": "validada", "neumatico": "pendiente"}
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(
    Path("output/projects/porsche_gt3_rs/exports/004/manifest.json").is_file(),
    "sin el intento 004",
)
class TestPorscheAttempt004(unittest.TestCase):
    def test_only_validation_gates_block(self) -> None:
        from src.project.export import inspect_export

        a = Path("output/projects/porsche_gt3_rs/exports/004")
        rep = json.loads((a / "export_report.json").read_text(encoding="utf-8"))
        self.assertTrue(rep["errors"])
        self.assertTrue(
            all(e["code"].startswith("validacion_pendiente.") for e in rep["errors"])
        )
        self.assertTrue(inspect_export(a)["intact"])
        susp = (a / "beam2ac_porsche_gt3_rs" / "data" / "suspensions.ini").read_text(
            encoding="utf-8"
        )
        self.assertIn("TYPE=DWB", susp)
        self.assertFalse(math.isnan(float(susp.split("ROD_LENGTH=")[1].split("\n")[0])))


if __name__ == "__main__":
    unittest.main()
