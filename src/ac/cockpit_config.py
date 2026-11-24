"""Configuración de cabina del ensamblado: ``DRIVEREYES``, CSP, ``driver3d.ini`` y
ediciones de ``car.ini``.

Integra en el ensamblador lo validado en el juego con las variantes v7–v12
(docs/research/informe_v12_estado_y_hoja_de_ruta_2026-10-24.md):

* ``[GRAPHICS] DRIVEREYES`` (v7): la cámara de cabina de BeamNG
  (``camerasInternal`` con ``cockpitCamera: true``) convertida a ejes AC y al marco
  del KN5. ``DRIVEREYES`` va en el marco del KN5 (suelo = 0)
  (informe_prueba_manual_2026-10-24.md §B1)::

      ojos_AC = beamng_to_ac(cámara) + desfase_de_origen_AC

  El desfase es el mismo que se aplica al plan de mallas (``kunos_origin_offset``).
* ``extension/ext_config.ini`` (v8ic): ``CULL_MODE = NONE`` para los materiales del
  KN5 que BeamNG dibuja a doble cara (``doubleSided: true`` en ``*.materials.json``).
  Sin CSP el archivo se ignora (interior «vacío», límite conocido).
* ``data/driver3d.ini`` (v8dp): modelo de piloto de AC por nombre (no se copia) y
  ``HIDE_OBJECT`` de casco y cara, como en los ``driver3d.ini`` de Kunos.
* Opciones EXPERIMENTALES de ``[CONTROLS]`` (v8ff y v10), solo bajo petición
  explícita: no derivan de BeamNG y no se aplican por defecto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.ac.ini_format import sanitize_ini_comments
from src.coords import beamng_to_ac
from src.jbeam import ResolvedConfig, evaluate, parse_table

DUP_SUFFIX = re.compile(r"\.\d{3}$")  # duplicados de Blender: "nombre.001"


class CockpitConfigError(ValueError):
    """Falta un dato o el archivo no admite la edición pedida."""


# --------------------------------------------------------------------------- ojos
@dataclass(frozen=True)
class DriverEyes:
    camera_beamng: tuple[float, float, float]
    offset_ac: tuple[float, float, float]
    eyes_ac: tuple[float, float, float]
    camera_name: str


def cockpit_camera(resolved: ResolvedConfig) -> tuple[str, tuple[float, float, float]]:
    """Cámara interior con ``cockpitCamera: true`` (una sola en la configuración)."""
    variables = resolved.variable_values()
    found = []
    for _, section in resolved.section_contributions("camerasInternal"):
        for row in parse_table(section).rows:
            if row.properties.get("cockpitCamera") is True:
                pos = tuple(evaluate(row.get(c), variables) for c in ("x", "y", "z"))
                if not all(isinstance(v, (int, float)) for v in pos):
                    raise CockpitConfigError(f"cámara no numérica: {pos!r}")
                found.append((str(row.get("type")), pos))
    if len(found) != 1:
        raise CockpitConfigError(
            f"se esperaba una cámara con cockpitCamera=true, hay {len(found)}"
        )
    return found[0]  # type: ignore[return-value]


def driver_eyes(
    resolved: ResolvedConfig, offset_ac: tuple[float, float, float]
) -> DriverEyes:
    name, cam = cockpit_camera(resolved)
    ac = beamng_to_ac(cam)
    eyes = tuple(a + o for a, o in zip(ac, offset_ac))
    return DriverEyes(cam, tuple(offset_ac), eyes, name)  # type: ignore[arg-type]


def drivereyes_line(e: DriverEyes) -> str:
    x, y, z = e.eyes_ac
    cx, cy, cz = e.camera_beamng
    ox, oy, oz = (0.0 if abs(v) < 5e-5 else v for v in e.offset_ac)
    return (
        f"DRIVEREYES={x:.3f}, {y:.3f}, {z:.3f}\t\t; camara de cabina {e.camera_name} "
        f"de BeamNG {cx:g}, {cy:g}, {cz:g} convertida a ejes AC y al marco del KN5 "
        f"con el desfase de origen {ox:.4g}, {oy:.4f}, {oz:.4f}"
    )


# --------------------------------------------------------------------- car.ini
def _lines(text: str) -> list[str]:
    return text.split("\n")


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    heads = [i for i, ln in enumerate(lines) if ln.strip().upper() == f"[{section}]"]
    if not heads:
        return None
    if len(heads) > 1:
        raise CockpitConfigError(f"sección {section} repetida")
    start = heads[0]
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")
        ),
        len(lines),
    )
    return start, end


def _key(line: str) -> str:
    return line.partition(";")[0].split("=")[0].strip().upper()


def _vector(text: str, key: str = "DRIVEREYES") -> tuple[float, ...]:
    try:
        return tuple(float(v) for v in text.split(","))
    except ValueError:
        raise CockpitConfigError(f"{key} no numérico: {text!r}") from None


def add_graphics_line(
    car_ini: str, key: str, want: tuple[float, ...], line: str
) -> str:
    """Escribe ``[GRAPHICS] <key>`` (la línea completa ``line``) sin tocar nada más.

    * Sin ``[GRAPHICS]``: añade la sección al final con esa única clave.
    * Con ``[GRAPHICS]`` y sin la clave: añade la línea al final de la sección; el
      resto de claves y su orden no cambian.
    * Con la clave y el mismo valor (``want``, a 3 decimales como se escribe): no
      cambia nada, así que una segunda ejecución da el mismo texto.
    * Con la clave distinta o repetida, varias ``[GRAPHICS]`` o una cabecera con
      comentario (``[GRAPHICS] ; …``, que AC podría no leer igual): error explícito.

    Conserva los saltos de línea del texto (``\\r\\n`` o ``\\n``).
    """
    crlf = "\r\n" in car_ini
    text = car_ini.replace("\r\n", "\n") if crlf else car_ini
    lines = _lines(text)
    odd = [
        ln.strip()
        for ln in lines
        if ln.partition(";")[0].strip().upper() == "[GRAPHICS]"
        and ln.strip().upper() != "[GRAPHICS]"
    ]
    if odd:
        raise CockpitConfigError(
            f"cabecera GRAPHICS con formato no soportado: {odd!r}; "
            "debe estar sola en su línea"
        )
    bounds = _section_bounds(lines, "GRAPHICS")  # error si está repetida
    if bounds is None:
        add = "\n[GRAPHICS]\n" + line + "\n"
        out = sanitize_ini_comments(text.rstrip("\n") + "\n" + add)
    else:
        start, end = bounds
        hits = [i for i in range(start + 1, end) if _key(lines[i]) == key]
        if len(hits) > 1:
            raise CockpitConfigError(f"GRAPHICS tiene {len(hits)} claves {key}")
        if hits:
            old = lines[hits[0]].partition(";")[0].split("=", 1)[1].strip()
            got = _vector(old, key)
            if len(got) == len(want) and all(
                abs(a - b) <= 5e-4 for a, b in zip(got, want)
            ):
                return car_ini  # ya está: no se toca ni el comentario
            raise CockpitConfigError(
                f"GRAPHICS ya tiene {key}={old} y el ensamblador calcula "
                f"{', '.join(f'{c:.3f}' for c in want)}; no se sobrescribe"
            )
        at = end
        while at - 1 > start and not lines[at - 1].strip():
            at -= 1  # tras la última línea con contenido de la sección
        lines.insert(at, line)
        out = sanitize_ini_comments("\n".join(lines))
    return out.replace("\n", "\r\n") if crlf else out


def add_basic_line(car_ini: str, key: str, line: str) -> str:
    """Añade ``[BASIC] <key>`` (línea completa ``line``) al final de ``[BASIC]``.

    G2.24 (``GRAPHICS_OFFSET`` derivado en la exportación). Error si ``[BASIC]`` falta o
    está repetida, o si la clave ya existe: este paso nunca sobrescribe un atributo."""
    crlf = "\r\n" in car_ini
    text = car_ini.replace("\r\n", "\n") if crlf else car_ini
    lines = _lines(text)
    bounds = _section_bounds(lines, "BASIC")
    if bounds is None:
        raise CockpitConfigError("car.ini sin sección [BASIC]")
    start, end = bounds
    if any(_key(lines[i]) == key for i in range(start + 1, end)):
        raise CockpitConfigError(f"[BASIC] ya tiene {key}; no se sobrescribe")
    at = end
    while at - 1 > start and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, line)
    out = sanitize_ini_comments("\n".join(lines))
    return out.replace("\n", "\r\n") if crlf else out


def add_drivereyes(car_ini: str, eyes: DriverEyes) -> str:
    """``[GRAPHICS] DRIVEREYES`` desde la cámara de BeamNG (ver
    :func:`add_graphics_line`: idempotente y sin sobrescribir otro valor)."""
    want = tuple(round(c, 3) for c in eyes.eyes_ac)
    return add_graphics_line(car_ini, "DRIVEREYES", want, drivereyes_line(eyes))


# Opciones experimentales: valor fijo y procedencia documentada (no derivan de BeamNG).
EXPERIMENTS: dict[str, dict[str, str]] = {
    "ffmult_1.95": {
        "key": "FFMULT",
        "value": "1.95",
        "comment": "DIAG test8ff EXPERIMENTAL: mediana de 114 coches instalados con "
        "car.ini suelto, rango 1.3-4.5; NO deriva de BeamNG",
        "evidence": "informe_prueba_v7_2026-10-24.md: FFB correcto con el G29",
    },
    "steer_lock_423": {
        "key": "STEER_LOCK",
        "value": "423",
        "comment": "DIAG test10sl EXPERIMENTAL: limite en el tope SUPPORT fh3-fx1 de "
        "BeamNG, cremallera 0,110 m, rueda interior 44 grados; antes {old}; no es "
        "una solucion confirmada",
        "evidence": "informe_direccion_piloto_integracion_2026-10-24.md; prueba con "
        "el G29 pendiente",
    },
}


def apply_experiment(car_ini: str, name: str) -> str:
    """Aplica una opción experimental de ``[CONTROLS]``.

    * ``ffmult_1.95``: inserta ``FFMULT`` justo tras la cabecera (no debe existir).
    * ``steer_lock_423``: sustituye la línea ``STEER_LOCK`` existente (debe existir:
      sin la dirección de la base física no tiene sentido).
    """
    if name not in EXPERIMENTS:
        raise CockpitConfigError(
            f"opción experimental desconocida: {name} (hay {sorted(EXPERIMENTS)})"
        )
    exp = EXPERIMENTS[name]
    lines = _lines(car_ini)
    bounds = _section_bounds(lines, "CONTROLS")
    if bounds is None:
        raise CockpitConfigError(
            f"{name}: car.ini sin CONTROLS (requiere una base física con dirección)"
        )
    start, end = bounds
    hits = [i for i in range(start + 1, end) if _key(lines[i]) == exp["key"]]
    if exp["key"] == "FFMULT":
        if hits:
            raise CockpitConfigError("CONTROLS ya tiene FFMULT")
        lines.insert(start + 1, f"FFMULT={exp['value']}\t\t; {exp['comment']}")
    else:
        if len(hits) != 1:
            raise CockpitConfigError(f"se esperaba un STEER_LOCK, hay {len(hits)}")
        old = lines[hits[0]].partition(";")[0].split("=", 1)[1].strip()
        comment = exp["comment"].format(old=old)
        lines[hits[0]] = f"STEER_LOCK={exp['value']}\t\t; {comment}"
    return sanitize_ini_comments("\n".join(lines))


# ------------------------------------------------------------------- CSP interior
def double_sided_materials(
    kn5_material_names: list[str],
    materials_json: Path,
    dae_materials: Iterable[str] | None = None,
) -> list[str]:
    """Nombres del KN5 cuyo material de BeamNG tiene ``doubleSided: true``.

    Los duplicados de Blender (``.001``) se resuelven a su material de origen.
    """
    from src.ac.kn5_materials import load_materials, material_key

    bm = load_materials(materials_json)  # G2.24: también materials.cs
    return sorted(
        {
            n
            for n in kn5_material_names
            if bm.get(material_key(n, bm, dae_materials) or "", {}).get("doubleSided")
            is True
        }
    )


def render_ext_config(materials: list[str], materials_json_name: str) -> str:
    if not materials:
        raise CockpitConfigError("ningún material a doble cara")
    bases = {DUP_SUFFIX.sub("", n) for n in materials}
    if any("," in n for n in materials):
        raise CockpitConfigError("un nombre de material lleva coma")
    text = (
        "; beam2ac: sin recorte de caras (CSP) para los materiales que BeamNG dibuja a "
        "doble cara\n"
        f"; doubleSided true en {materials_json_name}: {len(bases)} materiales, "
        f"{len(materials)} nombres en el KN5 (duplicados .001 incluidos).\n"
        "; Solo afecta a estos nombres exactos del KN5 del coche; geometria y "
        "materiales del KN5 sin cambios. Sin CSP se ignora.\n"
        "[SHADER_REPLACEMENT_0_BEAM2AC_DOUBLESIDED]\n"
        "MATERIALS = " + ", ".join(materials) + "\nCULL_MODE = NONE\n"
    )
    return sanitize_ini_comments(text)


# --------------------------------------------------------------------- piloto
# Modelo de piloto por defecto de AC (content/driver/driver.kn5), referenciado por
# nombre; nunca se copia. HIDE_OBJECT: casco y cara, como en los driver3d.ini de
# Kunos (nombres comprobados en driver.kn5, informe_prueba_v7 §1).
DRIVER_MODEL = "driver"
HIDE_OBJECTS = ("DRIVER:HELMET", "DRIVER:GEO_Driver_FACE")


def render_driver3d_ini(
    model: str = DRIVER_MODEL, hide: tuple[str, ...] = HIDE_OBJECTS
) -> str:
    """``data/driver3d.ini``: sin él, CSP falla con ``driverModel_HR`` nulo
    (ensamblado.md §9.8). ``POSITION=0,0,0`` como en los 4 oficiales inspeccionados;
    la posición del piloto la da ``driver_base_pos.knh``.
    """
    out = [
        "; beam2ac: modelo de piloto de AC referenciado por nombre (no se copia)",
        "[MODEL]",
        f"NAME={model}",
        "POSITION=0,0,0",
    ]
    if hide:
        out += [
            "",
            "; ocultar casco y cara desde la cabina, como los driver3d.ini de Kunos",
        ]
        for i, name in enumerate(hide):
            if i:
                out.append("")
            out += [f"[HIDE_OBJECT_{i}]", f"NAME={name}"]
    return sanitize_ini_comments("\n".join(out) + "\n")


def describe(eyes: DriverEyes | None, experiments: list[str]) -> dict[str, Any]:
    return {
        "drivereyes": (
            None
            if eyes is None
            else {
                "camera": eyes.camera_name,
                "camera_beamng": eyes.camera_beamng,
                "offset_ac": eyes.offset_ac,
                "eyes_ac": eyes.eyes_ac,
            }
        ),
        "experimental": [
            {"name": n, **{k: v for k, v in EXPERIMENTS[n].items() if k != "comment"}}
            for n in experiments
        ],
    }
