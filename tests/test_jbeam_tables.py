import unittest

from src.jbeam.lax_json import loads
from src.jbeam.tables import TableFormatError, is_header_table, parse_table

NODES = loads("""[
    ["id", "posX", "posY", "posZ"],
    {"nodeWeight": 2.0},
    {"group": "body"},
    ["n1", 0.1, 0.2, 0.3],
    {"nodeWeight": 5.0},
    ["n2", 1, 2, 3, {"group": "wheel", "extraProp": true}],
    ["n3", 4, 5, 6]
]""").value


class TestParseTable(unittest.TestCase):
    def test_header_and_rows(self):
        table = parse_table(NODES)
        self.assertEqual(table.header, ("id", "posX", "posY", "posZ"))
        self.assertEqual([r.get("id") for r in table.rows], ["n1", "n2", "n3"])
        self.assertEqual(
            table.rows[0].values, {"id": "n1", "posX": 0.1, "posY": 0.2, "posZ": 0.3}
        )
        self.assertEqual(table.issues, [])

    def test_modifiers_apply_to_following_rows(self):
        n1, n2, n3 = parse_table(NODES).rows
        self.assertEqual(n1.modifiers, {"nodeWeight": 2.0, "group": "body"})
        self.assertEqual(n2.modifiers["nodeWeight"], 5.0)
        self.assertEqual(n3.properties, {"nodeWeight": 5.0, "group": "body"})

    def test_inline_properties_override_modifiers_only_for_that_row(self):
        _, n2, n3 = parse_table(NODES).rows
        self.assertEqual(n2.inline, {"group": "wheel", "extraProp": True})
        self.assertEqual(n2.properties["group"], "wheel")
        self.assertEqual(n3.properties["group"], "body")

    def test_rows_keep_original_index(self):
        self.assertEqual([r.index for r in parse_table(NODES).rows], [3, 5, 6])

    def test_initial_modifiers_continue_previous_state(self):
        first = parse_table([["name"], {"radius": 0.3}, ["a"]])
        second = parse_table(
            [["name"], ["FR"]], initial_modifiers=first.final_modifiers
        )
        self.assertEqual(second.rows[0].properties, {"radius": 0.3})

    def test_dicts_before_header_count_as_modifiers(self):
        table = parse_table([{"k": 1}, ["a", "b"], ["x", "y"]])
        self.assertEqual(table.rows[0].modifiers, {"k": 1})

    def test_reference_suffix_is_kept_raw(self):
        table = parse_table([["id1:", "id2:"], ["a", "b"]])
        self.assertEqual(table.rows[0].values, {"id1:": "a", "id2:": "b"})


class TestIrregularRows(unittest.TestCase):
    def test_short_row_is_reported_not_filled(self):
        table = parse_table([["a", "b", "c"], [1]])
        self.assertEqual(table.rows[0].values, {"a": 1})
        self.assertEqual(len(table.issues), 1)

    def test_short_row_ending_in_dict_uses_it_as_inline(self):
        table = parse_table([["a", "b", "c"], [1, {"p": 2}]])
        self.assertEqual(table.rows[0].inline, {"p": 2})
        self.assertEqual(table.rows[0].values, {"a": 1})

    def test_extra_values_are_kept_and_reported(self):
        table = parse_table([["a"], [1, 2, 3]])
        self.assertEqual(table.rows[0].extra, (2, 3))
        self.assertEqual(len(table.issues), 1)

    def test_dict_in_last_column_is_ambiguous_and_reported(self):
        table = parse_table([["a", "opts"], [1, {"x": 1}]])
        self.assertEqual(table.rows[0].values["opts"], {"x": 1})
        self.assertEqual(table.rows[0].inline, {})
        self.assertEqual(len(table.issues), 1)


class TestTableErrors(unittest.TestCase):
    def test_not_a_list(self):
        with self.assertRaises(TableFormatError):
            parse_table({"a": 1})

    def test_empty_section_is_an_empty_table(self):
        # Caso real: "nodes": [] en camso_fueltank_35b84.jbeam
        table = parse_table([])
        self.assertEqual((table.header, table.rows, table.issues), ((), [], []))

    def test_modifiers_without_header_keep_state_and_are_reported(self):
        table = parse_table([{"k": 1}], initial_modifiers={"r": 2})
        self.assertEqual(table.rows, [])
        self.assertEqual(table.final_modifiers, {"r": 2, "k": 1})
        self.assertEqual(len(table.issues), 1)

    def test_header_with_non_strings(self):
        with self.assertRaises(TableFormatError):
            parse_table([[1, 2], [3, 4]])

    def test_scalar_entry(self):
        with self.assertRaises(TableFormatError):
            parse_table([["a"], 5])

    def test_is_header_table(self):
        self.assertTrue(is_header_table(NODES))
        self.assertFalse(is_header_table([-3.3, 0, 3.14]))  # gearRatios
        self.assertFalse(is_header_table({"a": 1}))
        self.assertFalse(is_header_table([]))


if __name__ == "__main__":
    unittest.main()
