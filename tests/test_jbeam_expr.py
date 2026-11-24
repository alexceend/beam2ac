import math
import unittest

from src.jbeam.expr import ExpressionError, evaluate, is_dynamic

VARS = {"$a": 2.0, "$zero": 0, "$off": False, "$b": 3.0}


class TestEvaluate(unittest.TestCase):
    def test_non_dynamic_values_pass_through(self):
        for value in (1.5, "texto", None, [1], {"a": 1}, "|NM_METAL"):
            self.assertEqual(evaluate(value, VARS), value)
            self.assertFalse(is_dynamic(value))

    def test_plain_reference(self):
        self.assertEqual(evaluate("$a", VARS), 2.0)
        self.assertIsNone(evaluate("$missing", VARS))

    def test_arithmetic_and_precedence(self):
        self.assertEqual(evaluate("$=1 + 2 * 3", VARS), 7)
        self.assertEqual(evaluate("$=(1 + 2) * 3", VARS), 9)
        self.assertEqual(evaluate("$=10 - 4 - 3", VARS), 3)
        self.assertEqual(evaluate("$=-$a * 2", VARS), -4)
        self.assertEqual(evaluate("$=$a / 4", VARS), 0.5)
        self.assertEqual(evaluate("$=1.5e1", VARS), 15)

    def test_lua_or_semantics(self):
        self.assertEqual(evaluate("$=$missing or 1", VARS), 1)
        self.assertEqual(evaluate("$=$zero or 5", VARS), 0)  # 0 es verdadero en Lua
        self.assertEqual(evaluate("$=$off or 7", VARS), 7)
        self.assertEqual(evaluate("$=($missing or 0) + ($a or 0)", VARS), 2)

    def test_and_not(self):
        self.assertEqual(evaluate("$=$a and $b", VARS), 3)
        self.assertIsNone(evaluate("$=nil and 1", VARS))
        self.assertTrue(evaluate("$=not nil", VARS))

    def test_real_sample_expressions(self):
        self.assertAlmostEqual(
            evaluate(
                "$=2716.932129*$BrakePowerFront*0.800000", {"$BrakePowerFront": 1}
            ),
            2173.5457032,
        )
        self.assertAlmostEqual(
            evaluate("$=0.0842997 + ($frontSuspensionOffsetX or 0)", {}), 0.0842997
        )

    def test_division_by_zero_follows_lua(self):
        self.assertEqual(evaluate("$=1/0", VARS), math.inf)
        self.assertEqual(evaluate("$=-1/0", VARS), -math.inf)
        self.assertTrue(math.isnan(evaluate("$=0/0", VARS)))


class TestExpressionErrors(unittest.TestCase):
    CASES = [
        "$=$missing * 2",  # aritmética con nil
        "$=math.max(1, 2)",  # funciones no soportadas
        "$=1 < 'a'",  # comparación de orden con no numérico
        "$=2^nil",
        "$=",
        "$=(1 + 2",
        "$=1 2",
        "$=$MaxRegenTorque$*(1-$x)",  # caso real mal formado (pieza inactiva)
        "$=true + 1",
        "$=",
        "$bad-name",
    ]

    def test_errors(self):
        for text in self.CASES:
            with self.subTest(text):
                with self.assertRaises(ExpressionError):
                    evaluate(text, VARS)


if __name__ == "__main__":
    unittest.main()
