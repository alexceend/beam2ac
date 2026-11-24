import json
import unittest

from src.jbeam.lax_json import (
    BOM,
    COMMA_BEFORE_COLON,
    COMMENT,
    CONTENT_AFTER_ROOT,
    DUPLICATE_KEY,
    EXTRA_COMMA,
    IMPLICIT_ROOT_OBJECT,
    MISSING_COMMA,
    NUMBER_FORMAT,
    PLUS_SIGN,
    TRAILING_COMMA,
    JBeamSyntaxError,
    loads,
)


class TestValidStructures(unittest.TestCase):
    def test_strict_json_matches_json_module(self):
        text = (
            '{"a": [1, -2, 3.5, -4e-2, 1E3], "b": {"c": "x\\"y\\\\z\\n\\u00e9"},'
            ' "d": [true, false, null], "e": [], "f": {}}'
        )
        doc = loads(text)
        self.assertEqual(doc.value, json.loads(text))
        self.assertEqual(doc.leniencies, [])

    def test_int_and_float_types_are_kept(self):
        value = loads("[1, 1.0, 2e1]").value
        self.assertIsInstance(value[0], int)
        self.assertIsInstance(value[1], float)
        self.assertIsInstance(value[2], float)

    def test_member_order_is_preserved(self):
        self.assertEqual(list(loads('{"z":1,"a":2,"m":3}').value), ["z", "a", "m"])


class TestComments(unittest.TestCase):
    def test_line_and_block_comments(self):
        text = """{
            // comentario de línea
            "a": 1, /* bloque
            multilínea */ "b": [1, /* dentro */ 2]
        }"""
        doc = loads(text)
        self.assertEqual(doc.value, {"a": 1, "b": [1, 2]})
        self.assertEqual(doc.count(COMMENT), 3)

    def test_comment_markers_inside_strings_are_text(self):
        doc = loads('{"url": "https://wiki.beamng.com/x", "c": "/* no */"}')
        self.assertEqual(doc.value["url"], "https://wiki.beamng.com/x")
        self.assertEqual(doc.value["c"], "/* no */")
        self.assertEqual(doc.count(COMMENT), 0)

    def test_commented_out_entries_disappear(self):
        doc = loads('[\n  //["a", 1],\n  ["b", 2]\n]')
        self.assertEqual(doc.value, [["b", 2]])


class TestLeniencies(unittest.TestCase):
    def test_missing_commas_are_recorded_with_position(self):
        doc = loads('{"a": 1\n "b": [1 2]}')
        self.assertEqual(doc.value, {"a": 1, "b": [1, 2]})
        notes = [n for n in doc.leniencies if n.kind == MISSING_COMMA]
        self.assertEqual([(n.line, n.column) for n in notes], [(2, 2), (2, 10)])

    def test_missing_comma_between_row_and_inline_dict(self):
        # Caso real: ["fh1r","fco1r"{"name":"spring_FR"}]
        doc = loads('[["fh1r","fco1r"{"name":"spring_FR"}]]')
        self.assertEqual(doc.value, [["fh1r", "fco1r", {"name": "spring_FR"}]])
        self.assertEqual(doc.count(MISSING_COMMA), 1)

    def test_trailing_commas(self):
        doc = loads('{"a": [1, 2,], "b": {"c": 1,},}')
        self.assertEqual(doc.value, {"a": [1, 2], "b": {"c": 1}})
        self.assertEqual(doc.count(TRAILING_COMMA), 3)

    def test_repeated_commas_and_comma_after_root(self):
        doc = loads('{"a": 1,, "b": [1,,2]},\n')
        self.assertEqual(doc.value, {"a": 1, "b": [1, 2]})
        self.assertEqual(doc.count(EXTRA_COMMA), 3)

    def test_bom(self):
        doc = loads('﻿{"a": 1}')
        self.assertEqual(doc.value, {"a": 1})
        self.assertEqual(doc.count(BOM), 1)

    def test_duplicate_key_last_wins_and_is_recorded(self):
        doc = loads('{"a": 1, "b": 2, "a": 3}')
        self.assertEqual(doc.value, {"b": 2, "a": 3})
        self.assertEqual(doc.count(DUPLICATE_KEY), 1)


