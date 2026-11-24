"""Fase 7: ensamblado de la carpeta del coche para Assetto Corsa.

Uso:
    python -m src.ac.assemble <carpeta_vehiculo> <archivo.pc>
        [--car-id ID] [--driver-mass 70] [--artifacts output/<id>]
        [--out output/<id>/assembly] [--blender RUTA] [--ac-root RUTA_AC]

Reutiliza (copia, sin modificarlos) los artefactos de las fases 3–5:
``<artifacts>/engine``, ``/drivetrain`` y ``/chassis``. Genera solo lo respaldado
(docs/research/ensamblado.md):

* ``data/car.ini`` — SCREEN_NAME, TOTALMASS (masa BeamNG sin combustible + piloto),
  FUEL, MAX_FUEL. El resto de claves queda como pendiente comentado.
* ``data/lods.ini`` — un único LOD (solo hay un modelo).
* ``data/setup.ini`` — mínimo, sin reglajes ajustables ni valores físicos: AC solo
  construye el menú rápido de boxes si este archivo carga, y CSP falla sin él
  (ensamblado.md §9.12).
* ``ui/ui_car.json`` — datos de la fuente y curvas netas de ``power.lut``.
* ``skins/<pintura por defecto>/ui_skin.json``.
* ``<id>.kn5`` — reconstruido con la herramienta de la fase 6 y el origen que pide
  el SDK de Kunos (ruedas en Y = 0, caja centrada en X/Z). El KN5 de la fase 6 no
  se toca.

Mejoras integradas desde las variantes v7–v12, validadas en el juego salvo donde
se indica (docs/research/informe_integracion_ensamblador_2026-10-25.md):

* texturas difusas en el KN5 (``kn5_materials``, v7);
* ``collider.kn5`` con perfil medido (``collider_profile``, v7; necesita perfil);
* ``[GRAPHICS] DRIVEREYES`` desde la cámara de cabina de BeamNG (v7);
* ``extension/ext_config.ini`` de CSP para los materiales a doble cara (v8ic);
* ``data/driver3d.ini`` con casco y cara ocultos (v8dp);
* volante bajo ``STEER_HR`` (``steer_wheel``, v12; sentido de giro sin probar);
* ``driver_base_pos.knh`` con el piloto en el asiento y las manos ante el aro
  (``driver_pose``, v8dp + v12). Necesita ``--ac-root``: deriva de un archivo de
  Kunos y NO es distribuible;
* ``validate_references`` y la comprobación de archivos protegidos en el informe.

Fase C (variantes v14ha, v15li, v17cam y v18bu, validadas en el juego):

* luces: 19 mallas de luz renombradas en el KN5, ``data/lights.ini`` y secciones CSP de
  marcha atrás e intermitentes en ``ext_config.ini`` (``lights.apply_lights``; necesita
  ``lights`` en el perfil y falla si el KN5 no cumple sus precondiciones);
* manos: ``animations/steer.ksanim`` (agarre alterno ``v14ha``) y
  ``[STEER_ANIMATION]`` en ``driver3d.ini`` (``hands``; necesita ``hands`` en el perfil
  y la pose del piloto, así que también ``--ac-root``; no distribuible);
* cámaras: ``data/dash_cam.ini``, ``BONNET_CAMERA_POS`` y ``BUMPER_CAMERA_POS``
  (``cameras``; reglas de los coches de referencia, medidas en el KN5 de este coche).

Las constantes propias del coche (cortes del collider, materiales del volante,
caderas del piloto, luces y ciclo de manos) vienen de
``src/ac/vehicle_profiles/<id>.json`` (``--profile``); sin perfil, esas mejoras se
omiten y el informe lo dice.

``--physics-base BASE`` copia sin cambios la física de una base: las fases 3–5 aún no
generan esos valores (suspensión editada a mano el 2026-10-23, neumático, frenos y
dirección de v6). Lo recomendado es una **base declarada** de ``src.ac.physics_base``
(p. ej. la congelada desde ``pkg_v18bu``): solo física y requisitos de carga, cada
archivo con su hash. Un paquete de prueba como ``pkg_v6_drive`` sigue funcionando, pero
de él se copia todo ``data/``. ``car.ini`` se toma como plantilla y solo se le añade
``DRIVEREYES`` y las cámaras (también si ya tiene ``[GRAPHICS]``; si una clave ya
existe con el mismo valor no se toca, y con otro valor es un error) y las opciones
``--experimental`` pedidas. Sin esa opción, la física
sale de las fases 3–5 como antes.

``--experimental ffmult_1.95`` / ``steer_lock_423``: valores de AC sin respaldo en
BeamNG, nunca por defecto (``cockpit_config.EXPERIMENTS``).

No genera ``sfx``/``sounds.ini`` (el audio necesita FMOD: contrato en
docs/research/audio_pipeline_contrato.md) ni retrovisores. Escribe ``<out>/<id>/``
(carpeta del coche) y ``<out>/assembly_report.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.ac import cockpit_config
from src.ac import physics_base as physics_base_mod
from src.ac.car_validate import (
    DOCUMENTED_OPTIONAL_NULLS,
    LIKELY_LOAD_NULLS,
    LOAD_NULLS,
    changed_protected,
    classify,
    read_ini,
    validate_references,
    validate_structure,
)
from src.ac import cameras as ac_cameras
from src.ac import hands as ac_hands
from src.ac.collider_profile import write_profile_collider
from src.ac.driver_pose import build_driver_pose
from src.ac.ingame_package import sha256, tree_hashes
from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_materials import (
    diffuse_sources,
    paint_dependent_materials,
    split_kn5,
    write_with_diffuse,
)
from src.ac.kn5_write import box_mesh, signed_volume, write_simple_kn5
from src.ac.lights import LightsError, apply_lights, light_functions
from src.ac.steer_wheel import rig_steering_wheel
from src.coords import beamng_to_ac
from src.extract.engine_camso import HP_W
from src.extract.mass_geometry import compute_mass_geometry
from src.jbeam import PartIndex, ResolvedConfig, lax_json, load, load_pc, resolve
from src.mesh.build import build_kn5, plan_for_vehicle
from src.mesh.plan import (
    ScenePlan,
    add_cockpit_lr_nulls,
    add_steer_hr_diagnostic_nulls,
    kunos_origin_offset,
    translate_plan,
)
from src.ac.ini_format import sanitize_ini_comments
from src.safe_paths import ensure_safe_output_path

# Kunos (car.ini de ks_toyota_celica_st185 y ks_audi_r8_lms): "total vehicle
# weight in kg with driver and no fuel"; en ambos TOTALMASS − peso UI = 75 kg.
KUNOS_DRIVER_MASS_EVIDENCE = 75.0
DEFAULT_DRIVER_MASS = 70.0  # decisión 2 del proyecto (configurable)

ARTIFACTS = {
    "engine": ("engine.ini", "power.lut"),
    "drivetrain": ("drivetrain.ini",),
    "chassis": ("suspensions.ini", "brakes.ini", "tyres.ini"),
}
REQUIRED_DATA = (
    "car.ini",
    "engine.ini",
    "power.lut",
    "drivetrain.ini",
    "suspensions.ini",
    "brakes.ini",
    "tyres.ini",
    "lods.ini",
    "setup.ini",
)
CAR_INI_PENDING = (
    "[BASIC] GRAPHICS_OFFSET / GRAPHICS_PITCH_ROTATION: sin método de cálculo "
    "documentado",
    "[BASIC] INERTIA: Kunos pide 'generic width, height, length'; sus propios "
    "valores no coinciden con las medidas reales; candidatos en assembly_report.json",
    "[GRAPHICS] DRIVEREYES y cámaras: no hay interior ni posición del piloto",
    "[CONTROLS] STEER_LOCK / STEER_RATIO / LINEAR_STEER_ROD_RATIO / FFMULT: "
    "semántica de steeringWheelLock de BeamNG sin verificar",
    "[FUEL] CONSUMPTION: fórmula de AC distinta; no derivada",
    "[FUELTANK] POSITION: el depósito de BeamNG no tiene nodos",
    "[RIDE] PICKUP_*_HEIGHT: depende del CdG en AC (pendiente)",
    "[PIT_STOP]: reglas de juego sin equivalente en BeamNG",
)


DRIVEREYES_PENDING = CAR_INI_PENDING[2]
PROFILE_DIR = Path(__file__).resolve().parent / "vehicle_profiles"
# Archivos de data/ que genera siempre el ensamblador (nunca se copian de la base
# física). car.ini se toma de la base como plantilla.
ASSEMBLER_OWNED_DATA = ("lods.ini", "driver3d.ini", "car.ini")
# Claves de car.ini [GRAPHICS] que escribe el ensamblador (ojos y cámaras).
ASSEMBLER_GRAPHICS_KEYS = ("DRIVEREYES", "BONNET_CAMERA_POS", "BUMPER_CAMERA_POS")


class AssemblyError(RuntimeError):
    """Faltan artefactos o datos para ensamblar."""


def load_profile(path: Path | None, car_id: str) -> tuple[dict | None, str | None]:
    """Perfil del vehículo: ``path`` o, si no se da, el del repositorio para el id."""
    p = path or PROFILE_DIR / f"{car_id}.json"
    if not p.is_file():
        if path is not None:
            raise AssemblyError(f"no existe el perfil {p}")
        return None, None
    profile = json.loads(p.read_text(encoding="utf-8"))
    if profile.get("car_id") != car_id:
        raise AssemblyError(f"el perfil {p} es de {profile.get('car_id')!r}")
    return profile, str(p)


def physics_base_files(base: Path) -> dict[str, Any]:
    """Archivos de física de un paquete base y comprobación con su manifiesto.

    * **Base declarada** (``src.ac.physics_base``, manifiesto ``kind`` =
      ``beam2ac_physics_base``): se copian exactamente sus ``physics_files`` (física y
      requisitos de carga), con cada hash verificado. ``car.ini`` es la plantilla.
    * **Paquete de prueba** (comportamiento anterior, p. ej. ``pkg_v6_drive``): se
      copian todos los de ``data/`` salvo los del ensamblador. Si el paquete tiene
      ``manifest.json`` en su carpeta padre (los de ``ingame_cycle``), sus hashes deben
      coincidir: la base no puede haber cambiado desde que se probó. Ojo: así se
      copiarían también cámaras, luces o audio si el paquete los tiene.
    """
    declared = physics_base_mod.declared_manifest(base)
    if declared is not None:
        try:
            return physics_base_mod.load_declared(base, declared)
        except physics_base_mod.PhysicsBaseError as e:
            raise AssemblyError(str(e)) from None
    data = base / "data"
    if not (data / "lods.ini").is_file() or not (data / "car.ini").is_file():
        raise AssemblyError(f"{base} no parece un paquete de coche")
    files = sorted(
        p.name
        for p in data.iterdir()
        if p.is_file() and p.name not in ASSEMBLER_OWNED_DATA
    )
    manifest = base.parent / "manifest.json"
    manifest_check: dict[str, Any] = {"manifest": None}
    if manifest.is_file():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        if Path(m.get("package_dir", "")).resolve() == base.resolve():
            same = tree_hashes(base) == m.get("files")
            manifest_check = {"manifest": str(manifest), "matches": same}
            if not same:
                raise AssemblyError(f"{base} no coincide con su manifiesto")
    hashes = tree_hashes(data)
    return {
        "dir": str(base),
        "files": {f"data/{n}": hashes[n] for n in files},
        "car_ini_template": hashes["car.ini"],
        **manifest_check,
    }


def materials_json(vehicle_dir: Path, pc_path: Path) -> Path:
    """``<config>.materials.json`` (o el único ``*.materials.json`` del vehículo).

    G2.24: sin ningún ``*.materials.json``, el ``materials.cs`` del vehículo (exportador
    200409; se lee con ``src.ac.kn5_materials.load_materials``)."""
    p = vehicle_dir / f"{pc_path.stem}.materials.json"
    if p.is_file():
        return p
    found = sorted(vehicle_dir.glob("*.materials.json"))
    if not found and (vehicle_dir / "materials.cs").is_file():
        return vehicle_dir / "materials.cs"
    if len(found) != 1:
        raise AssemblyError(f"no se sabe qué materials.json usar: {found}")
    return found[0]


def _copy(src: Path, dst: Path) -> Path:
    target = ensure_safe_output_path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    return target


def _write(dst: Path, text: str) -> Path:
    target = ensure_safe_output_path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def check_artifacts(artifacts: Path) -> dict[str, Path]:
    found, missing = {}, []
    for folder, names in ARTIFACTS.items():
        for name in names:
            path = artifacts / folder / name
            (
                found.__setitem__(name, path)
                if path.is_file()
                else missing.append(str(path))
            )
    if missing:
        raise AssemblyError(
            "faltan artefactos de fases anteriores (ejecute src.ac.engine, "
            f"src.ac.drivetrain y src.ac.chassis con --out): {missing}"
        )
    return found


def read_power_lut(path: Path) -> list[tuple[float, float]]:
    points = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rpm, torque = line.split("|")
            points.append((float(rpm), float(torque)))
    return points


def render_car_ini(
    screen_name: str,
    total_mass: float,
    fuel: float,
    max_fuel: float,
    vehicle_mass: float,
    driver_mass: float,
    pending: tuple[str, ...] = CAR_INI_PENDING,
) -> str:
    lines = [
        "; BORRADOR generado por beam2ac (fase 7). Ver docs/research/ensamblado.md",
        "",
        "[HEADER]",
        "VERSION=1\t\t; car.ini oficiales: ks_audi_r8_lms usa 1, "
        "ks_toyota_celica_st185 usa 2",
        "",
        "[INFO]",
        f"SCREEN_NAME={screen_name}",
        "",
        "[BASIC]",
        f"TOTALMASS={total_mass:.1f}\t\t; con piloto y sin combustible "
        f"(convención Kunos): {vehicle_mass:.2f} kg BeamNG + {driver_mass:g} kg piloto",
        "",
        "[FUEL]",
        f"FUEL={fuel:g}\t\t; startingFuelCapacity ($fuel) de BeamNG, litros",
        f"MAX_FUEL={max_fuel:g}\t\t; fuelCapacity de BeamNG, litros",
        "",
        "; PENDIENTE (no se escriben valores sin respaldo):",
    ]
    lines += [f";  - {p}" for p in pending]
    return sanitize_ini_comments("\n".join(lines) + "\n")


def render_lods_ini(kn5_name: str) -> str:
    return (
        "; Un único modelo (no hay LOD B/C/D). OUT = fin de la cadena de LOD del\n"
        "; ejemplo del SDK de Kunos (AC_Pipeline_PUB_Rev2.0.pdf, pág. 12: OUT=1500).\n"
        "; Valor de renderizado, no físico.\n"
        "[LOD_0]\n"
        f"FILE={kn5_name}\n"
        "IN=0\n"
        "OUT=1500\n"
    )


# Secciones que admite el setup.ini mínimo. Ninguna define un reglaje ajustable
# (no hay MIN/MAX/STEP) ni un valor físico; DISPLAY_METHOD solo controla cómo se
# muestran los valores en la pantalla de reglajes.
SETUP_INI_SECTIONS = {"DISPLAY_METHOD": {"SHOW_CLICKS": "0"}}


def render_setup_ini() -> str:
    """``data/setup.ini`` mínimo (ensamblado.md §9.12).

    AC (pantalla de reglajes de AC) solo llama a menú de pit de AC
    si ``<coche>/data/setup.ini`` carga (lectura de INI de AC). Sin él,
    menú de pit de AC queda nulo y CSP lo desreferencia al aplicar
    ``[MOVABLE] PITS`` (al fijar su posición). Verificado en el juego con
    las variantes ``pkg_v3_ui`` (falla) y ``pkg_v5_setup`` (carga).
    No se ofrecen reglajes: no hay rangos respaldados para este coche.
    """
    lines = [
        "; setup.ini mínimo generado por beam2ac (ensamblado.md §9.12).",
        "; Sin reglajes ajustables ni valores físicos. AC necesita que este archivo",
        "; exista para construir el menú rápido de boxes.",
        "",
    ]
    for section, keys in SETUP_INI_SECTIONS.items():
        lines.append(f"[{section}]")
        lines += [f"{k}={v}" for k, v in keys.items()]
    return sanitize_ini_comments("\n".join(lines) + "\n")


def build_ui(
    info: dict,
    cfg_info: dict,
    mass: float,
    curve: list[tuple[float, float]],
    traction: str | None,
) -> dict[str, Any]:
    power = [(rpm, t * rpm * 2 * math.pi / 60 / HP_W) for rpm, t in curve]
    peak_t = max(curve, key=lambda p: p[1])
    peak_p = max(power, key=lambda p: p[1])
    specs = {
        "bhp": f"{round(peak_p[1])}bhp",
        "torque": f"{round(peak_t[1])}Nm",
        "weight": f"{round(mass)}kg",
        "pwratio": f"{mass / peak_p[1]:.2f}kg/hp",
    }
    top = cfg_info.get("Top Speed")
    if isinstance(top, (int, float)):
        specs["topspeed"] = f"{round(top * 3.6)}km/h"  # unidad m/s inferida (fase 4)
    acc = cfg_info.get("0-100 km/h")
    if isinstance(acc, (int, float)):
        specs["acceleration"] = f"{acc:g}s 0-100"
    tags = []
    if traction:
        tags.append(traction.lower())
    if str(cfg_info.get("Transmission", "")).lower() == "manual":
        tags.append("manual")
    if str(cfg_info.get("Induction Type", "")).lower() == "turbo":
        tags.append("turbo")
    years = cfg_info.get("Years") or {}
    ui = {
        "name": info.get("Name", ""),
        "brand": info.get("Brand", ""),
        "description": (
            "Exportado de Automation (Camshaft Software LTD), "
            "licencia CC BY-NC-SA 4.0. Convertido con beam2ac; validación solo "
            "estructural, sin probar en el juego."
        ),
        "tags": tags,
        "specs": specs,
        "torqueCurve": [[rpm, round(t, 2)] for rpm, t in curve],
        "powerCurve": [[rpm, round(p, 2)] for rpm, p in power],
        "author": info.get("Author", ""),
    }
    if isinstance(years.get("min"), int):
        ui["year"] = years["min"]
    return ui


def inertia_candidates(geometry) -> dict[str, Any]:
    i = geometry.inertia_ac
    m = geometry.total_mass
    pitch, yaw, roll = i[0][0], i[1][1], i[2][2]
    try:
        width = math.sqrt(6 * (roll + yaw - pitch) / m)
        height = math.sqrt(6 * (roll + pitch - yaw) / m)
        length = math.sqrt(6 * (pitch + yaw - roll) / m)
        box = [width, height, length]
    except ValueError:
        box = None
    return {
        "equivalent_box_from_point_masses": box,
        "formula": "I = m/12·(a²+b²) por eje (supone que AC usa una caja uniforme)",
        "status": "candidato; no escrito (semántica de INERTIA de AC sin verificar)",
    }


def write_collider(plan: ScenePlan, path: Path) -> dict[str, Any]:
    """``collider.kn5``: caja de la carrocería principal (SDK de Kunos, pág. 36).

    Reglas del SDK: forma simple, cerrada, sin UV ni textura, ≤ 40/60 triángulos,
    material "GL", pivote en 0,0,0 con la orientación de los nulos de rueda, sin
    bajar del suelo del coche. La caja se DERIVA de la geometría: es la caja
    envolvente (en ejes AC, ya con el origen de Kunos) de la malla de la raíz con
    la MAYOR caja envolvente (la carrocería, que envuelve al resto). No es una forma
    ajustada como las oficiales (44–58 vértices): sobreestima las esquinas.

    G2.24: antes se elegía la malla de la raíz con más vértices. En los 200409 esa es
    la malla del motor (``*_block_mid_actor_*_sectionmesh``, ~130 000 vértices) y en
    algunas exportaciones 38022/40340 ``chassis1``: el collider quedaba como la caja
    del motor o del bastidor, más corta y baja que la carrocería. En el Ibiza los dos
    criterios dan ``35b84_body0`` (además usa el collider de su perfil).
    """
    roots = [o for o in plan.objects if o.parent is None]
    if not roots:
        raise AssemblyError("sin mallas en la raíz para derivar el collider")

    def box_volume(o: Any) -> float:
        lo, hi = o.bbox_beamng
        return (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])

    body = max(roots, key=box_volume)
    corners = [
        beamng_to_ac((x, y, z))
        for x in (body.bbox_beamng[0][0], body.bbox_beamng[1][0])
        for y in (body.bbox_beamng[0][1], body.bbox_beamng[1][1])
        for z in (body.bbox_beamng[0][2], body.bbox_beamng[1][2])
    ]
    lo = tuple(min(c[i] for c in corners) for i in range(3))
    hi = tuple(max(c[i] for c in corners) for i in range(3))
    mesh = box_mesh("GEO_collider", lo, hi)  # type: ignore[arg-type]
    write_simple_kn5(path, [mesh], [("GL", "GL")], root="collider")
    return {
        "source_object": body.name,
        "box_ac_min": lo,
        "box_ac_max": hi,
        "triangles": len(mesh.indices) // 3,
        "status": "derivado (caja envolvente de la carrocería); no ajustado a la forma",
    }


def texture_sources(
    kn5_material_names: list[str],
    info: dict[str, Any],
    cfg_info: dict[str, Any],
    vehicle_dir: Path,
    pc_path: Path,
) -> tuple[list[Any], dict[str, Any]]:
    """Fuentes de color difuso del KN5 (paso 3a), con la pintura de
    ``src.extract.paints``.

    Con la pintura de la configuración resuelta da las mismas fuentes que la versión
    anterior (``info.json`` → ``paints[defaultPaintName1]``). Sin ella ya no aborta:
    solo los materiales ``instanceDiffuse`` quedan sin textura y el informe lo dice
    (igual que la etapa ``model`` del orquestador).
    """
    from src.extract.paints import read_paints
    from src.jbeam import lax_json

    pc_data = lax_json.load(pc_path).value if pc_path.is_file() else None
    meta = vehicle_dir / "paints.meta"
    rep = read_paints(
        info,
        cfg_info,
        pc_data if isinstance(pc_data, dict) else None,
        meta.read_bytes() if meta.is_file() else None,
        config_info_name=f"info_{pc_path.stem}.json",
        pc_name=pc_path.name,
    )
    sel = rep.selected(1)
    mj = materials_json(vehicle_dir, pc_path)
    needs = paint_dependent_materials(kn5_material_names, mj)
    rgb = sel.paint.rgb if sel and sel.paint else None
    sources = diffuse_sources(kn5_material_names, mj, vehicle_dir, rgb)
    report: dict[str, Any] = {
        "paint": sel.name if sel and sel.paint else None,
        "materials": len(sources),
        "distinct_textures": len({s.texture_name for s in sources if s.texture_name}),
        "kinds": {
            k: sum(s.kind == k for s in sources)
            for k in sorted({s.kind for s in sources})
        },
    }
    if needs and rgb is None:
        report["paint_unresolved"] = {
            "materials": needs,
            "origin": sel.origin if sel else "sin selección",
            "note": "sin textura: elegir la pintura es una decisión pendiente",
        }
    return sources, report


def assemble(
    vehicle_dir: Path,
    pc_path: Path,
    car_id: str,
    artifacts: Path,
    out: Path,
    blender: str | None,
    driver_mass: float = DEFAULT_DRIVER_MASS,
    ac_root: Path | None = None,
    physics_base: Path | None = None,
    profile: dict | None = None,
    profile_path: str | None = None,
    experiments: tuple[str, ...] = (),
    textures: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Ensambla la carpeta del coche (ver el docstring del módulo).

    ``profile=None`` omite las mejoras propias del coche (collider con perfil,
    volante y pose); ``physics_base`` copia la física de un paquete sin cambios.
    """
    out = ensure_safe_output_path(out)
    car = ensure_safe_output_path(out / car_id)
    # La salida por defecto del CLI es la carpeta de producción: no se pisa nada sin
    # pedirlo expresamente.
    if not overwrite and car.is_dir() and any(car.iterdir()):
        raise AssemblyError(f"{car} ya existe y no está vacía (use --overwrite)")
    for name in experiments:
        if name not in cockpit_config.EXPERIMENTS:
            raise AssemblyError(f"opción experimental desconocida: {name}")
    if experiments and physics_base is None:
        raise AssemblyError(
            "las opciones experimentales editan CONTROLS: requieren --physics-base"
        )
    if physics_base is not None:
        # carpeta de la base declarada o la de su coche
        physics_base = physics_base_mod.resolve_base_dir(physics_base)
    base_info = physics_base_files(physics_base) if physics_base else None
    resolved: ResolvedConfig = resolve(
        PartIndex.from_directories(vehicle_dir), load_pc(pc_path)
    )
    geometry = compute_mass_geometry(resolved)
    info = load(vehicle_dir / "info.json").value
    cfg_info_path = vehicle_dir / f"info_{pc_path.stem}.json"
    cfg_info = load(cfg_info_path).value if cfg_info_path.is_file() else {}
    variables = resolved.variable_values()
    fuel_section = next(
        (
            v
            for _, v in resolved.section_contributions("mainTank")
            if isinstance(v, dict)
        ),
        {},
    )
    from src.jbeam import evaluate

    max_fuel = evaluate(fuel_section.get("fuelCapacity"), variables)
    fuel = evaluate(fuel_section.get("startingFuelCapacity"), variables)
    if not isinstance(max_fuel, (int, float)) or not isinstance(fuel, (int, float)):
        raise AssemblyError(f"combustible no numérico: {fuel!r}/{max_fuel!r}")

    # 0. plan de mallas y origen de Kunos (también lo usa DRIVEREYES)
    plan = plan_for_vehicle(vehicle_dir, pc_path)
    offset = kunos_origin_offset(plan)
    offset_ac = beamng_to_ac(offset)
    eyes = cockpit_config.driver_eyes(resolved, offset_ac)

    # 1. data/: física (fases 3–5 o base física) + generados
    kn5_name = f"{car_id}.kn5"
    total_mass = geometry.total_mass + driver_mass
    if base_info is None:
        found = check_artifacts(artifacts)
        copied = {
            name: str(_copy(path, car / "data" / name)) for name, path in found.items()
        }
        car_ini = render_car_ini(
            info.get("Name", car_id),
            total_mass,
            fuel,
            max_fuel,
            geometry.total_mass,
            driver_mass,
            tuple(p for p in CAR_INI_PENDING if p != DRIVEREYES_PENDING),
        )
    else:
        assert physics_base is not None
        copied = {
            rel: str(_copy(physics_base / rel, car / rel)) for rel in base_info["files"]
        }
        found = {
            Path(rel).name: car / rel
            for rel in base_info["files"]
            if Path(rel).name in ("power.lut", "drivetrain.ini")
        }
        car_ini = (physics_base / "data" / "car.ini").read_text(encoding="utf-8")
    car_ini = cockpit_config.add_drivereyes(car_ini, eyes)
    for name in experiments:
        car_ini = cockpit_config.apply_experiment(car_ini, name)
    _write(car / "data" / "car.ini", car_ini)
    _write(car / "data" / "lods.ini", render_lods_ini(kn5_name))
    generated = ["data/car.ini", "data/lods.ini"]
    if not (car / "data" / "setup.ini").is_file():
        _write(car / "data" / "setup.ini", render_setup_ini())
        generated.append("data/setup.ini")
    _write(car / "data" / "driver3d.ini", cockpit_config.render_driver3d_ini())
    generated.append("data/driver3d.ini")

    # 2. ui y skin
    curve = read_power_lut(found["power.lut"])
    drivetrain_ini = read_ini(found["drivetrain.ini"])
    traction = drivetrain_ini.get("TRACTION", {}).get("TYPE")
    ui = build_ui(info, cfg_info, geometry.total_mass, curve, traction)
    _write(car / "ui" / "ui_car.json", json.dumps(ui, indent=2, ensure_ascii=False))
    paint_name = cfg_info.get("defaultPaintName1") or "default"
    skin = str(paint_name).lower()
    _write(
        car / "skins" / skin / "ui_skin.json",
        json.dumps({"skinname": paint_name}, indent=2),
    )
    generated += ["ui/ui_car.json", f"skins/{skin}/ui_skin.json"]

    # 3. KN5 con origen Kunos (herramienta de la fase 6, carpeta de trabajo propia)
    # STEER_HR vacío bajo COCKPIT_HR (fase 7.1); el volante se monta en el paso 3c.
    tplan = add_steer_hr_diagnostic_nulls(
        add_cockpit_lr_nulls(translate_plan(plan, offset))
    )
    kn5_report: dict[str, Any] = {"offset_beamng": offset, "offset_ac": offset_ac}
    stages = out / "_kn5_stages"
    base_kn5: Path | None = None
    steer_report: dict[str, Any] | None = None
    lights_res = None
    skipped: dict[str, str] = {}
    if blender:
        base_kn5, verification = build_kn5(tplan, out / "_kn5_build", kn5_name, blender)
        kn5_report.update(
            {
                "verification_ok": verification.ok,
                "errors": verification.errors,
                "base_sha256": sha256(base_kn5),
            }
        )
        current = base_kn5
        # 3a. texturas difusas (v7)
        if textures:
            parts = split_kn5(base_kn5.read_bytes())
            sources, tex_report = texture_sources(
                [m.name for m in parts.materials], info, cfg_info, vehicle_dir, pc_path
            )
            ensure_safe_output_path(stages).mkdir(parents=True, exist_ok=True)
            current = write_with_diffuse(
                current, stages / f"textured_{kn5_name}", sources
            )
            kn5_report["textures"] = tex_report
        else:
            skipped["textures"] = "desactivadas (--no-textures)"
        # 3b. volante bajo STEER_HR (v12)
        if profile and profile.get("steering_wheel"):
            dst = stages / f"steer_{kn5_name}"
            steer_report = rig_steering_wheel(current, dst, profile["steering_wheel"])
            current = dst
            kn5_report["steering_wheel"] = steer_report
        else:
            skipped["steering_wheel"] = "sin perfil del vehículo"
        # 3b2. luces (v15li): nombre único para cada malla de luz; necesita perfil
        if profile and profile.get("lights"):
            try:
                lights_res = apply_lights(
                    current.read_bytes(),
                    light_functions(resolved),
                    lax_json.load(materials_json(vehicle_dir, pc_path)).value,
                    profile["lights"],
                )
            except LightsError as e:
                raise AssemblyError(f"luces: {e}") from None
            dst = ensure_safe_output_path(stages / f"lights_{kn5_name}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(lights_res.kn5)
            current = dst
            kn5_report["lights_renamed"] = lights_res.renamed
        else:
            skipped["lights"] = "sin perfil del vehículo (lights)"
        _copy(current, car / kn5_name)
    else:
        kn5_report["error"] = "Blender no disponible: KN5 no generado"
        skipped.update(
            {
                k: "sin KN5 (Blender no disponible)"
                for k in (
                    "textures",
                    "steering_wheel",
                    "lights",
                    "csp",
                    "driver_pose",
                    "hands",
                    "cameras",
                )
            }
        )

    # 3c. collider: con perfil, medido en el KN5 base (v7); si no, caja envolvente
    if base_kn5 is not None and profile and profile.get("collider"):
        collider_report = write_profile_collider(
            base_kn5, car / "collider.kn5", profile["collider"]
        )
    else:
        collider_report = write_collider(tplan, car / "collider.kn5")
        skipped["collider_profile"] = (
            "sin KN5" if base_kn5 is None else "sin perfil del vehículo"
        )
    generated.append("collider.kn5")

    # 3d. CSP: materiales a doble cara (v8ic)
    csp_report = None
    if (car / kn5_name).is_file():
        mats = [m.name for m in split_kn5((car / kn5_name).read_bytes()).materials]
        mj = materials_json(vehicle_dir, pc_path)
        double = cockpit_config.double_sided_materials(mats, mj)
        ext = cockpit_config.render_ext_config(double, mj.name) if double else ""
        if lights_res is not None:  # v15li: se AÑADEN las secciones CSP de luces
            ext += lights_res.csp_emissive
            _write(car / "data" / "lights.ini", lights_res.lights_ini)
            generated.append("data/lights.ini")
        if ext:
            _write(car / "extension" / "ext_config.ini", ext)
            generated.append("extension/ext_config.ini")
            csp_report = {
                "materials": len(double),
                "source": mj.name,
                "light_sections": lights_res is not None,
            }
        generated.append(kn5_name)

    # 3e. pose del piloto (v8dp + v12): local, con la instalación del usuario
    pose_report = None
    if profile and profile.get("driver") and steer_report and ac_root:
        drv = profile["driver"]
        src_knh = (
            ac_root
            / "content"
            / "cars"
            / drv["pose_source_car"]
            / "driver_base_pos.knh"
        )
        if not src_knh.is_file():
            raise AssemblyError(f"no existe {src_knh}")
        nd = int(drv["rim_frame_decimals"])
        pose_report = build_driver_pose(
            src_knh,
            car / "driver_base_pos.knh",
            tuple(drv["hips_target"]),
            tuple(round(t, nd) for t in steer_report["rim_centre"]),
            tuple(round(t, nd) for t in steer_report["rim_axis"]),
            float(drv["knuckle_target_m"]),
        )
        generated.append("driver_base_pos.knh")
    elif "driver_pose" not in skipped:
        skipped["driver_pose"] = (
            "requiere --ac-root (pose de Kunos local, no distribuible)"
            if not ac_root
            else "sin perfil del vehículo o sin volante montado"
        )

    # 3f. manos (v14ha): clip de agarre alterno sobre la pose; necesita perfil y pose
    hands_report = None
    if profile and profile.get("hands") and pose_report is not None:
        mats = [m.name for m in split_kn5((car / kn5_name).read_bytes()).materials]
        token = profile["steering_wheel"]["rim_material_token"]
        try:
            centre, axis = ac_hands.rim(car / kn5_name, mats, token)
            res = ac_hands.alternating_grip(
                (car / "driver_base_pos.knh").read_bytes(),
                centre,
                axis,
                profile["hands"],
            )
            driver3d = ac_hands.add_steer_animation(
                cockpit_config.render_driver3d_ini(), res.lock_deg
            )
        except ac_hands.HandsError as e:
            raise AssemblyError(f"manos: {e}") from None
        anim = ensure_safe_output_path(car / "animations" / ac_hands.ANIM_NAME)
        anim.parent.mkdir(parents=True, exist_ok=True)
        anim.write_bytes(res.ksanim)
        _write(car / "data" / "driver3d.ini", driver3d)
        generated.append(f"animations/{ac_hands.ANIM_NAME}")
        hands_report = {
            "variant": ac_hands.VARIANT,
            "lock_deg": res.lock_deg,
            "frames": res.frames,
            "rim_centre": res.rim_centre,
            "rim_axis": res.rim_axis,
            "base_angles": res.base_angles,
            "reach_margin_m": res.reach_margin_m,
            "sha256": sha256(anim),
        }
    elif "hands" not in skipped:
        skipped["hands"] = (
            "sin perfil del vehículo (hands)"
            if not (profile and profile.get("hands"))
            else "sin pose del piloto (driver_base_pos.knh)"
        )

    # 3g. cámaras (v17cam + v18bu): dash_cam.ini, BONNET y BUMPER medidos en el KN5
    cameras_report = None
    if (car / kn5_name).is_file():
        basic = read_ini(car / "data" / "car.ini").get("BASIC", {})
        go = tuple(float(v) for v in basic.get("GRAPHICS_OFFSET", "0, 0, 0").split(","))
        pitch = float(basic.get("GRAPHICS_PITCH_ROTATION", "0"))
        eyes3 = tuple(float(f"{c:.3f}") for c in eyes.eyes_ac)  # como se escribe
        try:
            cams = ac_cameras.cameras_from_kn5(car / kn5_name, eyes3, go, pitch)
            for key, value, line in (
                ("BONNET_CAMERA_POS", cams.bonnet_body, ac_cameras.bonnet_line(cams)),
                ("BUMPER_CAMERA_POS", cams.bumper_body, ac_cameras.bumper_line(cams)),
            ):
                car_ini = cockpit_config.add_graphics_line(car_ini, key, value, line)
        except (ac_cameras.CameraError, cockpit_config.CockpitConfigError) as e:
            raise AssemblyError(f"cámaras: {e}") from None
        _write(car / "data" / "car.ini", car_ini)
        dash = ensure_safe_output_path(car / "data" / "dash_cam.ini")
        body = ac_cameras.render_dash_cam_ini(cams)
        if dash.is_file() and dash.read_bytes() != body:
            raise AssemblyError("data/dash_cam.ini de la base distinto del calculado")
        dash.write_bytes(body)
        generated.append("data/dash_cam.ini")
        cameras_report = asdict(cams)
    elif "cameras" not in skipped:
        skipped["cameras"] = "sin KN5"

    # 4. validación
    reference = None
    driver_dir = None
    if ac_root:
        ref_dir = ac_root / "content" / "cars" / "ks_toyota_celica_st185" / "data"
        if ref_dir.is_dir():
            reference = sorted(p.name for p in ref_dir.glob("*"))
        if (ac_root / "content" / "driver").is_dir():
            driver_dir = ac_root / "content" / "driver"
    required = REQUIRED_DATA + ("driver3d.ini",)
    required += tuple(
        Path(g).name for g in generated if g in ("data/dash_cam.ini", "data/lights.ini")
    )
    if base_info is not None:
        required += tuple(
            Path(r).name for r in base_info["files"] if Path(r).name not in required
        )
    validation = validate_structure(car, required, reference)
    _consistency(
        validation,
        car,
        geometry,
        driver_mass,
        total_mass,
        ui,
        curve,
        tplan,
        kn5_name,
        physics_from_base=base_info is not None,
        eyes=eyes,
        steer_rigged=steer_report is not None,
    )
    if driver_dir is not None:
        _check_hide_objects(validation, driver_dir)
    references = validate_references(car, driver_dir)
    protected = None
    if base_info is not None:
        assert physics_base is not None
        changed = changed_protected(physics_base, car, list(base_info["files"]))
        validation.check(
            "base física: archivos copiados idénticos byte a byte",
            not changed,
            changed,
        )
        _check_car_ini_template(validation, physics_base, car, experiments)
        protected = {**base_info, "changed": changed}

    report = {
        "car_dir": str(car),
        "profile": profile_path,
        "physics_source": "base física (copia)" if base_info else "fases 3–5",
        "physics_base": protected,
        "reused_artifacts": {k: str(v) for k, v in found.items()},
        "copied_to": copied,
        "generated": generated,
        "skipped": skipped,
        "total_mass": {
            "beamng_without_fuel_kg": geometry.total_mass,
            "driver_kg": driver_mass,
            "totalmass": total_mass if base_info is None else None,
            "evidence": "Kunos: 'total vehicle weight in kg with driver and no fuel'; "
            f"TOTALMASS − peso UI = {KUNOS_DRIVER_MASS_EVIDENCE:g} kg "
            "en 2 coches oficiales",
            "note": (
                None
                if base_info is None
                else "TOTALMASS lo fija la base física (sin bujes; ensamblado.md §9.13)"
            ),
        },
        "inertia": inertia_candidates(geometry),
        "kn5": kn5_report,
        "collider": collider_report,
        "cockpit": {
            **cockpit_config.describe(eyes, list(experiments)),
            "csp": csp_report,
            "driver_pose": pose_report,
            "hands": hands_report,
            "cameras": cameras_report,
        },
        "lights": {
            "renamed": lights_res.renamed if lights_res else None,
            "count": len(lights_res.lights) if lights_res else 0,
        },
        "classification": classify(car, car.name, _kn5_names(car / kn5_name)),
        "validation": asdict(validation),
        "references": asdict(references),
        "ok": validation.ok
        and references.ok
        and bool(kn5_report.get("verification_ok", False)),
    }
    _write(
        out / "assembly_report.json",
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
    )
    return report


def _check_hide_objects(v, driver_dir: Path) -> None:
    model = driver_dir / f"{cockpit_config.DRIVER_MODEL}.kn5"
    if not model.is_file():
        v.check("driver3d.ini: existe el modelo de piloto", False, str(model))
        return
    names = _kn5_names(model)
    missing = [n for n in cockpit_config.HIDE_OBJECTS if n not in names]
    v.check("driver3d.ini: HIDE_OBJECT existen en el modelo", not missing, missing)


def _check_car_ini_template(v, base: Path, car: Path, experiments) -> None:
    """car.ini = plantilla de la base + claves de GRAPHICS del ensamblador + claves
    experimentales pedidas. Si la base ya trae ``[GRAPHICS]``, sus otras claves deben
    seguir igual (las del ensamblador solo se aceptan si coinciden).
    """
    old = read_ini(base / "data" / "car.ini")
    new = read_ini(car / "data" / "car.ini")
    exp_keys = {cockpit_config.EXPERIMENTS[e]["key"] for e in experiments}

    def _strip(ini: dict) -> dict:
        out = {
            s: {
                k: val
                for k, val in keys.items()
                if not (s == "CONTROLS" and k in exp_keys)
                and not (s == "GRAPHICS" and k in ASSEMBLER_GRAPHICS_KEYS)
            }
            for s, keys in ini.items()
        }
        if not out.get("GRAPHICS", True):
            del out["GRAPHICS"]  # sección que solo tenía claves del ensamblador
        return out

    strip, old_strip = _strip(new), _strip(old)
    v.check(
        "car.ini: física de la base intacta (salvo opciones experimentales)",
        strip == old_strip,
        sorted(exp_keys),
    )


def _consistency(
    v,
    car,
    geometry,
    driver_mass,
    total_mass,
    ui,
    curve,
    tplan,
    kn5_name,
    physics_from_base: bool = False,
    eyes=None,
    steer_rigged: bool = False,
):
    data = car / "data"
    car_ini = read_ini(data / "car.ini")
    tm = float(car_ini["BASIC"]["TOTALMASS"])
    if physics_from_base:
        # La base física fija su propio TOTALMASS (carrocería sin bujes, §9.13).
        v.checks.append(
            {
                "check": "TOTALMASS: lo fija la base física (no se compara con fase 2)",
                "ok": True,
                "detail": tm,
                "informative": True,
            }
        )
    else:
        v.check(
            "TOTALMASS − piloto = masa BeamNG (fase 2)",
            abs(tm - driver_mass - geometry.total_mass) < 0.06,
            {"TOTALMASS": tm, "fase2": geometry.total_mass},
        )
    if eyes is not None:
        got = car_ini.get("GRAPHICS", {}).get("DRIVEREYES", "")
        want = ", ".join(f"{c:.3f}" for c in eyes.eyes_ac)
        v.check("car.ini DRIVEREYES = cámara de cabina de BeamNG", got == want, got)
    v.check(
        "peso UI = masa sin piloto",
        ui["specs"]["weight"] == f"{round(geometry.total_mass)}kg",
        ui["specs"]["weight"],
    )
    tyres = read_ini(data / "tyres.ini")
    radius = {w.radius for w in geometry.wheels}
    v.check(
        "tyres.ini RADIUS = radio de rueda (fase 2)",
        {float(tyres[s]["RADIUS"]) for s in ("FRONT", "REAR")} == radius,
        sorted(radius),
    )
    susp = read_ini(data / "suspensions.ini")
    v.check(
        "suspensions.ini WHEELBASE = batalla (fase 2)",
        abs(float(susp["BASIC"]["WHEELBASE"]) - geometry.wheelbase) < 1e-4,
        susp["BASIC"]["WHEELBASE"],
    )
    engine = read_ini(data / "engine.ini")
    limiter = float(engine["ENGINE_DATA"]["LIMITER"])
    v.check("LIMITER = último rpm de power.lut", limiter == curve[-1][0], limiter)
    v.check(
        "ui torqueCurve = power.lut",
        [p[0] for p in ui["torqueCurve"]] == [p[0] for p in curve],
        len(curve),
    )
    lods = read_ini(data / "lods.ini")
    v.check(
        "lods.ini apunta al KN5 del coche", lods["LOD_0"]["FILE"] == kn5_name, kn5_name
    )
    setup = read_ini(data / "setup.ini")
    v.check(
        "setup.ini mínimo: sin reglajes ni valores físicos (§9.12)",
        setup == SETUP_INI_SECTIONS,
        setup,
    )

    kn5_path = car / kn5_name
    if not kn5_path.is_file():
        v.check("existe el KN5", False, str(kn5_path))
        return
    kn5 = read_kn5(kn5_path)
    names = {n.name for n in kn5.nodes}
    missing_nulls = [n for n in LOAD_NULLS if n not in names]
    v.check(
        "nulos imprescindibles WHEEL_xx/SUSP_xx (SDK + KN5 instalados)",
        not missing_nulls,
        missing_nulls,
    )
    # Bloqueo, no error: la fuente no tiene volante ni interior (ensamblado.md §8).
    missing_likely = [n for n in LIKELY_LOAD_NULLS if n not in names]
    v.check(
        "STEER_HR (AC lo busca; en todos los coches no de tráfico)",
        not missing_likely,
        missing_likely,
        blocker=True,
    )
    v.checks.append(
        {
            "check": "DISC_xx (SDK; ausentes en ks_audi_r8_lms oficial: no bloquean)",
            "ok": all(n in names for n in DOCUMENTED_OPTIONAL_NULLS),
            "detail": [n for n in DOCUMENTED_OPTIONAL_NULLS if n not in names],
            "informative": True,
        }
    )
    steer_hr = [n for n in kn5.nodes if n.name == "STEER_HR"]
    v.check(
        "STEER_HR hijo de COCKPIT_HR (como en 126/128 coches ks_*)",
        bool(steer_hr)
        and steer_hr[0].parent >= 0
        and kn5.nodes[steer_hr[0].parent].name == "COCKPIT_HR",
        "STEER_HR",
        blocker=True,
    )
    if steer_rigged:
        geo = [n for n in kn5.nodes if n.name == "GEO_Steer_HR"]
        meshes = [
            n
            for n in kn5.nodes
            if n.type == 2 and geo and n.parent == kn5.nodes.index(geo[0])
        ]
        v.check(
            "STEER_HR → GEO_Steer_HR con las mallas del volante (v12)",
            len(geo) == 1
            and bool(steer_hr)
            and kn5.nodes[geo[0].parent].name == "STEER_HR"
            and bool(meshes),
            [n.name for n in meshes],
        )
    else:
        v.checks.append(
            {
                "check": "STEER_HR es diagnóstico: vacío, en el origen, sin volante",
                "ok": True,
                "detail": "ensamblado.md §9.1",
                "informative": True,
            }
        )
    cockpit = [n for n in kn5.nodes if n.name == "STEER_LR"]
    v.check(
        "STEER_LR hijo de COCKPIT_LR (como en los KN5 oficiales)",
        bool(cockpit)
        and cockpit[0].parent >= 0
        and kn5.nodes[cockpit[0].parent].name == "COCKPIT_LR",
        "STEER_LR",
    )
    wheel_pts = [
        p
        for n in kn5.nodes
        if n.type == 2 and _under(kn5, n, "WHEEL_")
        for p in kn5.mesh_world_vertices(n)
    ]
    ground = min(p[1] for p in wheel_pts)
    v.check("ruedas tocan Y = 0 (SDK Kunos)", abs(ground) < 1e-3, ground)
    all_pts = [p for n in kn5.nodes if n.type == 2 for p in kn5.mesh_world_vertices(n)]
    cx = (min(p[0] for p in all_pts) + max(p[0] for p in all_pts)) / 2
    cz = (min(p[2] for p in all_pts) + max(p[2] for p in all_pts)) / 2
    v.check(
        "caja centrada en X y Z (SDK Kunos)",
        abs(cx) < 1e-3 and abs(cz) < 1e-3,
        [cx, cz],
    )
    if not physics_from_base:
        v.check("total de TOTALMASS escrito", tm == round(total_mass, 1), tm)
    _check_collider(v, car / "collider.kn5", all_pts)


def _kn5_names(path: Path) -> set[str]:
    return (
        {n.name for n in read_kn5(path, keep_vertices=False).nodes}
        if path.is_file()
        else set()
    )


def _check_collider(v, path: Path, model_pts) -> None:
    if not path.is_file():
        v.check("existe collider.kn5", False, str(path))
        return
    col = read_kn5(path)
    meshes = [n for n in col.nodes if n.type == 2]
    v.check("collider: una malla", len(meshes) == 1, len(meshes))
    if len(meshes) != 1:
        return
    m = meshes[0]
    pts = col.mesh_world_vertices(m)
    v.check(
        "collider: shader GL, sin texturas",
        col.materials == [("GL", "GL")] and not col.textures,
        col.materials,
    )
    v.check(
        "collider: ≤ 60 triángulos (SDK)",
        len(m.indices) // 3 <= 60,
        len(m.indices) // 3,
    )
    edges: dict[tuple[int, int], int] = {}
    for i in range(0, len(m.indices), 3):
        tri = m.indices[i : i + 3]
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            key = (min(a, b), max(a, b))
            edges[key] = edges.get(key, 0) + 1
    v.check(
        "collider: cerrado (cada arista en 2 triángulos)",
        all(c == 2 for c in edges.values()),
        len(edges),
    )
    v.check(
        "collider: volumen firmado positivo (como los oficiales)",
        signed_volume(pts, m.indices) > 0,
        signed_volume(pts, m.indices),
    )
    floor = min(p[1] for p in pts)
    v.check("collider: no baja del suelo del coche (Y ≥ 0)", floor >= -1e-6, floor)


def _under(kn5, node, prefix: str) -> bool:
    i = node.parent
    while i >= 0:
        if kn5.nodes[i].name.startswith(prefix):
            return True
        i = kn5.nodes[i].parent
    return False


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m src.ac.assemble", description=__doc__
    )
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("--car-id")
    parser.add_argument("--driver-mass", type=float, default=DEFAULT_DRIVER_MASS)
    parser.add_argument("--artifacts")
    parser.add_argument("--out")
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument("--ac-root")
    parser.add_argument(
        "--physics-base",
        help="paquete cuya física se copia sin cambios (p. ej. pkg_v6_drive/...)",
    )
    parser.add_argument("--profile", help="perfil del vehículo (JSON)")
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help="omite collider con perfil, volante y pose",
    )
    parser.add_argument("--no-textures", action="store_true")
    parser.add_argument(
        "--experimental",
        action="append",
        default=[],
        choices=sorted(cockpit_config.EXPERIMENTS),
        help="opción experimental de CONTROLS (requiere --physics-base)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="permite escribir en una carpeta de coche que ya tiene archivos",
    )
    args = parser.parse_args(argv)
    vehicle = Path(args.vehicle_dir)
    car_id = args.car_id or vehicle.name
    artifacts = Path(args.artifacts or f"output/{car_id}")
    out = Path(args.out or f"output/{car_id}/assembly")
    profile, profile_path = (
        (None, None)
        if args.no_profile
        else load_profile(Path(args.profile) if args.profile else None, car_id)
    )
    report = assemble(
        vehicle,
        Path(args.pc),
        car_id,
        artifacts,
        out,
        args.blender,
        args.driver_mass,
        Path(args.ac_root) if args.ac_root else None,
        physics_base=Path(args.physics_base) if args.physics_base else None,
        profile=profile,
        profile_path=profile_path,
        experiments=tuple(args.experimental),
        textures=not args.no_textures,
        overwrite=args.overwrite,
    )
    val, refs = report["validation"], report["references"]
    print(f"Coche: {report['car_dir']}")
    print(f"Física: {report['physics_source']}  Perfil: {report['profile']}")
    tm = read_ini(Path(report["car_dir"]) / "data" / "car.ini")["BASIC"]["TOTALMASS"]
    print(f"TOTALMASS: {tm} kg")
    print(f"KN5: {report['kn5'].get('verification_ok', report['kn5'].get('error'))}")
    for k, why in report["skipped"].items():
        print(f"  OMITIDO {k}: {why}")
    print(
        f"Errores: {len(val['errors'])}  Bloqueos: {len(val['blockers'])}  "
        f"Referencias: {len(refs['errors'])} errores"
    )
    for e in val["errors"] + refs["errors"]:
        print(f"  ERROR {e}")
    for b in val["blockers"]:
        print(f"  BLOQUEO {b}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
