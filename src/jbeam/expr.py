"""Evaluador seguro de valores con variables de JBeam.

Formas de valor:

* ``"$nombre"`` → valor de la variable (``None`` si no existe).
* ``"$=expresión"`` → expresión con sintaxis de Lua.

Subconjunto soportado (inventario de las muestras de Automation y de los coches
oficiales ``pessima``/``etk800``, que usan ``case(...)`` y ``== nil``):

    números, 'cadenas', $variables, nil, true, false, ( ), unario -, + - * / % ^,
    == ~= < <= > >=, and, or, not y las funciones del contexto del juego
    (``expressionParser.lua``: ``case``, ``clamp``, ``round``, ``square``, ``sign`` y
    las de ``math`` sin prefijo)

Semántica de Lua: solo ``nil`` y ``false`` son falsos (``0 or 5`` vale 0);
``a or b`` devuelve ``a`` si es verdadero; aritmética con ``nil`` es un error;
``x/0`` da ±inf (o nan para ``0/0``). Cualquier otra construcción lanza
:class:`ExpressionError`; no se usa ``eval``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

_KEYWORDS = {"and", "or", "not", "nil", "true", "false"}


def _case(selector: Any, *args: Any) -> Any:
    """``case`` de ``expressionParser.lua``: bool -> 1.er/2.o arg; numero n -> arg n
    (1-based, ``floor``); fuera de rango, el ultimo."""
    if isinstance(selector, bool):
        index = 1 if selector else 2
    elif isinstance(selector, (int, float)):
        index = math.floor(selector)
    else:
        raise ExpressionError("case solo admite booleanos o números como selector")
    if not args:
        return None
    value = args[index - 1] if 1 <= index <= len(args) else None
    return value if value is not None else args[-1]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _round(v: float, places: float = 0) -> float:
    mult = 10 ** int(places)
    return math.floor(v * mult + 0.5) / mult


def _sign(v: float) -> float:
    return float((v > 0) - (v < 0))


# funciones del contexto de expresiones del juego (sin prefijo ``math.``)
_FUNCTIONS: dict[str, Any] = {
    "case": _case,
    "clamp": _clamp,
    "round": _round,
    "square": lambda v: v * v,
    "sign": _sign,
    **{
        n: getattr(math, n)
        for n in ("floor", "ceil", "sqrt", "sin", "cos", "tan", "exp", "atan")
    },
    "abs": abs,
    "min": min,
    "max": max,
}


class ExpressionError(ValueError):
    """Expresión no válida o fuera del subconjunto soportado."""


def _tokenize(text: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            start = i
            while i < n and (text[i].isdigit() or text[i] == "."):
                i += 1
            if i < n and text[i] in "eE":
                i += 1
                if i < n and text[i] in "+-":
                    i += 1
                while i < n and text[i].isdigit():
                    i += 1
            raw = text[start:i]
            try:
                tokens.append(("num", float(raw)))
            except ValueError:
                raise ExpressionError(f"número inválido {raw!r}") from None
        elif ch == "$":
            start = i + 1
            i = start
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            if i == start:
                raise ExpressionError(f"'$' sin nombre de variable en {text!r}")
            tokens.append(("var", "$" + text[start:i]))
        elif ch.isalpha() or ch == "_":
            start = i
            while i < n and (text[i].isalnum() or text[i] == "_"):
                i += 1
            word = text[start:i]
            if word in _FUNCTIONS:
                tokens.append(("fn", word))
                continue
            if word not in _KEYWORDS:
                raise ExpressionError(
                    f"identificador no soportado {word!r} en {text!r}"
                )
            tokens.append(("kw", word))
        elif text[i : i + 2] in ("==", "~=", "<=", ">="):
            tokens.append(("op", text[i : i + 2]))
            i += 2
        elif ch in "+-*/()%^<>,":
            tokens.append(("op", ch))
            i += 1
        elif ch in ("'", '"'):
            end = text.find(ch, i + 1)
            if end < 0:
                raise ExpressionError(f"cadena sin cerrar en {text!r}")
            tokens.append(("str", text[i + 1 : end]))
            i = end + 1
        else:
            raise ExpressionError(f"símbolo no soportado {ch!r} en {text!r}")
    return tokens


_COMPARE = ("==", "~=", "<", "<=", ">", ">=")


def _equal(a: Any, b: Any) -> bool:
    """``==`` de Lua: tipos distintos son distintos (sin error); bool != numero."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return a == b


def _truthy(value: Any) -> bool:
    return value is not None and value is not False