class TestSyntaxErrors(unittest.TestCase):
    CASES = {
        "llave sin cerrar": '{"a": 1',
        "corchete sin cerrar": "[1, 2",
        "cierre que no corresponde": '{"a": [1}',
        "cadena sin terminar": '{"a": "abc}',
        "salto de línea en cadena": '{"a": "ab\ncd"}',
        "comentario sin cerrar": '{"a": 1 /* ...',
        "identificador suelto": '{"a": FLT_MAX}',
        "clave sin comillas": "{a: 1}",
        "falta dos puntos": '{"a" 1}',
        "clave numérica": "{1: 2}",
        "contenido tras la raíz": '{"a": 1} {"b": 2}',
        "valor tras cierre sobrante": '{"a": 1}} 5',
        "cierre sobrante en raíz implícita": '"a": 1 }',
        "raíz implícita sin dos puntos": '"a", 1',
        "coma antes de dos puntos sin dos puntos": '{"a", "b": 1}',
        "archivo vacío": "  // solo un comentario\n",
        "número con punto inicial": "[.5]",
        # "[5.]" y "[+5]" se aceptan desde 2026-10-26 (json.lua: readNumber y
        # peekTable[43]); ver TestOfficialSyntax2026. Siguen siendo errores:
        "signo más sin número": "[+]",
        "signo más y letra": "[+a]",
        "signo más y menos": "[+-5]",
        "solo signo y punto": "[-.]",
        "más y punto sin dígitos": "[+.]",
        "escape inválido": '["\\q"]',
        "unicode inválido": '["\\u12G4"]',
        "carácter extraño": "[1; 2]",
        "dos puntos en array": "[1: 2]",
    }

    def test_invalid_inputs_raise(self):
        for name, text in self.CASES.items():
            with self.subTest(name):
                with self.assertRaises(JBeamSyntaxError):
                    loads(text)

    def test_error_reports_line_column_and_source(self):
        with self.assertRaises(JBeamSyntaxError) as ctx:
            loads('{\n  "a": 1,\n  "b": FLT_MAX\n}', source="x.jbeam")
        err = ctx.exception
        self.assertEqual((err.line, err.column), (3, 8))
        self.assertIn("x.jbeam:3:8", str(err))

    def test_unclosed_bracket_points_to_opening(self):
        with self.assertRaises(JBeamSyntaxError) as ctx:
            loads('{"a": [1, 2,\n "b": 3}')
        # el "}" final no cierra el "[": se informa del token inesperado
        self.assertEqual(ctx.exception.line, 2)


class TestOfficialSyntax2026(unittest.TestCase):
    """Sintaxis de 22 JBeam oficiales que json.lua acepta (ampliación 2026-10-26)."""

    def test_plus_sign(self):
        # covet_engine.jbeam:1075 [3000, +9]; etk800_exhaust: "$+eqFundamentalGain":+4
        doc = loads('{"t": [[3000, +9]], "$+g": +4, "f": +2.5e1, "h": +.5}')
        self.assertEqual(doc.value, {"t": [[3000, 9]], "$+g": 4, "f": 25.0, "h": 0.5})
        self.assertIsInstance(doc.value["$+g"], int)
        self.assertEqual(doc.count(PLUS_SIGN), 4)

    def test_number_without_fraction_or_integer_part(self):
        # vivace_suspension_F.jbeam:787 1.; flatbed_load_pipes.jbeam:1424 -.013
        doc = loads("[0., 1., -.013, 5.e2, -2.]")
        self.assertEqual(doc.value, [0.0, 1.0, -0.013, 500.0, -2.0])
        self.assertTrue(all(isinstance(v, float) for v in doc.value))
        notes = [n for n in doc.leniencies if n.kind == NUMBER_FORMAT]
        self.assertEqual(len(notes), 5)
        self.assertEqual(notes[2].detail, "sin parte entera: -.013")

    def test_plain_numbers_unchanged(self):
        doc = loads("[0, -1, 1.5, -0.25, 1e3, -2E-2]")
        self.assertEqual(doc.value, [0, -1, 1.5, -0.25, 1000.0, -0.02])
        self.assertEqual(doc.leniencies, [])

    def test_trailing_closers_after_root(self):
        # cones.jbeam / nine_skins.jbeam: "},<salto>}" al final del archivo
        doc = loads('{"a": {"b": 1},\n},\n}\n]')
        self.assertEqual(doc.value, {"a": {"b": 1}})
        self.assertEqual(doc.count(CONTENT_AFTER_ROOT), 2)

    def test_implicit_root_object(self):
        # sunburst2_exhaust_turbo_race.jbeam: sin '{' inicial ni '}' final
        doc = loads('"p":{\n "information":{"name":"x",},\n},\n"q": [1]')
        self.assertEqual(doc.value, {"p": {"information": {"name": "x"}}, "q": [1]})
        self.assertEqual(doc.count(IMPLICIT_ROOT_OBJECT), 1)

    def test_comma_before_colon(self):
        # large_tire_dump.jbeam:69 "spoke1",: {...}
        doc = loads('{"rails": {"spoke1",: {"looped": false}, "spoke2",,: {}}}')
        self.assertEqual(
            doc.value, {"rails": {"spoke1": {"looped": False}, "spoke2": {}}}
        )
        self.assertEqual(doc.count(COMMA_BEFORE_COLON), 3)

    def test_string_root_is_still_a_string(self):
        # comportamiento anterior conservado (no es un objeto implícito sin ':')
        self.assertEqual(loads('"abc"').value, "abc")


if __name__ == "__main__":
    unittest.main()
