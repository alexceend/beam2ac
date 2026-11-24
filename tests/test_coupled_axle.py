"""Eje trasero torsional (``dwb_coupled``): virtualización, cinemática y reglas.

Casos sintéticos (vigas sueltas) para ``virtualize_torsion_axle`` y, si el zip existe,
un eje torsional real del corpus de Automation (solo lectura; se omite si falta).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from src.attrs.report import resolve_vehicle
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract.components import Beam
from src.extract.coupled_axle import virtualize_torsion_axle
from src.extract.linkage import DWB_KINDS
from src.extract.mass_geometry import compute_mass_geometry
from src.extract.nodes import Node
from src.extract.suspension import build_suspension
from src.jbeam import resolve
from src.pipeline.physics_preview import load_vehicle_zip, preview_physics

ROOT = Path(__file__).resolve().parents[1]
MODS = Path(os.environ.get("LOCALAPPDATA", "")) / "BeamNG/BeamNG.drive/current/mods"
ZIP = MODS / "thershow_ranunculus_alfonsino.zip"
RULES = ROOT / "catalog" / "rules"


def _node(i: str, x: float, y: float, z: float) -> Node:
    return Node(i, (x, y, z), (x, y, z), 1.0, "p", None)


def _beams(*pairs: tuple[str, str]) -> list[Beam]:
    return [Beam("p", p, None, {}) for p in pairs]


def _synthetic() -> tuple[dict[str, Node], list[Beam], dict]:
    """Semiejes simétricos: buje (``hr1``/``hr2``), nodos ``ar``/``br`` del brazo, una
    rótula ``pr`` con tres vigas a la carrocería (``c1..c3``) y una viga cruzada entre
    los nodos móviles ``br``–``bl``."""
    nodes: dict[str, Node] = {}
    beams: list[Beam] = []
    for s, x in (("r", -0.6), ("l", 0.6)):
        for name, p in {
            f"h{s}1": (x, 0.0, 0.1),
            f"h{s}2": (x, 0.0, -0.1),
            f"a{s}": (x, 0.2, 0.0),
            f"b{s}": (x, -0.3, 0.1),
            f"p{s}": (x * 0.9, -0.4, 0.0),
        }.items():
            nodes[name] = _node(name, *p)
        beams += _beams(
            (f"h{s}1", f"a{s}"),
            (f"h{s}2", f"a{s}"),
            (f"h{s}1", f"p{s}"),
            (f"a{s}", f"p{s}"),
            (f"b{s}", f"p{s}"),
            (f"b{s}", f"h{s}1"),
        )
        for k in (1, 2, 3):
            nodes[f"c{s}{k}"] = _node(
                f"c{s}{k}", x * 0.5, -0.4 + 0.1 * k, 0.3 * k - 0.4
            )
            beams += _beams((f"p{s}", f"c{s}{k}"))
    beams += _beams(("br", "bl"), ("ar", "pl"))
    hubs = {"RR": ("hr1", "hr2"), "RL": ("hl1", "hl2")}
    return nodes, beams, hubs


class TestVirtualize(unittest.TestCase):
    def _run(self, nodes, beams, hubs, body=None):
        body = body or {n for n in nodes if n[0] in "habp" and not n.startswith(("c",))}
        return virtualize_torsion_axle(
            hubs=hubs, axle_nodes=body, nodes=nodes, rigid_beams=beams, exclude=set()
        )

    def test_pivot_becomes_fixed_anchor(self):
        nodes, beams, hubs = _synthetic()
        out_nodes, out_beams, info = self._run(nodes, beams, hubs)
        self.assertEqual(info["RR"]["pivot"], "pr")
        self.assertEqual(info["RR"]["other_pivot"], "pl")
        self.assertIn("_vp_pr", out_nodes)
        self.assertEqual(out_nodes["_vp_pr"].pos, nodes["pr"].pos)
        pairs = {frozenset(b.nodes) for b in out_beams}
        # el brazo se une al pivote como a un punto fijo; al del otro lado también
        self.assertIn(frozenset(("ar", "_vp_pr")), pairs)
        self.assertIn(frozenset(("ar", "_vp_pl")), pairs)
        # las vigas del buje al pivote son redundantes (cuerpo rígido)
        self.assertNotIn(frozenset(("hr1", "pr")), pairs)
        self.assertNotIn(frozenset(("hr1", "_vp_pr")), pairs)

    def test_moving_cross_beam_is_dropped_and_recorded(self):
        nodes, beams, hubs = _synthetic()
        _, out_beams, info = self._run(nodes, beams, hubs)
        self.assertIn(("br", "bl"), info["RR"]["dropped"])
        self.assertIn(("bl", "br"), info["RL"]["dropped"])
        self.assertNotIn(
            frozenset(("br", "bl")), {frozenset(b.nodes) for b in out_beams}
        )

    def test_not_torsional_returns_none(self):
        nodes, beams, hubs = _synthetic()
        # sin rótulas: quitar las vigas del pivote izquierdo a la carrocería
        few = [
            b for b in beams if not (b.nodes[0] == "pl" and b.nodes[1].startswith("c"))
        ]
        self.assertIsNone(self._run(nodes, few, hubs))
        # un solo lado
        self.assertIsNone(self._run(nodes, beams, {"RR": hubs["RR"]}))


@unittest.skipUnless(ZIP.is_file(), "eje torsional del corpus no disponible")
class TestTorsionBeamCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.src = load_vehicle_zip(ZIP, None, "3edae.pc")
        cls.res = resolve(cls.src.index, cls.src.pc)
        cls.susp = build_suspension(cls.res, compute_mass_geometry(cls.res))

    def test_rear_is_dwb_coupled_with_exact_kinematics(self):
        rear = self.susp.axles[1].corners
        self.assertEqual({c.linkage.kind for c in rear}, {"dwb_coupled"})
        for c in rear:
            lk = c.linkage
            self.assertEqual(lk.rank, 5)
            self.assertLessEqual(lk.deviation_deg, 0.01)
            self.assertLessEqual(lk.deviation_m, 1e-4)
            self.assertIn(lk.kind, DWB_KINDS)
            self.assertIsNotNone(lk.spring_motion_ratio)
        # DWB simétrico: los anclajes de las dos ruedas coinciden (rueda derecha)
        a, b = (c.linkage.anchors for c in rear)
        for k in a:
            for u, v in zip(a[k], b[k]):
                self.assertAlmostEqual(u, v, places=5)

    def test_front_is_not_touched(self):
        self.assertNotIn(
            "dwb_coupled", {c.linkage.kind for c in self.susp.axles[0].corners}
        )

    def test_attributes_resolve_without_manual_rear(self):
        pv = preview_physics(self.src, 0.0)
        _, results = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), driver_mass_kg=0.0)
        by = {r.target: r for r in results}
        self.assertEqual(by["ac:suspensions.ini/REAR/TYPE"].value, "DWB")
        for t in (
            "ac:suspensions.ini/REAR/SPRING_RATE",
            "ac:suspensions.ini/REAR/HUB_MASS",
            "ac:suspensions.ini/ARB/REAR",
            "ac:car.ini/BASIC/TOTALMASS",
            "ac:suspensions.ini/BASIC/CG_LOCATION",
        ):
            self.assertEqual(by[t].status, "inferred", t)
        self.assertIn("coupled", by["ac:suspensions.ini/REAR/TYPE"].rule)
        # masa por rueda = mitad del cuerpo rígido acoplado (121,97 kg incl. ruedas)
        self.assertAlmostEqual(by["ac:suspensions.ini/REAR/HUB_MASS"].value, 60.9864, 3)


class TestCoupledRules(unittest.TestCase):
    def test_rules_are_exclusive_with_other_rear_dwb_kinds(self):
        rules = [
            json.loads(p.read_text("utf-8"))
            for p in sorted(RULES.glob("susp.*_rear_coupled_40340@1.json"))
        ]
        self.assertEqual(len(rules), 22)
        for d in rules:
            conds = {c["fact"]: c for c in d["conditions"]}
            self.assertEqual(conds["ir.formats.exporter_version"]["value"], ["40340"])
            self.assertEqual(
                conds["ir.suspension.axles.rear.linkage"]["value"], ["dwb_coupled"]
            )
            self.assertEqual(d["status"], "provisional")
            self.assertTrue(any("dwb_coupled" in a for a in d["assumptions"]))


if __name__ == "__main__":
    unittest.main()
