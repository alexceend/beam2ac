"""Pruebas de src.ac.kn5_tree: edición del árbol de un KN5 sin tocar la geometría."""

import math
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_tree import (
    Node,
    dump,
    frame_matrix,
    invert_rigid,
    load,
    mat_mul,
    mesh_payload,
    read_mesh,
    split_mesh,
    walk,
)
from src.ac.kn5_write import box_mesh, write_simple_kn5

_TMP = tempfile.TemporaryDirectory()
OUT = Path(_TMP.name)


def _kn5(name: str) -> bytes:
    meshes = [
        box_mesh("a", (0, 0, 0), (1, 1, 1)),
        box_mesh("b", (2, 0, 0), (3, 2, 1), material=1),
    ]
    write_simple_kn5(OUT / name, meshes, [("m0", "ksPerPixel"), ("m1", "ksPerPixel")])
    return (OUT / name).read_bytes()


def _world_triangles(path: Path) -> Counter:
    k = read_kn5(path)
    tris = Counter()
    for n in k.nodes:
        if n.type == 2:
            vs = k.mesh_world_vertices(n)
            for t in range(0, len(n.indices), 3):
                tris[
                    tuple(
                        tuple(round(c, 5) for c in vs[i]) for i in n.indices[t : t + 3]
                    )
                ] += 1
    return tris


class TestKn5Tree(unittest.TestCase):
    def test_roundtrip_identical_bytes(self):
        data = _kn5("rt.kn5")
        parts, root = load(data)
        self.assertEqual(dump(parts, root), data)

    def test_reparent_under_inverse_pair_keeps_world_geometry(self):
        data = _kn5("rp.kn5")
        parts, root = load(data)
        before = _world_triangles(OUT / "rp.kn5")
        parent_of_a = next(p for n, p in walk(root) if n.name == "a" and n.type == 2)
        mesh_a = next(n for n in parent_of_a.children if n.name == "a")
        parent_of_a.children.remove(mesh_a)
        m = frame_matrix((0.3, 0.7, 0.4), (0.0, -0.4, 0.9))
        steer = Node(1, "STEER_HR", 1, b"\0" * 64)
        steer.set_matrix(m)
        geo = Node(1, "GEO_Steer_HR", 1, b"\0" * 64)
        geo.set_matrix(invert_rigid(m))
        geo.children.append(mesh_a)
        steer.children.append(geo)
        root.children.append(steer)
        (OUT / "rp_out.kn5").write_bytes(dump(parts, root))
        self.assertEqual(_world_triangles(OUT / "rp_out.kn5"), before)

    def test_split_mesh_preserves_triangles(self):
        data = _kn5("sp.kn5")
        parts, root = load(data)
        node = next(n for n, _ in walk(root) if n.type == 2 and n.name == "a")
        mesh = read_mesh(node)
        a, b = split_mesh(mesh, lambda t: t < 4)
        self.assertEqual(len(a.indices) + len(b.indices), len(mesh.indices))

        def tri(m):
            return Counter(
                tuple(m.vertices[i] for i in m.indices[t : t + 3])
                for t in range(0, len(m.indices) // 3 * 3, 3)
            )

        self.assertEqual(tri(a) + tri(b), tri(mesh))
        self.assertTrue(
            set(a.vertices) <= set(mesh.vertices)
            and set(b.vertices) <= set(mesh.vertices)
        )
        # la carga útil reescrita se vuelve a leer igual
        node.payload = mesh_payload(a)
        self.assertEqual(read_mesh(node).vertices, a.vertices)

    def test_frame_matrix_orthonormal_right_handed(self):
        m = frame_matrix((1, 2, 3), (0.0004, -0.0158, 0.9999))
        x, y, z = (m[i][:3] for i in range(3))
        dot = lambda a, b: sum(p * q for p, q in zip(a, b))  # noqa: E731
        for a, b in ((x, y), (y, z), (x, z)):
            self.assertAlmostEqual(dot(a, b), 0.0, places=9)
        for a in (x, y, z):
            self.assertAlmostEqual(dot(a, a), 1.0, places=9)
        cross = [
            x[1] * y[2] - x[2] * y[1],
            x[2] * y[0] - x[0] * y[2],
            x[0] * y[1] - x[1] * y[0],
        ]
        self.assertTrue(all(math.isclose(c, d, abs_tol=1e-9) for c, d in zip(cross, z)))
        ident = mat_mul(m, invert_rigid(m))
        self.assertTrue(
            all(abs(ident[i][j] - (i == j)) < 1e-9 for i in range(4) for j in range(4))
        )


if __name__ == "__main__":
    unittest.main()
