"""Catálogo de referencias de AC con una instalación sintética (y la real si existe)."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.catalog.ac_refs import (
    DEFAULT_AC_CARS,
    build_index,
    classify_origin,
    reference_values,
    scan,
)

ENGINE_A = "[ENGINE_DATA]\nLIMITER=7000 ; rpm\nMINIMUM=900\n"
ENGINE_B = "[ENGINE_DATA]\nLIMITER=8000\nMINIMUM=1000\n"


def tree_hash(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class TestSyntheticInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cars = Path(self.tmp.name) / "cars"

        def car(cid, ui=None, engine=None, acd=False):
            d = self.cars / cid
            (d / "ui").mkdir(parents=True)
            if ui is not None:
                (d / "ui" / "ui_car.json").write_text(json.dumps(ui), encoding="utf-8")
            if engine is not None:
                (d / "data").mkdir()
                (d / "data" / "engine.ini").write_text(engine, encoding="utf-8")
            if acd:
                (d / "data.acd").write_bytes(b"\x00cifrado")

        car("ks_oficial", {"name": "A"}, ENGINE_A, acd=True)
        car("ks_editado", {"name": "B", "author": "Kunos / edit by X"}, acd=True)
        car("mod_1", {"name": "C", "author": "Alguien"}, ENGINE_A)
        car(
            "mod_2", {"name": "D", "author": "Otro", "parent": "mod_1"}, ENGINE_A
        )  # copia idéntica
        car("mod_3", {"name": "E", "author": "Otro"}, ENGINE_B)
        car("antiguo", {"name": "F"}, acd=True)
        car("auto_x", {"name": "G", "author": "Camshaft Software LTD"}, ENGINE_B)
        car("beam2ac_test", {"name": "H"}, ENGINE_B)
        car("vacio", {"name": "I"})
        self.before = tree_hash(self.cars)

    def tearDown(self):
        self.tmp.cleanup()

    def test_classification_and_data_state(self):
        refs = {r.car_id: r for r in scan(self.cars)}
        cls = {k: r.origin["class"] for k, r in refs.items()}
        self.assertEqual(
            cls,
            {
                "antiguo": "origin_undeclared",
                "auto_x": "ac_ui_declares_automation",
                "beam2ac_test": "beam2ac_generated",
                "ks_editado": "mod_derived_from_kunos",
                "ks_oficial": "kunos_probable",
                "mod_1": "mod",
                "mod_2": "mod",
                "mod_3": "mod",
                "vacio": "origin_undeclared",
            },
        )
        st = {k: r.data_state for k, r in refs.items()}
        self.assertEqual(
            (st["ks_oficial"], st["ks_editado"], st["mod_1"], st["vacio"]),
            ("both", "encrypted", "readable", "absent"),
        )
        self.assertEqual(refs["ks_editado"].physics, {})  # cifrado: no se lee
        self.assertTrue(
            any("cuál carga el juego" in x for x in refs["ks_oficial"].limitations)
        )
        self.assertEqual(refs["mod_2"].ui["parent"], "mod_1")

    def test_independence_by_content_and_exclusion(self):
        index = build_index(scan(self.cars))
        entry = index["keys"]["ac:engine.ini/ENGINE_DATA/LIMITER"]
        self.assertEqual(entry["independent_values"], 2)  # ENGINE_A y ENGINE_B
        self.assertEqual(
            entry["cars"], 5
        )  # ks_oficial, mod_1, mod_2, mod_3, auto_x (beam2ac excluido)
        values = {
            v["value"]: v
            for v in reference_values(index, "ac:engine.ini/ENGINE_DATA/LIMITER")
        }
        self.assertEqual(
            sorted(values["7000"]["cars"]), ["ks_oficial", "mod_1", "mod_2"]
        )
        self.assertNotIn("beam2ac_test", values["8000"]["cars"])
        self.assertEqual(index["summary"]["excluded_beam2ac"], ["beam2ac_test"])
        self.assertEqual(index["summary"]["ac_ui_declares_automation"], ["auto_x"])
        self.assertEqual(reference_values(index, "ac:engine.ini/NO/EXISTE"), [])

    def test_sources_are_not_modified(self):
        build_index(scan(self.cars))
        self.assertEqual(tree_hash(self.cars), self.before)

    def test_classify_origin_signals(self):
        o = classify_origin("ks_x", {"author": "Kunos Simulazioni"})
        self.assertEqual(o["class"], "kunos_probable")
        self.assertIn("heurística", o["method"])


@unittest.skipUnless(DEFAULT_AC_CARS.is_dir(), "Assetto Corsa no instalado")
class TestRealInstall(unittest.TestCase):
    def test_project_package_is_excluded_and_kunos_values_found(self):
        index = build_index(scan(DEFAULT_AC_CARS))
        # el paquete beam2ac instalado cambia con las pruebas manuales del usuario
        # (test16sn hasta la fase B4; beam2ac_seat_ibiza_acfr después): se comprueba
        # que todo paquete beam2ac_* instalado queda excluido de las referencias
        installed = sorted(
            p.name for p in DEFAULT_AC_CARS.iterdir() if p.name.startswith("beam2ac_")
        )
        self.assertEqual(sorted(index["summary"]["excluded_beam2ac"]), installed)
        groups = reference_values(
            index, "ac:drivetrain.ini/GEARBOX/VALID_SHIFT_RPM_WINDOW"
        )
        kunos = sorted(g["value"] for g in groups if "kunos_probable" in g["origins"])
        self.assertEqual(kunos, ["1000", "1000", "800", "800"])
        for g in groups:
            self.assertFalse(any(c.startswith("beam2ac_") for c in g["cars"]))


if __name__ == "__main__":
    unittest.main()
