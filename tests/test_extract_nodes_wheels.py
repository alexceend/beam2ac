import unittest

from src.extract.nodes import UnsupportedGeometry, apply_node_offset, collect_nodes
from src.extract.wheels import collect_wheels

from .jbeam_fixtures import (
    NODE_HEADER,
    SLOT_HEADER,
    WHEEL_HEADER,
    resolve_parts,
    simple_car,
    wheel_props,
)


def _offset_vehicle(child_nodes, child_slot_offset=None, grandchild_offset=None):
    """main -> susp (nodeOffset (0.1, -1, 0.5)) -> hub [-> wheel opcional]."""
    hub_slots = [SLOT_HEADER]
    if grandchild_offset is not None:
        opts = {"nodeOffset": grandchild_offset} if grandchild_offset else {}
        hub_slots.append(["wheel", "wheel_part", "W", opts])
    susp_opts = child_slot_offset or {"nodeOffset": {"x": 0.1, "y": -1.0, "z": 0.5}}
    return {
        "car": {
            "slotType": "main",
            "slots": [SLOT_HEADER, ["susp", "susp_part", "Susp", susp_opts]],
        },
        "susp_part": {
            "slotType": "susp",
            "nodes": [NODE_HEADER, {"nodeWeight": 2}, *child_nodes],
            "slots": [SLOT_HEADER, ["hub", "hub_part", "Hub"]],
        },
        "hub_part": {
            "slotType": "hub",
            "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["h1l", 0.6, 0.0, 0.0]],
            "slots": hub_slots,
        },
        "wheel_part": {
            "slotType": "wheel",
            "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["w1l", 0.7, -1.0, 0.5]],
        },
    }


class TestNodeOffset(unittest.TestCase):
    def test_symmetric_x_rule(self):
        self.assertEqual(apply_node_offset((0.5, 1, 2), (0.1, 1, 1)), (0.6, 2, 3))
        self.assertEqual(apply_node_offset((-0.5, 1, 2), (0.1, 1, 1)), (-0.6, 2, 3))
        self.assertEqual(apply_node_offset((0.0, 1, 2), (0.0, 1, 1)), (0.0, 2, 3))

    def test_x_zero_with_lateral_offset_follows_game_sign_rule(self):
        # G2.13: antes se esperaba UnsupportedGeometry («lado ambiguo»). El juego
        # aplica x + sign(x)·off.x con sign(0) = 0 (utils.lua, mathlib.lua:1134) y lo
        # confirma el volcado de BeamNG (output/g213_chassis_geometry_2026-11-03/banco):
        # el nodo no se mueve en x y sí en y/z.
        self.assertEqual(apply_node_offset((0.0, 0, 0), (0.1, 0, 0)), (0.0, 0, 0))
        self.assertEqual(apply_node_offset((0.0, 1, 2), (0.1, 0.5, -1)), (0.0, 1.5, 1))
        self.assertEqual(apply_node_offset((-0.0, 1, 2), (0.1, 0, 0))[0], 0.0)

    def test_offset_applied_inherited_and_replaced(self):
        parts = _offset_vehicle(
            [["s1l", 0.5, 0.0, 0.0], ["s1r", -0.5, 0.0, 0.0]],
            grandchild_offset={"x": 0, "y": 0, "z": 0},
        )
        nodes = {n.id: n for n in collect_nodes(resolve_parts(parts))}
        self.assertEqual(nodes["s1l"].pos, (0.6, -1.0, 0.5))
        self.assertEqual(nodes["s1r"].pos, (-0.6, -1.0, 0.5))
        self.assertEqual(nodes["s1l"].raw_pos, (0.5, 0.0, 0.0))
        self.assertEqual(nodes["h1l"].pos, (0.7, -1.0, 0.5))  # heredado
        self.assertEqual(nodes["w1l"].pos, (0.7, -1.0, 0.5))  # reemplazado por 0

    def test_offset_expressions_use_variables(self):
        parts = _offset_vehicle(
            [["s1l", 0.5, "$=0.25 + 0.25", 0.0]],
            child_slot_offset={"nodeOffset": {"x": "$=$dx or 0", "y": 0, "z": 0}},
        )
        nodes = {
            n.id: n for n in collect_nodes(resolve_parts(parts, pc_vars={"$dx": 0.2}))
        }
        self.assertEqual(nodes["s1l"].pos, (0.7, 0.5, 0.0))

    def test_node_on_centerline_under_lateral_offset_keeps_x(self):
        # G2.13: expectativa anterior (error) obsoleta; ver la prueba anterior.
        parts = _offset_vehicle([["c", 0.0, 0.0, 0.0]])
        nodes = {n.id: n for n in collect_nodes(resolve_parts(parts))}
        self.assertEqual(nodes["c"].pos, (0.0, -1.0, 0.5))