def _num(value: Any, op: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExpressionError(
            f"operación aritmética '{op}' con valor no numérico {value!r}"
        )
    return float(value)


class _Parser:
    """Descenso recursivo con la precedencia de Lua.

    De menor a mayor: or < and < comparación < (+ -) < (* / %) < unario < ^.
    """

    def __init__(self, tokens: list[tuple[str, Any]], variables: Mapping[str, Any]):
        self.tokens = tokens
        self.variables = variables
        self.i = 0

    def _peek(self) -> tuple[str, Any] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _take(self, kind: str, value: Any = None) -> bool:
        tok = self._peek()
        if tok and tok[0] == kind and (value is None or tok[1] == value):
            self.i += 1
            return True
        return False

    def parse(self) -> Any:
        if not self.tokens:
            raise ExpressionError("expresión vacía")
        result = self._or()
        if self._peek() is not None:
            raise ExpressionError(f"token inesperado {self._peek()[1]!r}")
        return result

    # Nota: se evalúan ambos lados de and/or; con este subconjunto no hay efectos
    # secundarios, salvo que un error aritmético en la rama no usada también se
    # informa (más estricto que Lua, nunca más permisivo).
    def _or(self) -> Any:
        left = self._and()
        while self._take("kw", "or"):
            right = self._and()
            left = left if _truthy(left) else right
        return left

    def _and(self) -> Any:
        left = self._comparison()
        while self._take("kw", "and"):
            right = self._comparison()
            left = right if _truthy(left) else left
        return left

    def _comparison(self) -> Any:
        left = self._additive()
        while (tok := self._peek()) and tok[0] == "op" and tok[1] in _COMPARE:
            self.i += 1
            right = self._additive()
            if tok[1] == "==":
                left = _equal(left, right)
            elif tok[1] == "~=":
                left = not _equal(left, right)
            else:
                a, b = _num(left, tok[1]), _num(right, tok[1])
                left = {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[tok[1]]
        return left

    def _additive(self) -> Any:
        left = self._term()
        while (tok := self._peek()) and tok[0] == "op" and tok[1] in "+-":
            self.i += 1
            right = self._term()
            a, b = _num(left, tok[1]), _num(right, tok[1])
            left = a + b if tok[1] == "+" else a - b
        return left

    def _term(self) -> Any:
        left = self._unary()
        while (tok := self._peek()) and tok[0] == "op" and tok[1] in ("*", "/", "%"):
            self.i += 1
            right = self._unary()
            a, b = _num(left, tok[1]), _num(right, tok[1])
            if tok[1] == "*":
                left = a * b
            elif tok[1] == "%":
                left = math.fmod(a, b) if b != 0 else math.nan
                if left and (left < 0) != (b < 0):  # modulo de Lua: signo del divisor
                    left += b
            elif b != 0:
                left = a / b
            else:
                left = math.nan if a == 0 else math.copysign(math.inf, a)
        return left

    def _unary(self) -> Any:
        if self._take("op", "-"):
            return -_num(self._unary(), "-")
        if self._take("kw", "not"):
            return not _truthy(self._unary())
        return self._power()

    def _power(self) -> Any:
        base = self._primary()
        if self._take("op", "^"):  # asociativa por la derecha, mas fuerte que el unario
            exp = self._unary()
            a, b = _num(base, "^"), _num(exp, "^")
            try:
                return math.pow(a, b)
            except (ValueError, OverflowError):
                return math.nan
        return base

    def _primary(self) -> Any:
        tok = self._peek()
        if tok is None:
            raise ExpressionError("expresión incompleta")
        self.i += 1
        kind, value = tok
        if kind == "num":
            return value
        if kind == "str":
            return value
        if kind == "fn":
            if not self._take("op", "("):
                raise ExpressionError(f"falta '(' tras {value!r}")
            args: list[Any] = []
            if not self._take("op", ")"):
                args.append(self._or())
                while self._take("op", ","):
                    args.append(self._or())
                if not self._take("op", ")"):
                    raise ExpressionError("falta ')'")
            if value != "case":
                for a in args:
                    _num(a, value)
            try:
                return _FUNCTIONS[value](*args)
            except (TypeError, ValueError) as exc:
                raise ExpressionError(f"{value}(): {exc}") from None
        if kind == "var":
            return self.variables.get(value)
        if kind == "kw" and value in ("nil", "true", "false"):
            return {"nil": None, "true": True, "false": False}[value]
        if kind == "op" and value == "(":
            inner = self._or()
            if not self._take("op", ")"):
                raise ExpressionError("falta ')'")
            return inner
        raise ExpressionError(f"token inesperado {value!r}")


def is_dynamic(value: Any) -> bool:
    """True si el valor es una referencia ``$nombre`` o una expresión ``$=...``."""
    return isinstance(value, str) and value.startswith("$")


def evaluate(value: Any, variables: Mapping[str, Any]) -> Any:
    """Resuelve ``value`` si es dinámico; si no, lo devuelve sin cambios.

    ``variables`` usa las claves con ``$`` (``{"$frontPSI": 37.0}``).
    """
    if not is_dynamic(value):
        return value
    if value.startswith("$="):
        return _Parser(_tokenize(value[2:]), variables).parse()
    name = value
    if not name[1:] or not all(c.isalnum() or c == "_" for c in name[1:]):
        raise ExpressionError(f"referencia de variable inválida {value!r}")
    return variables.get(name)
