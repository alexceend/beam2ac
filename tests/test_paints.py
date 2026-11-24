"""Pinturas de BeamNG (src/extract/paints.py) y su uso en las texturas del KN5."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_materials import (
    Kn5Material,
    Kn5Parts,
    apply_diffuse,
    diffuse_sources,
    paint_dependent_materials,
)
from src.extract.paints import (
    PaintFormatError,
    decode_paints_meta,
    parse_legacy_color,
    parse_paint,
    read_paints,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"

RED = {
    "baseColor": [0.847059, 0.0941176, 0, 1],
    "clearcoat": 1,
    "clearcoatRoughness": 0,
    "metallic": 0.752381,
    "roughness": 0.263333,
}


def meta_record(p: dict) -> bytes:
    return struct.pack(
        "<4d4f",
        *p["baseColor"],
        p["clearcoat"],
        p["clearcoatRoughness"],
        p["metallic"],
        p["roughness"],
    )


class TestParsePaint(unittest.TestCase):
    def test_modern_paint_and_unknown_fields_kept(self) -> None:
        p = parse_paint("X", {**RED, "class": "custom", "sheen": 3}, "src")
        self.assertEqual(p.base_color, (0.847059, 0.0941176, 0.0, 1.0))
        self.assertEqual(p.paint_class, "custom")
        self.assertEqual(p.unknown, {"sheen": 3})
        self.assertEqual(p.source, "src")

    def test_invalid_paints_raise(self) -> None:
        for bad in (
            "blanco",
            {},
            {"baseColor": [1, 1]},
            {"baseColor": [1, "a", 1, 1]},
            {"baseColor": [1, 1, 1, float("nan")]},
            {"baseColor": [1, 1, 1, 1], "metallic": True},
        ):
            with self.subTest(bad=bad), self.assertRaises(PaintFormatError):
                parse_paint("X", bad, "src")

    def test_legacy_color_string(self) -> None:
        p = parse_legacy_color("Old", "0.1 0.2 0.3 1.2 0.5 0.3 1 0", "src")
        self.assertEqual(p.base_color, (0.1, 0.2, 0.3, 1.2))
        self.assertEqual(
            (p.metallic, p.roughness, p.clearcoat, p.clearcoat_roughness),
            (0.5, 0.3, 1.0, 0.0),
        )
        with self.assertRaises(PaintFormatError):
            parse_legacy_color("Old", "1 2 3", "src")


class TestPaintsMeta(unittest.TestCase):
    def test_one_and_two_records(self) -> None:
        one = decode_paints_meta(meta_record(RED))
        self.assertEqual(len(one), 1)
        self.assertAlmostEqual(one[0]["metallic"], 0.752381, places=6)
        two = decode_paints_meta(meta_record(RED) * 2)
        self.assertEqual(len(two), 2)

    def test_unknown_size_rejected(self) -> None:
        for data in (b"", b"\0" * 47, b"\0" * 50):
            with self.subTest(n=len(data)), self.assertRaises(PaintFormatError):
                decode_paints_meta(data)

    def test_unknown_meta_kept_as_hex_with_diagnostic(self) -> None:
        rep = read_paints({"paints": {"Default1": RED}}, {}, None, b"\1\2\3")
        self.assertEqual(rep.meta, {"raw_hex": "010203", "records": None})
        self.assertIn("pintura.meta_desconocido", [d.code for d in rep.diagnostics])

    def test_meta_matching_and_mismatch(self) -> None:
        info = {"paints": {"Default1": RED}}
        ok = read_paints(
            info, {"defaultPaintName1": "Default1"}, None, meta_record(RED)
        )
        self.assertTrue(ok.meta["records"][0]["matches_expected"])
        other = dict(RED, metallic=0.1)
        bad = read_paints(info, {}, None, meta_record(other))
        self.assertFalse(bad.meta["records"][0]["matches_expected"])
        self.assertIn(
            "pintura.meta_sin_correspondencia", [d.code for d in bad.diagnostics]
        )


class TestSelection(unittest.TestCase):
    PAINTS = {"paints": {"A": RED, "B": dict(RED, metallic=0.1)}}

    def names(self, model: dict, config: dict) -> list:
        rep = read_paints({**self.PAINTS, **model}, config)
        return [(s.name, s.resolved) for s in rep.selection]

    def test_config_names_and_slot_inheritance(self) -> None:
        # vehiclePaints.lua l. 311-317: huecos 2 y 3 heredan el 1 de la config.
        self.assertEqual(
            self.names({}, {"defaultPaintName1": "B"}),
            [("B", True), ("B", True), ("B", True)],
        )
        # hueco 1 vacío: el del modelo.
        self.assertEqual(
            self.names({"defaultPaintName1": "A"}, {"defaultPaintName2": "B"}),
            [("A", True), ("B", True), ("A", True)],
        )

    def test_missing_selection_is_not_invented(self) -> None:
        rep = read_paints(self.PAINTS, {})
        self.assertFalse(rep.selected(1).resolved)
        self.assertIsNone(rep.selected(1).paint)
        self.assertIn("pintura.sin_seleccion", [d.code for d in rep.diagnostics])

    def test_unknown_name_is_error(self) -> None:
        rep = read_paints(self.PAINTS, {"defaultPaintName1": "Z"})
        self.assertFalse(rep.selected(1).resolved)
        self.assertIn("pintura.nombre_desconocido", [d.code for d in rep.diagnostics])

    def test_legacy_keys_override_modern(self) -> None:
        # convertVehicleInfo: con una clave antigua, defaultPaintNameN = default_color_N
        rep = read_paints(
            self.PAINTS,
            {"default_color": "B", "defaultPaintName1": "A"},
        )
        self.assertEqual(rep.selected(1).name, "B")
        self.assertIn("pintura.clave_sobrescrita", [d.code for d in rep.diagnostics])

    def test_legacy_inline_color_and_colors_dict(self) -> None:
        rep = read_paints(
            {"colors": {"Old": "0.1 0.2 0.3 1 0 0 0 0"}},
            {"default_color": [0.5, 0.5, 0.5, 1]},
        )
        self.assertIn("Old", rep.paints)
        self.assertEqual(rep.selected(1).paint.base_color, (0.5, 0.5, 0.5, 1.0))

    def test_multipaint_left_unresolved(self) -> None:
        rep = read_paints(self.PAINTS, {"defaultMultiPaintSetup": "team"})
        self.assertFalse(any(s.resolved for s in rep.selection))
        self.assertEqual(
            rep.raw_unparsed["info_<config>.json:defaultMultiPaintSetup"], "team"
        )
        # en el modelo solo afecta a los huecos que heredan del modelo
        rep = read_paints(
            {**self.PAINTS, "defaultMultiPaintSetup": "team"},
            {"defaultPaintName1": "A"},
        )
        self.assertTrue(all(s.resolved for s in rep.selection))

    def test_pc_paints_reported_not_used(self) -> None:
        rep = read_paints(
            self.PAINTS, {"defaultPaintName1": "A"}, {"paints": [RED, {"x": 1}]}
        )
        self.assertEqual(len(rep.pc_paints), 1)
        self.assertIn("<config>.pc:paints[2]", rep.raw_unparsed)
        self.assertEqual(rep.selected(1).name, "A")
        codes = [d.code for d in rep.diagnostics]
        self.assertIn("pintura.pc_precedencia_sin_verificar", codes)

    def test_invalid_paint_kept_raw(self) -> None:
        rep = read_paints(
            {"paints": {"A": RED, "Bad": "x"}}, {"defaultPaintName1": "A"}
        )
        self.assertEqual(rep.raw_unparsed["info.json:paints/Bad"], "x")
        self.assertTrue(rep.selected(1).resolved)
        json.dumps(rep.to_json())  # serializable


class TestPaintOptionalTextures(unittest.TestCase):
    def test_no_paint_keeps_other_textures(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mats = {
                "m_paint": {"Stages": [{"instanceDiffuse": True}]},
                "m_col": {"Stages": [{"diffuseColor": [0.5, 0.5, 0.5, 1]}]},
            }
            mj = d / "m.materials.json"
            mj.write_text(json.dumps(mats), encoding="utf-8")
            names = ["m_paint", "m_col"]
            self.assertEqual(paint_dependent_materials(names, mj), ["m_paint"])
            src = diffuse_sources(names, mj, d, None)
            self.assertEqual(
                [s.kind for s in src], ["pintura_sin_resolver", "diffuseColor"]
            )
            parts = Kn5Parts(
                6,
                0,
                [],
                [Kn5Material(n, "ksPerPixel", 0, 0, 0, [], []) for n in names],
                b"",
            )
            out = apply_diffuse(parts, src)
            self.assertEqual(out.materials[0].slots, [])  # intacto
            self.assertEqual(out.materials[1].slots[0][0], "txDiffuse")
            self.assertEqual(len(out.textures), 1)


def _vehicle_report(d: Path):
    pc = next(d.glob("*.pc"))
    return read_paints(
        json.loads((d / "info.json").read_text(encoding="utf-8")),
        json.loads((d / f"info_{pc.stem}.json").read_text(encoding="utf-8")),
        json.loads(pc.read_text(encoding="utf-8")),
        (d / "paints.meta").read_bytes() if (d / "paints.meta").is_file() else None,
    )


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestSampleVehicles(unittest.TestCase):
    """Regresión con los vehículos de samples_beamng (solo lectura)."""

    def vdir(self, name: str) -> Path:
        found = list(SAMPLES.glob(f"*/vehicles/{name}"))
        if not found:
            self.skipTest(f"falta {name}")
        return found[0]

    def test_sucette_without_meta_or_default_paint(self) -> None:
        for name in ("sucette_poubelle", "sucette_poubelle_turbo"):
            with self.subTest(name=name):
                d = self.vdir(name)
                rep = _vehicle_report(d)
                self.assertIsNone(rep.meta)
                self.assertFalse(rep.selected(1).resolved)
                self.assertEqual(len(rep.paints), 13)
                codes = {x.code for x in rep.diagnostics}
                self.assertEqual(
                    codes, {"pintura.sin_seleccion", "pintura.meta_ausente"}
                )
                # ningún material depende de la pintura: textura completa sin ella
                mj = next(d.glob("*.materials.json"))
                bm = json.loads(mj.read_text(encoding="utf-8"))
                self.assertEqual(paint_dependent_materials(list(bm), mj), [])

    def test_vehicles_with_meta(self) -> None:
        for name in ("autist_cuanto", "hyacinth_sage", "quail_mirabilis"):
            with self.subTest(name=name):
                rep = _vehicle_report(self.vdir(name))
                self.assertEqual(rep.selected(1).name, "Default1")
                recs = rep.meta["records"]
                self.assertEqual(len(recs), 1)
                self.assertTrue(recs[0]["matches_expected"])
                self.assertEqual(rep.diagnostics, [])


@unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
class TestIbizaPaints(unittest.TestCase):
    def test_two_record_meta_and_unchanged_selection(self) -> None:
        rep = _vehicle_report(IBIZA)
        self.assertEqual(rep.selected(1).name, "Default1")  # lo que usaba el modelo
        recs = rep.meta["records"]
        self.assertEqual([r["expected"] for r in recs], ["Default1", "Default2"])
        self.assertTrue(all(r["matches_expected"] for r in recs))


if __name__ == "__main__":
    unittest.main()
