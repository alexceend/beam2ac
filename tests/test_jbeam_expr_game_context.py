"""Expresiones de los coches oficiales: case(), == nil y operadores (expressionParser.lua)."""

import unittest

from src.jbeam.expr import ExpressionError, evaluate

V = {"$a": 2.0, "$b": None}


class TestGameExpressions(unittest.TestCase):
    def test_values(self) -> None:
        cases = [
            ("$=case($b == nil, $a*900, 5)", 1800.0),
            ("$=case($a == nil, 1, $a+1)", 3.0),
            ("$=case(2, 10, 20, 30)", 20.0),
            ("$=case(9, 10, 20, 30)", 30.0),  # fuera de rango: el último
            ("$=$a ^ 2 + 1", 5.0),
            ("$=-2^2", -4.0),
            ("$=5 % 3", 2.0),
            ("$=-5 % 3", 1.0),  # módulo de Lua: signo del divisor
            ("$=$b == nil and 1 or 2", 1.0),
            ("$=$a >= 2", True),
            ("$=$b ~= nil", False),
            ("$=max(1, 3)", 3.0),
            ("$=clamp(5, 0, 2)", 2.0),
            ("$='x'", "x"),
        ]
        for expr, want in cases:
            with self.subTest(expr):
                self.assertEqual(evaluate(expr, V), want)

    def test_errors_are_explicit(self) -> None:
        for expr in ("$=case('a', 1, 2)", "$=1 + nil", "$=foo(1)", "$=max(1,"):
            with self.subTest(expr):
                with self.assertRaises(ExpressionError):
                    evaluate(expr, V)


if __name__ == "__main__":
    unittest.main()