class TestNodeValidation(unittest.TestCase):
    def _single(self, nodes, **extra):
        return {"car": {"slotType": "main", "nodes": nodes, **extra}}

    def test_masses_from_modifiers_and_inline(self):
        parts = self._single(
            [
                NODE_HEADER,
                {"nodeWeight": 3},
                ["a", 0, 0, 0],
                ["b", 1, 0, 0, {"nodeWeight": 5}],
            ]
        )
        nodes = collect_nodes(resolve_parts(parts))
        self.assertEqual([n.mass for n in nodes], [3.0, 5.0])

    def test_unverified_rules_raise(self):
        cases = {
            "sin nodeWeight": self._single([NODE_HEADER, ["a", 0, 0, 0]]),
            "scalenodeWeight": self._single(
                [NODE_HEADER, {"nodeWeight": 1}, ["a", 0, 0, 0]], scalenodeWeight=2
            ),
            "coordenada no numérica": self._single(
                [NODE_HEADER, {"nodeWeight": 1}, ["a", "x", 0, 0]]
            ),
            "masa negativa": self._single(
                [NODE_HEADER, {"nodeWeight": -1}, ["a", 0, 0, 0]]
            ),
        }
        for name, parts in cases.items():
            with self.subTest(name):
                with self.assertRaises(UnsupportedGeometry):
                    collect_nodes(resolve_parts(parts))

    def test_duplicate_node_id_across_parts_last_merged_wins(self):
        # slotSystem.lua: la raíz va primero y los hijos se fusionan después
        # (postorden); tableSchema.lua: newList[id] = fila => gana la fusionada la última
        parts = {
            "car": {
                "slotType": "main",
                "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["a", 0, 0, 0]],
                "slots": [SLOT_HEADER, ["x", "other", "X"]],
            },
            "other": {
                "slotType": "x",
                "nodes": [NODE_HEADER, {"nodeWeight": 2}, ["a", 1, 0, 0]],
            },
        }
        overrides = []
        nodes = collect_nodes(resolve_parts(parts), overrides)
        self.assertEqual([(n.id, n.part, n.mass) for n in nodes], [("a", "other", 2.0)])
        self.assertEqual(
            [(o.part, o.replaced_part, o.replaced_mass) for o in overrides],
            [("other", "car", 1.0)],
        )

    def test_duplicate_node_id_postorder_merge_rank(self):
        # a -> (b -> c): orden de fusión car, c, b, a... el hijo profundo antes que el padre
        parts = {
            "car": {"slotType": "main", "slots": [SLOT_HEADER, ["x", "pb", "B"]]},
            "pb": {
                "slotType": "x",
                "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["n", 0, 0, 0]],
                "slots": [SLOT_HEADER, ["y", "pc", "C"]],
            },
            "pc": {
                "slotType": "y",
                "nodes": [NODE_HEADER, {"nodeWeight": 5}, ["n", 2, 0, 0]],
            },
        }
        nodes = collect_nodes(resolve_parts(parts))
        # pc se fusiona antes que pb: gana pb (el padre), aunque pc se visita después
        self.assertEqual([(n.part, n.mass) for n in nodes], [("pb", 1.0)])

    def test_non_zero_node_move_raises(self):
        parts = {
            "car": {
                "slotType": "main",
                "slots": [
                    SLOT_HEADER,
                    ["e", "eng", "E", {"nodeMove": {"x": 0.1, "y": 0, "z": 0}}],
                ],
            },
            "eng": {
                "slotType": "e",
                "nodes": [NODE_HEADER, {"nodeWeight": 1}, ["a", 1, 0, 0]],
            },
        }
        with self.assertRaises(UnsupportedGeometry):
            collect_nodes(resolve_parts(parts))

    def test_empty_nodes_section(self):
        self.assertEqual(collect_nodes(resolve_parts(self._single([]))), [])


