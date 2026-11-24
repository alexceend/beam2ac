"""Construye la escena AC en Blender a partir del plan y exporta el KN5.

Se ejecuta DENTRO de Blender (3.6), sin interfaz:

    blender --background --factory-startup --python src/mesh/blender_build.py -- \
        <plan.json> <carpeta_salida> [--kn5 NOMBRE.kn5] [--save-blend]

Autónomo: solo usa ``bpy``/``mathutils`` y la biblioteca estándar (no importa el
paquete ``src``, que exige otra versión de Python). La carpeta de salida la valida
antes ``src.mesh.build`` con ``ensure_safe_output_path``; este script además se
niega a escribir dentro de ``sample_beamng``/``sample_assetto``/
``sample_assetto_unpacked``.

Pasos:
1. Escena vacía; activa el addon ``assetto_corsa_tools``.
2. Importa cada DAE del plan con el importador COLLADA de Blender, sin unidades
   (``import_units=False``): los vértices quedan en unidades del archivo y la
   unidad la aplica la matriz del plan. Comprueba que cada malla importada tiene
   matriz identidad y el número de vértices esperado.
3. Por cada objeto del plan: copia los datos de malla (no toca la importada),
   les aplica la matriz del plan (DAE → BeamNG en metros) y crea el objeto.
4. Crea los Empties ``WHEEL_xx``/``SUSP_xx`` con su matriz y cuelga de ellos las
   mallas (una malla nunca tiene hijos: limitación del addon).
5. Elimina las mallas importadas originales.
6. Verifica vértices y cajas en mundo frente al plan (tolerancia 0,1 mm).
7. Exporta con ``KN5FileWriter`` del addon (el operador abre ventanas emergentes).
   El addon convierte a AC con (x, z, −y).
Escribe ``build_log.json`` con todas las comprobaciones y avisos.
"""

import json
import os
import sys

import addon_utils
import bpy
from mathutils import Matrix

TOLERANCE = 1e-4  # m
# G2.18: Blender recorta los nombres de ID a 63 bytes (UTF-8) al importar; el plan
# guarda el nombre completo del nodo DAE (``mesh``). Mismo recorte que
# ``src.mesh.plan.blender_name`` (este script no importa ``src``).
BLENDER_NAME_BYTES = 63


def _blender_name(name):
    raw = name.encode("utf-8")
    if len(raw) <= BLENDER_NAME_BYTES:
        return name
    return raw[:BLENDER_NAME_BYTES].decode("utf-8", errors="ignore")


PROTECTED = ("sample_beamng", "sample_assetto", "sample_assetto_unpacked")


def _args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit(
            "uso: ... -- <plan.json> <carpeta_salida> [--kn5 X] [--save-blend]"
        )
    opts = {"plan": argv[0], "out": argv[1], "kn5": "model.kn5", "save_blend": False}
    rest = argv[2:]
    if "--kn5" in rest:
        opts["kn5"] = rest[rest.index("--kn5") + 1]
    opts["save_blend"] = "--save-blend" in rest
    out = os.path.abspath(opts["out"])
    if any(part in PROTECTED for part in out.replace("\\", "/").split("/")):
        raise SystemExit(f"salida dentro de una muestra de referencia: {out}")
    return opts


def _bbox(obj):
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return lo, hi


def _repair_normals(mesh):
    """Sustituye normales de esquina NaN/inf por la normal de su cara.

    Solo toca las esquinas inválidas (el DAE de la muestra trae 56 normales NaN en
    35b84_chassis1); el resto de normales personalizadas se conserva.
    """
    if not mesh.has_custom_normals:
        return 0
    mesh.calc_normals_split()
    normals = [tuple(loop.normal) for loop in mesh.loops]
    bad = 0
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            n = normals[li]
            if not all(c == c and abs(c) != float("inf") for c in n):
                normals[li] = tuple(poly.normal)
                bad += 1
    if bad:
        mesh.normals_split_custom_set(normals)
    mesh.free_normals_split()
    return bad


