"""Pruebas de src.ac.kn5_materials (texturas difusas) y del prisma de collider."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_materials import (
    DiffuseSource,
    Kn5FormatError,
    apply_diffuse,
    build_kn5,
    diffuse_sources,
    solid_dds,
    split_kn5,
)
from src.ac.kn5_write import (
    box_mesh,
    profile_prism_mesh,
    signed_volume,
    write_simple_kn5,
)

_TMP = tempfile.TemporaryDirectory()
OUT = Path(_TMP.name)


def _tiny_kn5(name: str, materials) -> bytes:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    meshes = [
        box_mesh(f"m{i}", (0, 0, 0), (1, 1, 1), material=i)
        for i in range(len(materials))
    ]
    write_simple_kn5(path, meshes, materials)
    return path.read_bytes()


class TestKn5Materials(unittest.TestCase):
    def test_roundtrip_is_identical(self):
        data = _tiny_kn5("rt.kn5", [("a", "ksPerPixel"), ("b.001", "ksPerPixel")])
        self.assertEqual(build_kn5(split_kn5(data)), data)

    def test_solid_dds_header_and_pixels(self):
        d = solid_dds((10, 20, 30))
        self.assertEqual(d[:4], b"DDS ")
        size, flags, h, w, pitch = struct.unpack_from("<5I", d, 4)
        self.assertEqual((size, h, w, pitch), (124, 4, 4, 16))
        self.assertEqual(
            struct.unpack_from("<2I", d, 76), (32, 0x41)
        )  # tamaño y flags del pixfmt
        self.assertEqual(len(d), 128 + 16 * 4)
        self.assertEqual(d[128:132], bytes([30, 20, 10, 255]))  # BGRA

    def test_apply_diffuse_keeps_nodes_and_links_slots(self):
        data = _tiny_kn5("ap.kn5", [("a", "ksPerPixel"), ("b.001", "ksPerPixel")])
        parts = split_kn5(data)
        tex = solid_dds((1, 2, 3))
        srcs = [
            DiffuseSource("a", "a", "diffuseColor", "t.dds", tex),
            DiffuseSource("b.001", "b", "diffuseColor", "t.dds", tex),
        ]
        new = split_kn5(build_kn5(apply_diffuse(parts, srcs)))
        self.assertEqual(new.nodes, parts.nodes)
        self.assertEqual([t.name for t in new.textures], ["t.dds"])  # deduplicada
        self.assertEqual(
            [m.slots for m in new.materials], [[("txDiffuse", 0, "t.dds")]] * 2
        )
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "ap_out.kn5").write_bytes(build_kn5(apply_diffuse(parts, srcs)))
        k = read_kn5(OUT / "ap_out.kn5")  # el lector del proyecto lo acepta
        self.assertEqual(k.textures, ["t.dds"])

    def test_apply_diffuse_rejects_incomplete_mapping(self):
        parts = split_kn5(
            _tiny_kn5("inc.kn5", [("a", "ksPerPixel"), ("b", "ksPerPixel")])
        )
        with self.assertRaises(Kn5FormatError):
            apply_diffuse(
                parts,
                [DiffuseSource("a", "a", "diffuseColor", "t", solid_dds((0, 0, 0)))],
            )

    def test_diffuse_sources_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "map.dds").write_bytes(b"DDS " + b"\0" * 124)
            mats = {
                "m_tex": {
                    "Stages": [
                        {
                            "diffuseMap": "vehicles/x/map.dds",
                            "diffuseColor": [1, 1, 1, 1],
                        }
                    ]
                },
                "m_paint": {
                    "Stages": [
                        {"instanceDiffuse": True, "diffuseColor": [1, 0.5, 1, 1]}
                    ]
                },
                "m_col": {
                    "Stages": [{"diffuseColor": [0.0470588, 0.0470588, 0.0470588, 0]}]
                },
            }
            (d / "m.materials.json").write_text(json.dumps(mats), encoding="utf-8")
            src = diffuse_sources(
                ["m_tex", "m_paint.001", "m_col"], d / "m.materials.json", d, (1, 1, 1)
            )
            self.assertEqual(
                [s.kind for s in src], ["diffuseMap", "pintura", "diffuseColor"]
            )
            self.assertEqual(src[0].texture_name, "map.dds")
            self.assertEqual(src[1].texture_name, "beam2ac_col_ff80ff.dds")
            self.assertEqual(src[2].texture_name, "beam2ac_col_0c0c0c.dds")
            with self.assertRaises(Kn5FormatError):
                diffuse_sources(["desconocido"], d / "m.materials.json", d, (1, 1, 1))


class TestProfilePrism(unittest.TestCase):
    STATIONS = [
        (-2.0, 0.20, 1.0),
        (-1.0, 0.14, 1.35),
        (0.8, 0.11, 1.30),
        (2.0, 0.07, 0.55),
    ]

    def test_closed_positive_and_exact_volume(self):
        m = profile_prism_mesh("c", self.STATIONS, 0.87)
        self.assertEqual(len(m.indices) // 3, 8 * (len(self.STATIONS) - 1) + 4)
        edges = {}
        for i in range(0, len(m.indices), 3):
            t = m.indices[i : i + 3]
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                edges[(a, b)] = edges.get((a, b), 0) + 1
        # cerrado y orientación coherente: cada arista dirigida una vez,
        # y su inversa también
        self.assertTrue(all(c == 1 for c in edges.values()))
        self.assertTrue(all((b, a) in edges for a, b in edges))
        area = sum(
            (z1 - z0) * ((t0 - b0) + (t1 - b1)) / 2
            for (z0, b0, t0), (z1, b1, t1) in zip(self.STATIONS, self.STATIONS[1:])
        )
        self.assertAlmostEqual(
            signed_volume(m.vertices, m.indices), area * 2 * 0.87, places=6
        )

    def test_two_stations_equal_box_volume(self):
        m = profile_prism_mesh("c", [(0, 0, 1), (1, 0, 1)], 0.5)
        self.assertAlmostEqual(signed_volume(m.vertices, m.indices), 1.0)

    def test_rejects_bad_stations(self):
        with self.assertRaises(ValueError):
            profile_prism_mesh("c", [(1, 0, 1), (0, 0, 1)], 0.5)
        with self.assertRaises(ValueError):
            profile_prism_mesh("c", [(0, 1, 1), (1, 0, 1)], 0.5)


if __name__ == "__main__":
    unittest.main()
