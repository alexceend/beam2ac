"""Comparador de equivalencia geométrica de KN5 (src.ac.kn5_compare)."""

from __future__ import annotations

import json
import math
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from src.ac import kn5_compare as kc
from src.ac.kn5_materials import Kn5Material, Kn5Parts, Kn5Texture, build_kn5
from src.ac.kn5_tree import MeshData, Node, load, mesh_payload, serialize_tree, walk
from src.safe_paths import PROJECT_ROOT

OUT = PROJECT_ROOT / "output" / "seat_ibiza_acfr"
C_KN5 = OUT / "phaseC_2026-10-25/run1/_kn5_build/seat_ibiza_acfr.kn5"
D_KN5 = OUT / "phaseD_2026-10-25/run1/_kn5_build/seat_ibiza_acfr.kn5"
IDENT = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def rec(pos, nrm=(0.0, 1.0, 0.0), uv=(0.0, 0.0), tan=(1.0, 0.0, 0.0)) -> bytes:
    return struct.pack("<3f3f2f3f", *pos, *nrm, *uv, *tan)


QUAD = [
    rec((0, 0, 0), uv=(0, 0)),
    rec((1, 0, 0), uv=(1, 0)),
    rec((1, 0, 1), uv=(1, 1)),
    rec((0, 0, 1), uv=(0, 1)),
]
QUAD_IDX = [0, 1, 2, 0, 2, 3]


def mesh(name, verts=None, idx=None, material=0) -> Node:
    m = MeshData(
        b"\x01\x01\x00",
        list(verts or QUAD),
        list(idx or QUAD_IDX),
        material,
        0,
        0.0,
        1000.0,
        True,
    )
    return Node(2, name, 1, mesh_payload(m))


def dummy(name, children, matrix=IDENT) -> Node:
    return Node(1, name, 1, struct.pack("<16f", *matrix), list(children))


def model(**over) -> dict:
    """Escena base: raíz → grupo con DOS mallas llamadas «dup» + malla «body»."""
    return {
        "dup0": mesh("dup"),
        "dup1": mesh("dup", material=1),
        "body": mesh("body"),
        "grp_matrix": IDENT,
        "materials": [
            Kn5Material(
                "m0",
                "ksPerPixel",
                0,
                0,
                0,
                [("ksDiffuse", (0.5,) * 10)],
                [("txDiffuse", 0, "tex0")],
            ),
            Kn5Material("m1", "ksPerPixel", 0, 0, 0, [("ksDiffuse", (0.25,) * 10)], []),
        ],
        "textures": [Kn5Texture("tex0", b"DDS-data")],
        **over,
    }


def kn5(m: dict, root: Node | None = None) -> bytes:
    if root is None:
        root = dummy(
            "root", [dummy("grp", [m["dup0"], m["dup1"]], m["grp_matrix"]), m["body"]]
        )
    return build_kn5(
        Kn5Parts(5, b"", m["textures"], m["materials"], serialize_tree(root))
    )


BASE = kn5(model())


def cmp(b: bytes) -> dict:
    return kc.compare_bytes(BASE, b)


class TestIdentity(unittest.TestCase):
    def test_self_and_byte_copy(self):
        self.assertEqual(kc.compare_bytes(BASE, BASE)["verdict"], kc.IDENTICAL)
        self.assertEqual(
            kc.compare_bytes(BASE, bytes(bytearray(BASE)))["verdict"], kc.IDENTICAL
        )
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.kn5", Path(d) / "b.kn5"
            a.write_bytes(BASE)
            shutil.copyfile(a, b)
            r = kc.compare_files(a, b)
            self.assertEqual(r["verdict"], kc.IDENTICAL)
            self.assertTrue(r["byte_identical"])

    def test_instances_identified_by_tree_position(self):
        r = kc.read_model(BASE)
        names = {p: i["name"] for p, i in r["instances"].items()}
        self.assertEqual(
            names,
            {"0": "root", "0/0": "grp", "0/0/0": "dup", "0/0/1": "dup", "0/1": "body"},
        )
        self.assertEqual(r["problems"], [])


class TestRepresentation(unittest.TestCase):
    def test_reordered_and_duplicated_vertices_are_equivalent(self):
        # vértices en otro orden + un registro repetido; mismos triángulos y giro
        order = [2, 0, 3, 1]
        verts = [QUAD[i] for i in order] + [QUAD[2]]
        remap = {old: new for new, old in enumerate(order)}
        # el 2.º triángulo (0, 2, 3) usa la copia (índice 4) del registro 2
        idx = [remap[0], remap[1], remap[2], remap[0], 4, remap[3]]
        r = cmp(kn5(model(dup1=mesh("dup", verts, idx, material=1))))
        self.assertEqual(r["verdict"], kc.EQUIVALENT, r["differences"])
        self.assertFalse(r["byte_identical"])
        self.assertEqual([x["instance"] for x in r["representation"]], ["0/0/1"])
        rep = r["representation"][0]
        self.assertEqual(rep["stats_a"]["vertices"], 4)
        self.assertEqual(rep["stats_b"]["vertices"], 5)
        self.assertEqual(rep["stats_b"]["duplicate_vertex_records"], 1)
        self.assertTrue(rep["same_distinct_records"])

    def test_rotated_triangle_start_is_equivalent(self):
        idx = [1, 2, 0, 2, 3, 0]  # mismo giro, otro vértice inicial
        r = cmp(kn5(model(body=mesh("body", QUAD, idx))))
        self.assertEqual(r["verdict"], kc.EQUIVALENT)


