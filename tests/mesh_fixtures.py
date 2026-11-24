"""Generadores de DAE y KN5 mínimos para pruebas (escritos en carpetas temporales)."""

from __future__ import annotations

import struct
from pathlib import Path

from src.safe_paths import ensure_safe_output_path

NS = "http://www.collada.org/2005/11/COLLADASchema"


def write_dae(
    path: Path,
    geoms: dict[str, list[tuple[float, float, float]]],
    unit: float = 0.01,
    up: str = "Z_UP",
    nan_normals: int = 0,
    node_extra: str = "",
    matrix: str = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1",
) -> Path:
    """DAE con una geometría y un nodo (con <matrix>) por entrada de ``geoms``."""
    lib, scene = [], []
    for name, pts in geoms.items():
        flat = " ".join(f"{c:g}" for p in pts for c in p)
        normals = " ".join(
            ["nan(ind)"] * nan_normals + ["0"] * (3 - nan_normals % 3 or 3)
        )
        lib.append(
            f'<geometry id="{name}-lib" name="{name}Mesh"><mesh>'
            f'<source id="{name}-POSITION"><float_array id="{name}-POSITION-array" '
            f'count="{len(pts) * 3}">{flat}</float_array></source>'
            f'<source id="{name}-Normal0"><float_array id="{name}-Normal0-array">'
            f"{normals}</float_array></source>"
            f'<vertices id="{name}-VERTEX"><input semantic="POSITION" '
            f'source="#{name}-POSITION"/></vertices></mesh></geometry>'
        )
        scene.append(
            f'<node name="{name}" id="{name}"><matrix sid="matrix">{matrix}</matrix>'
            f'{node_extra}<instance_geometry url="#{name}-lib"/></node>'
        )
    text = (
        f'<?xml version="1.0"?><COLLADA xmlns="{NS}" version="1.4.1">'
        f'<asset><unit meter="{unit}" name="u"/><up_axis>{up}</up_axis></asset>'
        f"<library_geometries>{''.join(lib)}</library_geometries>"
        f'<library_visual_scenes><visual_scene id="s">{"".join(scene)}'
        "</visual_scene></library_visual_scenes></COLLADA>"
    )
    target = ensure_safe_output_path(path)
    target.write_text(text, encoding="utf-8")
    return target


def _s(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<i", len(data)) + data


def _dummy(name: str, children: int, matrix: list[float]) -> bytes:
    return (
        struct.pack("<i", 1)
        + _s(name)
        + struct.pack("<i", children)
        + b"\x01"
        + struct.pack("<16f", *matrix)
    )


def _mesh(name: str, verts, normal=(0.0, 1.0, 0.0)) -> bytes:
    out = (
        struct.pack("<i", 2)
        + _s(name)
        + struct.pack("<i", 0)
        + b"\x01"
        + b"\x01\x01\x00"
    )
    out += struct.pack("<i", len(verts))
    for v in verts:
        out += struct.pack("<3f3f2f3f", *v, *normal, 0, 0, 1, 0, 0)
    out += struct.pack("<i", 3) + struct.pack("<3H", 0, 1, 2) + struct.pack("<i", 0)
    out += b"\x00" * 29
    return out


def translation(x: float, y: float, z: float) -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def write_kn5(path: Path, nan_normal: bool = False) -> Path:
    """root -> WHEEL_LF (en 1,2,3) -> malla TIRE con 3 vértices locales."""
    body = b"sc6969" + struct.pack("<I", 5)
    body += struct.pack("<i", 0)  # texturas
    body += struct.pack("<i", 1) + _s("mat") + _s("ksPerPixel") + b"\x00\x00"
    body += struct.pack("<i", 0) + struct.pack("<i", 0) + struct.pack("<i", 0)
    body += _dummy("root", 1, translation(0, 0, 0))
    body += _dummy("WHEEL_LF", 1, translation(1, 2, 3))
    normal = (float("nan"), 0.0, 0.0) if nan_normal else (0.0, 1.0, 0.0)
    body += _mesh("TIRE", [(0.1, 0, 0), (-0.1, 0, 0), (0, 0.3, 0)], normal)
    target = ensure_safe_output_path(path)
    target.write_bytes(body)
    return target
