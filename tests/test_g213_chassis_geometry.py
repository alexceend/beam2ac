"""G2.13: geometría del chasis (reglas de nodos del juego, IR y reglas 200409).

Casos sintéticos (JBeam mínimos) para las dos reglas del juego y vehículos reales de
``samples_beamng/`` (solo lectura). Si existe el volcado del banco de BeamNG
(``output/g213_chassis_geometry_2026-11-03/banco``), se compara también con él.
Informe: ``docs/research/g213_chassis_geometry_2026-11-03.md``.
"""

from __future__ import annotations

import copy
import json
import math
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.convert.service import ConversionSession
from src.extract.mass_geometry import compute_mass_geometry
from src.extract.nodes import NodeOverride, collect_nodes
from src.importers.beamng import import_preview
from src.ir.param import Param
from src.jbeam import resolve
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

from .jbeam_fixtures import NODE_HEADER, SLOT_HEADER, resolve_parts

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
DUMP = (
    ROOT / "output/g213_chassis_geometry_2026-11-03/banco/userpath/current/beam2ac_geo"
)
ATACAMA, PATAGONIA = "2020_rpm_atacama_sport__4x4", "2020_rpm_patagonia_xt_v8"
SEVEN = [
    ATACAMA,
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    PATAGONIA,
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
]
WB, TF, TR = (
    "ac:suspensions.ini/BASIC/WHEELBASE",
    "ac:suspensions.ini/FRONT/TRACK",
    "ac:suspensions.ini/REAR/TRACK",
)
NEW_RULES = {
    WB: "susp.wheelbase_200409@1",
    TF: "susp.track_front_200409@1",
    TR: "susp.track_rear_200409@1",
}


def _vd(name: str) -> Path:
    return SAMPLES / name / "vehicles" / name


def _have(name: str) -> bool:
    return _vd(name).is_dir()


def _source(name: str):
    vd = _vd(name)
    return load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])


@lru_cache(maxsize=None)
def _ir(name: str):
    return import_preview(preview_physics(_source(name), 70.0), 70.0)


def _resolve(ir) -> dict:
    return {r.target: r for r in ConversionSession.from_ir(ir).resolve()}


class TestSyntheticGameRules(unittest.TestCase):
    def test_duplicate_id_in_same_part_last_row_wins(self):
        parts = {
            "car": {
                "slotType": "main",
                "nodes": [
                    NODE_HEADER,
                    {"nodeWeight": 1},
                    ["a", -0.1, 0, 0],
                    ["b", 0.5, 0, 0],
                    {"nodeWeight": 2},
                    ["a", 0.1, 0, 0],
                ],
            }
        }
        overrides: list[NodeOverride] = []
        nodes = collect_nodes(resolve_parts(parts), overrides)
        self.assertEqual([n.id for n in nodes], ["b", "a"])
        a = next(n for n in nodes if n.id == "a")
        self.assertEqual((a.pos, a.mass), ((0.1, 0.0, 0.0), 2.0))
        self.assertEqual(len(overrides), 1)
        o = overrides[0]
        self.assertEqual((o.id, o.part), ("a", "car"))
        self.assertEqual((o.replaced_raw_pos, o.replaced_mass), ((-0.1, 0.0, 0.0), 1.0))
        self.assertLess(o.replaced_row_index, o.kept_row_index)
        # sin lista: mismo resultado, sin registro
        self.assertEqual(
            [n.pos for n in collect_nodes(resolve_parts(parts))], [n.pos for n in nodes]
        )

    def test_duplicate_id_across_parts_last_merged_wins(self):
        # antes se rechazaba; el orden de fusión se contrastó con slotSystem.lua (G2.49)
        parts = {
            "car": {
                "slotType": "main",
                "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["a", 0, 0, 0]],
                "slots": [SLOT_HEADER, ["x", "other", "X"]],
            },
            "other": {
                "slotType": "x",
                "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["a", 1, 0, 0]],
            },
        }
        (node,) = collect_nodes(resolve_parts(parts))
        self.assertEqual((node.part, node.raw_pos[0]), ("other", 1))

    def test_centerline_node_keeps_x_and_gets_y_z(self):
        parts = {
            "car": {
                "slotType": "main",
                "slots": [
                    SLOT_HEADER,
                    [
                        "axle",
                        "axle_part",
                        "A",
                        {"nodeOffset": {"x": 0.05, "y": 1.4, "z": 1}},
                    ],
                ],
            },
            "axle_part": {
                "slotType": "axle",
                "nodes": [
                    NODE_HEADER,
                    {"nodeWeight": 20},
                    ["ax1", 0.0, 0.2, 0.0],
                    ["hl", 0.7, 0.0, 0.0],
                    ["hr", -0.7, 0.0, 0.0],
                ],
            },
        }
        n = {x.id: x for x in collect_nodes(resolve_parts(parts))}
        self.assertEqual(n["ax1"].pos[0], 0.0)
        self.assertAlmostEqual(n["ax1"].pos[1], 1.6, places=12)
        self.assertAlmostEqual(n["ax1"].pos[2], 1.0, places=12)
        self.assertAlmostEqual(n["hl"].pos[0], 0.75)
        self.assertAlmostEqual(n["hr"].pos[0], -0.75)


