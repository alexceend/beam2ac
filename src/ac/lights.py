"""Luces del coche a partir del ``glowMap`` de BeamNG (EXPERIMENTAL; el ensamblador las
aplica con :func:`apply_lights` si el perfil del vehículo tiene ``lights``).

Fuente (JBeam, sección ``glowMap``): cada material de luz tiene ``simpleFunction``
con la señal eléctrica que lo enciende (``lowbeam``/``highbeam``, ``lowhighbeam``,
``brake``, ``reverse``, ``signal_L``, ``signal_R``) y variantes ``_on``/``_off`` en
``*.materials.json`` (el color de ``emissiveFactor`` de ``_on`` da el tono).

Destino en Assetto Corsa:

* ``data/lights.ini`` (AC original): ``[LIGHT_n]`` se enciende con los faros;
  ``[BRAKE_n]`` con el freno, y ``OFF_COLOR`` es su brillo con los faros encendidos sin
  frenar (luz de posición trasera). ``NAME`` = nombre de malla o nulo del KN5.
* ``extension/ext_config.ini`` (CSP): ``[EMISSIVE_REVERSE_n]``,
  ``[EMISSIVE_TURNSIGNAL_LEFT_n]`` / ``RIGHT_n`` (``NAME``, ``COLOR``, ``LAG``,
  ``LOCATION``). Sin CSP no hay intermitentes ni marcha atrás.

El KN5 exportado llama ``35b84_body0`` a 71 mallas (una por material), así que hay que
dar un nombre único a cada malla de luz: :func:`rename_light_meshes` solo cambia el
nombre del nodo (la geometría no se toca) y añade ``ksEmissive = 0`` a sus materiales,
como hacen los exportadores habituales.

Las intensidades (:data:`INTENSITY`) son heurísticas en la escala de los ``lights.ini``
de referencia (AE86, Celica, Abarth, S13); hay que ajustarlas en el juego.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from src.ac.ini_format import sanitize_ini_comments
from src.ac.kn5_materials import Kn5Material, build_kn5
from src.ac.kn5_tree import load, read_mesh, serialize_tree, walk
from src.jbeam import ResolvedConfig

# señal de BeamNG → función de AC
FUNCTIONS = {
    "lowbeam": "HEADLIGHT",
    "highbeam": "HEADLIGHT",
    "lowhighbeam": "TAIL",
    "running": "TAIL",  # luz de posición/diurna/matrícula: encendida con los faros
    "brake": "BRAKE",
    "reverse": "REVERSE",
    "signal_L": "SIGNAL_L",
    "signal_R": "SIGNAL_R",
}
# multiplicador de emisión por función y pieza (vidrio, bombilla, catadióptrico)
INTENSITY = {
    ("HEADLIGHT", "glass"): 20.0,
    ("HEADLIGHT", "bulb"): 60.0,
    ("TAIL", "bulb"): 30.0,
    ("BRAKE", "glass"): 60.0,
    ("BRAKE", "bulb"): 80.0,
    ("REVERSE", "glass"): 25.0,
    ("SIGNAL_L", "glass"): 25.0,
    ("SIGNAL_L", "bulb"): 40.0,
    ("SIGNAL_L", "reflector"): 20.0,
}
INTENSITY.update(
    {("SIGNAL_R", k): v for (f, k), v in list(INTENSITY.items()) if f == "SIGNAL_L"}
)
TAIL_FROM_BRAKE_GLASS = 0.2  # OFF_COLOR del vidrio de freno = 20 % (luz de posición)
INTERIOR_DIM = 0.1  # piezas de luz dentro del habitáculo (testigos): 10 %


class LightsError(ValueError):
    """Datos de luces incompletos o ambiguos."""


@dataclass(frozen=True)
class LightMesh:
    material: str
    function: str
    kind: str  # glass | bulb | reflector
    colour: tuple[float, float, float]  # tono de emissiveFactor de _on (0..1)
    mesh_name: str
    centre_z: float  # para LOCATION y para separar el habitáculo
    interior: bool


def light_functions(
    resolved: ResolvedConfig,
    skipped: list[str] | None = None,
    overrides: dict[str, str | None] | None = None,
) -> dict[str, str]:
    """Material → función de AC, desde ``glowMap``. Se elige la señal de mayor peso.

    Una señal sin equivalente deja fuera ESE material (se anota en ``skipped``) en vez de
    descartar todas las luces. ``overrides`` = elección del usuario: ``{material: función}`` añade o
    sustituye la detección y ``None`` quita el material (no es una luz)."""
    out: dict[str, str] = {}
    for _, section in resolved.section_contributions("glowMap"):
        for material, spec in section.items():
            if not isinstance(spec, dict) or "simpleFunction" not in spec:
                continue
            signals = spec["simpleFunction"]
            unknown = [s for s in signals if s not in FUNCTIONS]
            if unknown:
                if skipped is None:
                    raise LightsError(f"{material}: señales sin equivalente {unknown}")
                skipped.append(f"{material}: señales sin equivalente {unknown}")
                continue
            funcs = {FUNCTIONS[s] for s in signals}
            if len(funcs) != 1:
                raise LightsError(f"{material}: varias funciones {sorted(funcs)}")
            func = funcs.pop()
            if material in out and out[material] != func:
                raise LightsError(f"{material}: glowMap contradictorio entre piezas")
            out[material] = func
    for material, func in (overrides or {}).items():
        if func is None:
            out.pop(material, None)
        elif func in LIGHT_FUNCTIONS:
            out[material] = func
        else:
            raise LightsError(f"{material}: función de luz desconocida {func!r}")
    return out


# funciones que el usuario puede asignar a mano a un material (ver ``light_functions``)
LIGHT_FUNCTIONS = ("HEADLIGHT", "TAIL", "BRAKE", "REVERSE", "SIGNAL_L", "SIGNAL_R")
# tono por defecto (0..1) de un material de luz sin variante ``_on`` (asignado a mano)
DEFAULT_COLOUR = {
    "HEADLIGHT": (1.0, 0.95, 0.85),
    "TAIL": (1.0, 0.1, 0.05),
    "BRAKE": (1.0, 0.05, 0.03),
    "REVERSE": (1.0, 1.0, 1.0),
    "SIGNAL_L": (1.0, 0.5, 0.0),
    "SIGNAL_R": (1.0, 0.5, 0.0),
}


def kind_of(material: str) -> str:
    m = material.lower()
    if "bulb" in m:
        return "bulb"
    if "reflector" in m:
        return "reflector"
    return "glass"


def on_colour(
    materials_json: dict, material: str, function: str | None = None
) -> tuple[float, float, float]:
    st = (materials_json.get(material + "_on", {}).get("Stages") or [{}])[0]
    emis = st.get("emissiveFactor")
    if not isinstance(emis, list) or len(emis) < 3:
        if function in DEFAULT_COLOUR and material + "_on" not in materials_json:
            return DEFAULT_COLOUR[
                function
            ]  # luz asignada a mano: sin variante _on que dé el tono
        raise LightsError(f"{material}_on sin emissiveFactor")
    peak = max(emis[:3]) or 1.0
    return tuple(float(c) / peak for c in emis[:3])  # type: ignore[return-value]


def rename_light_meshes(
    data: bytes,
    functions: dict[str, str],
    materials_json: dict,
    interior_z: tuple[float, float],
) -> tuple[bytes, list[LightMesh]]:
    """Da un nombre único (``B2AC_<FUNCIÓN>_<n.º de material>``) a cada malla de luz
    y añade ``ksEmissive`` a sus materiales. Si un material de luz está en varias mallas (p. ej. los
    intermitentes de los dos retrovisores) cada una recibe un sufijo ``_<k>``. Falla si un material de luz
    no tiene ninguna malla o si el nombre ya existe."""
    parts, root = load(data)
    mats = [m.name for m in parts.materials]
    existing = Counter(n.name for n, _ in walk(root))
    by_mat: dict[str, list] = {}
    for node, _ in walk(root):
        if node.type == 2:
            by_mat.setdefault(mats[read_mesh(node).material], []).append(node)
    lights: list[LightMesh] = []
    for material, function in sorted(functions.items()):
        nodes = by_mat.get(material, [])
        if not nodes:
            raise LightsError(f"{material}: ninguna malla usa este material")
        num = re.match(r"material_(\d+)_", material)
        stem = f"B2AC_{function}_{num.group(1) if num else len(lights)}"
        for k, node in enumerate(nodes):
            new = stem if len(nodes) == 1 else f"{stem}_{k}"
            if existing[new]:
                raise LightsError(f"el nombre {new} ya existe")
            pos = read_mesh(node).positions()
            zs = [p[2] for p in pos]
            node.name = new
            lights.append(
                LightMesh(
                    material,
                    function,
                    kind_of(material),
                    on_colour(materials_json, material, function),
                    new,
                    sum(zs) / len(zs),
                    interior_z[0] < min(zs) and max(zs) < interior_z[1],
                )
            )
    wanted = set(functions)
    new_mats = []
    for m in parts.materials:
        props = list(m.props)
        if m.name in wanted and not any(p == "ksEmissive" for p, _ in props):
            props.append(("ksEmissive", (0.0,) * 10))
        new_mats.append(
            Kn5Material(
                m.name, m.shader, m.blend, m.alpha_tested, m.depth, props, list(m.slots)
            )
        )
    parts.materials = new_mats
    parts.nodes = serialize_tree(root)
    return build_kn5(parts), lights


def _colour(light: LightMesh, scale: float = 1.0) -> str:
    k = INTENSITY.get(
        (light.function, light.kind), INTENSITY.get((light.function, "glass"), 20.0)
    )
    k *= scale * (INTERIOR_DIM if light.interior else 1.0)
    return ", ".join(f"{c * k:.3g}" for c in light.colour)


def render_lights_ini(lights: list[LightMesh]) -> str:
    out = [
        "; beam2ac (experimental): luces desde el glowMap de BeamNG;"
        " intensidades heuristicas",
        "[HEADER]",
        "VERSION=2",
    ]
    n_light = n_brake = 0
    for li in sorted(lights, key=lambda x: x.mesh_name):
        if li.function in ("HEADLIGHT", "TAIL"):
            out += [
                "",
                f"[LIGHT_{n_light}]",
                f"NAME={li.mesh_name}",
                f"COLOR={_colour(li)}",
            ]
            n_light += 1
        elif li.function == "BRAKE":
            off = (
                _colour(li, TAIL_FROM_BRAKE_GLASS) if li.kind == "glass" else "0, 0, 0"
            )
            out += [
                "",
                f"[BRAKE_{n_brake}]",
                f"NAME={li.mesh_name}",
                f"COLOR={_colour(li)}",
                f"OFF_COLOR={off}",
            ]
            n_brake += 1
    return sanitize_ini_comments("\n".join(out) + "\n")


def render_csp_emissive(lights: list[LightMesh], car_centre_z: float = 0.0) -> str:
    out = [
        "",
        "; beam2ac (experimental): marcha atras e intermitentes desde el glowMap"
        " de BeamNG (CSP)",
    ]
    counters: Counter = Counter()
    section = {
        "REVERSE": "EMISSIVE_REVERSE",
        "SIGNAL_L": "EMISSIVE_TURNSIGNAL_LEFT",
        "SIGNAL_R": "EMISSIVE_TURNSIGNAL_RIGHT",
    }
    for li in sorted(lights, key=lambda x: x.mesh_name):
        if li.function not in section:
            continue
        name = section[li.function]
        i = counters[name]
        counters[name] += 1
        out += [
            "",
            f"[{name}_{i}]",
            f"NAME={li.mesh_name}",
            f"COLOR={_colour(li)}",
            "LAG=0.8" if li.function == "REVERSE" else "LAG=0.9",
        ]
        if li.function != "REVERSE":
            out.append(f"LOCATION={'FRONT' if li.centre_z > car_centre_z else 'REAR'}")
    return sanitize_ini_comments("\n".join(out) + "\n")


# ------------------------------------------------------------------ ensamblador
@dataclass(frozen=True)
class LightsResult:
    kn5: bytes
    lights: list[LightMesh]
    renamed: list[tuple[str, str]]  # (nombre anterior, nombre nuevo)
    lights_ini: str
    csp_emissive: str


def apply_lights(
    kn5_bytes: bytes,
    functions: dict[str, str],
    materials_json: dict,
    profile: dict,
) -> LightsResult:
    """Luces del ensamblador con las precondiciones del perfil del vehículo.

    ``profile`` (``vehicle_profiles/<id>.json``, clave ``lights``):

    * ``interior_z``: [z_min, z_max] del habitáculo en el marco del KN5 (atenúa los
      testigos interiores);
    * ``expected_count``: número de materiales de luz del ``glowMap``;
    * ``source_mesh_name``: nombre que el exportador da hoy a esas mallas (en el Ibiza,
      ``35b84_body0``).

    Falla (``LightsError``) si el ``glowMap`` no da ``expected_count`` materiales, si
    una malla de luz no se llama ``source_mesh_name`` (KN5 ya renombrado o de otro
    coche) o si el árbol del KN5 cambia en algo más que esos nombres. Mismas
    comprobaciones que ``tools/variants/build_v15li.py`` (variante validada).
    """
    expected = int(profile["expected_count"])
    # sin perfil del vehículo (generado): source_mesh_name = None no comprueba el nombre de origen
    source = profile["source_mesh_name"]
    source = None if source is None else str(source)
    interior = tuple(float(v) for v in profile["interior_z"])
    if len(functions) != expected:
        raise LightsError(
            f"el glowMap da {len(functions)} materiales de luz; el perfil espera "
            f"{expected}"
        )
    new_kn5, lights = rename_light_meshes(
        kn5_bytes, functions, materials_json, interior
    )
    _, ra = load(kn5_bytes)
    _, rb = load(new_kn5)
    na, nb = list(walk(ra)), list(walk(rb))
    renamed = [(x.name, y.name) for (x, _), (y, _) in zip(na, nb) if x.name != y.name]
    same_tree = len(na) == len(nb) and all(
        x.type == y.type
        and x.payload == y.payload
        and x.active == y.active
        and len(x.children) == len(y.children)
        for (x, _), (y, _) in zip(na, nb)
    )
    if not same_tree:
        raise LightsError("el KN5 cambió en algo más que los nombres de las mallas")
    wrong = [] if source is None else [old for old, _ in renamed if old != source]
    # con perfil del vehículo cada material de luz debe ser una sola malla (``expected``)
    want = len(lights) if source is None else expected
    if len(renamed) != want or wrong:
        raise LightsError(
            f"mallas de luz renombradas: {len(renamed)} (esperadas: {want}); nombres "
            f"de origen distintos de {source!r}: {sorted(set(wrong))}"
        )
    return LightsResult(
        new_kn5, lights, renamed, render_lights_ini(lights), render_csp_emissive(lights)
    )
