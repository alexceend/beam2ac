"""Imagen de portada de un coche (``skins/<skin>/preview.jpg``): render con texturas desde arriba en diagonal.

KN5 → OBJ con ``tools/kn5-obj-converter-main`` (texturas incluidas) → Blender (Workbench, color = textura), cámara
delante-izquierda y arriba sobre fondo oscuro, 1024×576 como los coches de Kunos. Solo se usa si el usuario no ha
subido su propia imagen (``details.py``). Es una vista previa, no un render fotorrealista.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONVERTER = ROOT / "tools" / "kn5-obj-converter-main" / "convert.py"
SIZE = (1024, 576)

BLENDER_SCRIPT = r"""
import bpy, sys
from mathutils import Vector
obj_path, out = sys.argv[sys.argv.index("--") + 1:][-2:]
W, H = 1024, 576
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=obj_path)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
ctr = (lo + hi) / 2
size = (hi - lo).length
# El importador de OBJ enlaza las texturas de forma poco fiable con tantos materiales: se rehacen a mano desde el
# .mtl (map_Kd) con un solo nodo de imagen activo, que es el que usa Workbench con color = textura.
import os
mtl = os.path.splitext(obj_path)[0] + ".mtl"
kd, cur = {}, None
for line in open(mtl, encoding="utf-8", errors="ignore"):
    w = line.strip().split(None, 1)
    if len(w) == 2 and w[0] == "newmtl":
        cur = w[1]
    elif len(w) == 2 and w[0] == "map_Kd" and cur:
        kd[cur] = os.path.join(os.path.dirname(obj_path), w[1].replace("\\", os.sep))
for mat in bpy.data.materials:
    path = kd.get(mat.name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    if path and os.path.isfile(path):
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(path, check_existing=False)
        mat.node_tree.nodes.active = tex
    else:
        mat.diffuse_color = (0.35, 0.35, 0.35, 1.0)
scn = bpy.context.scene
scn.render.engine = "BLENDER_WORKBENCH"
sh = scn.display.shading
sh.light = "STUDIO"; sh.color_type = "TEXTURE"; sh.show_shadows = True; sh.shadow_intensity = 0.4
sh.show_specular_highlight = True
scn.view_settings.view_transform = "Standard"
scn.render.resolution_x, scn.render.resolution_y = W, H
scn.render.image_settings.file_format = "JPEG"
scn.render.image_settings.quality = 90
scn.world = bpy.data.worlds.new("w"); scn.world.color = (0.012, 0.012, 0.016)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); scn.collection.objects.link(cam); scn.camera = cam
cam.data.lens = 45
# KN5 (x izquierda, y arriba, z adelante) -> Blender (x, -z, y): el frente es -Y; camara delante-izquierda y arriba
cam.location = ctr + Vector((0.85, -1.0, 0.55)).normalized() * size * 1.75
t = bpy.data.objects.new("t", None); t.location = ctr + Vector((0, 0, -0.03 * size)); scn.collection.objects.link(t)
c = cam.constraints.new("TRACK_TO"); c.target = t; c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"
scn.render.filepath = out
bpy.ops.render.render(write_still=True)
"""


class CoverError(RuntimeError):
    pass


_RENDER_LOCK = (
    threading.Lock()
)  # un solo Blender a la vez (la interfaz y la exportación pueden pedir a la vez)
_CACHE: dict[tuple, bytes] = (
    {}
)  # (ruta, mtime, tamaño) del KN5 → JPEG; el mismo modelo no se renderiza dos veces


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


WRAPPER = r"""
import runpy, sys
a = sys.argv[sys.argv.index("--") + 1:]
converter, kn5dir, script = a[0], a[1], a[2]
orig = sys.argv[:]
sys.argv = [converter, kn5dir]
runpy.run_path(converter, run_name="__main__")  # KN5 -> OBJ (necesita numpy: el Python de Blender lo trae)
sys.argv = orig
runpy.run_path(script, run_name="__main__")  # lee obj y png de sys.argv tras «--»
"""


def render_cover(kn5: Path, blender: str, timeout: int = 300) -> bytes:
    """JPEG de portada renderizado desde ``kn5``. Todo corre dentro de Blender: su Python trae numpy, que el
    conversor KN5 → OBJ necesita y que el Python de la aplicación puede no tener. Una petición a la vez; las que
    esperan reciben el resultado de la primera si el KN5 es el mismo."""
    st = Path(kn5).stat()
    key = (str(Path(kn5).resolve()), st.st_mtime_ns, st.st_size)
    with _RENDER_LOCK:
        if key not in _CACHE:
            if len(_CACHE) >= 8:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = _render(Path(kn5), blender, timeout)
        return _CACHE[key]


def _render(kn5: Path, blender: str, timeout: int) -> bytes:
    if not CONVERTER.is_file():
        raise CoverError(f"falta {CONVERTER}")
    with tempfile.TemporaryDirectory(prefix="beam2ac_cover_") as tmp:
        t = Path(tmp)
        (t / "kn5").mkdir()
        shutil.copyfile(kn5, t / "kn5" / "car.kn5")
        (t / "render.py").write_text(BLENDER_SCRIPT, encoding="utf-8")
        (t / "wrapper.py").write_text(WRAPPER, encoding="utf-8")
        obj, png = t / "kn5" / "output" / "car.obj", t / "cover.jpg"
        try:
            r = subprocess.run(
                [
                    blender,
                    "--background",
                    "--python",
                    str(t / "wrapper.py"),
                    "--",
                    str(CONVERTER),
                    str(t / "kn5"),
                    str(t / "render.py"),
                    str(obj),
                    str(png),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise CoverError(f"Blender tardó más de {timeout} s") from None
        if not png.is_file():
            raise CoverError(
                f"Blender no generó la imagen: {(r.stderr or r.stdout)[-400:]}"
            )
        return (
            png.read_bytes()
        )  # Blender ya lo escribe en JPEG 1024×576: no hace falta Pillow