class TestRealDifferences(unittest.TestCase):
    def _check(self, b: bytes, instance: str | None, what: str):
        r = cmp(b)
        self.assertEqual(r["verdict"], kc.DIFFERENT, r)
        hits = [
            d
            for d in r["differences"]
            if what in d["what"] and (instance is None or d.get("instance") == instance)
        ]
        self.assertTrue(hits, r["differences"])
        return r

    def test_vertex_attributes(self):
        for k, new in (
            ("position", rec((1, 0.001, 0), uv=(1, 0))),
            ("normal", rec((1, 0, 0), nrm=(0, 0.99, 0.1), uv=(1, 0))),
            ("uv", rec((1, 0, 0), uv=(1, 0.5))),
            ("tangent", rec((1, 0, 0), uv=(1, 0), tan=(0, 0, 1))),
            ("-0.0", rec((1, 0, -0.0), uv=(1, 0))),
        ):
            with self.subTest(k=k):
                verts = list(QUAD)
                verts[1] = new
                # solo cambia la segunda «dup»: debe localizarse en 0/0/1, no en 0/0/0
                self._check(
                    kn5(model(dup1=mesh("dup", verts, material=1))),
                    "0/0/1",
                    "triángulos",
                )

    def test_winding_and_indices(self):
        self._check(
            kn5(model(body=mesh("body", QUAD, [0, 2, 1, 0, 2, 3]))), "0/1", "triángulos"
        )
        self._check(
            kn5(model(body=mesh("body", QUAD, [0, 1, 3, 0, 2, 3]))), "0/1", "triángulos"
        )

    def test_multiplicity_of_triangles(self):
        r = self._check(
            kn5(model(body=mesh("body", QUAD, QUAD_IDX + [0, 1, 2]))),
            "0/1",
            "triángulos",
        )
        d = [x for x in r["differences"] if "triángulos" in x["what"]][0]
        self.assertEqual((d["only_in_a"], d["only_in_b"]), (0, 1))

    def test_mesh_material_and_material_table(self):
        self._check(kn5(model(body=mesh("body", material=1))), "0/1", "malla: material")
        mats = model()["materials"]
        mats[0] = Kn5Material(
            "m0",
            "ksPerPixel",
            0,
            0,
            0,
            [("ksDiffuse", (0.6,) * 10)],
            [("txDiffuse", 0, "tex0")],
        )
        self._check(kn5(model(materials=mats)), None, "material 0")

    def test_textures(self):
        self._check(
            kn5(model(textures=[Kn5Texture("tex0", b"DDS-other")])), None, "texturas"
        )

    def test_hierarchy_and_transform(self):
        m = model()
        moved = dummy("root", [dummy("grp", [m["dup0"]]), m["body"], m["dup1"]])
        self._check(kn5(m, moved), None, "jerarquía")
        renamed = dummy(
            "root", [dummy("grp", [m["dup0"], mesh("dupX", material=1)]), m["body"]]
        )
        self._check(kn5(m, renamed), "0/0/1", "jerarquía")
        mat = list(IDENT)
        mat[12] = 0.5
        self._check(kn5(model(grp_matrix=tuple(mat))), "0/0", "transformación")


