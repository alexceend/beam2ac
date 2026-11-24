"""Volante animable bajo ``STEER_HR`` sin tocar ningún vértice (variante v12).

AC gira el nulo ``STEER_HR`` alrededor de su Z local (eje de la columna), como en los
coches de Kunos. El KN5 base trae un ``STEER_HR`` vacío en el origen; aquí:

1. Se mide el marco del volante en el aro (materiales que contienen
   ``rim_material_token``): centro = media de sus vértices en mundo; eje = normal del
   plano del aro (dirección de menor varianza, por iteración de potencias sobre
   ``tr(C)·I − C``), orientado hacia +Z (delante).
2. ``STEER_HR`` = marco con Z = eje (:func:`frame_matrix`); su hijo ``GEO_Steer_HR``
   lleva la matriz inversa, de modo que ``GEO·STEER = I`` y las mallas conservan sus
   coordenadas.
3. Las mallas de ``move_materials`` (hijas de ``parent_mesh``) se mueven enteras bajo
   ``GEO_Steer_HR``, byte a byte.
4. La malla de ``split_material`` se parte por triángulos enteros: los que tienen
   todos sus vértices a ``≤ split_axial_max_m`` del plano del aro (eje) giran; el resto
   (tubo y columna) queda fijo. Los vértices compartidos se duplican (costura).

Los materiales y el corte son propios del coche (perfil del vehículo). Todas las
comprobaciones de la herramienta de v12 se repiten aquí; si alguna falla, se lanza
:class:`SteerWheelError` y no se escribe nada.
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_materials import split_kn5
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
from src.safe_paths import ensure_safe_output_path

POWER_ITERATIONS = 500


class SteerWheelError(RuntimeError):
    """El volante no se puede montar con seguridad."""


_RIM_CACHE: dict = {}
_RIM_LOCK = threading.Lock()


def rim_frame(kn5_path: Path, materials: list[str], token: str):
    """(centro, eje, nº de vértices) del aro medido en mundo.

    Se memoriza por KN5 (ruta, fecha y tamaño): el editor de cámaras llama hasta 150 veces
    con el mismo modelo y cada lectura del KN5 tarda ~0,7 s."""
    st = Path(kn5_path).stat()
    key = (str(Path(kn5_path).resolve()), st.st_mtime_ns, st.st_size, tuple(materials), token)
    with _RIM_LOCK:
        if key not in _RIM_CACHE:
            if len(_RIM_CACHE) > 16:
                _RIM_CACHE.clear()
            _RIM_CACHE[key] = _rim_frame(kn5_path, materials, token)
        c, z, n = _RIM_CACHE[key]
    return list(c), list(z), n


def _rim_frame(kn5_path: Path, materials: list[str], token: str):
    ref = read_kn5(kn5_path)
    rim = [
        v
        for n in ref.nodes
        if n.type == 2 and n.vertices and token in materials[n.material]
        for v in ref.mesh_world_vertices(n)
    ]
    return rim_frame_points(rim, f"aro ('{token}')")


def rim_frame_points(rim: list, what: str = "aro"):
    """(centro, eje, nº de vértices) de una nube de puntos de aro (en mundo)."""
    if len(rim) < 3:
        raise SteerWheelError(f"sin vértices de {what}")
    c = [sum(v[i] for v in rim) / len(rim) for i in range(3)]
    cov = [
        [sum((v[i] - c[i]) * (v[j] - c[j]) for v in rim) / len(rim) for j in range(3)]
        for i in range(3)
    ]
    tr = sum(cov[i][i] for i in range(3))
    a = [[(tr if i == j else 0) - cov[i][j] for j in range(3)] for i in range(3)]
    z = [0.0, 0.0, 1.0]
    for _ in range(POWER_ITERATIONS):
        z = [sum(a[i][j] * z[j] for j in range(3)) for i in range(3)]
        nz = math.sqrt(sum(t * t for t in z))
        z = [t / nz for t in z]
    if z[2] < 0:
        z = [-t for t in z]
    return c, z, len(rim)


def _path_to(target: Node, node: Node, acc: tuple = ()):
    if node is target:
        return acc
    for ch in node.children:
        r = _path_to(target, ch, acc + (node,))
        if r is not None:
            return r
    return None


def _by_bytes(tree: Node, kn):
    """vértice (material, 44 bytes) → posiciones en mundo; triángulos por material."""
    out: dict = {}
    tri: Counter = Counter()
    for (n, _), kn_node in zip(walk(tree), kn.nodes):
        if n.name != kn_node.name:
            raise SteerWheelError("orden de nodos distinto al del lector")
        if n.type == 2:
            m = read_mesh(n)
            tri[m.material] += len(m.indices) // 3
            for raw, w in zip(m.vertices, kn.mesh_world_vertices(kn_node)):
                out.setdefault((m.material, raw), []).append(w)
    return out, tri


def rig_steering_wheel(src: Path, dst: Path, profile: dict[str, Any]) -> dict[str, Any]:
    data = Path(src).read_bytes()
    parts, root = load(data)
    if dump(parts, root) != data:
        raise SteerWheelError("el árbol no se reescribe idéntico")
    parts, root = load(data)
    mats = [m.name for m in parts.materials]
    order = list(walk(root))
    c, z, rim_vertices = rim_frame(Path(src), mats, profile["rim_material_token"])
    frame = frame_matrix(c, z)

    move = set(profile["move_materials"])
    # Mallas partidas: forma antigua (``split_material`` + corte axial ≤ ``split_axial_max_m``)
    # o nueva, de volante detectado (``splits`` + volumen: radio y distancia axial al plano
    # del aro; los triángulos con los 3 vértices dentro giran).
    legacy = "split_material" in profile
    if legacy:
        splits = [(profile["split_material"], profile["split_mesh_name"])]
    else:
        splits = [(s["material"], s["mesh_name"]) for s in profile.get("splits", [])]
    split_names = {m for m, _ in splits}
    to_move, split_nodes = [], {}
    for n, p in order:
        if n.type == 2 and p is not None and p.name == profile["parent_mesh"]:
            mname = mats[read_mesh(n).material]
            if mname in move:
                to_move.append((n, p))
            elif mname in split_names:
                if mname in split_nodes:
                    raise SteerWheelError(f"{mname}: más de una malla")
                split_nodes[mname] = n
    if len(to_move) != len(move) or len(split_nodes) != len(split_names):
        raise SteerWheelError(
            f"mallas del volante no encontradas: {len(to_move)}/{len(move)}, "
            f"partidas={len(split_nodes)}/{len(split_names)}"
        )
    steer = [(n, p) for n, p in order if n.name == "STEER_HR"]
    if len(steer) != 1 or steer[0][0].type != 1 or steer[0][0].children:
        raise SteerWheelError("se esperaba un STEER_HR nulo y vacío")
    steer_node = steer[0][0]
    for anc in _path_to(steer_node, root) or ():
        m = anc.matrix
        if not all(abs(m[i][j] - (i == j)) < 1e-6 for i in range(4) for j in range(4)):
            raise SteerWheelError(f"antecesor de STEER_HR no identidad: {anc.name}")

    # ---- edición
    steer_node.set_matrix(frame)
    geo = Node(1, "GEO_Steer_HR", 1, b"\0" * 64)
    geo.set_matrix(invert_rigid(frame))
    steer_node.children.append(geo)
    for n, p in to_move:
        p.children.remove(n)
        geo.children.append(n)
    def axial(v):
        return sum((v[j] - c[j]) * z[j] for j in range(3))

    def radial(v):
        d = [v[j] - c[j] for j in range(3)]
        return math.sqrt(max(0.0, sum(x * x for x in d) - axial(v) ** 2))

    if legacy:
        cut = float(profile["split_axial_max_m"])

        def turns(pos, idx):
            return max(axial(pos[i]) for i in idx) <= cut

    else:
        rmax, amax = float(profile["split_radius_m"]), float(profile["split_axial_m"])

        def turns(pos, idx):
            return all(radial(pos[i]) <= rmax and abs(axial(pos[i])) <= amax for i in idx)

    split_reports, seam, pieces = [], 0, 0
    for mname, mesh_name in splits:
        split_node = split_nodes[mname]
        mesh = read_mesh(split_node)
        pos = mesh.positions()
        wheel_part, rest = split_mesh(
            mesh, lambda t, pos=pos, mesh=mesh: turns(pos, mesh.indices[3 * t : 3 * t + 3])
        )
        split_node.payload = mesh_payload(rest)
        geo.children.append(
            Node(2, mesh_name, split_node.active, mesh_payload(wheel_part))
        )
        s = len(wheel_part.vertices) + len(rest.vertices) - len(mesh.vertices)
        seam += s
        pieces += 1
        split_reports.append(
            {
                "material": mname,
                "turning_triangles": len(wheel_part.indices) // 3,
                "fixed_triangles": len(rest.indices) // 3,
                "seam_vertices": s,
            }
        )
    if legacy:
        split_reports[0]["axial_max_m"] = cut
    out = dump(parts, root)
    target = ensure_safe_output_path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(out)

    # ---- comprobaciones (releyendo el archivo escrito)
    try:
        kb, ka = read_kn5(Path(src)), read_kn5(target)
        wb, tb = _by_bytes(load(data)[1], kb)
        wa, ta = _by_bytes(load(out)[1], ka)
        moved_mats = {i for i, nm in enumerate(mats) if nm in move | split_names}
        dev_fixed = dev_moved = 0.0
        extra = 0
        for key in set(wb) | set(wa):
            a, b = sorted(wa.get(key, [])), sorted(wb.get(key, []))
            if len(a) != len(b):
                extra += len(a) - len(b)
                a = a[: len(b)]
            d = max((math.dist(p, q) for p, q in zip(a, b)), default=0.0)
            if key[0] in moved_mats:
                dev_moved = max(dev_moved, d)
            else:
                dev_fixed = max(dev_fixed, d)
        pa, pb = split_kn5(out), split_kn5(data)
        ws = [n for n in ka.nodes if n.name == "STEER_HR"][0]
        product = mat_mul(geo.matrix, steer_node.matrix)
        checks = {
            "mallas no tocadas: desviación en mundo exactamente 0": dev_fixed == 0.0,
            "mallas del volante: desviación en mundo < 1e-6 m": dev_moved < 1e-6,
            "triángulos por material idénticos": ta == tb,
            "vértices añadidos = duplicados de la costura": extra == seam,
            "texturas idénticas": [(t.name, t.data) for t in pa.textures]
            == [(t.name, t.data) for t in pb.textures],
            "materiales idénticos": [
                (m.name, m.shader, m.props, m.slots) for m in pa.materials
            ]
            == [(m.name, m.shader, m.props, m.slots) for m in pb.materials],
            "nodos: +1 (GEO_Steer_HR) + una parte por malla partida": len(ka.nodes)
            == len(kb.nodes) + 1 + pieces,
            "STEER_HR · GEO_Steer_HR = identidad": all(
                abs(product[i][j] - (i == j)) < 1e-6 for i in range(4) for j in range(4)
            ),
            "STEER_HR en el centro del aro y Z = eje": math.dist(ws.translation, c)
            < 1e-6
            and abs(sum(ws.world[2][j] * z[j] for j in range(3)) - 1) < 1e-6,
        }
    except Exception:
        target.unlink()
        raise
    if not all(checks.values()):
        target.unlink()
        raise SteerWheelError(
            "comprobación fallida: " + ", ".join(k for k, v in checks.items() if not v)
        )
    return {
        "rim_centre": c,
        "rim_axis": z,
        "rim_vertices": rim_vertices,
        "tilt_deg": math.degrees(math.asin(-z[1])),
        "moved_meshes": [n.name for n, _ in to_move],
        "split": split_reports[0] if split_reports else None,
        "splits": split_reports,
        "deviation_moved_m": dev_moved,
        "deviation_fixed_m": dev_fixed,
        "checks": checks,
    }
