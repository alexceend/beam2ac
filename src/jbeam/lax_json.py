"""Lector tolerante de JSON "laxo" tal como aparece en archivos JBeam y .pc.

Diferencias con JSON estricto que se ACEPTAN (cada una se registra como
:class:`Leniency`, para que nada se tolere en silencio):

* comentarios ``// ...`` hasta fin de línea y ``/* ... */``;
* coma omitida entre dos valores de un array o entre dos miembros de un objeto;
* coma final antes de ``]`` o ``}``;
* comas repetidas (``,,``), coma justo tras ``[``/``{`` y comas después del
  valor raíz (vistas en ``camso_suspension_R_35b84.jbeam`` y
  ``camso_engine_structure_35b84.jbeam``, que BeamNG carga);
* BOM UTF-8 al principio del archivo;
* una o más comas entre ``:`` y el valor (``"clave":, {...}``), vistas en 19 JBeam
  oficiales (p. ej. ``bluebuck_bumper_F.jbeam``: ``{"bluebuck_bumper_F":,``). El
  decodificador del juego (``lua/common/json.lua``, lector de objetos tras ``readKey``)
  salta espacios **y comas** antes de leer el valor, así que el valor es el siguiente
  elemento. Si tras las comas no hay un valor (``{"a":,}``) sigue siendo un error,
  como en el juego (``peekTable['}']`` es ``error_input``).

Ampliación del 2026-10-26 (22 JBeam oficiales; cada caso citado por su código en
``lua/common/json.lua`` del juego, ver ``docs/research/jbeam_lector.md``):

* ``plus_sign``: ``+`` delante de un número (``[3000, +9]``). ``peekTable[43]`` llama
  a ``readNumber`` tras el ``+``. Solo si sigue un dígito o un punto.
* ``number_format``: número sin parte entera tras un signo (``-.013``, ``+.5``) o
  sin decimales tras el punto (``0.``, ``1.``). ``readNumber`` acepta cero dígitos a
  ambos lados del punto. Un número que empieza por ``.`` (``[.5]``) sigue siendo un
  error, como en el juego (``peekTable[46]`` es ``error_input``), y también ``-.``
  sin ningún dígito (más estricto que el juego, que lo lee como 0).
* ``comma_before_colon``: comas entre la clave y ``:`` (``"spoke1",: {...}``);
  ``readKey`` salta espacios y comas antes de exigir ``:``.
* ``content_after_root``: ``}`` o ``]`` sobrantes tras la raíz (``},\\n}``).
  ``decode`` devuelve la raíz sin mirar lo que sigue. Solo se toleran cierres y
  comas: otro contenido (p. ej. un segundo objeto) sigue siendo un error, aunque el
  juego lo ignoraría, para no descartar datos en silencio.
* ``implicit_root_object``: documento sin ``{`` inicial que empieza por
  ``"clave": valor`` (``sunburst2_exhaust_turbo_race.jbeam``). ``decode`` lee pares
  clave/valor de primer nivel hasta el final. Un cierre sobrante en ese modo sigue
  siendo un error, como en el juego (``readKey`` no acepta ``}``).

Todo lo demás es un :class:`JBeamSyntaxError` con línea y columna: llaves o
corchetes sin cerrar, cadenas sin terminar, claves que no son cadenas (el juego
admite claves sin comillas y el separador ``=``, pero no aparecen en ningún archivo
y no se toleran), ``:`` que falta, identificadores desconocidos, contenido tras la
raíz que no sean cierres o comas, etc. No se intenta "reparar" el texto.

Claves duplicadas dentro de un objeto: se conserva la ÚLTIMA (como hace
``json.loads``) y se registra una :class:`Leniency` ``duplicate_key``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

# Tipos de tolerancia registrados.
COMMENT = "comment"
MISSING_COMMA = "missing_comma"
TRAILING_COMMA = "trailing_comma"
EXTRA_COMMA = "extra_comma"
DUPLICATE_KEY = "duplicate_key"
BOM = "bom"
COMMA_AFTER_COLON = "comma_after_colon"
PLUS_SIGN = "plus_sign"
NUMBER_FORMAT = "number_format"
COMMA_BEFORE_COLON = "comma_before_colon"
CONTENT_AFTER_ROOT = "content_after_root"
IMPLICIT_ROOT_OBJECT = "implicit_root_object"

_PLAIN = re.compile(r'[^"\\\n]+')
_SPACE = re.compile(r"[ \t\r\n]+")
_STOP = '"\\\n'
_LITERALS: dict[str, Any] = {"true": True, "false": False, "null": None}
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class JBeamSyntaxError(ValueError):
    """Error de sintaxis con posición (1-based)."""

    def __init__(self, message: str, line: int, column: int, source: str = "") -> None:
        self.message = message
        self.line = line
        self.column = column
        self.source = source
        where = f"{source}:" if source else ""
        super().__init__(f"{where}{line}:{column}: {message}")


@dataclass(frozen=True)
class Leniency:
    """Desviación de JSON estricto que se ha aceptado."""

    kind: str
    line: int
    column: int
    detail: str = ""


@dataclass
class LaxDocument:
    """Resultado de leer un archivo: el valor raíz y las tolerancias aplicadas."""

    value: Any
    source: str = ""
    leniencies: list[Leniency] = field(default_factory=list)

    def count(self, kind: str) -> int:
        return sum(1 for item in self.leniencies if item.kind == kind)


class _Token(NamedTuple):  # decenas de miles por archivo: más barato que dataclass
    kind: str  # "punct" | "string" | "number" | "literal"
    value: Any
    line: int
    column: int
    text: str


class _Lexer:
    def __init__(self, text: str, source: str, notes: list[Leniency]) -> None:
        self.text = text
        self.source = source
        self.notes = notes
        self.pos = 0
        self.line = 1
        self.col = 1
        if text.startswith("﻿"):
            notes.append(Leniency(BOM, 1, 1))
            self.pos = 1

    def _error(self, message: str, line: int | None = None, col: int | None = None):
        return JBeamSyntaxError(
            message,
            self.line if line is None else line,
            self.col if col is None else col,
            self.source,
        )

    def _advance(self, count: int = 1) -> None:
        seg = self.text[self.pos : self.pos + count]
        nl = seg.count("\n")
        if nl:
            self.line += nl
            self.col = count - seg.rfind("\n")
        else:
            self.col += count
        self.pos += count

    def tokens(self) -> list[_Token]:
        out: list[_Token] = []
        text = self.text
        n = len(text)
        while self.pos < n:
            ch = text[self.pos]
            if ch in " \t\r\n":
                self._advance(_SPACE.match(text, self.pos).end() - self.pos)
                continue
            line, col = self.line, self.col
            if ch == "/" and text.startswith("//", self.pos):
                end = text.find("\n", self.pos)
                end = n if end < 0 else end
                self.notes.append(Leniency(COMMENT, line, col, "//"))
                self._advance(end - self.pos)
                continue
            if ch == "/" and text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                if end < 0:
                    raise self._error("comentario /* sin cerrar", line, col)
                self.notes.append(Leniency(COMMENT, line, col, "/* */"))
                self._advance(end + 2 - self.pos)
                continue
            if ch in "[]{}:,":
                out.append(_Token("punct", ch, line, col, ch))
                self.pos += 1
                self.col += 1
                continue
            if ch == '"':
                out.append(self._string(line, col))
                continue
            if ch == "-" or ch.isdigit():
                out.append(self._number(line, col))
                continue
            if ch == "+" and text[self.pos + 1 : self.pos + 2] in tuple("0123456789."):
                # json.lua peekTable[43]: lee el número que sigue al '+'.
                self.notes.append(Leniency(PLUS_SIGN, line, col))
                self._advance()
                out.append(self._number(line, col, plus=True))
                continue
            if ch.isalpha() or ch == "_":
                start = self.pos
                while self.pos < n and (
                    text[self.pos].isalnum() or text[self.pos] == "_"
                ):
                    self._advance()
                word = text[start : self.pos]
                if word not in _LITERALS:
                    raise self._error(
                        f"identificador no válido '{word}' "
                        "(las claves y textos van entre comillas)",
                        line,
                        col,
                    )
                out.append(_Token("literal", _LITERALS[word], line, col, word))
                continue
            raise self._error(f"carácter inesperado {ch!r}", line, col)
        return out

    def _string(self, line: int, col: int) -> _Token:
        text = self.text
        self._advance()  # comilla inicial
        parts: list[str] = []
        start = self.pos
        while True:
            if self.pos >= len(text):
                raise self._error("cadena sin terminar", line, col)
            if text[self.pos] not in _STOP:  # salta en bloque los caracteres normales
                m = _PLAIN.match(text, self.pos)
                self._advance(m.end() - self.pos)
                continue
            ch = text[self.pos]
            if ch == '"':
                parts.append(text[start : self.pos])
                self._advance()
                break
            if ch == "\n":
                raise self._error("salto de línea dentro de una cadena", line, col)
            if ch == "\\":
                parts.append(text[start : self.pos])
                if self.pos + 1 >= len(text):
                    raise self._error("cadena sin terminar", line, col)
                esc = text[self.pos + 1]
                if esc in _ESCAPES:
                    parts.append(_ESCAPES[esc])
                    self._advance(2)
                elif esc == "u":
                    hexdigits = text[self.pos + 2 : self.pos + 6]
                    try:
                        parts.append(chr(int(hexdigits, 16)))
                    except ValueError:
                        raise self._error(
                            f"escape \\u inválido: {hexdigits!r}"
                        ) from None
                    if len(hexdigits) != 4:
                        raise self._error(f"escape \\u inválido: {hexdigits!r}")
                    self._advance(6)
                else:
                    raise self._error(f"escape inválido \\{esc}")
                start = self.pos
                continue
            self._advance()
        value = "".join(parts)
        return _Token("string", value, line, col, value)

    def _number(self, line: int, col: int, plus: bool = False) -> _Token:
        """Número JSON más dos formas que acepta ``readNumber`` de json.lua.

        ``-.5``/``+.5`` (sin parte entera tras un signo) y ``5.`` (punto sin
        decimales) se registran como ``number_format``. Hace falta al menos un
        dígito: ``-.`` sigue siendo un error.
        """
        text = self.text
        n = len(text)
        start = self.pos
        i = self.pos
        signed = plus
        if text[i] == "-":
            i += 1
            signed = True
        digits_start = i
        while i < n and text[i].isdigit():
            i += 1
        int_digits = i - digits_start
        if int_digits == 0 and not (signed and i < n and text[i] == "."):
            raise self._error("número sin dígitos", line, col)
        is_float = False
        if i < n and text[i] == ".":
            i += 1
            frac_start = i
            while i < n and text[i].isdigit():
                i += 1
            frac_digits = i - frac_start
            if int_digits == 0 and frac_digits == 0:
                raise self._error("número sin dígitos", line, col)
            if int_digits == 0 or frac_digits == 0:
                what = "sin parte entera" if int_digits == 0 else "punto sin decimales"
                self.notes.append(
                    Leniency(NUMBER_FORMAT, line, col, f"{what}: {text[start:i]}")
                )
            is_float = True
        if i < n and text[i] in "eE":
            i += 1
            if i < n and text[i] in "+-":
                i += 1
            exp_start = i
            while i < n and text[i].isdigit():
                i += 1
            if i == exp_start:
                raise self._error("exponente sin dígitos", line, col)
            is_float = True
        raw = text[start:i]
        self._advance(i - start)
        value: int | float = float(raw) if is_float else int(raw)
        return _Token("number", value, line, col, ("+" if plus else "") + raw)


class _Parser:
    def __init__(
        self, tokens: list[_Token], source: str, notes: list[Leniency]
    ) -> None:
        self.tokens = tokens
        self.source = source
        self.notes = notes
        self.i = 0

    def _peek(self) -> _Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _error_at(self, tok: _Token | None, message: str) -> JBeamSyntaxError:
        if tok is None:
            last = self.tokens[-1] if self.tokens else None
            line = last.line if last else 1
            col = (last.column + len(last.text)) if last else 1
            return JBeamSyntaxError(
                f"fin de archivo inesperado: {message}", line, col, self.source
            )
        return JBeamSyntaxError(message, tok.line, tok.column, self.source)

    def parse_document(self) -> Any:
        if not self.tokens:
            raise JBeamSyntaxError("archivo vacío", 1, 1, self.source)
        first = self.tokens[0]
        second = self.tokens[1] if len(self.tokens) > 1 else None
        if (
            first.kind == "string"
            and second is not None
            and second.kind == "punct"
            and second.value in ":,"
        ):
            # json.lua decode(): sin '{'/'[' inicial lee pares clave/valor hasta el
            # final del texto. Un cierre sobrante en este modo es un error (readKey).
            self.notes.append(Leniency(IMPLICIT_ROOT_OBJECT, first.line, first.column))
            return self._members(None, None)
        value = self.value()
        while (tok := self._peek()) is not None and tok.kind == "punct":
            if tok.value == ",":
                self.notes.append(
                    Leniency(EXTRA_COMMA, tok.line, tok.column, "tras la raíz")
                )
            elif tok.value in "}]":
                # json.lua decode() devuelve la raíz sin leer lo que sigue.
                self.notes.append(
                    Leniency(CONTENT_AFTER_ROOT, tok.line, tok.column, tok.value)
                )
            else:
                break
            self.i += 1
        extra = self._peek()
        if extra is not None:
            raise self._error_at(
                extra, f"contenido después del valor raíz: {extra.text!r}"
            )
        return value

    def value(self) -> Any:
        tok = self._peek()
        if tok is None:
            raise self._error_at(None, "se esperaba un valor")
        if tok.kind == "punct":
            if tok.value == "{":
                return self._object()
            if tok.value == "[":
                return self._array()
            raise self._error_at(
                tok, f"se esperaba un valor y se encontró {tok.text!r}"
            )
        self.i += 1
        return tok.value

    def _starts_value(self, tok: _Token | None) -> bool:
        return tok is not None and (tok.kind != "punct" or tok.value in "[{")

    def _array(self) -> list[Any]:
        open_tok = self.tokens[self.i]
        self.i += 1
        items: list[Any] = []
        expecting_value = True  # justo tras "[" o tras ","
        while True:
            tok = self._peek()
            if tok is None:
                raise self._error_at(open_tok, "'[' sin cerrar")
            if tok.kind == "punct" and tok.value == "]":
                if items and expecting_value:
                    self.notes.append(Leniency(TRAILING_COMMA, tok.line, tok.column))
                self.i += 1
                return items
            if tok.kind == "punct" and tok.value == ",":
                if expecting_value:
                    self.notes.append(Leniency(EXTRA_COMMA, tok.line, tok.column))
                self.i += 1
                expecting_value = True
                continue
            if not self._starts_value(tok):
                raise self._error_at(tok, f"token inesperado {tok.text!r} en un array")
            if not expecting_value:
                self.notes.append(Leniency(MISSING_COMMA, tok.line, tok.column))
            items.append(self.value())
            expecting_value = False

    def _object(self) -> dict[str, Any]:
        open_tok = self.tokens[self.i]
        self.i += 1
        return self._members(open_tok, "}")

    def _members(self, open_tok: _Token | None, close: str | None) -> dict[str, Any]:
        """Miembros de un objeto hasta ``close`` o, si es ``None``, hasta el final
        del texto (raíz implícita)."""
        members: dict[str, Any] = {}
        expecting_member = True
        while True:
            tok = self._peek()
            if tok is None:
                if close is None:
                    return members
                raise self._error_at(open_tok, "'{' sin cerrar")
            if tok.kind == "punct" and tok.value == "}":
                if close is None:
                    raise self._error_at(tok, "'}' sin '{' en una raíz sin llaves")
                if members and expecting_member:
                    self.notes.append(Leniency(TRAILING_COMMA, tok.line, tok.column))
                self.i += 1
                return members
            if tok.kind == "punct" and tok.value == ",":
                if expecting_member:
                    self.notes.append(Leniency(EXTRA_COMMA, tok.line, tok.column))
                self.i += 1
                expecting_member = True
                continue
            if tok.kind != "string":
                raise self._error_at(
                    tok, f"la clave debe ser una cadena, no {tok.text!r}"
                )
            if not expecting_member:
                self.notes.append(Leniency(MISSING_COMMA, tok.line, tok.column))
            self.i += 1
            # "clave",: valor — readKey de json.lua salta comas antes de ':'.
            while (
                (comma := self._peek()) is not None
                and comma.kind == "punct"
                and comma.value == ","
            ):
                self.notes.append(
                    Leniency(COMMA_BEFORE_COLON, comma.line, comma.column, tok.value)
                )
                self.i += 1
            colon = self._peek()
            if colon is None or colon.kind != "punct" or colon.value != ":":
                raise self._error_at(
                    colon, f"falta ':' después de la clave {tok.value!r}"
                )
            self.i += 1
            # "clave":, valor — el juego ignora las comas entre ':' y el valor.
            while (
                (comma := self._peek()) is not None
                and comma.kind == "punct"
                and comma.value == ","
            ):
                self.notes.append(
                    Leniency(COMMA_AFTER_COLON, comma.line, comma.column, tok.value)
                )
                self.i += 1
            if tok.value in members:
                self.notes.append(
                    Leniency(DUPLICATE_KEY, tok.line, tok.column, tok.value)
                )
                del members[tok.value]  # la última gana y queda en su posición
            members[tok.value] = self.value()
            expecting_member = False


def loads(text: str, source: str = "") -> LaxDocument:
    """Lee texto JSON laxo. Lanza JBeamSyntaxError si no es válido."""
    notes: list[Leniency] = []
    tokens = _Lexer(text, source, notes).tokens()
    value = _Parser(tokens, source, notes).parse_document()
    return LaxDocument(value=value, source=source, leniencies=notes)


def load(path: str | Path) -> LaxDocument:
    """Lee un archivo (UTF-8) en solo lectura."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return loads(text, source=str(path))
