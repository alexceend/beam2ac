"""Comparador de equivalencia geométrica de dos KN5 (solo diagnóstico).

Uso:
    python -m src.ac.kn5_compare A.kn5 B.kn5 [--report informe.json]

No modifica los KN5. El informe (JSON, sin fechas: reproducible) solo se escribe en una
ruta nueva permitida por ``src.safe_paths``.

Veredictos (de más a menos fuerte):

* ``idéntico byte a byte``: los dos archivos tienen los mismos bytes.
* ``equivalente (solo representación)``: cabecera, texturas, materiales, jerarquía y
  transformaciones idénticos, y en cada instancia de malla el **multiconjunto** de
  triángulos es idéntico. Un triángulo es la terna de registros de vértice completos
  (posición, normal, UV y tangente, 44 bytes) en su orden de giro, rotada para empezar
  por el menor registro sin invertir el sentido. Cambian solo el orden de los vértices o
  de los índices, o hay registros de vértice repetidos o sin usar.
* ``diferente``: hay al menos una diferencia real, localizada por instancia.
* ``no concluyente``: algún archivo no se puede interpretar (datos malformados, nodos
  con piel de tipo 3, índices fuera de rango) o contiene NaN o infinitos en un valor
  comparado. Nunca se declara equivalencia en ese caso.

Igualdad «exacta» = igualdad bit a bit de los floats (``-0.0`` ≠ ``0.0``). Las mallas se
identifican por su posición en el árbol (``0/2/1``: hijo 1 del hijo 2 de la raíz), no
por el nombre, que puede repetirse.

Formato: el mismo que leen ``kn5_materials.split_kn5`` (cabecera, texturas, materiales)
y ``kn5_tree.parse_tree`` (nodos de tipo 1 y 2), reutilizados aquí.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from src.ac.kn5_materials import Kn5FormatError, split_kn5
from src.ac.kn5_tree import VERTEX_SIZE, parse_tree

TOOL_VERSION = 1
IDENTICAL = "idéntico byte a byte"
EQUIVALENT = "equivalente (solo representación)"
DIFFERENT = "diferente"
INCONCLUSIVE = "no concluyente"
MAX_SAMPLES = 5


class Kn5Unreadable(Exception):
    """El KN5 no se puede interpretar con este comparador."""


# ------------------------------------------------------------------ lectura
def _finite(values) -> bool:
    return all(math.isfinite(v) for v in values)


def _canon(tri: tuple[bytes, bytes, bytes]) -> tuple[bytes, bytes, bytes]:
    k = min(range(3), key=lambda i: tri[i])
    return tri[k:] + tri[:k]


def _mesh(path: str, payload: bytes, problems: list[str]) -> dict[str, Any]:
    try:
        flags = payload[:3]
        vc = struct.unpack_from("<i", payload, 3)[0]
        p = 7
        if vc < 0 or p + vc * VERTEX_SIZE > len(payload):
            raise Kn5Unreadable(f"{path}: nº de vértices inválido ({vc})")
        verts = [
            payload[p + i * VERTEX_SIZE : p + (i + 1) * VERTEX_SIZE] for i in range(vc)
        ]
        p += vc * VERTEX_SIZE
        ic = struct.unpack_from("<i", payload, p)[0]
        if ic < 0 or ic % 3:
            raise Kn5Unreadable(f"{path}: nº de índices inválido ({ic})")
        idx = list(struct.unpack_from(f"<{ic}H", payload, p + 4))
        p += 4 + ic * 2
        tail = payload[p:]
        if len(tail) != 33:
            raise Kn5Unreadable(f"{path}: cola de malla de {len(tail)} bytes (33)")
        material, layer = struct.unpack_from("<iI", tail, 0)
        floats = struct.unpack_from("<6f", tail, 8)
        renderable = tail[32]
    except struct.error as e:
        raise Kn5Unreadable(f"{path}: malla truncada ({e})") from None
    bad = [i for i in idx if i >= vc]
    if bad:
        raise Kn5Unreadable(
            f"{path}: {len(bad)} índices fuera de rango (máx. {vc - 1})"
        )
    nonfinite = sum(1 for v in verts if not _finite(struct.unpack("<11f", v)))
    if nonfinite:
        problems.append(f"{path}: {nonfinite} vértices con NaN o infinitos")
    if not _finite(floats):
        problems.append(f"{path}: LOD o esfera envolvente con NaN o infinitos")
    tris = [tuple(verts[j] for j in idx[k : k + 3]) for k in range(0, ic, 3)]
    counts = Counter(verts)
    used = set(idx)
    return {
        "flags": flags.hex(),
        "material": material,
        "layer": layer,
        "lod_in_out": tail[8:16].hex(),
        "sphere": tail[16:32].hex(),
        "renderable": renderable,
        "vertices": verts,
        "indices": idx,
        "triangles": Counter(_canon(t) for t in tris),
        "stats": {
            "vertices": vc,
            "indices": ic,
            "triangles": len(tris),
            "distinct_vertex_records": len(counts),
            "duplicate_vertex_records": sum(c - 1 for c in counts.values() if c > 1),
            "unused_vertices": sum(1 for i in range(vc) if i not in used),
            "repeated_triangles": sum(
                c - 1 for c in Counter(_canon(t) for t in tris).values() if c > 1
            ),
        },
    }


def read_model(data: bytes) -> dict[str, Any]:
    """Modelo comparable de un KN5. Lanza :class:`Kn5Unreadable` si no se interpreta."""
    problems: list[str] = []
    try:
        parts = split_kn5(data)
        root = parse_tree(parts.nodes)
    except (Kn5FormatError, struct.error, UnicodeDecodeError, IndexError) as e:
        raise Kn5Unreadable(f"KN5 no interpretable: {e}") from None
    textures = [
        {
            "name": t.name,
            "active": t.active,
            "bytes": len(t.data),
            "sha256": hashlib.sha256(t.data).hexdigest(),
        }
        for t in parts.textures
    ]
    materials = []
    for i, m in enumerate(parts.materials):
        props = []
        for name, vals in m.props:
            if not _finite(vals):
                problems.append(f"material {i} {m.name!r}: propiedad {name} no finita")
            props.append((name, struct.pack("<10f", *vals).hex()))
        materials.append(
            {
                "name": m.name,
                "shader": m.shader,
                "blend": m.blend,
                "alpha_tested": m.alpha_tested,
                "depth": m.depth,
                "props": props,
                "slots": [list(s) for s in m.slots],
            }
        )
    tex_names = {t.name for t in parts.textures}
    unresolved = sorted(
        {s[2] for m in parts.materials for s in m.slots if s[2] not in tex_names}
    )
    instances: dict[str, dict[str, Any]] = {}

    def visit(node, path: str) -> None:
        inst: dict[str, Any] = {
            "name": node.name,
            "type": node.type,
            "active": node.active,
            "children": len(node.children),
        }
        if node.type == 1:
            vals = struct.unpack("<16f", node.payload)
            if not _finite(vals):
                problems.append(f"{path} {node.name!r}: matriz con NaN o infinitos")
            inst["matrix"] = node.payload.hex()
        else:
            inst["mesh"] = _mesh(path, node.payload, problems)
            if not 0 <= inst["mesh"]["material"] < len(parts.materials):
                raise Kn5Unreadable(
                    f"{path}: material {inst['mesh']['material']} inexistente"
                )
        instances[path] = inst
        for i, child in enumerate(node.children):
            visit(child, f"{path}/{i}")

    visit(root, "0")
    return {
        "version": parts.version,
        "extra": parts.extra.hex(),
        "textures": textures,
        "materials": materials,
        "unresolved_texture_refs": unresolved,
        "instances": instances,
        "problems": problems,
    }


# ------------------------------------------------------------------ comparación
def _sample(counter: Counter) -> list[list[list[float]]]:
    out = []
    for tri in list(counter.elements())[:MAX_SAMPLES]:
        out.append([[round(x, 6) for x in struct.unpack("<11f", r)] for r in tri])
    return out


def _compare_mesh(path: str, name: str, a: dict, b: dict, diffs: list, reps: list):
    for key in ("flags", "material", "layer", "lod_in_out", "sphere", "renderable"):
        if a[key] != b[key]:
            diffs.append(
                {
                    "instance": path,
                    "name": name,
                    "what": f"malla: {key}",
                    "a": a[key],
                    "b": b[key],
                }
            )
    if a["triangles"] != b["triangles"]:
        only_a, only_b = (
            a["triangles"] - b["triangles"],
            b["triangles"] - a["triangles"],
        )
        diffs.append(
            {
                "instance": path,
                "name": name,
                "what": "malla: triángulos "
                "(registro completo, sentido de giro, multiplicidad)",
                "only_in_a": sum(only_a.values()),
                "only_in_b": sum(only_b.values()),
                "sample_only_a": _sample(only_a),
                "sample_only_b": _sample(only_b),
            }
        )
    elif a["vertices"] != b["vertices"] or a["indices"] != b["indices"]:
        ca, cb = Counter(a["vertices"]), Counter(b["vertices"])
        more_b, more_a = cb - ca, ca - cb
        reps.append(
            {
                "instance": path,
                "name": name,
                "what": "representación de vértices/índices (triángulos idénticos)",
                "stats_a": a["stats"],
                "stats_b": b["stats"],
                "records_with_more_copies_in_a": [
                    {
                        "copies_extra": n,
                        "record": [round(x, 6) for x in struct.unpack("<11f", r)],
                    }
                    for r, n in list(more_a.items())[:MAX_SAMPLES]
                ],
                "records_with_more_copies_in_b": [
                    {
                        "copies_extra": n,
                        "record": [round(x, 6) for x in struct.unpack("<11f", r)],
                    }
                    for r, n in list(more_b.items())[:MAX_SAMPLES]
                ],
                "same_distinct_records": set(ca) == set(cb),
            }
        )


def compare_bytes(a: bytes, b: bytes) -> dict[str, Any]:
    report: dict[str, Any] = {
        "tool": "src.ac.kn5_compare",
        "tool_version": TOOL_VERSION,
        "inputs": {
            "a": {"sha256": hashlib.sha256(a).hexdigest(), "bytes": len(a)},
            "b": {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)},
        },
        "byte_identical": a == b,
        "components": {},
        "differences": [],
        "representation": [],
        "problems": [],
    }
    try:
        ma, mb = read_model(a), read_model(b)
    except Kn5Unreadable as e:
        report["problems"].append(str(e))
        report["verdict"] = IDENTICAL if a == b else INCONCLUSIVE
        return report
    report["problems"] = [f"a: {p}" for p in ma["problems"]] + [
        f"b: {p}" for p in mb["problems"]
    ]
    diffs, reps = report["differences"], report["representation"]
    comp = report["components"]
    comp["header"] = (ma["version"], ma["extra"]) == (mb["version"], mb["extra"])
    if not comp["header"]:
        diffs.append(
            {
                "what": "cabecera",
                "a": [ma["version"], ma["extra"]],
                "b": [mb["version"], mb["extra"]],
            }
        )
    comp["textures"] = ma["textures"] == mb["textures"]
    if not comp["textures"]:
        diffs.append(
            {
                "what": "texturas (nombre, activa, tamaño, sha256; en orden)",
                "a": ma["textures"],
                "b": mb["textures"],
            }
        )
    comp["materials"] = ma["materials"] == mb["materials"]
    if not comp["materials"]:
        for i in range(max(len(ma["materials"]), len(mb["materials"]))):
            x = ma["materials"][i] if i < len(ma["materials"]) else None
            y = mb["materials"][i] if i < len(mb["materials"]) else None
            if x != y:
                diffs.append({"what": f"material {i}", "a": x, "b": y})
    comp["texture_refs"] = (
        ma["unresolved_texture_refs"] == mb["unresolved_texture_refs"]
    )
    report["unresolved_texture_refs"] = {
        "a": ma["unresolved_texture_refs"],
        "b": mb["unresolved_texture_refs"],
    }
    ia, ib = ma["instances"], mb["instances"]
    hierarchy_ok = True
    for path in sorted(set(ia) | set(ib)):
        x, y = ia.get(path), ib.get(path)
        if x is None or y is None:
            hierarchy_ok = False
            diffs.append(
                {
                    "instance": path,
                    "what": "jerarquía: instancia en un solo KN5",
                    "a": x and x["name"],
                    "b": y and y["name"],
                }
            )
            continue
        head = ("name", "type", "active", "children")
        if any(x[k] != y[k] for k in head):
            hierarchy_ok = False
            diffs.append(
                {
                    "instance": path,
                    "what": "jerarquía: nombre/tipo/activo/hijos",
                    "a": [x[k] for k in head],
                    "b": [y[k] for k in head],
                }
            )
            continue
        if x["type"] == 1 and x["matrix"] != y["matrix"]:
            diffs.append(
                {
                    "instance": path,
                    "name": x["name"],
                    "what": "transformación local (matriz)",
                    "a": list(struct.unpack("<16f", bytes.fromhex(x["matrix"]))),
                    "b": list(struct.unpack("<16f", bytes.fromhex(y["matrix"]))),
                }
            )
        if x["type"] == 2:
            _compare_mesh(path, x["name"], x["mesh"], y["mesh"], diffs, reps)
    comp["hierarchy"] = hierarchy_ok
    comp["meshes"] = {
        "instances": sum(1 for v in ia.values() if v["type"] == 2),
        "with_real_differences": sorted(
            {d["instance"] for d in diffs if d.get("what", "").startswith("malla")}
        ),
        "representation_only": [r["instance"] for r in reps],
    }
    if a == b:
        report["verdict"] = IDENTICAL
    elif report["problems"]:
        report["verdict"] = INCONCLUSIVE
    elif diffs:
        report["verdict"] = DIFFERENT
    else:
        report["verdict"] = EQUIVALENT
    return report


def compare_files(a: Path, b: Path) -> dict[str, Any]:
    report = compare_bytes(Path(a).read_bytes(), Path(b).read_bytes())
    report["inputs"]["a"]["path"] = str(a)
    report["inputs"]["b"]["path"] = str(b)
    return report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m src.ac.kn5_compare", description=__doc__.splitlines()[0]
    )
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--report", help="JSON nuevo (no se sobrescribe)")
    args = ap.parse_args(argv)
    report = compare_files(Path(args.a), Path(args.b))
    if args.report:
        from src.safe_paths import ensure_safe_output_path

        target = ensure_safe_output_path(args.report)
        if target.exists():
            print(f"ERROR: ya existe {target}; no se sobrescribe", file=sys.stderr)
            return 3
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    print(f"Veredicto: {report['verdict']}")
    print(f"Byte a byte: {report['byte_identical']}")
    for k, v in report["components"].items():
        print(f"  {k}: {v}")
    for d in report["differences"][:20]:
        print(f"  DIFERENCIA {d.get('instance', '')} {d.get('name', '')}: {d['what']}")
    for r in report["representation"]:
        print(
            f"  REPRESENTACIÓN {r['instance']} {r['name']}: "
            f"{r['stats_a']} → {r['stats_b']}"
        )
    for p in report["problems"]:
        print(f"  PROBLEMA {p}")
    return {IDENTICAL: 0, EQUIVALENT: 0, DIFFERENT: 1, INCONCLUSIVE: 2}[
        report["verdict"]
    ]


if __name__ == "__main__":
    sys.exit(main())
