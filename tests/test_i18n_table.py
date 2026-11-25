"""El diccionario inglés de la interfaz está generado y es coherente con sus plantillas."""

import json
import unittest
from pathlib import Path

from tools import gen_i18n

STATIC = Path("src/app/static")


class TestI18n(unittest.TestCase):
    def test_generated_file_in_sync(self) -> None:
        table = {}
        for es, en in gen_i18n.PAIRS:
            table.setdefault(
                " ".join(es.split()) if "\n" in es else es.strip(), en.strip()
            )
        text = (STATIC / "i18n_en.js").read_text(encoding="utf-8")
        got = json.loads(text[text.index("{") : text.rindex("}") + 1])
        self.assertEqual(got, table, "ejecute python tools/gen_i18n.py")

    def test_placeholders_match(self) -> None:
        for es, en in gen_i18n.PAIRS:
            self.assertEqual(es.count("{}"), en.count("{}"), es)

    def test_every_page_loads_i18n(self) -> None:
        for page in ("desktop.html", "cockpit.html", "scene.html", "index.html"):
            html = (STATIC / page).read_text(encoding="utf-8")
            self.assertRegex(html, r"/static/i18n_en\.js[\s\S]*/static/i18n\.js", page)


if __name__ == "__main__":
    unittest.main()
