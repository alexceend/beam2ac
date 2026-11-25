"""SPIKE del KN5 en Python puro (``src.mesh.pure_kn5``): datos sintéticos y, si existe,
comparación con un KN5 construido con Blender (se omite sin los archivos)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_materials import DiffuseSource, apply_diffuse, build_kn5, split_kn5
from src.ac.kn5_tree import parse_tree, read_mesh, walk
from src.mesh import pure_kn5

ROOT = Path(__file__).resolve().parent.parent

DAE = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit meter="0.01" name="centimeter"/><up_axis>Z_UP</up_axis></asset>
<library_materials>
 <material id="mA" name="mA"/><material id="mB" name="mB"/>
</library_materials>
<library_geometries><geometry id="g-lib"><mesh>
 <source id="P"><float_array id="Pa" count="18">0 0 0 100 0 0 100 100 0 0 100 0 200 0 0 200 100 0</float_array></source>
 <source id="N"><float_array id="Na" count="3">0 0 1</float_array></source>
 <source id="T"><float_array id="Ta" count="12">0 0 1 0 1 1 0 1 2 0 2 1</float_array></source>
 <vertices id="V"><input semantic="POSITION" source="#P"/></vertices>
 <polylist material="sA" count="1">
  <input semantic="VERTEX" offset="0" source="#V"/>
  <input semantic="NORMAL" offset="1" source="#N"/>
  <input semantic="TEXCOORD" offset="2" set="0" source="#T"/>
  <vcount>4</vcount><p>0 0 0 1 0 1 2 0 2 3 0 3</p>
 </polylist>
 <triangles material="sB" count="2">
  <input semantic="VERTEX" offset="0" source="#V"/>
  <input semantic="NORMAL" offset="1" source="#N"/>
  <input semantic="TEXCOORD" offset="2" set="0" source="#T"/>
  <p>1 0 1 4 0 4 5 0 5  5 0 5 4 0 4 1 0 1</p>
 </triangles>
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene><node name="obj" id="obj">
 <instance_geometry url="#g-lib"><bind_material><technique_common>
  <instance_material symbol="sA" target="#mA"/>
  <instance_material symbol="sB" target="#mB"/>
 </technique_common></bind_material></instance_geometry>
</node></visual_scene></library_visual_scenes>
</COLLADA>
"""

# cm -> m, sin giro, desplazada 1 m en x de BeamNG
M = [[0.01, 0, 0, 1.0], [0, 0.01, 0, 0], [0, 0, 0.01, 0], [0, 0, 0, 1]]
FRAME = [[1, 0, 0, 5.0], [0, 1, 0, 2.0], [0, 0, 1, 3.0], [0, 0, 0, 1]]


def synthetic_plan(dae: Path, parent: str | None = None) -> dict:
    return {
        "objects": [
            {
                "name": "obj",
                "mesh": "obj",
                "dae": str(dae),
                "matrix": M,
                "parent": parent,
            }
        ],
        "empties": [
            {"name": "WHEEL_LF", "matrix": FRAME, "parent": None},
            {"name": "STEER", "matrix": FRAME, "parent": "WHEEL_LF"},
        ],
    }


class SyntheticTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dae = self.tmp / "a.dae"
        self.dae.write_text(DAE, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, parent: str | None = None):
        out = self.tmp / "o.kn5"
        stats = pure_kn5.build_kn5_pure(synthetic_plan(self.dae, parent), out)
        return stats, out

    def test_triangulation_and_duplicates(self) -> None:
        src, names = pure_kn5.read_dae_meshes(self.dae, {"obj"})
        self.assertEqual(names, ["mA", "mB"])
        quad, tris = src["obj"].primitives
        self.assertEqual(quad.triangles, 2)  # abanico (0,1,2) (0,2,3)
        self.assertEqual([c[0] for c in quad.corners], [0, 1, 2, 0, 2, 3])
        stats, _ = self.build()
        # los 2 triángulos de sB son la misma cara en sentido contrario: queda 1
        self.assertEqual(stats["triangles"], 3)
        self.assertEqual(stats["materials"], 2)

    def test_geometry_axes_uv_and_rotation(self) -> None:
        _, out = self.build()
        k = read_kn5(out)
        mesh = next(n for n in k.nodes if n.type == 2 and n.name == "obj")
        # vértice 0 del DAE (0,0,0): BeamNG (1,0,0) -> AC (x, z, -y) = (1, 0, 0);
        # vértice 2 (100,100,0) cm: BeamNG (2,1,0) -> AC (2, 0, -1)
        pts = {tuple(round(c, 5) for c in v) for v in mesh.vertices}
        self.assertIn((1.0, 0.0, 0.0), pts)
        self.assertIn((2.0, 0.0, -1.0), pts)
        parts = split_kn5(out.read_bytes())
        root = parse_tree(parts.nodes)
        m = read_mesh(next(n for n, _ in walk(root) if n.type == 2))
        # primer triángulo (v0, v1, v2) se escribe rotado (v1, v2, v0)
        self.assertEqual(m.indices[:3], [1, 2, 0])
        import struct

        rec = struct.unpack("<11f", m.vertices[0])
        self.assertEqual(rec[3:6], (0.0, 1.0, 0.0))  # normal (0,0,1) B -> (0,1,0) AC
        self.assertEqual(rec[6:8], (0.0, -0.0))  # UV (0,0) -> (u, -v)
        self.assertEqual(rec[8:], (0.0, 0.0, 0.0))  # tangente nula
        rec1 = struct.unpack("<11f", m.vertices[1])
        self.assertEqual(rec1[6:8], (1.0, -0.0))

    def test_tree_parent_inverse_and_kn5_valid_for_apply_diffuse(self) -> None:
        stats, out = self.build("WHEEL_LF")
        k = read_kn5(out)
        by = {n.name: n for n in k.nodes if n.type == 1}
        self.assertEqual(k.nodes[0].name, "BlenderFile")
        # BeamNG (5,2,3) -> AC (5,3,-2); hijo = inversa del padre
        self.assertEqual(
            tuple(round(c, 5) for c in by["WHEEL_LF"].local[3][:3]), (5, 3, -2)
        )
        self.assertEqual(
            tuple(round(c, 5) for c in by["obj"].local[3][:3]), (-5, -3, 2)
        )
        self.assertEqual(by["STEER"].local[3][:3], (0.0, 0.0, 0.0))
        # el árbol cuelga: raíz > WHEEL_LF > (STEER, obj) > mallas
        self.assertEqual(
            [k.nodes[c].name for c in by["WHEEL_LF"].children], ["STEER", "obj"]
        )
        parts = split_kn5(out.read_bytes())
        self.assertEqual([m.name for m in parts.materials], ["mA", "mB"])
        self.assertEqual(parts.materials[0].shader, "ksPerPixel")
        srcs = [
            DiffuseSource(m.name, m.name, "diffuseColor", f"{m.name}.dds", b"DDS ")
            for m in parts.materials
        ]
        again = build_kn5(apply_diffuse(parts, srcs))  # el flujo de texturas lo acepta
        self.assertEqual(len(split_kn5(again).textures), 2)

    def test_blender_material_names(self) -> None:
        got = pure_kn5.blender_material_names([["a", "b", "a"], ["a", "c"], ["a"]])
        self.assertEqual(got[0], {"a": "a", "b": "b"})
        self.assertEqual(got[1], {"a": "a.001", "c": "c"})
        self.assertEqual(got[2], {"a": "a.002"})
        long = "x" * 70
        n = pure_kn5.blender_material_names([[long], [long]])
        self.assertEqual(len(n[0][long].encode()), 63)
        self.assertTrue(n[1][long].endswith(".001"))
        self.assertLessEqual(len(n[1][long].encode()), 63)

    def test_split_over_16_bits(self) -> None:
        verts = [(float(i), 0.0, 0.0) + (0.0,) * 8 for i in range(70000)]
        idx = [i for t in range(23333) for i in (3 * t, 3 * t + 1, 3 * t + 2)]
        parts = pure_kn5._chunks(pure_kn5.BuiltMesh("m", "x", verts, idx))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p.vertices) <= 65535 for p in parts))
        self.assertEqual(sum(len(p.indices) for p in parts), len(idx))


REF_DIR = ROOT / "output" / "seat_ibiza_acfr" / "kn5_variability_2026-10-25" / "run_01"
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr" / "35b84"


@unittest.skipUnless(
    (REF_DIR / "seat_ibiza_acfr.kn5").exists() and (IBIZA / "35b84_mesh.dae").exists(),
    "falta el KN5 de referencia de Blender o la muestra del Ibiza",
)
class ReferenceTests(unittest.TestCase):
    """Estructura frente al KN5 de Blender (no bytes: la partición de vértices y
    algunas normales difieren; ver el docstring de ``pure_kn5``)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        plan = json.loads((REF_DIR / "scene_plan.json").read_text(encoding="utf-8"))
        cls.out = Path(cls._tmp.name) / "ibiza.kn5"
        cls.stats = pure_kn5.build_kn5_pure(plan, cls.out, [IBIZA])
        cls.a = read_kn5(REF_DIR / "seat_ibiza_acfr.kn5")
        cls.b = read_kn5(cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @staticmethod
    def _tree(k) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []

        def rec(i: int, path: str) -> None:
            n = k.nodes[i]
            out.append((path + "/" + n.name, n.type))
            for c in n.children:
                rec(c, path + "/" + n.name)

        rec(0, "")
        return out

    def test_hierarchy_and_materials(self) -> None:
        self.assertEqual(self._tree(self.a), self._tree(self.b))
        self.assertEqual(self.a.materials, self.b.materials)
        self.assertEqual(self.a.textures, self.b.textures)

    def test_indices_and_boxes(self) -> None:
        def meshes(k):
            return [
                (k.nodes[n.parent].name, k.materials[n.material][0], n)
                for n in k.nodes
                if n.type == 2
            ]

        ma, mb = meshes(self.a), meshes(self.b)
        self.assertEqual([x[:2] for x in ma], [x[:2] for x in mb])
        worst = 0.0
        for (_, _, x), (_, _, y) in zip(ma, mb):
            self.assertEqual(len(x.indices), len(y.indices))  # incl. caras repetidas
            pa, pb = self.a.mesh_world_vertices(x), self.b.mesh_world_vertices(y)
            for i in range(3):
                worst = max(
                    worst,
                    abs(min(p[i] for p in pa) - min(p[i] for p in pb)),
                    abs(max(p[i] for p in pa) - max(p[i] for p in pb)),
                )
        self.assertLess(worst, 1e-4)

    def test_empties(self) -> None:
        for n in self.a.nodes:
            if n.type == 1 and n.name.startswith(("WHEEL", "SUSP", "COCKPIT", "STEER")):
                m = self.b.find(n.name)[0]
                dev = max(
                    abs(p - q) for r, s in zip(n.local, m.local) for p, q in zip(r, s)
                )
                self.assertLess(dev, 1e-5, n.name)


if __name__ == "__main__":
    unittest.main()
