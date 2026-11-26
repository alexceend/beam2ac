"""Etapa ``model`` (fase B2): KN5 del coche, collider y CSP desde la geometría.

Reutiliza, sin cambiarlos, los pasos del ensamblador validados en el juego
(``src.ac.assemble``; docs/research/informe_integracion_ensamblador_2026-10-25.md):

1. plan de mallas del origen (carpeta o zip) y origen de Kunos
   (``kunos_origin_offset``: ruedas en Y = 0, caja centrada en X/Z);
2. nulos ``COCKPIT_LR``/``STEER_LR`` y ``STEER_HR`` vacíos (fase 7.1);
3. KN5 en Python puro (``src.mesh.build.build_kn5``) y verificación contra el plan;
4. texturas difusas (``kn5_materials``, v7); la pintura por defecto de la
   configuración (``src/extract/paints.py``: reglas de BeamNG para
   ``defaultPaintName1..3``) solo se exige a los materiales ``instanceDiffuse``;
5. con perfil del vehículo (``src/ac/vehicle_profiles/<vehicle_id>.json``, solo el del
   propio vehículo): volante bajo ``STEER_HR`` (v12), luces (v15li) y collider con
   perfil (v7). Sin perfil, collider = caja envolvente de la carrocería;
6. ``ext_config.ini`` de CSP para los materiales a doble cara (v8ic).

Con un zip, los ``.dae``, ``*.materials.json`` y texturas del vehículo se extraen a
una carpeta temporal (el constructor de mallas lee archivos); el zip no se modifica.

No hace: pose del piloto ni manos (derivan de archivos de Kunos: no distribuibles),
sonido, retrovisores.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from src.safe_paths import ensure_safe_output_path

# extensiones que el modelo o las texturas necesitan; el resto del zip no se extrae
# G2.29: + materials.cs (exportador 200409), paints.meta y la .pc (pintura elegida): sin
# ellos un zip salía sin texturas o sin pintura y la misma carpeta sí las tenía
EXTRACT_SUFFIXES = (".dae", ".json", ".dds", ".png", ".cs", ".meta", ".pc")
MODEL_KN5 = "model.kn5"
MODEL_OPTIONS_FILE = "model_options.json"  # opciones del modelo elegidas en el exportador (mapas normales, animaciones)
WHEEL_FILE = "steering_wheel.json"  # elección del usuario (material del aro); ver la ventana «Volante»
MAX_ZIP_ENTRIES = 20000  # tope de entradas de un zip que se recorre
MAX_EXTRACT_BYTES = 4 << 30  # tope de lo que se extrae de un zip (bomba de compresión)


class ModelError(RuntimeError):
    """No se puede construir el modelo 3D."""


def wheel_override_of(project: Any) -> dict[str, Any] | None:
    """Ajuste de volante del usuario (``<proyecto>/steering_wheel.json``) o ``None`` = SIN volante (defecto).

    ``{"enabled": true, "rim_material": opcional}``. Un archivo antiguo solo con ``rim_material`` cuenta como activado.
    """
    f = Path(project.root) / WHEEL_FILE
    if not f.is_file():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    return data if data.get("enabled", bool(data.get("rim_material"))) else None


def clean_lights(raw: Any) -> dict[str, str | None]:
    """Luces elegidas por el usuario: ``{material: función}`` (``HEADLIGHT``, ``TAIL``, ``BRAKE``,
    ``REVERSE``, ``SIGNAL_L``, ``SIGNAL_R``) o ``None`` = «este material no es una luz». Lo no válido se descarta.
    """
    from src.ac.lights import LIGHT_FUNCTIONS

    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in raw.items()
        if isinstance(k, str)
        and 0 < len(k) <= 200
        and (v is None or v in LIGHT_FUNCTIONS)
    }


def model_options_of(project: Any) -> dict[str, Any]:
    """``<proyecto>/model_options.json``: ``normal_maps`` (bool) y ``part_animations`` (lista de
    ``{kind, node, pivot, axis, angle_deg}``, ver ``src/ac/part_anim.py``). Sin archivo o con un archivo
    mal formado: todo desactivado (nunca falla)."""
    off: dict[str, Any] = {"normal_maps": False, "part_animations": [], "lights": {}}
    f = Path(project.root) / MODEL_OPTIONS_FILE
    if not f.is_file():
        return off
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return off
    if not isinstance(data, dict):
        return off
    anims = data.get("part_animations")
    return {
        "lights": clean_lights(data.get("lights")),
        "normal_maps": data.get("normal_maps") is True,
        "part_animations": (
            [a for a in anims if isinstance(a, dict)] if isinstance(anims, list) else []
        ),
    }


def tool_inputs(_project: Any = None) -> dict[str, Any]:
    """Huella de herramientas y perfiles (entra en la clave de la etapa).

    Sin ejecutar nada: sha256 de los perfiles de vehículo y las opciones del modelo.
    """
    info: dict[str, Any] = {}
    if (
        _project is not None
    ):  # la elección del volante cambia el modelo: entra en la clave
        info["steering_wheel_override"] = wheel_override_of(_project)
        info["model_options"] = model_options_of(_project)
    from src.ac.assemble import PROFILE_DIR

    info["profiles"] = {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(PROFILE_DIR.glob("*.json"))
    }
    return info


def profile_for(vehicle_id: str) -> tuple[dict | None, str | None, str | None]:
    """Perfil del propio vehículo (o ninguno) y su sha256. Nunca el de otro coche."""
    from src.ac.assemble import PROFILE_DIR, load_profile

    profile, path = load_profile(None, vehicle_id)
    if path is None:
        return None, None, None
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    rel = Path(path).relative_to(PROFILE_DIR.parents[2]).as_posix()
    return profile, rel, digest


def extract_vehicle(
    zip_path: Path,
    prefix: str,
    dest: Path,
    *,
    suffixes: frozenset[str] | None = EXTRACT_SUFFIXES,
    root_name: str = "vehicle",
) -> Path:
    """Extrae los archivos del vehículo (sin tocar el zip). Por defecto solo los que
    necesita el modelo; ``suffixes=None`` extrae todos (G2.47: bancos de BeamNG). Nunca
    sobrescribe, rechaza enlaces simbólicos y miembros fuera de la carpeta."""
    root = dest / root_name
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        if len(zf.namelist()) > MAX_ZIP_ENTRIES:
            raise ModelError(f"el zip tiene más de {MAX_ZIP_ENTRIES} entradas")
        for name in sorted(zf.namelist()):
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel = PurePosixPath(name[len(prefix) :])
            if ".." in rel.parts or (
                suffixes is not None and rel.suffix.lower() not in suffixes
            ):
                continue
            if (zf.getinfo(name).external_attr >> 16) & 0o170000 == 0o120000:
                raise ModelError(f"enlace simbólico en el zip: {name!r}")
            target = root.joinpath(*rel.parts)
            # G2.29: un miembro con «\» o letra de unidad escaparía de root en Windows
            if not target.resolve().is_relative_to(root.resolve()):
                raise ModelError(f"miembro del zip fuera del vehículo: {name!r}")
            total += zf.getinfo(name).file_size
            if total > MAX_EXTRACT_BYTES:
                raise ModelError("el zip descomprimido supera el límite de 4 GiB")
            if target.exists():
                raise ModelError(f"miembro duplicado en el zip: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            # el tamaño declarado puede mentir: el tope se aplica a los bytes leídos
            total -= zf.getinfo(name).file_size
            with zf.open(name) as src, target.open("wb") as out:
                while chunk := src.read(1 << 20):
                    total += len(chunk)
                    if total > MAX_EXTRACT_BYTES:
                        raise ModelError(
                            "el zip descomprimido supera el límite de 4 GiB"
                        )
                    out.write(chunk)
    return root


def remap_plan_paths(plan: Any, old_prefix: str, new_root: Path) -> Any:
    """Sustituye las rutas ``zip!/vehicles/<id>/...`` del plan por las extraídas."""
    from dataclasses import replace

    def fix(p: str) -> str:
        if not p.startswith(old_prefix):
            raise ModelError(f"malla fuera del vehículo: {p}")
        rel = PurePosixPath(p[len(old_prefix) :].replace("\\", "/"))
        return str(new_root.joinpath(*rel.parts))

    return replace(
        plan,
        objects=[replace(o, dae=fix(o.dae)) for o in plan.objects],
        unit_meter={fix(k): v for k, v in plan.unit_meter.items()},
        up_axis={fix(k): v for k, v in plan.up_axis.items()},
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_vehicle_paints(source: Any, vehicle_dir: Path, pc_path: Path) -> Any:
    """Pinturas del vehículo con procedencia (``src.extract.paints``)."""
    from src.extract.paints import read_paints
    from src.jbeam import lax_json

    pc_data = None
    if pc_path.is_file():
        try:
            pc_data = lax_json.load(pc_path).value
        except Exception:  # la .pc ya la validó la etapa source
            pc_data = None
    meta = vehicle_dir / "paints.meta"
    return read_paints(
        source.model_info,
        source.info,
        pc_data if isinstance(pc_data, dict) else None,
        meta.read_bytes() if meta.is_file() else None,
        config_info_name=f"info_{source.variant}.json",
        pc_name=pc_path.name,
    )


def build_model(
    source: Any,
    resolved: Any,
    project_source: Any,
    out: Path,
    wheel_override: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construye el modelo en ``out`` (carpeta de la etapa). Ver el docstring."""
    from src.ac import cockpit_config
    from src.ac.assemble import materials_json, write_collider
    from src.ac.collider_profile import write_profile_collider
    from src.ac.kn5_materials import (
        diffuse_sources,
        load_materials,
        paint_dependent_materials,
        split_kn5,
        upper_layer_paint,
        write_with_diffuse,
    )
    from src.ac.lights import LightsError, apply_lights, light_functions
    from src.ac.steer_detect import detect_steering_wheel
    from src.ac.steer_wheel import SteerWheelError, rig_steering_wheel
    from src.coords import beamng_to_ac
    from src.jbeam import vehicle_dirs_in_zip
    from src.mesh.build import build_kn5, plan_for_vehicle, plan_for_zip
    from src.mesh.plan import (
        add_cockpit_lr_nulls,
        add_steer_hr_diagnostic_nulls,
        kunos_origin_offset,
        translate_plan,
    )

    s = project_source
    report: dict[str, Any] = {"warnings": []}
    warn = report["warnings"].append
    profile, profile_path, profile_sha = profile_for(source.vehicle_id)
    report["profile"] = {"path": profile_path, "sha256": profile_sha}

    with tempfile.TemporaryDirectory(prefix="beam2ac_model_") as tmp:
        if s.kind == "dir":
            vehicle_dir = Path(s.path)
            pc_path = vehicle_dir / s.pc
            plan = plan_for_vehicle(vehicle_dir, pc_path)
        else:
            zp = Path(s.path)
            prefix = vehicle_dirs_in_zip(zp)[0]
            plan = plan_for_zip(zp, Path(s.common_zip) if s.common_zip else None, s.pc)
            vehicle_dir = extract_vehicle(zp, prefix, Path(tmp))
            plan = remap_plan_paths(plan, f"{zp}!/{prefix}", vehicle_dir)
            pc_path = vehicle_dir / f"{source.variant}.pc"
        offset = kunos_origin_offset(plan)
        offset_ac = beamng_to_ac(offset)
        tplan = add_steer_hr_diagnostic_nulls(
            add_cockpit_lr_nulls(translate_plan(plan, offset))
        )
        report["origin"] = {"offset_beamng": offset, "offset_ac": offset_ac}
        report["plan_issues"] = list(plan.issues)
        # G2.24: nombres de material del DAE (se recortan a 63 bytes; ver
        # src.ac.kn5_materials.material_key)
        from src.mesh.dae import material_names

        dae_materials: set[str] = set()
        for dae_path in sorted({o.dae for o in plan.objects}):
            if Path(dae_path).is_file():  # DAE de common.zip sin extraer: se omite
                dae_materials |= material_names(dae_path)

        # 1. KN5 base (Python puro) y verificación contra el plan
        build_dir = ensure_safe_output_path(out / "_build")
        base_kn5, verification = build_kn5(tplan, build_dir, MODEL_KN5)
        report["verification"] = {
            "ok": verification.ok,
            "errors": verification.errors,
            "base_sha256": _sha(base_kn5),
        }
        current = base_kn5
        ensure_safe_output_path(out / "_stages").mkdir()

        # 2. texturas difusas. La pintura (src/extract/paints.py) solo se exige a los
        # materiales instanceDiffuse; sin ella esos quedan sin textura y el resto se
        # texturiza igual (antes, sin pintura, se perdían todas las texturas, las
        # luces y CSP).
        paint_report = read_vehicle_paints(source, vehicle_dir, pc_path)
        sel = paint_report.selected(1)
        report["paint"] = sel.name if sel and sel.resolved else None
        report["paint_resolution"] = {
            "selection": paint_report.to_json()["selection"],
            "diagnostics": [d.__dict__ for d in paint_report.diagnostics],
        }
        try:
            mj = materials_json(vehicle_dir, pc_path)
        except Exception as exc:
            mj = None
            report["textures"] = None
            warn(f"sin texturas (el coche se verá negro): {type(exc).__name__}: {exc}")
        if mj is not None:
            try:
                parts = split_kn5(current.read_bytes())
                names = [m.name for m in parts.materials]
                needs_paint = paint_dependent_materials(names, mj, dae_materials)
                paint_rgb = sel.paint.rgb if sel and sel.paint else None
                if needs_paint and paint_rgb is None:
                    warn(
                        f"{len(needs_paint)} materiales dependen de la pintura "
                        f"(instanceDiffuse) y la configuración no la determina "
                        f"({sel.origin if sel else 'sin selección'}): quedan sin "
                        "textura; elegir una es una decisión de apariencia pendiente"
                    )
                upper = upper_layer_paint(names, mj, dae_materials)
                if upper:
                    # G2.24 (decisión del usuario): capa 0; la mezcla de capas de
                    # BeamNG no está verificada
                    warn(
                        f"{len(upper)} materiales tienen la "
                        f"pintura (instanceDiffuse) en "
                        "una capa superior; se usa el color de la capa 0 y la capa de "
                        "pintura no se interpreta (mezcla de capas sin verificar): "
                        f"{sorted(upper)[:6]}"
                    )
                try:
                    lens_funcs = light_functions(
                        resolved, [], None if profile else (options or {}).get("lights")
                    )
                    light_mats = set(lens_funcs)
                except LightsError:
                    lens_funcs, light_mats = {}, set()
                sources = diffuse_sources(
                    names,
                    mj,
                    vehicle_dir,
                    paint_rgb,
                    dae_materials,
                    opaque_materials=light_mats,
                    lens_functions=None if profile else lens_funcs,
                    decals=not profile,
                    normal_maps=bool((options or {}).get("normal_maps")),
                )
                dst = ensure_safe_output_path(out / "_stages" / f"textured_{MODEL_KN5}")
                current = write_with_diffuse(current, dst, sources)
                report["textures"] = {
                    "materials": len(sources),
                    "distinct_textures": len(
                        {x.texture_name for x in sources if x.texture_name}
                    ),
                    "kinds": {
                        k: sum(x.kind == k for x in sources)
                        for k in sorted({x.kind for x in sources})
                    },
                    "paint_dependent": len(needs_paint),
                    "upper_layer_paint": len(upper),
                    "materials_source": mj.name,
                }
            except Exception as exc:  # el modelo sigue siendo válido sin texturas
                report["textures"] = None
                warn(
                    f"sin texturas (el coche se verá negro): "
                    f"{type(exc).__name__}: {exc}"
                )

        # 3. volante: del perfil del propio vehículo o, sin perfil, detectado por geometría
        # (steer_detect.py: aro delante de los ojos del piloto); luces: solo con perfil
        dst = ensure_safe_output_path(out / "_stages" / f"steer_{MODEL_KN5}")
        profile_wheel_done = False
        if profile and profile.get("steering_wheel"):
            try:
                report["steering_wheel"] = rig_steering_wheel(
                    current, dst, profile["steering_wheel"]
                )
                current = dst
                profile_wheel_done = True
            except SteerWheelError as exc:
                # otra exportación del mismo coche (p. ej. sin la malla del volante activa): el perfil no encaja
                report["steering_wheel"] = None
                warn(
                    f"el perfil del vehículo no encaja con este modelo (volante sin montar: {exc}); "
                    "se intenta detectarlo por geometría"
                )
        if profile_wheel_done:
            pass
        elif wheel_override is None:
            # defecto: sin volante (casilla «Volante» sin marcar) ⇒ sin STEER_HR, sin manos ni piloto
            report["steering_wheel"] = None
            warn("sin volante: la casilla «Volante» del proyecto no está marcada")
        else:
            report["steering_wheel"] = None
            chosen = wheel_override.get(
                "rim_material"
            )  # elección del usuario (vacío: detección automática)
            try:
                wheel = detect_steering_wheel(
                    current,
                    cockpit_config.driver_eyes(resolved, offset_ac).eyes_ac,
                    chosen,
                )
                report["steering_wheel"] = rig_steering_wheel(current, dst, wheel)
                report["steering_wheel"]["detected"] = wheel["detected"]
                report["steering_wheel"]["rim_material"] = wheel["rim_material_token"]
                current = dst
                warn(
                    (
                        "volante elegido por el usuario: "
                        if chosen
                        else "volante detectado por geometría (sin perfil del vehículo): "
                    )
                    + f"{wheel['rim_material_token']}; sentido de giro sin probar en AC"
                )
            except (
                ValueError,
                SteerWheelError,
                cockpit_config.CockpitConfigError,
            ) as exc:
                warn(f"volante sin montar bajo STEER_HR (sin perfil; detección: {exc})")
        lights_res = None
        lights_profile = (profile or {}).get("lights")
        generic_lights = False
        glowmap_failed = False
        if not lights_profile and mj is not None:
            # sin perfil del vehículo: luces desde el glowMap con un perfil GENERADO (sin atenuar testigos
            # interiores ni comprobar el nombre de las mallas). Experimental, sin probar en AC.
            try:
                skipped: list[str] = []
                funcs = light_functions(
                    resolved, skipped, (options or {}).get("lights")
                )
                for why in skipped:
                    warn(f"luz sin generar (glowMap): {why}")
            except LightsError as exc:
                funcs = {}
                glowmap_failed = True
                warn(f"luces sin generar (glowMap): {exc}")
            if funcs:
                lights_profile = {
                    "expected_count": len(funcs),
                    "source_mesh_name": None,
                    "interior_z": [1e9, -1e9],
                }
                generic_lights = True
        if lights_profile and mj is not None:
            try:
                lights_res = apply_lights(
                    current.read_bytes(),
                    light_functions(
                        resolved, [], None if profile else (options or {}).get("lights")
                    ),
                    load_materials(mj),
                    lights_profile,
                )
            except LightsError as exc:
                if not generic_lights:
                    raise ModelError(f"luces: {exc}") from None
                warn(f"luces sin generar (perfil generado): {exc}")
        if lights_res is not None:
            dst = ensure_safe_output_path(out / "_stages" / f"lights_{MODEL_KN5}")
            dst.write_bytes(lights_res.kn5)
            current = dst
            ensure_safe_output_path(out / "lights.ini").write_text(
                lights_res.lights_ini, encoding="utf-8"
            )
            report["lights"] = {
                "count": len(lights_res.lights),
                "generated_profile": generic_lights,
            }
        else:
            report["lights"] = None
            if not generic_lights and lights_profile is None and not glowmap_failed:
                warn(
                    "sin luces: el glowMap no marca ninguna (o falta materials.json); "
                    "asígnalas a mano en «Luces» del exportador"
                )
        final = ensure_safe_output_path(out / MODEL_KN5)
        shutil.copyfile(current, final)
        report["kn5_sha256"] = _sha(final)
        report["materials"] = sorted(
            {m.name for m in split_kn5(final.read_bytes()).materials}
        )

        # 4. collider: con perfil, medido en el KN5 base; si no, caja envolvente
        collider = ensure_safe_output_path(out / "collider.kn5")
        if profile and profile.get("collider"):
            report["collider"] = write_profile_collider(
                base_kn5, collider, profile["collider"]
            )
            report["collider"].pop("source_kn5", None)  # ruta: no reproducible
        else:
            report["collider"] = write_collider(tplan, collider)
            warn("collider = caja envolvente de la carrocería (sin perfil)")

        # 5. CSP: materiales a doble cara (+ secciones de luces)
        ext = ""
        if mj is not None:
            double = cockpit_config.double_sided_materials(
                report["materials"], mj, dae_materials
            )
            if double:
                ext = cockpit_config.render_ext_config(double, mj.name)
            report["double_sided_materials"] = len(double)
            # alerón y piezas aerodinámicas finas: caras traseras reales (sin CSP la hoja desaparece desde un lado)
            from src.ac.double_sided import add_back_faces

            new_kn5, back = add_back_faces(final.read_bytes(), set(double))
            if back:
                ensure_safe_output_path(final).write_bytes(new_kn5)
                report["kn5_sha256"] = _sha(final)
                report["double_sided_geometry"] = sorted(set(back))
        if lights_res is not None:
            ext += lights_res.csp_emissive
        if ext:
            ensure_safe_output_path(out / "ext_config.ini").write_text(
                ext, encoding="utf-8"
            )

        # 5a. animaciones de piezas configuradas por el usuario (src/ac/part_anim.py)
        report["part_animations"] = None
        items = (options or {}).get("part_animations") or []
        if items:
            from src.ac import part_anim

            try:
                anim_files = part_anim.build_files(final, items)
                adir = out / "animations"
                for name, data in anim_files.items():
                    ensure_safe_output_path(adir / name).parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    ensure_safe_output_path(adir / name).write_bytes(data)
                report["part_animations"] = sorted(anim_files)
            except part_anim.PartAnimError as exc:
                warn(f"animaciones de piezas sin generar: {exc}")

        # 5b. llamas del escape: salidas de las vigas isExhaust (src/ac/flames.py), marco del KN5 (hip)
        try:
            from src.ac import flames as fl
            from src.ac.kn5_inspect import read_kn5
            from src.extract.components import collect_beams
            from src.extract.nodes import collect_nodes
            from src.extract.wheels import collect_wheels

            nodes = collect_nodes(resolved)
            outlets = fl.exhaust_outlets(
                {n.id: n.pos for n in nodes},
                [
                    (b.nodes[0], b.nodes[1])
                    for b in collect_beams(resolved)
                    if b.props.get("isExhaust")
                ],
            )
            kn5w = {
                n.name[-2:]: tuple(n.translation)
                for n in read_kn5(final, keep_vertices=False).nodes
                if n.name.startswith("WHEEL_") and n.type == 1
            }
            dy = fl.vertical_shift(
                kn5w, {w.name: tuple(w.center) for w in collect_wheels(resolved, nodes)}
            )
            if outlets and dy is not None:
                text, info = fl.render_flames_ini(outlets, dy)
                ensure_safe_output_path(out / "flames.ini").write_text(
                    text, encoding="utf-8"
                )
                report["flames"] = {"count": len(info), "outlets": info}
            else:
                report["flames"] = None
                warn("sin llamas: no hay vigas isExhaust o ruedas para situarlas")
        except Exception as exc:  # las llamas son opcionales: nunca rompen el modelo
            report["flames"] = None
            warn(f"sin llamas ({type(exc).__name__}: {exc})")

        # 6. ojos del piloto (cámara de cabina de BeamNG, marco del KN5)
        try:
            eyes = cockpit_config.driver_eyes(resolved, offset_ac)
            report["drivereyes"] = {
                "camera": eyes.camera_name,
                "camera_beamng": eyes.camera_beamng,
                "eyes_ac": eyes.eyes_ac,
                "line": cockpit_config.drivereyes_line(eyes),
            }
        except cockpit_config.CockpitConfigError as exc:
            report["drivereyes"] = None
            warn(f"sin DRIVEREYES: {exc}")
    ensure_safe_output_path(out / "model.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return report
