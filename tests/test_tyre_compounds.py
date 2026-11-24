"""Compuestos de AC en el tyres.ini convertido («Exported» + plantillas leídas de la instalación)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.ac import tyre_compounds as TC

CONVERTED = """[HEADER]
VERSION=10

[COMPOUND_DEFAULT]
INDEX=0

[FRONT]
WIDTH=0.2
RADIUS=0.31
RIM_RADIUS=0.22
ANGULAR_INERTIA=1.1
DAMP=700
RATE=200000
DX_REF=1.0

[REAR]
WIDTH=0.22
RADIUS=0.31
RIM_RADIUS=0.22
ANGULAR_INERTIA=1.3
DAMP=700
RATE=210000
DX_REF=1.0
"""
SOURCE = """[HEADER]
VERSION=10

[FRONT]
NAME=Street 90s
SHORT_NAME=SV
WIDTH=0.185
RADIUS=0.2888
RIM_RADIUS=0.1905
ANGULAR_INERTIA=0.83
DAMP=600
RATE=254573
DX_REF=1.22
WEAR_CURVE=street90_front.lut

[REAR]
NAME=Street 90s
SHORT_NAME=SV
WIDTH=0.185
RADIUS=0.2888
DX_REF=1.22
WEAR_CURVE=street90_rear.lut

[FRONT_1]
NAME=Street
SHORT_NAME=ST
WIDTH=0.185
RADIUS=0.2888
DX_REF=1.1
WEAR_CURVE=street_front.lut

[REAR_1]
NAME=Street
SHORT_NAME=ST
WIDTH=0.185
RADIUS=0.2888
DX_REF=1.1
WEAR_CURVE=street_rear.lut

[THERMAL_FRONT_1]
COOL_FACTOR=2.5
PERFORMANCE_CURVE=tcurve_street.lut
"""


def fake_ac(root: Path) -> Path:
    d = root / "content" / "cars" / "ks_toyota_ae86" / "data"
    d.mkdir(parents=True)
    (d / "tyres.ini").write_text(SOURCE, "utf-8")
    for n in (
        "street_front",
        "street_rear",
        "street90_front",
        "street90_rear",
        "tcurve_street",
    ):
        (d / f"{n}.lut").write_text(f"0|{n}\n", "utf-8")
    return root


class TestCompounds(unittest.TestCase):
    def test_exported_name_and_extra_compounds(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            ac = fake_ac(Path(t))
            text, files, notes = TC.apply(CONVERTED, ac, ["street", "street90s"])
            s = TC.parse_ini(text)
            self.assertEqual(
                (s["FRONT"]["NAME"], s["REAR"]["SHORT_NAME"]), ("Exported", "E")
            )
            self.assertEqual(
                s["COMPOUND_DEFAULT"]["INDEX"], "0"
            )  # el convertido sigue por defecto
            self.assertEqual(s["FRONT_1"]["NAME"], "Street")
            self.assertEqual(s["FRONT_2"]["NAME"], "Street 90s")
            # geometría del coche convertido, agarre de la plantilla
            self.assertEqual(
                (s["FRONT_1"]["WIDTH"], s["FRONT_1"]["RATE"]), ("0.2", "200000")
            )
            self.assertEqual(s["FRONT_2"]["RADIUS"], "0.31")
            self.assertEqual(s["FRONT_2"]["DX_REF"], "1.22")
            self.assertEqual(s["REAR_1"]["DX_REF"], "1.1")
            self.assertIn("THERMAL_FRONT_1", s)
            self.assertEqual(
                set(files),
                {
                    "street_front.lut",
                    "street_rear.lut",
                    "street90_front.lut",
                    "street90_rear.lut",
                    "tcurve_street.lut",
                },
            )
            self.assertEqual(len(notes), 3)

    def test_errors(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            ac = fake_ac(Path(t))
            with self.assertRaises(TC.CompoundError):
                TC.apply(CONVERTED, ac, ["nope"])
            with self.assertRaises(
                TC.CompoundError
            ):  # la instalación no tiene ese coche
                TC.apply(CONVERTED, ac, ["slick_soft"])
            with self.assertRaises(TC.CompoundError):
                TC.apply(CONVERTED, ac, ["street", "street"])
            av = {c["id"]: c["ok"] for c in TC.available(ac)}
            self.assertTrue(av["street"] and av["street90s"])
            self.assertFalse(av["slick_soft"])

    def test_parse_extras(self) -> None:
        from src.app.workspace import WorkspaceError, _parse_extras

        aids, comps = _parse_extras(
            {"abs": True, "abs_levels": 6, "tc": False, "tyre_compounds": ["street"]}
        )
        self.assertEqual(aids, {"abs": "on", "abs_levels": 6})
        self.assertEqual(comps, ["street"])
        self.assertEqual(_parse_extras(None), ({}, []))
        with self.assertRaises(WorkspaceError):
            _parse_extras({"tyre_compounds": ["nope"]})
        for bad in (
            {"abs": "off"},  # solo booleanos
            {"abs": True, "abs_levels": 1},
            {"abs": True, "abs_levels": 25},
            {"tc": True, "tc_levels": True},
            {"tc": True, "tc_levels": 6.9},
            {"abs": True, "abs_levels": float("inf")},
            {"tyre_compounds": ["street", "street"]},
        ):
            with self.subTest(bad), self.assertRaises(WorkspaceError):
                _parse_extras(bad)


if __name__ == "__main__":
    unittest.main()