class TestWheels(unittest.TestCase):
    def test_generated_mass_inertia_and_center(self):
        r = resolve_parts(simple_car())
        wheels = collect_wheels(r, collect_nodes(r))
        self.assertEqual([w.name for w in wheels], ["FL", "FR", "RL", "RR"])
        fl = wheels[0]
        self.assertAlmostEqual(fl.generated_mass, 4 * 2 * (1.0 + 0.5))
        self.assertAlmostEqual(fl.spin_inertia, 4 * 2 * (1.0 * 0.2**2 + 0.5 * 0.3**2))
        self.assertAlmostEqual(fl.center[0], 0.8)
        self.assertEqual(fl.center[1:], (-1.0, 0.3))

    def test_modifiers_continue_from_previous_part(self):
        # Como en la muestra: una pieza define radio/pesos, otra crea las ruedas.
        parts = {
            "car": {
                "slotType": "main",
                "nodes": [
                    NODE_HEADER,
                    {"nodeWeight": 1},
                    ["o", 0.9, 0, 0.3],
                    ["i", 0.7, 0, 0.3],
                ],
                "slots": [SLOT_HEADER, ["a", "props", "A"], ["b", "data", "B"]],
            },
            "props": {"slotType": "a", "pressureWheels": [WHEEL_HEADER, wheel_props()]},
            "data": {
                "slotType": "b",
                "pressureWheels": [
                    WHEEL_HEADER,
                    ["W", "h", "t", "o", "i", 9999, "o", 1],
                ],
            },
        }
        r = resolve_parts(parts)
        (wheel,) = collect_wheels(r, collect_nodes(r))
        self.assertEqual(wheel.radius, 0.3)
        self.assertEqual(wheel.part, "data")

    def test_wheel_offset_moves_center_along_axis(self):
        # addPressureWheel: centro = punto medio + eje (n1 -> n2, normalizado) * offset
        base = simple_car()
        shifted = simple_car(wheel_overrides={"wheelOffset": 0.02})
        rb, rs = resolve_parts(base), resolve_parts(shifted)
        wb = collect_wheels(rb, collect_nodes(rb))[0]
        ws = collect_wheels(rs, collect_nodes(rs))[0]
        d = [b - a for a, b in zip(wb.center, ws.center)]
        self.assertAlmostEqual(sum(c * c for c in d) ** 0.5, 0.02, places=9)

    def test_unsupported_wheels_raise(self):
        cases = {
            "hasTire false": simple_car(wheel_overrides={"hasTire": False}),
            "falta radius": simple_car(wheel_overrides={"radius": None}),
            "numRays no entero": simple_car(wheel_overrides={"numRays": 3.5}),
        }
        bad_node = simple_car()
        bad_node["wheel_part"]["pressureWheels"][2][3] = "nope"
        cases["nodo de eje inexistente"] = bad_node
        other = simple_car()
        other["wheel_part"]["hubWheels"] = []
        cases["sección hubWheels"] = other
        for name, parts in cases.items():
            with self.subTest(name):
                r = resolve_parts(parts)
                with self.assertRaises(UnsupportedGeometry):
                    collect_wheels(r, collect_nodes(r))


if __name__ == "__main__":
    unittest.main()
