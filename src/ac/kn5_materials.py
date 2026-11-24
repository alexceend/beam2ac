"""Texturas difusas para un KN5 ya exportado, a partir de los materiales de BeamNG.

El KN5 que exporta el addon de Blender no lleva texturas: sus materiales ``ksPerPixel``
no tienen ranura ``txDiffuse`` y AC los pinta negros
(docs/research/informe_prueba_manual_2026-10-24.md).
Este módulo reescribe SOLO los bloques de texturas y materiales; los bytes de los nodos
(geometría, UV, jerarquía) se copian sin tocar.

Formato del KN5 (v5/v6, el mismo que leen :mod:`src.ac.kn5_inspect` y
``tools/kn5-obj-converter-main/convert.py``)::

    "sc6969" + uint32 versión (+ int32 extra si versión > 5)
    int32 nº texturas; por textura: int32 activa, cadena nombre, int32 tamaño, bytes
    int32 nº materiales; por material:
        cadena nombre, cadena shader, byte alphaBlendMode, byte alphaTested,
        int32 depthMode (versión > 4),
        int32 nº propiedades; por propiedad: cadena nombre, 10 float (valor, v2, v3, v4)
        int32 nº ranuras; por ranura: cadena sampler, int32 slot, cadena textura
    nodos (árbol)

Correspondencia BeamNG → AC (solo color difuso; sin PBR, mapas normales ni opacidad):
    1. ``diffuseMap`` del primer stage → se embebe el DDS original tal cual; una textura
       neutra de ``vehicles/common`` (:data:`NEUTRAL_COMMON_TEXTURES`, ``null.dds``
       blanco) cuenta como sin textura (paso 3: ``diffuseColor``).
    2. ``instanceDiffuse`` (pintura) → color sólido = pintura × ``diffuseColor``.
       Sin pintura resuelta (``paint_rgb=None``) el material se deja SIN textura
       (``kind="pintura_sin_resolver"``): no se inventa un color.
    3. resto → color sólido = ``diffuseColor``.
    Componente de 0..1 → byte ``round(c·255)``, sin conversión de gamma (no verificada).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src.jbeam import lax_json
from src.safe_paths import ensure_safe_output_path

KN5_MAGIC = b"sc6969"
DUP_SUFFIX = re.compile(r"\.\d{3}$")  # duplicados de Blender: "nombre.001"


class Kn5FormatError(ValueError):
    """KN5 no válido o no soportado."""


@dataclass
class Kn5Texture:
    name: str
    data: bytes
    active: int = 1


@dataclass
class Kn5Material:
    name: str
    shader: str
    blend: int
    alpha_tested: int
    depth: int | None
    props: list[tuple[str, tuple[float, ...]]] = field(default_factory=list)
    slots: list[tuple[str, int, str]] = field(default_factory=list)


@dataclass
class Kn5Parts:
    version: int
    extra: bytes  # int32 adicional de la versión > 5 (vacío si no hay)
    textures: list[Kn5Texture]
    materials: list[Kn5Material]
    nodes: bytes  # árbol de nodos, sin interpretar


class _Cursor:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def raw(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise Kn5FormatError("fin de archivo inesperado")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def i32(self) -> int:
        return struct.unpack("<i", self.raw(4))[0]

    def string(self) -> str:
        return self.raw(self.i32()).decode("utf-8")


def _s(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<i", len(data)) + data


def split_kn5(data: bytes) -> Kn5Parts:
    c = _Cursor(data)
    if c.raw(6) != KN5_MAGIC:
        raise Kn5FormatError("cabecera inválida")
    version = struct.unpack("<I", c.raw(4))[0]
    extra = c.raw(4) if version > 5 else b""
    textures = []
    for _ in range(c.i32()):
        active = c.i32()
        name = c.string()
        textures.append(Kn5Texture(name, c.raw(c.i32()), active))
    materials = []
    for _ in range(c.i32()):
        name, shader = c.string(), c.string()
        blend, alpha = c.raw(2)
        depth = c.i32() if version > 4 else None
        props = []
        for _ in range(c.i32()):
            pname = c.string()
            props.append((pname, struct.unpack("<10f", c.raw(40))))
        slots = []
        for _ in range(c.i32()):
            sampler = c.string()
            slot = c.i32()
            slots.append((sampler, slot, c.string()))
        materials.append(Kn5Material(name, shader, blend, alpha, depth, props, slots))
    return Kn5Parts(version, extra, textures, materials, data[c.pos :])


def build_kn5(parts: Kn5Parts) -> bytes:
    out = KN5_MAGIC + struct.pack("<I", parts.version) + parts.extra
    out += struct.pack("<i", len(parts.textures))
    for t in parts.textures:
        out += (
            struct.pack("<i", t.active)
            + _s(t.name)
            + struct.pack("<i", len(t.data))
            + t.data
        )
    out += struct.pack("<i", len(parts.materials))
    for m in parts.materials:
        out += (
            _s(m.name) + _s(m.shader) + bytes([m.blend & 0xFF, m.alpha_tested & 0xFF])
        )
        if parts.version > 4:
            out += struct.pack("<i", m.depth or 0)
        out += struct.pack("<i", len(m.props))
        for pname, vals in m.props:
            out += _s(pname) + struct.pack("<10f", *vals)
        out += struct.pack("<i", len(m.slots))
        for sampler, slot, tex in m.slots:
            out += _s(sampler) + struct.pack("<i", slot) + _s(tex)
    return out + parts.nodes


def solid_dds(rgb: tuple[int, int, int], size: int = 4) -> bytes:
    """DDS sin comprimir A8R8G8B8 (sin mipmaps) de un color sólido opaco."""
    if not all(0 <= c <= 255 for c in rgb):
        raise ValueError(f"color fuera de rango {rgb}")
    flags = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000  # CAPS|HEIGHT|WIDTH|PITCH|PIXELFORMAT
    header = struct.pack("<7I", 124, flags, size, size, size * 4, 0, 0) + b"\0" * 44
    pixfmt = struct.pack(
        "<8I", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000
    )  # DDPF_RGB | DDPF_ALPHAPIXELS
    caps = struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    pixel = bytes([rgb[2], rgb[1], rgb[0], 255])  # BGRA
    return b"DDS " + header + pixfmt + caps + pixel * (size * size)


def to_byte(c: float) -> int:
    return max(0, min(255, round(float(c) * 255)))


@dataclass
class DiffuseSource:
    material: str  # nombre en el KN5
    beamng_material: str
    kind: str  # "diffuseMap" | "pintura" | "diffuseColor" | "pintura_sin_resolver"
    texture_name: str | None  # None = el material se deja como estaba
    data: bytes
    note: str = ""


# G2.24: texturas de ``vehicles/common`` (common.zip de BeamNG, no incluidas en la
# exportación) que son NEUTRAS para el color difuso: el color resultante es
# ``diffuseColor``. Lista CERRADA con el contenido comprobado (solo lectura) en
# <BeamNG.drive>/content/vehicles/common.zip:
# null.dds = 32×32 DXT5, todos los bloques ``ffff 000000000000`` (alfa 255) y
# ``ffff 0000 00000000`` (color 0xFFFF = blanco): blanco opaco ⇒ blanco × diffuseColor.
NEUTRAL_COMMON_TEXTURES: dict[str, str] = {
    "vehicles/common/null.dds": "blanco opaco 32×32 DXT5 (common.zip, G2.24)",
}


def load_materials(path: Path) -> dict:
    """Materiales de BeamNG con la forma de ``<config>.materials.json``: el JSON tal
    cual o ``materials.cs`` del exportador 200409 (:mod:`src.extract.materials_cs`)."""
    if Path(path).suffix.lower() == ".cs":
        from src.extract.materials_cs import load

        return load(path).materials
    return lax_json.load(path).value


def material_key(
    name: str, bm: dict, dae_materials: Iterable[str] | None = None
) -> str | None:
    """Material de BeamNG de un nombre del KN5: sin el sufijo de duplicado de Blender
    (``.001``) y, si no existe tal cual, el ÚNICO nombre de BeamNG que Blender recorta a
    ese nombre (63 bytes, ``src.mesh.plan.blender_name``; nombres largos del 200409).

    El recorte puede juntar un material con sus variantes de luz (``…_on``, ``…_off``):
    ``dae_materials`` (``src.mesh.dae.material_names``) limita los candidatos a los que
    usa el DAE, que es de donde Blender toma el nombre. Ambiguo ⇒ ``None`` (error)."""
    from src.mesh.plan import blender_name

    base = DUP_SUFFIX.sub("", name)
    if base in bm:
        return base
    allowed = set(dae_materials) if dae_materials is not None else None
    hits = [
        k for k in bm if blender_name(k) == base and (allowed is None or k in allowed)
    ]
    return hits[0] if len(hits) == 1 else None


def upper_layer_paint(
    kn5_material_names: list[str],
    materials: Path,
    dae_materials: Iterable[str] | None = None,
) -> list[str]:
    """Materiales del KN5 con ``instanceDiffuse`` en una capa distinta de la 0. Su
    mezcla de capas no está verificada: :func:`diffuse_sources` usa la capa 0 (decisión
    del usuario en G2.24) y quien llama debe avisar."""
    bm = load_materials(materials)
    out = []
    for name in kn5_material_names:
        key = material_key(name, bm, dae_materials)
        stages = ((bm.get(key) or {}).get("Stages") or [{}]) if key else [{}]
        if any(isinstance(s, dict) and s.get("instanceDiffuse") for s in stages[1:]):
            out.append(name)
    return out


def diffuse_sources(
    kn5_material_names: list[str],
    materials_json: Path,
    vehicle_dir: Path,
    paint_rgb: tuple[float, float, float] | None,
    dae_materials: Iterable[str] | None = None,
) -> list[DiffuseSource]:
    """Fuente de color difuso de cada material del KN5.

    Falla si algún material no tiene equivalente en BeamNG. ``paint_rgb`` solo se usa
    en los materiales ``instanceDiffuse``; con ``None`` esos quedan sin textura.
    ``materials_json`` puede ser también ``materials.cs`` (:func:`load_materials`).
    """
    bm = load_materials(materials_json)
    out = []
    for name in kn5_material_names:
        base = material_key(name, bm, dae_materials)
        if base is None:
            raise Kn5FormatError(f"material sin equivalente en BeamNG: {name}")
        stages = bm[base].get("Stages") or [{}]
        st = stages[0]
        colour = list(st.get("diffuseColor", [1, 1, 1, 1]))[:3]
        if str(st.get("diffuseMap", "")) in NEUTRAL_COMMON_TEXTURES:
            st = {k: v for k, v in st.items() if k != "diffuseMap"}
        if "diffuseMap" in st:
            fname = str(st["diffuseMap"]).split("/")[-1]
            path = vehicle_dir / fname
            if not path.is_file():
                # textura virtual de BeamNG (``<base>_lerpto_0xAARRGGBB_<%>.dds``: la genera el
                # juego): un material sin ese fichero no debe tirar TODAS las texturas. Cualquier
                # otro fichero ausente sigue siendo error (no se degrada en silencio).
                m = re.search(r"_lerpto_0x([0-9a-fA-F]{8})_(\d+)\.dds$", fname)
                if not m:
                    raise Kn5FormatError(f"{name}: falta {path}")
                if int(m[2]) == 100:  # 100 %: el color destino, sin depender de la base
                    argb = int(m[1], 16)
                    rgb = ((argb >> 16) & 255, (argb >> 8) & 255, argb & 255)
                    why = f"textura virtual {fname} (lerp 100 %): color plano del destino"
                else:  # la base no se conoce: el color del material
                    rgb = tuple(to_byte(c) for c in colour)
                    why = f"textura virtual {fname} (lerp parcial): color plano del material"
                out.append(
                    DiffuseSource(
                        name,
                        base,
                        "diffuseColor",
                        "beam2ac_col_%02x%02x%02x.dds" % rgb,
                        solid_dds(rgb),  # type: ignore[arg-type]
                        why,
                    )
                )
                continue
            data = path.read_bytes()
            if data[:4] != b"DDS ":
                raise Kn5FormatError(f"{name}: {fname} no es DDS")
            note = (
                ""
                if all(abs(c - 1) < 1e-6 for c in colour)
                else (f"diffuseColor {colour} no aplicado sobre la textura")
            )
            out.append(DiffuseSource(name, base, "diffuseMap", fname, data, note))
            continue
        if st.get("instanceDiffuse"):
            if paint_rgb is None:
                out.append(
                    DiffuseSource(
                        name,
                        base,
                        "pintura_sin_resolver",
                        None,
                        b"",
                        "instanceDiffuse sin pintura resuelta: sin textura",
                    )
                )
                continue
            rgb = tuple(to_byte(p * c) for p, c in zip(paint_rgb, colour))
            kind = "pintura"
        else:
            rgb = tuple(to_byte(c) for c in colour)
            kind = "diffuseColor"
        tex = "beam2ac_col_%02x%02x%02x.dds" % rgb
        dds = solid_dds(rgb)  # type: ignore[arg-type]
        out.append(DiffuseSource(name, base, kind, tex, dds))
    return out


def paint_dependent_materials(
    kn5_material_names: list[str],
    materials_json: Path,
    dae_materials: Iterable[str] | None = None,
) -> list[str]:
    """Materiales del KN5 cuyo color depende de la pintura (``instanceDiffuse`` en el
    primer stage, el mismo criterio que :func:`diffuse_sources`)."""
    bm = load_materials(materials_json)
    out = []
    for name in kn5_material_names:
        key = material_key(name, bm, dae_materials)
        m = bm.get(key) if key else None
        stages = (m or {}).get("Stages") or [{}]
        if isinstance(stages[0], dict) and stages[0].get("instanceDiffuse"):
            out.append(name)
    return out


def apply_diffuse(parts: Kn5Parts, sources: list[DiffuseSource]) -> Kn5Parts:
    """Embebe cada textura una vez y liga ``txDiffuse`` (slot 0) en cada material."""
    by_mat = {s.material: s for s in sources}
    if set(by_mat) != {m.name for m in parts.materials}:
        raise Kn5FormatError("las fuentes no cubren exactamente los materiales del KN5")
    textures = list(parts.textures)
    known = {t.name: t.data for t in textures}
    materials = []
    for m in parts.materials:
        s = by_mat[m.name]
        if s.texture_name is None:  # sin fuente decidida: material intacto
            materials.append(m)
            continue
        if s.texture_name in known and known[s.texture_name] != s.data:
            raise Kn5FormatError(
                f"dos texturas distintas con el nombre {s.texture_name}"
            )
        if s.texture_name not in known:
            textures.append(Kn5Texture(s.texture_name, s.data))
            known[s.texture_name] = s.data
        slots = [x for x in m.slots if x[0] != "txDiffuse"] + [
            ("txDiffuse", 0, s.texture_name)
        ]
        materials.append(
            Kn5Material(
                m.name, m.shader, m.blend, m.alpha_tested, m.depth, list(m.props), slots
            )
        )
    return Kn5Parts(parts.version, parts.extra, textures, materials, parts.nodes)


def write_with_diffuse(src: Path, dst: Path, sources: list[DiffuseSource]) -> Path:
    parts = split_kn5(Path(src).read_bytes())
    target = ensure_safe_output_path(dst)
    target.write_bytes(build_kn5(apply_diffuse(parts, sources)))
    return target
