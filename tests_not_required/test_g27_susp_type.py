"""Apartado de ``tests/test_g27_susp_type.py``.

Contenido: clase ``TestV1EqualsV2``.
Ver ``tests_not_required/README.md``.
"""

import unittest
from tests.test_g27_susp_type import LABELS, _facts, _resolve


class TestV1EqualsV2(unittest.TestCase):
    def test_labels(self) -> None:
        for axle in ("front", "rear"):
            target = f"ac:suspensions.ini/{axle.upper()}/TYPE"
            for label in LABELS + [None]:
                facts = _facts(axle, label)
                r1 = _resolve(facts, target, True)
                r2 = _resolve(facts, target, False)
                with self.subTest(axle=axle, label=label):
                    for f in (
                        "value",
                        "unit",
                        "status",
                        "confidence",
                        "source",
                        "assumptions",
                        "open_issues",
                        "requires_approval",
                    ):
                        self.assertEqual(getattr(r1, f), getattr(r2, f), f)
                    if r2.value is None:
                        why = [
                            x["reason"] for x in r2.rejected if "susp.type" in x["rule"]
                        ]
                        expect = (
                            "falta el hecho ir.suspension"
                            if label is None
                            else "text_contains_map: sin correspondencia"
                        )
                        self.assertTrue(any(expect in w for w in why), why)
                    else:
                        self.assertEqual(r2.value, "STRUT")
                        self.assertEqual(
                            r2.inputs[0]["fact"], f"ir.suspension.axles.{axle}.label"
                        )