def main():
    opts = _args()
    plan = json.load(open(opts["plan"], encoding="utf-8"))
    os.makedirs(opts["out"], exist_ok=True)
    log = {
        "plan": os.path.abspath(opts["plan"]),
        "checks": [],
        "errors": [],
        "warnings": [],
    }

    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("assetto_corsa_tools", default_set=True)

    imported = {}
    for dae in sorted({o["dae"] for o in plan["objects"]}):
        before = set(bpy.data.objects)
        bpy.ops.wm.collada_import(filepath=dae, import_units=False)
        # El importador deja los vértices en unidades del archivo y pone la unidad
        # como escala del objeto. Se usan los datos crudos (la unidad ya está en
        # la matriz del plan), así que solo se comprueba que sea exactamente eso.
        expected = Matrix.Diagonal((*([plan["unit_meter"][dae]] * 3), 1.0))
        # Solo las mallas que usa el plan deben llegar con esa matriz; las que no
        # se exportan (p. ej. sin flexbody activo) no afectan al KN5: aviso.
        needed = {_blender_name(o["mesh"]) for o in plan["objects"] if o["dae"] == dae}
        clash = {}
        for o in plan["objects"]:
            if o["dae"] == dae:
                clash.setdefault(_blender_name(o["mesh"]), set()).add(o["mesh"])
        for short, full in sorted(clash.items()):
            if len(full) > 1:
                log["errors"].append(
                    f"mallas {sorted(full)}: mismo nombre recortado por Blender "
                    f"'{short}'"
                )
        for ob in set(bpy.data.objects) - before:
            if ob.type != "MESH":
                continue
            diff = max(
                abs(ob.matrix_world[i][j] - expected[i][j])
                for i in range(4)
                for j in range(4)
            )
            if diff > 1e-9:
                (log["errors"] if ob.name in needed else log["warnings"]).append(
                    f"{ob.name}: matriz importada {list(ob.matrix_world)}"
                    + ("" if ob.name in needed else " (no está en el plan)")
                )
            # Prefijo "__": libera el nombre para el objeto final y el addon
            # ignora estos objetos aunque quedaran en la escena.
            imported[ob.name] = ob
            ob.name = "__src_" + ob.name
            ob.data.name = "__src_" + ob.data.name

    empties = {}
    for e in plan["empties"]:
        em = bpy.data.objects.new(e["name"], None)
        em.empty_display_type = "ARROWS"
        em.empty_display_size = 0.2
        em.matrix_world = Matrix(e["matrix"])
        bpy.context.scene.collection.objects.link(em)
        empties[e["name"]] = em
    bpy.context.view_layer.update()
    for e in plan["empties"]:
        parent_name = e.get("parent")
        if parent_name:
            if parent_name not in empties:
                log["errors"].append(
                    f"Empty '{e['name']}': padre '{parent_name}' inexistente"
                )
                continue
            child, parent = empties[e["name"]], empties[parent_name]
            child.parent = parent
            child.matrix_parent_inverse = parent.matrix_world.inverted()
    bpy.context.view_layer.update()

    built = []
    for o in plan["objects"]:
        src = imported.get(_blender_name(o["mesh"]))
        if src is None:
            log["errors"].append(f"malla '{o['mesh']}' no importada")
            continue
        if len(src.data.vertices) != o["vertex_count"]:
            log["errors"].append(
                f"{o['mesh']}: {len(src.data.vertices)} vértices en Blender, "
                f"{o['vertex_count']} en el DAE"
            )
        raw = [v.co for v in src.data.vertices]
        raw_dev = max(
            max(abs(min(p[i] for p in raw) - o["bbox_dae"][0][i]) for i in range(3)),
            max(abs(max(p[i] for p in raw) - o["bbox_dae"][1][i]) for i in range(3)),
        )
        if raw_dev > 1e-3:  # unidades del DAE
            log["errors"].append(f"{o['mesh']}: datos importados ≠ DAE ({raw_dev})")
        data = src.data.copy()
        data.name = o["name"]
        data.transform(Matrix(o["matrix"]))
        data.update()
        repaired = _repair_normals(data)
        if repaired:
            log["warnings"].append(
                f"{o['name']}: {repaired} normales de esquina no finitas sustituidas "
                "por la normal de su cara"
            )
        if not data.uv_layers:
            # G2.18: el escritor de KN5 del addon calcula tangentes y aborta sin un
            # mapa UV (calc_tangents). Mallas así llegan cuando el DAE referencia un
            # TEXCOORD que el importador rechaza (p. ej. NaN en la fuente UV de
            # prothean). Se añade un mapa UV vacío (0, 0): solo afecta a texturas.
            data.uv_layers.new(name="UVMap")
            log["warnings"].append(
                f"{o['name']}: sin mapa UV en Blender; añadido uno vacío (0, 0) para "
                "el escritor de KN5 (sin textura posible en esta malla)"
            )
        ob = bpy.data.objects.new(o["name"], data)
        bpy.context.scene.collection.objects.link(ob)
        if o["parent"]:
            parent = empties[o["parent"]]
            ob.parent = parent
            ob.matrix_parent_inverse = parent.matrix_world.inverted()
        built.append((o, ob))

    for ob in list(imported.values()):
        mesh = ob.data
        bpy.data.objects.remove(ob)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.context.view_layer.update()

    for o, ob in built:
        lo, hi = _bbox(ob)
        dev = max(
            max(abs(lo[i] - o["bbox_beamng"][0][i]) for i in range(3)),
            max(abs(hi[i] - o["bbox_beamng"][1][i]) for i in range(3)),
        )
        if ob.children:
            log["errors"].append(f"la malla '{ob.name}' tiene hijos")
        log["checks"].append(
            {
                "object": ob.name,
                "parent": ob.parent.name if ob.parent else None,
                "vertices": len(ob.data.vertices),
                "bbox_world": [list(lo), list(hi)],
                "bbox_max_deviation_m": dev,
                "materials": [m.name for m in ob.data.materials if m],
            }
        )
        if dev > TOLERANCE:
            log["errors"].append(f"{ob.name}: caja desviada {dev:.6f} m del plan")
    for name, em in empties.items():
        log["checks"].append(
            {"empty": name, "location": list(em.matrix_world.translation)}
        )

    kn5_path = os.path.join(opts["out"], opts["kn5"])
    if not log["errors"]:
        from assetto_corsa_tools.exporter import KN5FileWriter

        warnings = []
        with open(kn5_path, "wb") as f:
            KN5FileWriter(f, bpy.context, {}, warnings).write()
        log["warnings"] += warnings
        log["kn5"] = kn5_path
    if opts["save_blend"]:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(opts["out"], "scene.blend"))
    log["blender_version"] = bpy.app.version_string
    with open(os.path.join(opts["out"], "build_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print("BUILD_ERRORS", len(log["errors"]))
    sys.exit(1 if log["errors"] else 0)


main()
