"""Contratos de los pipelines de conversión y adaptadores aislados (fase 8).

Cada :class:`PipelineSpec` documenta, para un pipeline EXISTENTE del proyecto: entradas
y formatos, salidas, dependencias externas, atributos que necesita, transformaciones,
validaciones disponibles, limitaciones, y si se puede ejecutar de forma independiente y
repetible (con la evidencia de esa repetibilidad).

Detección de herramientas (:func:`tool_status`): SOLO comprueba rutas (``Path.exists``).
Nunca ejecuta los juegos. La aplicación no necesita programas externos para el modelo 3D ni para el sonido.

Adaptadores aislados (no escriben nada, no modifican paquetes):
* :func:`geometry_plan_summary`: plan de escena de mallas (``src.mesh.build.plan_for_vehicle``).
* :func:`audio_capabilities`: qué operaciones de audio están disponibles.
* ``src.pipeline.physics_preview``: física en memoria (fases 2–5 y 7).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PipelineSpec:
    id: str
    title: str
    modules: tuple[str, ...]
    entry: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    tools: dict[str, tuple[str, ...]]  # operación -> herramientas externas necesarias
    attributes: tuple[str, ...]  # atributos de AC que produce o necesita
    transforms: tuple[str, ...]
    validations: tuple[str, ...]
    limitations: tuple[str, ...]
    standalone: bool
    repeatable: str  # evidencia de repetibilidad o «no verificado»
    writes: str  # dónde escribe (siempre vía safe_paths) o «nada»
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PIPELINES: tuple[PipelineSpec, ...] = (
    PipelineSpec(
        "geometry",
        "Geometría y mallas (DAE → KN5)",
        (
            "src/mesh/dae.py",
            "src/mesh/flexbody.py",
            "src/mesh/plan.py",
            "src/mesh/build.py",
        ),
        "python -m src.mesh.build <carpeta> <pc> --out … [--plan-only]",
        (
            "carpeta o zip de vehículo BeamNG (JBeam + .pc; plan_for_zip sin extraer)",
            "DAE con `<unit>` y `<up_axis>` (cm, Z arriba en Automation)",
        ),
        (
            "plan.json",
            "`<car>.kn5` (jerarquía WHEEL_xx/SUSP_xx/COCKPIT_HR con Empties)",
        ),
        {"plan": (), "build_kn5": ()},
        ("ac:car.ini/BASIC/GRAPHICS_OFFSET (origen del KN5)",),
        (
            "flexbody: pos + Rx·Ry·Rz·diag(scale)·(unidad·v)",
            "ejes: la escena va en ejes BeamNG; el constructor de KN5 convierte (x, z, −y)",
        ),
        (
            "verify_kn5 (posiciones ±0,01 m frente al plan)",
            "kn5_inspect",
            "tests/test_mesh_*.py",
        ),
        (
            "sin mallas skinned",
            "lee .dae de carpeta o de zip (plan_for_zip; "
            "los DAE de common.zip solo a petición)",
            "partición de mallas > 65 536 vértices por el addon",
            "LOD único; collider aparte",
        ),
        True,
        "KN5 idéntico byte a byte entre ejecuciones (fase C: phaseC/run1 = run2)",
        "output/<car>/mesh",
    ),
    PipelineSpec(
        "materials_textures",
        "Materiales y texturas",
        ("src/ac/kn5_materials.py", "src/ac/assemble.py (materials_json)"),
        "assemble (paso de texturas) / kn5_materials.write_with_diffuse",
        (
            "<vehículo>.materials.json de BeamNG",
            "texturas .dds del vehículo",
            "KN5 generado",
        ),
        ("KN5 con texturas difusas embebidas",),
        {"diffuse": ()},
        (),
        ("color/textura difusa por material (v7)",),
        ("validate_references (texturas referenciadas)",),
        (
            "solo difuso: normal, rugosidad y PBR sin equivalencia "
            "(incidencia por material)",
            "BC7 sin verificar",
        ),
        True,
        "idéntico byte a byte dentro del ensamblado (fase C)",
        "dentro del paquete ensamblado",
    ),
    PipelineSpec(
        "engine",
        "Motor y curva de potencia",
        (
            "src/extract/engine_camso.py",
            "src/ac/engine.py",
            "src/ac/coast_ref.py",
            "src/extract/engine_combustion.py",
        ),
        "python -m src.ac.engine <carpeta> <pc> [--out …]",
        (
            "powertrain camsoEngine + piezas EngineManagement",
            "camsoEngine.lua (semántica)",
        ),
        ("engine.ini (borrador)", "power.lut", "engine_report.json"),
        {"run": ()},
        ("ac:power.lut", "ac:engine.ini/ENGINE_DATA/*", "ac:engine.ini/COAST_REF/*"),
        (
            "par neto = fila 99 − fricción (boost incluido; sin [TURBO_n])",
            "COAST_REF lineal desde 0",
        ),
        (
            "compare_with_info (info_*.json)",
            "validations_bng.engine_curve",
            "tests/test_engine_*.py",
        ),
        (
            "INI solo para camsoEngine (Automation); combustionEngine: adaptador de "
            "solo lectura conectado al IR desde G2.9 (curva neta; turbo = estimación "
            "del juego, «estimated»; sin compresor/N2O ni freno motor; sin slots2; "
            "sin reglas de AC)",
            "inercia sin la del embrague",
            "retraso del turbo fuera de alcance",
        ),
        True,
        "determinista (pruebas de valores de referencia)",
        "solo con --out",
    ),
    PipelineSpec(
        "drivetrain",
        "Transmisión, diferencial y embrague",
        (
            "src/extract/drivetrain.py",
            "src/extract/traction.py",
            "src/ac/drivetrain.py",
        ),
        "python -m src.ac.drivetrain <carpeta> <pc> [--out …]",
        ("grafo powertrain (frictionClutch, manualGearbox, shaft, differential)",),
        ("drivetrain.ini (borrador)", "drivetrain_report.json"),
        {"run": ()},
        (
            "ac:drivetrain.ini/TRACTION/TYPE",
            "ac:drivetrain.ini/GEARS/*",
            "ac:drivetrain.ini/DIFFERENTIAL/PRELOAD",
            "ac:drivetrain.ini/GEARBOX/VALID_SHIFT_RPM_WINDOW",
            "ac:drivetrain.ini/CLUTCH/MAX_TORQUE",
        ),
        (
            "tracción por el grafo",
            "lsdPreload 'N/m' leído como N·m (supuesto)",
            "lockTorque por defecto (frictionClutch.lua:174)",
        ),
        (
            "compare_with_info (tracción, caja, velocidad punta)",
            "validate_structure (ventana de cambio > 0)",
        ),
        (
            "solo FWD/RWD",
            "POWER/COAST del diferencial, tiempos de cambio y autoembrague sin regla",
        ),
        True,
        "determinista",
        "solo con --out",
    ),
    PipelineSpec(
        "suspension",
        "Suspensión",
        (
            "src/extract/suspension.py",
            "src/ac/chassis.py",
            "src/attrs/candidates_dwb.py",
        ),
        "python -m src.ac.chassis <carpeta> <pc> [--out …]",
        ("nodos, vigas, coilovers (por propiedades, G2.14), torsionbars",),
        (
            "suspensions.ini (borrador: WHEELBASE, TRACK, TYPE=STRUT)",
            "candidatos DWB (informe de atributos)",
        ),
        {"run": ()},
        ("ac:suspensions.ini/*",),
        (
            "tipo por etiqueta declarada (MacPherson → STRUT)",
            "DWB: puntos = nodo − centro de rueda (ejes AC)",
        ),
        ("load_requirements ([REAR] TYPE)", "simetría izquierda/derecha del DWB"),
        (
            "muelles/amortiguadores en rueda dependen de la palanca (hipótesis)",
            "DWB trasero con rótulas dobles sin representación",
            "eje torsional sin tipo equivalente",
        ),
        True,
        "determinista",
        "solo con --out",
    ),
    PipelineSpec(
        "tyres",
        "Neumáticos",
        ("src/extract/brakes_tyres.py", "src/ac/chassis.py"),
        "python -m src.ac.chassis … (tyres.ini)",
        ("pressureWheels (radius, hubRadius, tireWidth, pressurePSI)",),
        ("tyres.ini (borrador: geometría y presión)",),
        {"run": ()},
        ("ac:tyres.ini/*",),
        ("radio sin carga", "presión en psi"),
        ("contraste con la etiqueta 215/45/17",),
        ("modelo de agarre de AC sin equivalente (96 claves)",),
        True,
        "determinista",
        "solo con --out",
    ),
    PipelineSpec(
        "brakes",
        "Frenos",
        ("src/extract/brakes_tyres.py", "src/ac/chassis.py"),
        "python -m src.ac.chassis … (brakes.ini)",
        ("brakeTorque, brakeInputSplit, brakeSplitCoef, parkingTorque por rueda",),
        ("brakes.ini (borrador: FRONT_SHARE)",),
        {"run": ()},
        ("ac:brakes.ini/DATA/*",),
        ("reparto a pedal 100 %",),
        (),
        (
            "AC no modela el codo de BeamNG",
            "MAX_TORQUE = rueda delantera + rueda trasera confirmado en AC "
            "(convenciones_fisicas_2026-10-26.md §2); sin aplicar (decisión D1)",
        ),
        True,
        "determinista",
        "solo con --out",
    ),
    PipelineSpec(
        "aero",
        "Aerodinámica",
        ("src/extract/aero.py",),
        "dentro de src.ac.chassis (informe)",
        ("cefaero (dragModel, downforceModel, downforceNodeGroups)", "cefaero.lua"),
        ("Cd·A, Cl·A y puntos en el informe; aero.ini mínimo de diagnóstico",),
        {"run": ()},
        ("ac:aero.ini/*",),
        ("Cl·A = F_obj / (½·1,225·v²)",),
        ("load_requirements (WING_n y DATA)",),
        (
            "CL > 0 = carga confirmado en AC (convenciones_fisicas §3); "
            "representación [DATA]/WING_n sin decidir",
            "solo cefaero (Automation)",
        ),
        True,
        "determinista",
        "nada (informe)",
    ),
    PipelineSpec(
        "cameras",
        "Cámaras",
        ("src/ac/cameras.py", "src/ac/cockpit_config.py"),
        "assemble (paso de cámaras)",
        ("KN5 generado", "camerasInternal de BeamNG (DRIVEREYES)"),
        ("car.ini [GRAPHICS] DRIVEREYES/BONNET/BUMPER", "dash_cam.ini"),
        {"run": ()},
        ("ac:car.ini/GRAPHICS/*",),
        (
            "reglas medidas en 5 referencias (medianas constantes)",
            "marco del cuerpo: KN5 − GRAPHICS_OFFSET",
        ),
        ("cámaras validadas en el juego (v17cam/v18bu)",),
        ("constantes de referencia de coches Kunos",),
        True,
        "idéntico byte a byte (fase C)",
        "dentro del paquete ensamblado",
    ),
    PipelineSpec(
        "driver",
        "Posición del conductor, manos y volante",
        (
            "src/ac/driver_pose.py",
            "src/ac/hands.py",
            "src/ac/steer_wheel.py",
            "src/ac/steer_anim.py",
        ),
        "assemble --ac-root … (pose y manos)",
        (
            "driver.kn5 local de Kunos (solo lectura)",
            "perfil del vehículo (caderas, nudillos, materiales del volante)",
        ),
        ("driver3d.ini", "driver_base_pos.knh", "steer.ksanim", "KN5 con STEER_HR"),
        {"pose": ("ac_root",)},
        ("ac:driver3d.ini/*",),
        ("IK de la pose", "agarre alterno de 125°"),
        ("validate_references (huesos del .knh)",),
        ("la pose y las manos derivan de archivos de Kunos: NO distribuibles",),
        True,
        "idéntico byte a byte (fase C)",
        "dentro del paquete ensamblado",
    ),
    PipelineSpec(
        "lights",
        "Iluminación",
        ("src/ac/lights.py",),
        "assemble (paso de luces)",
        ("glowMap de BeamNG", "materials.json", "KN5"),
        (
            "lights.ini",
            "extensión CSP (intermitentes, marcha atrás)",
            "KN5 con mallas de luz renombradas",
        ),
        {"run": ()},
        (),
        ("función de luz por glowMap", "color 'on' del material"),
        ("validate_references (nombres de luces)",),
        ("precondiciones del perfil (19 materiales en el Ibiza)",),
        True,
        "idéntico byte a byte (fase C)",
        "dentro del paquete ensamblado",
    ),
    PipelineSpec(
        "audio",
        "Audio y bancos de sonido",
        (
            "tools/variants/build_v16sn.py",
            "tools/variants/build_phaseD_audio.py",
            "docs/research/audio_pipeline_contrato.md",
        ),
        "--audio none|prebuilt|build (contrato; prebuilt implementado como variante)",
        ("banco precompilado + GUIDs.txt", "o muestras + plantilla del SDK (build)"),
        ("`sfx/<id>.bank`", "`sfx/GUIDs.txt`", "data/sounds.ini"),
        {"none": (), "prebuilt": (), "build": ()},
        (),
        ("recorte de GUIDs.txt",),
        ("validate_references (banco = carpeta, bank:/<id>)",),
        (
            "cambiar el id de la carpeta obliga a recompilar",
            "audio de BeamNG: licencia sin verificar",
        ),
        True,
        "banco precompilado copiado byte a byte (fase D)",
        "dentro del paquete ensamblado",
        optional=True,
    ),
    PipelineSpec(
        "assembly",
        "Ensamblado, metadatos y validación",
        (
            "src/ac/assemble.py",
            "src/ac/car_validate.py",
            "src/ac/physics_base.py",
            "src/safe_paths.py",
        ),
        "python -m src.ac.assemble <carpeta> <pc> [--physics-base "
        "…] [--ac-root …] [--overwrite]",
        (
            "artefactos de engine/drivetrain/chassis o base física congelada",
            "KN5 y perfil del vehículo",
        ),
        ("carpeta de coche de AC + manifest.json + assembly_report.json",),
        {"run": (), "pose": ("ac_root",)},
        ("ac:car.ini/*", "ac:setup.ini/*"),
        ("TOTALMASS con piloto (generador antiguo)",),
        (
            "validate_structure",
            "validate_references",
            "changed_protected",
            "manifiesto con sha256",
        ),
        (
            "nunca instala en AC",
            "física jugable copiada de la base congelada (no generada)",
        ),
        True,
        "determinista (phaseB/run1 = run2; phaseC/run1 = run2)",
        "output/<car>/assembly (rechaza sobrescribir)",
    ),
    PipelineSpec(
        "physics_preview",
        "Vista previa de física en memoria (adaptador de esta fase)",
        ("src/pipeline/physics_preview.py",),
        "python -m src.attrs resolve …",
        ("carpeta o zip (+ common.zip) de BeamNG",),
        ("objetos en memoria e INI en memoria; ningún archivo",),
        {"run": ()},
        ("todos los de engine/drivetrain/suspension/tyres/brakes",),
        ("los mismos que los pipelines de física",),
        (
            "ok solo si todos los pasos obligatorios terminan",
            "reproduce inventory.json run3",
        ),
        ("misma cobertura que los pipelines que envuelve",),
        True,
        "determinista (tests/test_attrs_vehicles.py)",
        "nada",
    ),
)


def spec(pipeline_id: str) -> PipelineSpec:
    for s in PIPELINES:
        if s.id == pipeline_id:
            return s
    raise KeyError(pipeline_id)


# --- herramientas externas (solo rutas, nunca se ejecutan) ---------------------------


def default_ac_root() -> Path | None:
    from src.project.export import default_ac_root as _d

    return _d()


def tool_status(ac_root: Path | None = None) -> dict[str, dict[str, Any]]:
    ac = Path(ac_root) if ac_root else (default_ac_root() or Path(""))
    return {
        "ac_root": {
            "available": (ac / "content").is_dir(),
            "path": str(ac),
            "use": "solo lectura",
        },
    }


def available_operations(
    pipeline: PipelineSpec, tools: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    out = {}
    for op, needed in pipeline.tools.items():
        missing = [t for t in needed if not tools.get(t, {}).get("available")]
        out[op] = {"available": not missing, "missing": missing}
    return out


def audio_capabilities(
    tools: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tools = tools or tool_status()
    ops = available_operations(spec("audio"), tools)
    status = (
        "banco de sonido propio incluido; los bancos precompilados también se aceptan"
    )
    return {"operations": ops, "status": status}


# --- adaptador de geometría


def geometry_plan_summary(
    vehicle_dir: Path | None = None,
    pc: Path | None = None,
    vehicle_zip: Path | None = None,
) -> dict[str, Any]:
    """Plan de escena sin escritura: objetos, Empties, unidades, avisos.
    Fuente: carpeta + ``.pc`` o ``vehicle_zip`` (leído en memoria, sin extraer)."""
    from src.mesh.build import plan_for_vehicle, plan_for_zip

    if vehicle_zip is not None:
        plan = plan_for_zip(vehicle_zip, pc_name=Path(pc).name if pc else None)
    else:
        plan = plan_for_vehicle(vehicle_dir, pc)
    return {
        "objects": len(plan.objects),
        "vertices": sum(o.vertex_count for o in plan.objects),
        "empties": sorted(e.name for e in plan.empties),
        "wheels": sorted(plan.wheel_centers),
        "unit_meter": plan.unit_meter,
        "up_axis": plan.up_axis,
        "issues": list(plan.issues),
        "parents": sorted({o.parent for o in plan.objects if o.parent}),
    }


__all__ = [
    "PIPELINES",
    "PipelineSpec",
    "audio_capabilities",
    "available_operations",
    "geometry_plan_summary",
    "spec",
    "tool_status",
    "field",
]
