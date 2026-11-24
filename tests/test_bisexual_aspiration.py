"""Generalizaciones de la conversión de ``bisexual_aspiration`` (40340, 2026-11-16).

El zip del usuario no está en el repositorio: se prueban las piezas con datos sintéticos.
"""

from __future__ import annotations

import unittest

from src.attrs import schema as S
from src.attrs.facts import Fact
from src.attrs.validations_bng import info_validations
from src.extract.components import Beam
from src.extract.drivetrain import Device, DrivetrainDataError
from src.extract.drivetrain import check_transmission_structure
from src.extract.nodes import Node
from src.extract.steered_axle import virtualize_steered_axle
from src.extract.steering import _on_rail


def dev(dtype, name, inp, idx=1, **params) -> Device:
    return Device(dtype, name, inp, idx, "sintética", params)


ENGINE = dev("combustionEngine", "mainEngine", "dummy", 0)


class TestManualWithRangeBox(unittest.TestCase):
    """4x4 con reductora: manualGearbox + rangeBox [1, 2.5]."""

    def test_accepted_when_initial_range_is_one(self) -> None:
        clutch = dev("frictionClutch", "clutch", "mainEngine")
        gb = dev("manualGearbox", "gearbox", "clutch")
        rb = dev("rangeBox", "rangebox", "gearbox", gearRatios=[1, 2.5])
        conv, rbox, issues = check_transmission_structure(
            ENGINE, gb, [clutch], [], [rb]
        )
        self.assertIsNone(conv)
        self.assertIs(rbox, rb)
        self.assertTrue(any("2.5" in i for i in issues))

    def test_initial_range_not_one_is_rejected(self) -> None:
        clutch = dev("frictionClutch", "clutch", "mainEngine")
        gb = dev("manualGearbox", "gearbox", "clutch")
        rb = dev("rangeBox", "rangebox", "gearbox", gearRatios=[2.5, 1])
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, gb, [clutch], [], [rb])

    def test_manual_with_converter_still_rejected(self) -> None:
        gb = dev("manualGearbox", "gearbox", "clutch")
        tc = dev("torqueConverter", "torqueConverter", "mainEngine")
        rb = dev("rangeBox", "rangebox", "gearbox", gearRatios=[1, 2.5])
        with self.assertRaises(DrivetrainDataError):
            check_transmission_structure(ENGINE, gb, [], [tc], [rb])


class TestOnRail(unittest.TestCase):
    NODES = {"a": (-1.0, 0.0, 0.0), "b": (1.0, 0.0, 0.0), "m": (0.0, 0.0, 0.0)}

    def test_end_and_middle(self) -> None:
        self.assertTrue(_on_rail("a", ["a", "b"], self.NODES))
        self.assertTrue(_on_rail("m", ["a", "b"], self.NODES))

    def test_off_line_or_beyond_end(self) -> None:
        nodes = dict(self.NODES, o=(0.0, 0.1, 0.0), far=(2.0, 0.0, 0.0))
        self.assertFalse(_on_rail("o", ["a", "b"], nodes))
        self.assertFalse(_on_rail("far", ["a", "b"], nodes))
        self.assertFalse(_on_rail("missing", ["a", "b"], nodes))


def _node(i: str, x: float, y: float, z: float) -> Node:
    return Node(i, (x, y, z), (x, y, z), 1.0, "t", None)


def _beam(a: str, b: str) -> Beam:
    return Beam("t", (a, b), None, {"beamSpring": 5e6})


def _axle_model() -> tuple[dict, list, dict, set]:
    """Eje rígido direccional sintético y simétrico: tubo ``c`` + montajes de bielas,
    una mangueta por lado (rótulas T/B, barra R, pinza K) y bielas a la carrocería."""
    nodes: dict[str, Node] = {"c": _node("c", 0, 0, 0)}
    beams: list[Beam] = []
    hubs: dict[str, tuple[str, str]] = {}
    tube = {"c"}
    for s, w in ((-1, "r"), (1, "l")):
        pts = {
            f"hi{w}": (s * 0.6, 0, 0),
            f"ho{w}": (s * 0.8, 0, 0),
            f"T{w}": (s * 0.65, 0, 0.11),
            f"B{w}": (s * 0.67, 0, -0.11),
            f"R{w}": (s * 0.70, -0.15, 0),
            f"K{w}": (s * 0.70, 0.15, 0),
            f"u{w}": (s * 0.40, 0, 0.11),  # montaje de la biela superior en el tubo
            f"l{w}": (s * 0.40, 0, -0.11),
            f"s{w}": (s * 0.30, -0.15, 0),  # extremo de la cremallera
            f"cu{w}": (s * 0.40, 0.40, 0.10),  # carrocería
            f"cl{w}": (s * 0.40, 0.40, -0.10),
        }
        for k, p in pts.items():
            nodes[k] = _node(k, *p)
        tube |= {f"u{w}", f"l{w}", f"s{w}"}
        hubs[w] = (f"hi{w}", f"ho{w}")
        for h in (f"hi{w}", f"ho{w}"):
            beams += [_beam(h, f"{x}{w}") for x in "TBRK"]
        beams += [_beam(f"T{w}", t) for t in ("c", f"u{w}", f"l{w}")]
        beams += [_beam(f"B{w}", t) for t in ("c", f"u{w}", f"l{w}")]
        beams += [_beam(f"R{w}", f"s{w}")]
        beams += [_beam(f"u{w}", f"cu{w}"), _beam(f"l{w}", f"cl{w}")]
    return nodes, beams, hubs, tube