@unittest.skipUnless(
    _have(ATACAMA) and _have(PATAGONIA), "muestras 200409 no disponibles"
)
class TestBlockedCars(unittest.TestCase):
    def test_atacama_live_axle_centre_nodes(self):
        res = resolve(_source(ATACAMA).index, _source(ATACAMA).pc)
        nodes = {n.id: n for n in collect_nodes(res)}
        for nid in ("ax1", "ax2", "ax3", "ax4"):
            n = nodes[nid]
            with self.subTest(node=nid):
                self.assertEqual(n.part, "Camso_liveaxle_4link_R")
                self.assertEqual(n.raw_pos[0], 0.0)
                self.assertEqual(n.pos[0], 0.0)  # sin desplazamiento lateral
                self.assertAlmostEqual(n.pos[1], n.raw_pos[1] + 1.37, places=12)
                self.assertAlmostEqual(n.pos[2], n.raw_pos[2] + 1.07226, places=12)
        g = compute_mass_geometry(res)
        self.assertEqual(len(g.axles), 2)
        self.assertAlmostEqual(g.wheelbase, 3.38, places=9)
        self.assertEqual(g.node_overrides, [])

    def test_patagonia_duplicated_centre_light(self):
        res = resolve(_source(PATAGONIA).index, _source(PATAGONIA).pc)
        g = compute_mass_geometry(res)
        self.assertEqual(g.node_count, 420)
        ids = sorted(o.id for o in g.node_overrides)
        self.assertEqual(ids, [f"Light_0_2410_1738_ffff7a00_{i}" for i in range(3)])
        for o in g.node_overrides:
            with self.subTest(node=o.id):
                self.assertEqual(o.replaced_mass, 0.05)
                # dos copias del trío de una luz centrada: la misma traslación de
                # +1,225 mm en x para los tres nodos (no es un espejo); mismas y, z
                self.assertAlmostEqual(
                    o.kept_raw_pos[0] - o.replaced_raw_pos[0], 0.001225, delta=2e-6
                )
                self.assertEqual(o.replaced_raw_pos[1:], o.kept_raw_pos[1:])
        self.assertIn("reemplazada", " ".join(g.issues))
        self.assertAlmostEqual(g.wheelbase, 3.07, places=9)

    def test_ir_geometry_present_and_mass_note(self):
        for name in (ATACAMA, PATAGONIA):
            ir = _ir(name)
            with self.subTest(car=name):
                for path in (
                    "mass.total",
                    "dimensions.wheelbase",
                    "dimensions.track_front",
                    "dimensions.track_rear",
                ):
                    p = ir.get(path)
                    self.assertTrue(p.present, path)
                    self.assertEqual(p.kind, "measured")
                self.assertEqual(ir.get("mass.total").unit, "kg")
                self.assertEqual(ir.get("dimensions.wheelbase").unit, "m")
        self.assertIn("tableSchema.lua", _ir(PATAGONIA).get("mass.total").note)
        self.assertNotIn("reemplazadas", _ir(ATACAMA).get("mass.total").note)


@unittest.skipUnless(DUMP.is_dir(), "volcado del banco de BeamNG no disponible")
class TestAgainstGameDump(unittest.TestCase):
    def test_positions_and_masses_equal_game(self):
        for name in (ATACAMA, PATAGONIA):
            if not (DUMP / f"{name}.json").is_file() or not _have(name):
                continue
            game = json.loads((DUMP / f"{name}.json").read_text("utf-8"))
            gn = {n["name"]: n for n in game["nodes"] if n.get("name")}
            res = resolve(_source(name).index, _source(name).pc)
            for n in collect_nodes(res):
                with self.subTest(car=name, node=n.id):
                    self.assertIn(n.id, gn)
                    d = math.dist(n.pos, gn[n.id]["pos"])
                    self.assertLess(d, 1e-9)
                    self.assertEqual(n.mass, gn[n.id]["nodeWeight"])


@unittest.skipUnless(all(_have(v) for v in SEVEN), "muestras 200409 no disponibles")
class TestRules200409(unittest.TestCase):
    def test_new_rules_export_geometry_as_inferred(self):
        for name in SEVEN:
            ir = _ir(name)
            res = _resolve(ir)
            for target, key in NEW_RULES.items():
                with self.subTest(car=name, target=target):
                    r = res[target]
                    self.assertEqual((r.source, r.rule), ("rule", key))
                    self.assertEqual(r.status, "inferred")  # nunca known (supuesto)
                    self.assertEqual(r.unit, "m")
            self.assertEqual(res[WB].value, ir.get("dimensions.wheelbase").value)

    def test_rules_limited_to_200409(self):
        ir = copy.deepcopy(_ir("2020_rpm_atlanta_gt_3_6t"))
        for version in ("38022", "40340", "999999"):
            ir.set("formats.exporter_version", Param.of(version, "text", "extracted"))
            res = _resolve(ir)
            for target, key in NEW_RULES.items():
                with self.subTest(version=version, target=target):
                    self.assertNotEqual(res[target].rule, key)

    def test_rule_definitions(self):
        cat = Catalog.load(REPO_CATALOG)
        for key in NEW_RULES.values():
            r = cat.rules[key]
            with self.subTest(rule=key):
                self.assertEqual(
                    (r.status, r.kind, r.scope.family),
                    ("provisional", "measured", "automation_export"),
                )
                conds = [(c.fact, c.op, c.value) for c in r.conditions]
                self.assertEqual(
                    conds, [("ir.formats.exporter_version", "in", ["200409"])]
                )
                self.assertTrue(r.assumptions)
        # las reglas del Ibiza no se amplían
        for key in ("susp.wheelbase@2", "susp.track_front@2", "susp.track_rear@2"):
            self.assertEqual(cat.rules[key].scope.vehicle, "seat_ibiza_acfr")


if __name__ == "__main__":
    unittest.main()
