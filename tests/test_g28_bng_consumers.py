"""G2.8: consumidores de hechos ``bng.*`` fuera del catálogo.

``validations_bng`` y ``candidates`` leían 20 hechos ``bng.*``; ahora leen el hecho
``ir.*`` del MISMO parámetro del IR. Se comprueba:

* por construcción: cada par (``bng``, ``ir``) sale del mismo parámetro
  (``legacy_facts.legacy_id``);
* en el código: no queda ninguna lectura ``bng.*`` (solo el ``covers`` publicado de la
  validación de masa, que sigue cubriendo ``bng.mass.total``);
* en el comportamiento: con SOLO los hechos ``ir.*`` las validaciones existen; con solo
  los ``bng.*``, ya no (prueba de que la lectura cambió de verdad).

La equivalencia de resultados en los 14 vehículos cargables está en
``output/g28_2026-11-01/consumidores_bng.py`` (código anterior reconstruido).
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from src.attrs.facts import Fact
from src.attrs.validations_bng import info_validations
from src.ir.legacy_facts import legacy_id
from src.ir.vehicle import VehicleIR

ROOT = Path(__file__).resolve().parents[1]
CONSUMERS = ("src/attrs/validations_bng.py", "src/attrs/candidates.py")
PAIRS = {
    "bng.engine.net_wot_curve": "ir.engine.wot_torque_curve",
    "bng.engine.peak_torque": "ir.engine.peak_torque",
    "bng.engine.peak_power": "ir.engine.peak_power",
    "bng.info.torque": "ir.declared.torque",
    "bng.info.exporter_version": "ir.formats.exporter_version",
    "bng.info.implied_power": "ir.declared.implied_power",
    "bng.info.power": "ir.declared.power",
    "bng.info.torque_peak_rpm": "ir.declared.torque_peak_speed",
    "bng.info.power_peak_rpm": "ir.declared.power_peak_speed",
    "bng.info.drivetrain": "ir.declared.drivetrain",
    "bng.drivetrain.traction": "ir.drivetrain.layout",
    "bng.info.weight": "ir.declared.weight",
    "bng.mass.total": "ir.mass.total",
    "bng.brakes.front_share_pedal_full": "ir.brakes.front_share_full_pedal",
    "bng.brakes.front_share_below_knee": "ir.brakes.front_share_below_knee",
    "bng.brakes.front.max_torque_per_wheel": (
        "ir.brakes.axles.front.max_torque_per_wheel"
    ),
    "bng.brakes.rear.max_torque_per_wheel": "ir.brakes.axles.rear.max_torque_per_wheel",
    "bng.brakes.rear.parking_torque_per_wheel": (
        "ir.brakes.axles.rear.parking_torque_per_wheel"
    ),
    "bng.aero.cla_front": "ir.aero.cla_front",
    "bng.aero.cla_rear": "ir.aero.cla_rear",
}


def _string_literals(rel: str) -> list[str]:
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


class TestPairsByConstruction(unittest.TestCase):
    def test_same_ir_parameter(self) -> None:
        ir = VehicleIR()
        for b, i in PAIRS.items():
            with self.subTest(fact=b):
                self.assertEqual(legacy_id(i[3:], ir), b)


class TestNoBngReads(unittest.TestCase):
    def test_only_published_covers_remains(self) -> None:
        for rel in CONSUMERS:
            with self.subTest(file=rel):
                bng = sorted(s for s in _string_literals(rel) if s.startswith("bng."))
                expected = (
                    ["bng.mass.total"] if rel.endswith("validations_bng.py") else []
                )
                self.assertEqual(bng, expected)

    def test_reads_are_ir_facts(self) -> None:
        used = {s for rel in CONSUMERS for s in _string_literals(rel)}
        self.assertLessEqual(set(PAIRS.values()), used)


def _facts(prefix_ir: bool) -> dict[str, Fact]:
    values = {
        "ir.engine.wot_torque_curve": [[1000.0, 100.0], [5000.0, 200.0]],
        "ir.engine.peak_torque": 200.0,
        "ir.engine.peak_power": 200.0 * 5000 * 3.141592653589793 / 30,
        "ir.declared.torque": 200,
        "ir.declared.power": 140,
        "ir.declared.drivetrain": "FWD",
        "ir.drivetrain.layout": "FWD",
        "ir.declared.weight": 1000,
        "ir.mass.total": 1000.2,
    }
    rev = {i: b for b, i in PAIRS.items()}
    out = {}
    for k, v in values.items():
        fid = k if prefix_ir else rev[k]
        out[fid] = Fact(fid, v, "1", "extracted", [])
    return out


class TestBehaviour(unittest.TestCase):
    def test_ir_facts_drive_the_validations(self) -> None:
        got = {
            v.id.rsplit(".", 1)[-1]: v for v in info_validations("v", "c", _facts(True))
        }
        self.assertEqual(sorted(got), ["engine_curve", "mass", "traction"])
        self.assertEqual(got["traction"].result, "pass")
        self.assertEqual(got["mass"].result, "pass")
        self.assertEqual(got["mass"].covers, ["bng.mass.total"])  # identidad publicada

    def test_bng_facts_alone_no_longer_read(self) -> None:
        self.assertEqual(info_validations("v", "c", _facts(False)), [])


if __name__ == "__main__":
    unittest.main()
