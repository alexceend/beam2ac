"""Apartado de ``tests/test_g25b_rules_ir.py``.

Contenido: clase ``TestEquivalence``, métodos ['test_same_result_as_previous_version'].
Ver ``tests_not_required/README.md``.
"""

import unittest
from tests.test_g25b_rules_ir import (
    IBIZA,
    MIGRATED,
    SAMPLES,
    catalog,
    comparable,
    facts_of,
    new_and_old,
    resolve,
)


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

    def test_same_result_as_previous_version(self) -> None:
        cat = catalog()
        for src, facts in self.vehicles:
            for base, mapping in MIGRATED.items():
                new_rule, old_rule = new_and_old(cat, base)
                with self.subTest(vehicle=src.vehicle_id, rule=base):
                    new = resolve(src, facts, new_rule.target)
                    old = resolve(src, facts, new_rule.target, drop_new=True)
                    if new.rule == new_rule.key:
                        self.assertEqual(old.rule, old_rule.key)
                    else:  # gana otra regla (p. ej. la de arquitectura) o nada
                        self.assertEqual(new.rule, old.rule)
                    self.assertEqual(comparable(new, mapping), comparable(old, mapping))
                    if src.vehicle_id == "seat_ibiza_acfr":
                        self.assertEqual(new.source, "rule")
