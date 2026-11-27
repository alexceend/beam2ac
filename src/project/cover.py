"""Imagen de portada de un coche (``skins/<skin>/preview.jpg``): vista desde arriba en diagonal.

Se dibuja en Python con Pillow, sin programas externos: los triángulos del KN5 se proyectan en ortogonal
desde delante-izquierda y arriba, se ordenan de lejos a cerca (algoritmo del pintor) y se sombrean planos con
el color medio de la textura difusa de su material, sobre fondo oscuro, a 1024×576 como los coches de Kunos.
Solo se usa si el usuario no ha subido su propia imagen (``details.py``). Es una vista previa esquemática,
no un render fotorrealista.
"""

from __future__ import annotations

import io
import math
import threading
from pathlib import Path

SIZE = (1024, 576)
SUPERSAMPLE = 2  # se dibuja al doble y se reduce (bordes menos dentados)
# dirección HACIA la cámara en ejes del KN5 (x izquierda, y arriba, z adelante): delante-izquierda y arriba
TO_CAMERA = (0.70, 0.48, 0.85)
LIGHT = (0.35, 0.85, 0.45)
GLASS = (26, 31, 38)

_RENDER_LOCK = (
    threading.Lock()
)  # una portada a la vez (la interfaz y la exportación pueden pedirla a la vez)
_CACHE: dict[tuple[str, int, int], bytes] = {}


class CoverError(RuntimeError):
    """No se pudo generar la portada."""


