"""Inventario de atributos de un vehículo BeamNG frente al destino Assetto Corsa (solo lectura).

    python tools/analysis/attribute_inventory.py \
        --vehicle-zip  <mods>/spicy_mandala.zip \
        --common-zip   <BeamNG>/content/vehicles/common.zip \
        --control-dir  sample_beamng/vehicles/seat_ibiza_acfr --control-pc 35b84.pc \
        --physics-base output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25 \
        --out          output/spicy_mandala/attribute_inventory_<fecha>

Qué hace:

1. Lee el vehículo **dentro del zip** (y ``common.zip``) con ``src.jbeam`` sin extraer nada,
   resuelve la config ``.pc`` y ejecuta EN MEMORIA los extractores y los generadores de
   borradores del conversor (fases 2–5). No escribe ningún INI: el texto generado solo se
   analiza para saber qué claves y valores produciría.
2. Hace lo mismo con el coche de control (Ibiza) para comprobar que la herramienta reproduce
   sus valores conocidos y para saber qué claves de la base física aprobada salen del
   conversor y cuáles se añadieron a mano (DIAG/EXPERIMENTAL).
3. Clasifica cada clave de destino mecánicamente (ver ``classify``) y guarda
   ``inventory.json`` en una carpeta nueva bajo ``output/`` (``new_output_dir``).

Las clasificaciones son hechos sobre el conversor y los datos; no crean reglas. Las
correspondencias validadas en el Ibiza NO se dan por válidas para otro vehículo.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac import chassis as ac_chassis  # noqa: E402
from src.ac import drivetrain as ac_drivetrain  # noqa: E402
from src.ac import engine as ac_engine  # noqa: E402
from src.ac.assemble import render_car_ini, render_setup_ini  # noqa: E402
from src.extract.engine_camso import build_engine_model, compare_with_info  # noqa: E402
from src.extract.mass_geometry import compute_mass_geometry  # noqa: E402
from src.jbeam import (  # noqa: E402
    PartIndex,
    ZipMember,
    ZipSource,
    evaluate,
    load_pc,
    loads,
    resolve,
    vehicle_dirs_in_zip,
)
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

DRIVER_MASS_DECISION = 70.0  # decisión 2 del proyecto (CLAUDE.md), no dato del vehículo
TAGS = ("DIAG", "EXPERIMENTAL", "PROVISIONAL", "PENDIENTE", "HIPÓTESIS", "BORRADOR")


# ---------------------------------------------------------------------------
# utilidades


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, (ZipMember, Path)):
        return str(value)
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def parse_ini_text(text: str) -> dict[tuple[str, str], tuple[str, str]]:
    """Misma lógica que ``car_validate.read_ini`` pero sobre texto y conservando el comentario."""
    out: dict[tuple[str, str], tuple[str, str]] = {}
    section: str | None = None
    for raw in text.splitlines():
        body, _, comment = raw.partition(";")
        line = body.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().upper()
            continue
        if "=" in line and section is not None:
            key, value = line.split("=", 1)
            out[(section, key.strip().upper())] = (value.strip(), comment.strip())
    return out


def stage(log: dict, name: str, fn: Callable[[], Any]) -> Any:
    """Ejecuta un paso del conversor y registra éxito o error sin detener el análisis."""
    try:
        value = fn()
        log[name] = {"ok": True}
        return value
    except Exception as exc:  # se registra el tipo y el mensaje exactos
        log[name] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "where": traceback.extract_tb(exc.__traceback__)[-1].filename.replace(
                str(ROOT), ""
            )
            + f":{traceback.extract_tb(exc.__traceback__)[-1].lineno}",
        }
        return None


# ---------------------------------------------------------------------------
# análisis de un vehículo


def analyse_vehicle(index: PartIndex, pc, info: dict, model_info: dict) -> dict[str, Any]:
    log: dict[str, Any] = {}
    out: dict[str, Any] = {"stages": log}
    resolved = stage(log, "resolve", lambda: resolve(index, pc))
    if resolved is None:
        return out
    out["resolve"] = {
        "root": resolved.root,
        "active_parts": [
            {"name": p.name, "slot": p.slot_type, "depth": p.depth, "chosen_by": p.chosen_by}
            for p in resolved.active
        ],
        "missing": [jsonable(m) for m in resolved.missing],
        "unreachable_pc": list(resolved.unreachable_pc),
        "variables": {k: jsonable(v.value) for k, v in resolved.variables.items()},
        "warnings": list(resolved.warnings),
        "pc_issues": list(pc.issues),
    }
    out["active_sections"] = sorted(
        {s for p in resolved.active for s in index[p.name].data}
    )
    out["active_powertrain"] = active_powertrain(resolved, index)

    geometry = stage(log, "mass_geometry", lambda: compute_mass_geometry(resolved))
    engine = stage(log, "engine_model", lambda: build_engine_model(resolved))
    rendered: dict[str, str] = {}
    if geometry is not None:
        out["mass_geometry"] = jsonable(geometry.to_dict())
    if engine is not None:
        checks = stage(log, "engine_info_checks", lambda: compare_with_info(engine, info))
        coast = stage(log, "engine_coast_fit", lambda: ac_engine.fit_coast_ref(engine))
        report = ac_engine.engine_report(engine, checks or [], coast)
        report["derived"].pop("curve", None)  # la curva completa no hace falta aquí
        report["derived"].pop("coast", None)
        out["engine"] = jsonable(report)
        text = stage(log, "render_engine_ini", lambda: ac_engine.render_engine_ini(engine, coast))
        if text:
            rendered["engine.ini"] = text
        lut = stage(log, "render_power_lut", lambda: ac_engine.render_power_lut(engine))
        if lut:
            out["power_lut_points"] = len([x for x in lut.splitlines() if "|" in x])
    if geometry is not None and engine is not None:
        dt = stage(
            log,
            "drivetrain",
            lambda: ac_drivetrain.analyse(resolved, geometry=geometry, engine=engine, info=info),
        )
        if dt is not None:
            out["drivetrain"] = jsonable(ac_drivetrain.drivetrain_report(dt))
            text = stage(log, "render_drivetrain_ini", lambda: ac_drivetrain.render_drivetrain_ini(dt))
            if text:
                rendered["drivetrain.ini"] = text
    ch = stage(log, "chassis", lambda: ac_chassis.analyse(resolved))
    if ch is not None:
        rep = stage(log, "chassis_report", lambda: ac_chassis.chassis_report(ch))
        if rep is not None:
            rep.pop("pending", None)
            out["chassis"] = jsonable(rep)
        for name, fn in (
            ("suspensions.ini", ac_chassis.render_suspensions_ini),
            ("brakes.ini", ac_chassis.render_brakes_ini),
            ("tyres.ini", ac_chassis.render_tyres_ini),
        ):
            text = stage(log, f"render_{name}", lambda fn=fn: fn(ch))
            if text:
                rendered[name] = text
    if geometry is not None:
        fuel = fuel_values(resolved)
        out["fuel"] = fuel
        if fuel.get("ok"):
            rendered["car.ini"] = render_car_ini(
                model_info.get("Name", "?"),
                geometry.total_mass + DRIVER_MASS_DECISION,
                fuel["startingFuelCapacity"],
                fuel["fuelCapacity"],
                geometry.total_mass,
                DRIVER_MASS_DECISION,
            )
    rendered["setup.ini"] = render_setup_ini()
    out["rendered_keys"] = {
        f: {f"{s}/{k}": v for (s, k), (v, _c) in parse_ini_text(t).items()}
        for f, t in rendered.items()
    }
    return out


def fuel_values(resolved) -> dict[str, Any]:
    """Misma lectura que ``assemble.py`` (sección ``mainTank``)."""
    section = next(
        (v for _, v in resolved.section_contributions("mainTank") if isinstance(v, dict)),
        {},
    )
    variables = resolved.variable_values()
    cap = evaluate(section.get("fuelCapacity"), variables)
    start = evaluate(section.get("startingFuelCapacity"), variables)
    ok = isinstance(cap, (int, float)) and isinstance(start, (int, float))
    return {"ok": ok, "fuelCapacity": cap, "startingFuelCapacity": start}


def active_powertrain(resolved, index: PartIndex) -> list[dict[str, Any]]:
    """Filas de las tablas ``powertrain`` de las piezas ACTIVAS (tipo, nombre, entrada)."""
    rows = []
    for p in resolved.active:
        table = index[p.name].data.get("powertrain")
        if not (isinstance(table, list) and table and isinstance(table[0], list)):
            continue
        head = table[0]
        for row in table[1:]:
            if isinstance(row, list) and len(row) >= len(head):
                rows.append({"part": p.name, **{h: row[i] for i, h in enumerate(head)
                                                if isinstance(row[i], (str, int, float))}})
    return rows


# ---------------------------------------------------------------------------
# base física aprobada del coche de control


def physics_base_keys(base_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    data = next(base_dir.glob("*/data"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for ini in sorted(data.glob("*.ini")):
        parsed = parse_ini_text(ini.read_text(encoding="utf-8", errors="replace"))
        out[ini.name] = {
            f"{s}/{k}": {
                "value": v,
                "comment": c[:240],
                "tags": [t for t in TAGS if t in c.upper()],
            }
            for (s, k), (v, c) in parsed.items()
        }
    return out


# ---------------------------------------------------------------------------
# clasificación


def _num_equal(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    try:
        xs = [float(x) for x in a.split(",")]
        ys = [float(y) for y in b.split(",")]
        return len(xs) == len(ys) and all(abs(x - y) <= 1e-6 * max(1, abs(x)) for x, y in zip(xs, ys))
    except ValueError:
        return a.strip() == b.strip()


# Claves de valor fijo que no dependen del vehículo, con la evidencia documentada. Solo estas
# se clasifican como requisito de formato; nada se asigna a esta categoría por su nombre.
FORMAT_CONSTANTS = {
    ("setup.ini", "DISPLAY_METHOD/SHOW_CLICKS"): "setup.ini mínimo, requisito de carga (ensamblado.md §9.12)",
    ("drivetrain.ini", "GEARBOX/VALID_SHIFT_RPM_WINDOW"): "sin la clave AC divide por 0 (ensamblado.md §9.15); "
    "el VALOR 800 es provisional por referencia (Celica/AE86 800; R8/Abarth 1000)",
    ("car.ini", "HEADER/VERSION"): "constante del generador (assemble.render_car_ini); Kunos usa 1 y 2",
    ("engine.ini", "HEADER/VERSION"): "constante del generador (ac/engine.render_engine_ini)",
    ("engine.ini", "HEADER/POWER_CURVE"): "nombre de archivo fijo del generador",
    ("engine.ini", "HEADER/COAST_CURVE"): "modo fijo del generador (FROM_COAST_REF)",
}
INDEXED = re.compile(r"^GEARS/GEAR_(\d+)$")


def _mentions(note: str, key: str) -> bool:
    """La nota pendiente nombra la clave como palabra completa (no como subcadena)."""
    return re.search(rf"(?<![A-Z_]){re.escape(key)}(?![A-Z_])", note) is not None


def classify(base: dict, control: dict, target: dict, pending: dict) -> list[dict[str, Any]]:
    """Una fila por clave de destino (base del Ibiza ∪ claves generadas).

    Categorías (mecánicas, en este orden):

    * ``requisito_formato``: clave de :data:`FORMAT_CONSTANTS` (evidencia citada).
    * ``no_aplica``: clave indexada que no existe en este vehículo (``GEAR_n`` con n > COUNT).
    * ``conocido_regla_ibiza``: el conversor la genera para el vehículo y para el Ibiza, y en
      el Ibiza con el MISMO valor que la base aprobada. La regla se validó solo en el Ibiza:
      para este vehículo el valor es una inferencia hasta que se valide aquí.
    * ``generado_no_aprobado``: el conversor la genera, pero la base aprobada del Ibiza usa otro
      valor o no la incluye: la regla no es la del paquete aprobado (ambigua).
    * ``no_generada``: el conversor la genera para el Ibiza pero no para este vehículo
      (precondición no cumplida, p. ej. un tipo de suspensión no soportado).
    * ``manual_en_ibiza``: está en la base aprobada pero el conversor no la genera (valor
      DIAG/EXPERIMENTAL/plantilla del ciclo del Ibiza): requiere intervención manual.
    """
    rows = []
    t_keys = target.get("rendered_keys", {})
    c_keys = control.get("rendered_keys", {})
    count = t_keys.get("drivetrain.ini", {}).get("GEARS/COUNT")
    files = sorted(set(base) | set(t_keys) | set(c_keys))
    for f in files:
        b = base.get(f, {})
        c = c_keys.get(f, {})
        t = t_keys.get(f, {})
        for key in sorted(set(b) | set(c) | set(t)):
            base_v = b.get(key, {}).get("value")
            short = key.split("/")[-1]
            row = {
                "file": f,
                "key": key,
                "target_value": t.get(key),
                "control_generated": c.get(key),
                "control_approved": base_v,
                "control_approved_tags": b.get(key, {}).get("tags", []),
                "control_approved_comment": b.get(key, {}).get("comment", ""),
                "pending_notes": [p for p in pending.get(f, []) if _mentions(p, short)],
            }
            m = INDEXED.match(key)
            if (f, key) in FORMAT_CONSTANTS:
                cat = "requisito_formato"
                row["evidence"] = FORMAT_CONSTANTS[(f, key)]
            elif m and count is not None and f == "drivetrain.ini" and int(m.group(1)) > int(count):
                cat = "no_aplica"
            elif key in t and key in c and _num_equal(c[key], base_v):
                cat = "conocido_regla_ibiza"
            elif key in t:
                cat = "generado_no_aprobado"
            elif key in c:
                cat = "no_generada"
            else:
                cat = "manual_en_ibiza"
            row["category"] = cat
            rows.append(row)
    return rows


def converter_string_literals() -> set[str]:
    """Literales de cadena del conversor (src/extract, src/ac, src/mesh): qué secciones lee."""
    names: set[str] = set()
    for folder in ("src/extract", "src/ac", "src/mesh"):
        for py in sorted((ROOT / folder).glob("*.py")):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.add(node.value)
    return names


def source_section_usage(target: dict) -> dict[str, list[str]]:
    literals = converter_string_literals()
    # Secciones con el nombre de un dispositivo del powertrain: se leen por ese nombre
    # (src/extract/drivetrain.py:107 y engine_camso.py:192, merge_section(resolved, name)).
    devices = {row.get("name") for row in target.get("active_powertrain", [])}
    sections = target.get("active_sections", [])
    return {
        "read_by_converter": [s for s in sections if s in literals],
        "read_by_device_name": [s for s in sections if s not in literals and s in devices],
        "not_read_by_converter": [
            s for s in sections if s not in literals and s not in devices
        ],
    }


# ---------------------------------------------------------------------------


def load_zip_vehicle(vehicle_zip: Path, common_zip: Path | None, pc_name: str | None):
    prefixes = vehicle_dirs_in_zip(vehicle_zip)
    if len(prefixes) != 1:
        raise SystemExit(f"el zip contiene {len(prefixes)} vehículos: {prefixes}; elegir uno")
    prefix = prefixes[0]
    pcs = ZipSource(vehicle_zip, prefix).members(".pc")
    if pc_name:
        pcs = [m for m in pcs if m.name == pc_name]
    if len(pcs) != 1:
        raise SystemExit(f"se necesita exactamente un .pc: {[m.member for m in pcs]}")
    pc_member = pcs[0]
    sources = [ZipSource(vehicle_zip, prefix)]
    if common_zip:
        sources.append(ZipSource(common_zip, "vehicles/common/"))
    index = PartIndex.from_sources(*sources)
    index_alone = PartIndex.from_sources(ZipSource(vehicle_zip, prefix))
    pc = load_pc(pc_member)
    info = _json_member(vehicle_zip, f"{prefix}info_{PurePosixPath(pc_member.member).stem}.json")
    model_info = _json_member(vehicle_zip, f"{prefix}info.json")
    return prefix, pc_member, index, index_alone, pc, info, model_info


def _json_member(zip_path: Path, member: str) -> dict:
    try:
        return loads(ZipMember(zip_path, member).read_text()).value
    except KeyError:
        return {}


def index_summary(index: PartIndex) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for notes in index.leniencies.values():
        for n in notes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
    return {
        "files": len(index.files),
        "parts": len(index.parts),
        "file_errors": {str(k): str(v) for k, v in index.file_errors.items()},
        "duplicates": [[n, str(a), str(b)] for n, a, b in index.duplicates],
        "issues": index.issues,
        "leniencies": kinds,
    }


def lua_comparison(vehicle_zip: Path, prefix: str, control_dir: Path) -> list[dict[str, Any]]:
    """sha256 de cada Lua del vehículo frente al Lua homónimo del coche de control."""
    rows = []
    for member, data in ZipSource(vehicle_zip, prefix + "lua/").read_all(".lua"):
        rel = member.member[len(prefix):]
        ctrl = control_dir / rel
        rows.append({
            "file": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "control_sha256": sha256_file(ctrl) if ctrl.is_file() else None,
            "identical_to_control": ctrl.is_file() and sha256_file(ctrl) == hashlib.sha256(data).hexdigest(),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicle-zip", required=True, type=Path)
    ap.add_argument("--pc", help="nombre del .pc dentro del zip (si hay más de uno)")
    ap.add_argument("--common-zip", type=Path)
    ap.add_argument("--control-dir", required=True, type=Path)
    ap.add_argument("--control-pc", required=True)
    ap.add_argument("--physics-base", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    inputs = [args.vehicle_zip] + ([args.common_zip] if args.common_zip else [])
    before = {str(p): sha256_file(p) for p in inputs}
    out_dir = new_output_dir(args.out)

    prefix, pc_member, index, index_alone, pc, info, model_info = load_zip_vehicle(
        args.vehicle_zip, args.common_zip, args.pc
    )
    target = analyse_vehicle(index, pc, info, model_info)
    target_alone = analyse_vehicle(index_alone, pc, info, model_info)

    ctrl_index = PartIndex.from_directories(args.control_dir)
    ctrl_pc = load_pc(args.control_dir / args.control_pc)
    ctrl_info_path = args.control_dir / f"info_{Path(args.control_pc).stem}.json"
    ctrl_info = loads(ctrl_info_path.read_text(encoding="utf-8")).value if ctrl_info_path.is_file() else {}
    ctrl_model = loads((args.control_dir / "info.json").read_text(encoding="utf-8")).value
    control = analyse_vehicle(ctrl_index, ctrl_pc, ctrl_info, ctrl_model)

    base = physics_base_keys(args.physics_base)
    rows = classify(base, control, target, ac_chassis.PENDING)
    after = {str(p): sha256_file(p) for p in inputs}

    report = {
        "schema": "beam2ac.attribute_inventory/1",
        "inputs": {
            "vehicle_zip": str(args.vehicle_zip),
            "prefix": prefix,
            "pc": str(pc_member),
            "common_zip": str(args.common_zip) if args.common_zip else None,
            "sha256_before": before,
            "sha256_after": after,
            "unchanged": before == after,
            "control": {"dir": str(args.control_dir), "pc": args.control_pc},
            "physics_base": str(args.physics_base),
            "driver_mass_decision_kg": DRIVER_MASS_DECISION,
        },
        "declared_info": {"model": model_info, "config": info},
        "index": {"with_common": index_summary(index), "vehicle_only": index_summary(index_alone)},
        "resolution_differs_without_common": (
            target.get("resolve", {}).get("active_parts")
            != target_alone.get("resolve", {}).get("active_parts")
        ),
        "lua_vs_control": lua_comparison(args.vehicle_zip, prefix, args.control_dir),
        "source_sections": source_section_usage(target),
        "target": target,
        "control": control,
        "inventory": rows,
        "category_counts": {
            c: sum(1 for r in rows if r["category"] == c)
            for c in sorted({r["category"] for r in rows})
        },
    }
    path = ensure_safe_output_path(out_dir / "inventory.json")
    path.write_text(json.dumps(jsonable(report), indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"inventario: {path}")
    print(f"entradas sin cambios: {before == after}")
    for name, st in target["stages"].items():
        print(f"  {name:24} {'ok' if st['ok'] else 'FALLO ' + st['error_type'] + ': ' + st['error'][:150]}")
    print(report["category_counts"])
    return 0 if before == after else 2


if __name__ == "__main__":
    sys.exit(main())