class TestSteeredAxle(unittest.TestCase):
    def test_five_virtual_rods_per_wheel(self) -> None:
        nodes, beams, hubs, tube = _axle_model()
        res = virtualize_steered_axle(
            hubs=hubs,
            axle_nodes=tube | {n for n in nodes if n[0] in "TBRK"},
            nodes=nodes,
            rigid_beams=beams,
            exclude=set(),
        )
        self.assertIsNotNone(res)
        out_nodes, out_beams, info = res
        for w in "rl":
            virtual = [
                b
                for b in out_beams
                if b.part == "_virtual" and b.nodes[1] in (f"T{w}", f"B{w}", f"R{w}")
            ]
            self.assertEqual(len(virtual), 5, w)
            # triángulo superior: dos pivotes simétricos respecto a la rótula
            top = sorted(b.nodes[0] for b in virtual if b.nodes[1] == f"T{w}")
            p1, p2 = (out_nodes[n].pos for n in top)
            self.assertAlmostEqual(p1[0] + p2[0], 2 * nodes[f"T{w}"].pos[0])
            self.assertEqual((p1[1], p1[2]), (0.40, 0.10))
            # la barra de dirección conserva su nodo real (extremo de la cremallera)
            self.assertIn((f"s{w}", f"R{w}"), [b.nodes for b in virtual])
        # las vigas de la mangueta al tubo desaparecen
        self.assertFalse(
            any(
                b.part == "t" and b.nodes in (("Tr", "c"), ("Br", "ur"))
                for b in out_beams
            )
        )

    def test_independent_wheels_are_not_a_steered_axle(self) -> None:
        nodes, beams, hubs, tube = _axle_model()
        # sin vigas de la mangueta al tubo no hay rótulas con ≥ 3 enlaces
        beams = [b for b in beams if b.nodes[1] not in ("c", "ur", "lr", "ul", "ll")]
        self.assertIsNone(
            virtualize_steered_axle(
                hubs=hubs,
                axle_nodes=tube,
                nodes=nodes,
                rigid_beams=beams,
                exclude=set(),
            )
        )


class TestInfoValidations(unittest.TestCase):
    def _facts(self, **kw) -> dict:
        base = {
            "ir.declared.drivetrain": "4WD",
            "ir.drivetrain.layout": "AWD",
            "ir.declared.weight": 1828.0,
            "ir.mass.total": 1866.83,
        }
        base.update(kw)
        return {k: Fact(k, v, "1", "extracted") for k, v in base.items()}

    def _by_id(self, facts) -> dict:
        return {v.id.split(".")[-1]: v for v in info_validations("car", "x", facts)}

    def test_4wd_label_equals_awd_layout(self) -> None:
        self.assertEqual(self._by_id(self._facts())["traction"].result, "pass")
        f = self._facts(**{"ir.declared.drivetrain": "RWD"})
        self.assertEqual(self._by_id(f)["traction"].result, "fail")

    def test_in_game_mass_overrides_info_weight(self) -> None:
        ref = S.EvidenceRef(type="file", path="m.json", locator="x")
        f = self._facts(**{"ir.mass.in_game_total": 1866.5})
        f["ir.mass.in_game_total"].refs = [ref]
        v = self._by_id(f)
        self.assertEqual(v["mass"].result, "fail")  # la ficha de Automation no cuadra
        self.assertEqual(v["mass_in_game"].result, "pass")
        self.assertEqual(v["mass_in_game"].method, "in_game_value")
        self.assertEqual(v["mass_in_game"].evidence, [ref])

    def test_in_game_mass_mismatch_fails(self) -> None:
        f = self._facts(**{"ir.mass.in_game_total": 1900.0})
        self.assertEqual(self._by_id(f)["mass_in_game"].result, "fail")


class TestAdvAwdLuaIsKnown(unittest.TestCase):
    """camso_advawd.lua (38597, 2026-11-17): Lua propio del exportador, no invalida la equivalencia."""

    def test_registered_with_its_sha(self) -> None:
        from src.importers.automation import KNOWN_LUA

        self.assertIn(
            "9d20181f2ff32d8dc5f2f3544f5b318f8a0fa435df4367005cf714ffdeefa9a5",
            KNOWN_LUA["camso_advawd"],
        )

if __name__ == "__main__":
    unittest.main()