class TestInconclusive(unittest.TestCase):
    def test_nan_and_inf_never_equivalent(self):
        verts = list(QUAD)
        verts[2] = rec((1, 0, 1), nrm=(math.nan, 1, 0), uv=(1, 1))
        nan_kn5 = kn5(model(body=mesh("body", verts)))
        self.assertEqual(cmp(nan_kn5)["verdict"], kc.INCONCLUSIVE)
        # mismo NaN en los dos, sin ser byte a byte iguales → no concluyente
        other = kn5(
            model(
                body=mesh("body", verts),
                dup1=mesh("dup", list(reversed(QUAD)), [3, 2, 1, 3, 1, 0], 1),
            )
        )
        r = kc.compare_bytes(nan_kn5, other)
        self.assertEqual(r["verdict"], kc.INCONCLUSIVE)
        self.assertTrue(any("NaN" in p for p in r["problems"]))
        mat = list(IDENT)
        mat[0] = math.inf
        self.assertEqual(
            cmp(kn5(model(grp_matrix=tuple(mat))))["verdict"], kc.INCONCLUSIVE
        )
        mats = model()["materials"]
        mats[1] = Kn5Material(
            "m1", "ksPerPixel", 0, 0, 0, [("ksDiffuse", (math.nan,) * 10)], []
        )
        self.assertEqual(cmp(kn5(model(materials=mats)))["verdict"], kc.INCONCLUSIVE)

    def test_malformed_and_unsupported(self):
        self.assertEqual(cmp(BASE[:-7])["verdict"], kc.INCONCLUSIVE)
        self.assertEqual(cmp(b"not a kn5")["verdict"], kc.INCONCLUSIVE)
        # índice fuera de rango (carga útil escrita a mano: mesh_payload lo rechaza)
        good = mesh("body").payload
        bad_idx = good[: 7 + 4 * 44 + 4] + struct.pack("<H", 9) + good[7 + 4 * 44 + 6 :]
        bad = kn5(model(body=Node(2, "body", 1, bad_idx)))
        r = cmp(bad)
        self.assertEqual(r["verdict"], kc.INCONCLUSIVE)
        self.assertIn("fuera de rango", " ".join(r["problems"]))
        # nodo con piel (tipo 3): no se interpreta
        parts_nodes = serialize_tree(dummy("root", [Node(3, "skin", 1, b"")]))
        skin = build_kn5(Kn5Parts(5, b"", [], [], parts_nodes))
        self.assertEqual(cmp(skin)["verdict"], kc.INCONCLUSIVE)


class TestCli(unittest.TestCase):
    def test_report_written_once_and_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.kn5", Path(d) / "b.kn5"
            a.write_bytes(BASE)
            b.write_bytes(kn5(model(body=mesh("body", QUAD, [1, 2, 0, 2, 3, 0]))))
            rep = Path(d) / "r" / "report.json"
            self.assertEqual(kc.main([str(a), str(b), "--report", str(rep)]), 0)
            first = rep.read_bytes()
            self.assertEqual(json.loads(first)["verdict"], kc.EQUIVALENT)
            self.assertEqual(kc.main([str(a), str(b), "--report", str(rep)]), 3)
            self.assertEqual(rep.read_bytes(), first)  # no se sobrescribe
            rep2 = Path(d) / "r" / "report2.json"
            kc.main([str(a), str(b), "--report", str(rep2)])
            self.assertEqual(
                json.loads(rep2.read_text(encoding="utf-8"))["verdict"],
                json.loads(first)["verdict"],
            )


class TestRealKn5(unittest.TestCase):
    """KN5 base de phaseC/run1 y phaseD/run1 (solo lectura; se omite si no existen)."""

    def setUp(self):
        if not (C_KN5.is_file() and D_KN5.is_file()):
            self.skipTest("sin los KN5 base de phaseC/phaseD")

    def test_phase_c_against_itself(self):
        self.assertEqual(kc.compare_files(C_KN5, C_KN5)["verdict"], kc.IDENTICAL)

    def test_phase_c_vs_phase_d(self):
        r = kc.compare_files(C_KN5, D_KN5)
        self.assertEqual(r["verdict"], kc.EQUIVALENT, r["differences"][:3])
        self.assertEqual(r["problems"], [])
        self.assertTrue(
            all(
                r["components"][k]
                for k in (
                    "header",
                    "textures",
                    "materials",
                    "hierarchy",
                    "texture_refs",
                )
            )
        )
        self.assertEqual([x["instance"] for x in r["representation"]], ["0/1/1"])
        rep = r["representation"][0]
        self.assertEqual(rep["name"], "35b84_chassis1")
        self.assertEqual(
            (rep["stats_a"]["vertices"], rep["stats_b"]["vertices"]), (7446, 7447)
        )
        self.assertEqual(
            (
                rep["stats_a"]["duplicate_vertex_records"],
                rep["stats_b"]["duplicate_vertex_records"],
            ),
            (14, 15),
        )
        self.assertEqual(rep["stats_a"]["triangles"], rep["stats_b"]["triangles"])
        self.assertTrue(rep["same_distinct_records"])
        self.assertEqual(len(rep["records_with_more_copies_in_b"]), 1)
        self.assertEqual(rep["records_with_more_copies_in_a"], [])
        # 0/1/1 es el nodo 74 del recorrido en preorden (la malla de las 4 instancias)
        _, root = load(C_KN5.read_bytes())
        order = [n for n, _ in walk(root)]
        self.assertEqual(
            [i for i, n in enumerate(order) if n.name == "35b84_chassis1"],
            [72, 73, 74, 75],
        )
        self.assertIs(order[74], root.children[1].children[1])


if __name__ == "__main__":
    unittest.main()