def to_jpeg(data: bytes) -> bytes:
    """Cualquier imagen → JPEG 1024×576 (recorte centrado a 16:9). Necesita Pillow."""
    try:
        from PIL import Image
    except ImportError:
        # sin Pillow solo se acepta un JPEG ya listo (el navegador lo recorta a 1024×576 antes de subirlo)
        if data[:3] == bytes([0xFF, 0xD8, 0xFF]):
            return data
        raise CoverError(
            "falta Pillow para convertir la imagen: sube un JPG (o instala pillow)"
        ) from None
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:
        raise CoverError(f"la imagen no se puede leer: {exc}") from None
    im = im.convert("RGB")
    w, h = im.size
    tw = int(h * SIZE[0] / SIZE[1])
    if tw <= w:  # más ancha que 16:9 → recorta a los lados; si no, recorta arriba/abajo
        box = ((w - tw) // 2, 0, (w - tw) // 2 + tw, h)
    else:
        th = int(w * SIZE[1] / SIZE[0])
        box = (0, (h - th) // 2, w, (h - th) // 2 + th)
    out = io.BytesIO()
    im.crop(box).resize(SIZE, Image.LANCZOS).save(out, "JPEG", quality=90)
    return out.getvalue()


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    n = math.sqrt(sum((x * x, y * y, z * z))) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _material_colors(parts) -> list[tuple[int, int, int]]:
    """Color medio (RGB) de la textura difusa de cada material; gris si no la tiene o no se lee."""
    from PIL import Image

    textures = {t.name: t.data for t in parts.textures}
    cache: dict[str, tuple[int, int, int]] = {}
    out = []
    for m in parts.materials:
        color = (140, 140, 140)
        name = next((n for slot, _, n in m.slots if slot == "txDiffuse"), None)
        if name in textures:
            if name not in cache:
                try:
                    im = Image.open(io.BytesIO(textures[name])).convert("RGB")
                    cache[name] = (
                        im.reduce(max(1, min(im.size) // 8))
                        .resize((1, 1), Image.BOX)
                        .getpixel((0, 0))
                    )
                except Exception:  # noqa: BLE001 - textura ilegible: se usa el gris
                    cache[name] = color
            color = cache[name]
        out.append(color)
    return out


def render_cover(kn5: Path) -> bytes:
    """JPEG de portada dibujado desde ``kn5``. Una petición a la vez; las que esperan reciben el resultado
    de la primera si el KN5 es el mismo."""
    st = Path(kn5).stat()
    key = (str(Path(kn5).resolve()), st.st_mtime_ns, st.st_size)
    with _RENDER_LOCK:
        if key not in _CACHE:
            if len(_CACHE) >= 8:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = _render(Path(kn5))
        return _CACHE[key]


def _render(kn5: Path) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise CoverError("falta Pillow para dibujar la portada") from exc
    from src.ac.kn5_inspect import Kn5Error, read_kn5
    from src.ac.kn5_materials import Kn5FormatError, split_kn5

    try:
        parts = split_kn5(kn5.read_bytes())
        model = read_kn5(kn5)
    except (
        Kn5Error,
        Kn5FormatError,
        OSError,
        ValueError,
        IndexError,
    ) as exc:  # UnicodeDecodeError es ValueError
        raise CoverError(f"no se puede leer el KN5: {exc}") from exc
    colors = _material_colors(parts)
    glass = {i for i, m in enumerate(model.materials) if m[1] == "ksPerPixelAlpha"}

    # base de la cámara (ortogonal): derecha, arriba y hacia la cámara
    d = _unit(TO_CAMERA)
    right = _unit(_cross((0.0, 1.0, 0.0), d))
    up = _cross(d, right)
    light = _unit(LIGHT)
    tris = []  # (profundidad, 3 puntos de pantalla, color)
    for node in model.nodes:
        if node.type != 2 or not node.vertices or not node.indices:
            continue
        if not node.flags.get("visible", 1) or node.flags.get("renderable") is False:
            continue
        pts = model.mesh_world_vertices(node)
        (r0, r1, r2), (u0, u1, u2), (d0, d1, d2) = right, up, d
        proj = [
            (
                x * r0 + y * r1 + z * r2,
                x * u0 + y * u1 + z * u2,
                x * d0 + y * d1 + z * d2,
            )
            for x, y, z in pts
        ]
        base = (
            colors[node.material]
            if node.material is not None and node.material < len(colors)
            else (140, 140, 140)
        )
        is_glass = node.material in glass or node.flags.get("transparent")
        idx = node.indices
        for t in range(0, len(idx) - 2, 3):
            a, b, c = idx[t], idx[t + 1], idx[t + 2]
            if a >= len(pts) or b >= len(pts) or c >= len(pts):
                continue
            pa, pb, pc = pts[a], pts[b], pts[c]
            n = _cross(
                (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]),
                (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]),
            )
            if _dot(n, d) < 0:  # normal hacia la cámara (la malla es de doble cara)
                n = (-n[0], -n[1], -n[2])
            k = 0.30 + 0.70 * max(0.0, _dot(_unit(n), light))
            col = GLASS if is_glass else base
            tris.append(
                (
                    (proj[a][2] + proj[b][2] + proj[c][2]) / 3.0,
                    proj[a][:2],
                    proj[b][:2],
                    proj[c][:2],
                    (int(col[0] * k), int(col[1] * k), int(col[2] * k)),
                )
            )
    if not tris:
        raise CoverError("el KN5 no tiene mallas visibles")
    xs = [p[0] for _, a, b, c, _ in tris for p in (a, b, c)]
    ys = [p[1] for _, a, b, c, _ in tris for p in (a, b, c)]
    w, h = SIZE[0] * SUPERSAMPLE, SIZE[1] * SUPERSAMPLE
    scale = 0.86 * min(
        w / ((max(xs) - min(xs)) or 1.0), h / ((max(ys) - min(ys)) or 1.0)
    )
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    mx, my = w / 2, h / 2
    img = Image.new("RGB", (w, h), (24, 27, 33))
    draw = ImageDraw.Draw(img)
    for y in range(h):  # degradado vertical sencillo
        v = int(18 + 22 * y / h)
        draw.line([(0, y), (w, y)], fill=(v, v + 3, v + 9))
    tris.sort(key=lambda t: t[0])  # de lejos a cerca
    for _, a, b, c, col in tris:
        draw.polygon(
            [
                (mx + (a[0] - cx) * scale, my - (a[1] - cy) * scale),
                (mx + (b[0] - cx) * scale, my - (b[1] - cy) * scale),
                (mx + (c[0] - cx) * scale, my - (c[1] - cy) * scale),
            ],
            fill=col,
        )
    img = img.resize(SIZE, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()
