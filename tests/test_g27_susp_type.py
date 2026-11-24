"""G2.7, paso 2 (G2.6a): ``text_contains_map`` y ``susp.type_{front,rear}@2``.

* La transformación: coincidencia, sin coincidencia, mayúsculas, orden de los pares,
  parámetros y entradas inválidos.
* Las reglas: la tabla en el catálogo es la del código (sin ampliar); solo cambian la
  entrada y la transformación.
* @1 = @2 en valor, estado y confianza con etiquetas reales; etiqueta ausente → sin
  valor; sin correspondencia → sin valor (cambia solo el motivo del rechazo).
"""

from __future__ import annotations

import unittest

from src.attrs import schema as S
from src.attrs import transforms as T
from src.attrs.engine import ResolutionEngine
from src.attrs.facts import Fact
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract.suspension import TYPE_BY_LABEL
from src.ir.legacy_facts import AC_SUSPENSION_TYPE_BY_LABEL

PAIRS = [["macpherson", "STRUT"], ["strut", "STRUT"]]
LABELS = [  # etiquetas reales de los 13 proyectos con etiqueta (G2.6, evidencia.md)
    "ACFR MacPherson Strut Front Suspension",
    "ACFR Torsion Beam Rear Suspension",
    "poubelle MacPherson Strut Front Suspension",
    "poubelle Semi Trailing Arm Rear Suspension",
    "Espol VTEC MacPherson Strut Front Suspension",
    "Espol VTEC Double Wishbone Rear Suspension",
    "GT3 RS Double Wishbone Front Suspension",
    "CUANTO Double Wishbone Rear Suspension",
]


def tcm(text, pairs=PAIRS, unit="text", out="enum"):
    return T.apply("text_contains_map", {"text": (text, unit)}, {"pairs": pairs}, out)


class TestTransform(unittest.TestCase):
    def test_match_and_case(self) -> None:
        self.assertEqual(tcm("ACFR MacPherson Strut Front"), "STRUT")
        self.assertEqual(tcm("MACPHERSON"), "STRUT")
        self.assertEqual(tcm("strut"), "STRUT")
        self.assertEqual(tcm("x", [["X", "Y"]]), "Y")  # aguja en mayúsculas

    def test_first_pair_wins(self) -> None:
        pairs = [["arm", "A"], ["trailing", "B"]]
        self.assertEqual(tcm("Semi Trailing Arm", pairs), "A")
        self.assertEqual(tcm("Semi Trailing Arm", pairs[::-1]), "B")

    def test_no_match(self) -> None:
        with self.assertRaisesRegex(T.TransformError, "sin correspondencia"):
            tcm("Torsion Beam Rear Suspension")

    def test_invalid(self) -> None:
        for bad in ([], [["", "X"]], [["a"]], "macpherson", [[1, "X"]], None):
            with self.subTest(pairs=bad), self.assertRaises(T.TransformError):
                tcm("macpherson", bad)
        with self.assertRaises(T.TransformError):
            tcm("", PAIRS)
        with self.assertRaises(T.TransformError):
            tcm(3.0, PAIRS)
        with self.assertRaises(T.TransformError):
            tcm("macpherson", unit="enum")
        with self.assertRaises(T.TransformError):
            tcm("macpherson", out="N·m")
        with self.assertRaisesRegex(T.TransformError, "falta la entrada 'text'"):
            T.apply("text_contains_map", {}, {"pairs": PAIRS}, "enum")

    def test_same_as_code_table_on_real_labels(self) -> None:
        for label in LABELS:
            old = next(
                (t for key, t in AC_SUSPENSION_TYPE_BY_LABEL if key in label.lower()),
                None,
            )
            with self.subTest(label=label):
                if old is None:
                    with self.assertRaises(T.TransformError):
                        tcm(label)
                else:
                    self.assertEqual(tcm(label), old)


class TestRules(unittest.TestCase):
    def test_v2_definitions(self) -> None:
        cat = Catalog.load(REPO_CATALOG)
        self.assertEqual(tuple(TYPE_BY_LABEL), tuple(AC_SUSPENSION_TYPE_BY_LABEL))
        for axle in ("front", "rear"):
            v1, v2 = cat.rules[f"susp.type_{axle}@1"], cat.rules[f"susp.type_{axle}@2"]
            with self.subTest(axle=axle):
                self.assertEqual(v2.supersedes, v1.key)
                self.assertEqual(
                    [r.key for r in cat.rules_for(v1.target) if r.id == v1.id],
                    [v2.key],
                )
                self.assertEqual(
                    v2.inputs, {"text": f"ir.suspension.axles.{axle}.label"}
                )
                self.assertEqual(v2.transform.id, "text_contains_map")
                self.assertEqual(
                    v2.transform.params["pairs"],
                    [list(p) for p in AC_SUSPENSION_TYPE_BY_LABEL],
                )
                self.assertEqual(v2.transform.output_unit, "enum")
                for field in (
                    "scope",
                    "status",
                    "assumptions",
                    "validations",
                    "kind",
                    "target",
                    "conditions",
                    "applies_when",
                    "exceptions",
                    "open_issues",
                ):
                    self.assertEqual(
                        S._to_plain(getattr(v2, field)), S._to_plain(getattr(v1, field))
                    )


def _resolve(facts: dict, target: str, drop_v2: bool, vehicle="seat_ibiza_acfr"):
    cat = Catalog.load(REPO_CATALOG)
    if drop_v2:
        for axle in ("front", "rear"):
            cat.rules.pop(f"susp.type_{axle}@2")
    # el Ibiza tiene decisiones de TYPE (base aprobada) que prevalecen: aquí se
    # comparan solo las reglas
    cat.decisions = [d for d in cat.decisions if d.target != target]
    ctx = context_from_facts(vehicle, "35b84", facts, cat)
    return ResolutionEngine(cat, ctx).resolve(target)


def _facts(axle: str, label: str | None) -> dict:
    facts = {}
    if label is not None:
        fid = f"ir.suspension.axles.{axle}.label"
        facts[fid] = Fact(fid, label, "text", "extracted")
        old = next(
            (t for key, t in AC_SUSPENSION_TYPE_BY_LABEL if key in label.lower()), None
        )
        if (
            old is not None
        ):  # como legacy_facts: el hecho bng.* solo con correspondencia
            bid = f"bng.suspension.{axle}.ac_type"
            facts[bid] = Fact(bid, old, "enum", "computed")
    return facts


if __name__ == "__main__":
    unittest.main()
