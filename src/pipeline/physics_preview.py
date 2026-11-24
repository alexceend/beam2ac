"""Vista previa de la física de un vehículo BeamNG, en memoria y sin escribir nada.

Ejecuta los mismos extractores (fases 2–5) y generadores de borradores
(``src.ac.engine``, ``src.ac.drivetrain``, ``src.ac.chassis``, ``render_car_ini``) que
el conversor, pero en vez de escribir archivos devuelve los objetos y el texto que se
generaría. Cada paso registra si terminó o con qué error, para que un fallo no se
presente como un resultado válido (``PhysicsPreview.ok`` solo es cierto si todos los
pasos obligatorios terminaron).

Fuentes admitidas (:func:`load_vehicle`): carpeta de vehículo o zip (con ``common.zip``
opcional), siempre en solo lectura.
"""

from __future__ import annotations

import hashlib
import traceback
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from src.ac import chassis as ac_chassis
from src.ac import drivetrain as ac_drivetrain
from src.ac import engine as ac_engine
from src.ac.assemble import render_car_ini, render_setup_ini
from src.extract.engine_camso import (
    CAMSO_ENGINE_LUA,
    EngineDataError,
    build_engine_model,
    compare_with_info,
    find_engine_device,
)
from src.extract.engine_combustion import read_combustion_engine, zip_resource_reader
from src.extract.mass_geometry import compute_mass_geometry
from src.jbeam import (
    PartIndex,
    PcConfig,
    ResolvedConfig,
    ZipMember,
    ZipSource,
    evaluate,
    load_pc,
    loads,
    parse_table,
    resolve,
    vehicle_dirs_in_zip,
)

# Pasos sin los cuales la vista previa no es válida.
REQUIRED_STAGES = ("resolve", "mass_geometry", "engine_model", "drivetrain", "chassis")


@dataclass
class VehicleSource:
    """Vehículo cargado en solo lectura."""

    vehicle_id: str
    variant: str
    description: str
    index: PartIndex
    pc: PcConfig
    info: dict[str, Any]
    model_info: dict[str, Any]
    lua_sha256: dict[str, str]  # ruta relativa (lua/...) -> sha256
    input_sha256: dict[str, str]  # zip o .pc de entrada -> sha256
    # G2.9: lector de recursos del sistema de archivos virtual del juego
    # (``vehicles/<id>/…``) para ``$=include('….csv')`` del motor ``combustionEngine``
    resource_reader: Callable[[str], bytes] | None = None


@dataclass
class PhysicsPreview:
    source: VehicleSource
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    resolved: ResolvedConfig | None = None
    geometry: Any = None
    engine: Any = None  # EngineModel de camsoEngine (único con borrador de AC)
    # G2.9: CombustionEngine (src.extract.engine_combustion); solo va al IR
    combustion_engine: Any = None
    engine_checks: list[Any] = field(default_factory=list)
    coast: Any = None
    drivetrain: Any = None
    chassis: Any = None
    fuel: dict[str, Any] = field(default_factory=dict)
    rendered: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.stages.get(s, {}).get("ok") for s in REQUIRED_STAGES)

    @property
    def failed_stages(self) -> list[str]:
        return [name for name, st in self.stages.items() if not st.get("ok")]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_text(text: str) -> dict[str, Any]:
    value = loads(text.replace("\r\n", "\n").replace("\r", "\n")).value
    return value if isinstance(value, dict) else {}


COMBUSTION_ENGINE = "combustionEngine"


def dir_resource_reader(vehicle_dir: Path) -> Callable[[str], bytes] | None:
    """Lector de ``vehicles/<id>/…`` desde la carpeta raíz de un mod descomprimido
    (``<raíz>/vehicles/<id>``). Solo lectura; ``KeyError`` si no existe o si la ruta
    sale de la raíz. ``None`` si la carpeta no sigue esa estructura."""
    vehicle_dir = Path(vehicle_dir)
    if vehicle_dir.parent.name != "vehicles":
        return None
    root = vehicle_dir.parent.parent.resolve()

    def read(uri: str) -> bytes:
        rel = uri.replace("\\", "/").lstrip("/")
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            raise KeyError(uri)
        return path.read_bytes()

    return read


def load_vehicle_dir(vehicle_dir: Path, pc_name: str) -> VehicleSource:
    vehicle_dir = Path(vehicle_dir)
    pc_path = vehicle_dir / pc_name
    variant = Path(pc_name).stem

    def info_file(name: str) -> dict[str, Any]:
        p = vehicle_dir / name
        return _json_text(p.read_text(encoding="utf-8")) if p.is_file() else {}

    lua = {
        p.relative_to(vehicle_dir).as_posix(): sha256_file(p)
        for p in sorted((vehicle_dir / "lua").rglob("*.lua"))
    }
    return VehicleSource(
        vehicle_id=vehicle_dir.name,
        variant=variant,
        description=str(vehicle_dir),
        index=PartIndex.from_directories(vehicle_dir),
        pc=load_pc(pc_path),
        info=info_file(f"info_{variant}.json"),
        model_info=info_file("info.json"),
        lua_sha256=lua,
        input_sha256={str(pc_path): sha256_file(pc_path)},
        resource_reader=dir_resource_reader(vehicle_dir),
    )


def load_vehicle_zip(
    vehicle_zip: Path, common_zip: Path | None = None, pc_name: str | None = None
) -> VehicleSource:
    vehicle_zip = Path(vehicle_zip)
    prefixes = vehicle_dirs_in_zip(vehicle_zip)
    if len(prefixes) != 1:
        raise ValueError(f"el zip contiene {len(prefixes)} vehículos: {prefixes}")
    prefix = prefixes[0]
    pcs = ZipSource(vehicle_zip, prefix).members(".pc")
    if pc_name:
        available = sorted(m.name for m in pcs)
        pcs = [m for m in pcs if m.name == pc_name]
        if not pcs:
            raise ValueError(
                f"la configuración {pc_name!r} no existe en {vehicle_zip.name} "
                f"(¿otra versión del juego?); hay {len(available)}: "
                f"{', '.join(available[:12])}{'…' if len(available) > 12 else ''}"
            )
    if len(pcs) != 1:
        raise ValueError(f"se necesita exactamente un .pc: {[m.member for m in pcs]}")
    pc_member = pcs[0]
    variant = PurePosixPath(pc_member.member).stem
    sources: list[Any] = [ZipSource(vehicle_zip, prefix)]
    hashes = {str(vehicle_zip): sha256_file(vehicle_zip)}
    if common_zip:
        sources.append(ZipSource(common_zip, "vehicles/common/"))
        hashes[str(common_zip)] = sha256_file(common_zip)

    def info_member(name: str) -> dict[str, Any]:
        try:
            return _json_text(ZipMember(vehicle_zip, prefix + name).read_text())
        except KeyError:
            return {}

    lua = {
        m.member[len(prefix) :]: sha256_bytes(data)
        for m, data in ZipSource(vehicle_zip, prefix + "lua/").read_all(".lua")
    }
    return VehicleSource(
        vehicle_id=prefix.split("/")[1],
        variant=variant,
        description=f"{vehicle_zip}!/{prefix}",
        index=PartIndex.from_sources(*sources),
        pc=load_pc(pc_member),
        info=info_member(f"info_{variant}.json"),
        model_info=info_member("info.json"),
        lua_sha256=lua,
        input_sha256=hashes,
        resource_reader=zip_resource_reader(
            vehicle_zip, *([common_zip] if common_zip else [])
        ),
    )


def _stage(log: dict, name: str, fn: Callable[[], Any]) -> Any:
    try:
        value = fn()
    except Exception as exc:  # se registra tipo, mensaje y lugar exactos
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        log[name] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "where": f"{Path(tb.filename).name}:{tb.lineno}",
        }
        return None
    log[name] = {"ok": True}
    return value


FUEL_TANK_TYPE = "fuelTank"


def fuel_tanks(resolved: ResolvedConfig) -> list[str]:
    """Nombres de los dispositivos ``fuelTank`` de las tablas ``energyStorage``
    (``["type", "name", …]``) de las piezas activas, sin repetir y ordenados."""
    names: set[str] = set()
    for active in resolved.active:
        for row in parse_table(active.part.data.get("energyStorage", [])).rows:
            name = row.get("name")
            if row.get("type") == FUEL_TANK_TYPE and isinstance(name, str):
                names.add(name)
    return sorted(names)


def fuel_values(resolved: ResolvedConfig) -> dict[str, Any]:
    """Capacidad y carga inicial del depósito de combustible.

    G2.8: el depósito es el ÚNICO dispositivo ``fuelTank`` declarado en
    ``energyStorage``, leído en la sección de su mismo nombre. Antes se suponía el
    nombre ``mainTank`` (el de los 14 vehículos disponibles y el que sigue leyendo
    ``assemble.py``, que no se toca). Sin depósito, o con varios (sumar o elegir no
    está verificado: AC tiene un único depósito), ``ok`` es falso y ``reason`` lo dice.
    """
    tanks = fuel_tanks(resolved)
    if len(tanks) != 1:
        return {
            "ok": False,
            "fuelCapacity": None,
            "startingFuelCapacity": None,
            "reason": (
                f"energyStorage sin dispositivo '{FUEL_TANK_TYPE}'"
                if not tanks
                else f"varios depósitos '{FUEL_TANK_TYPE}' {tanks}: suma o elección "
                "sin verificar"
            ),
        }
    section = next(
        (v for _, v in resolved.section_contributions(tanks[0]) if isinstance(v, dict)),
        {},
    )
    variables = resolved.variable_values()
    cap = evaluate(section.get("fuelCapacity"), variables)
    start = evaluate(section.get("startingFuelCapacity"), variables)
    ok = isinstance(cap, (int, float)) and isinstance(start, (int, float))
    out: dict[str, Any] = {"ok": ok, "fuelCapacity": cap, "startingFuelCapacity": start}
    if not ok:
        out["reason"] = (
            f"sección '{tanks[0]}' sin fuelCapacity/startingFuelCapacity numéricos"
        )
    return out


def preview_physics(
    source: VehicleSource, driver_mass_kg: float | None = None
) -> PhysicsPreview:
    """Ejecuta los pasos del conversor en memoria. ``driver_mass_kg`` solo afecta al
    texto de ``car.ini`` (decisión 2 del proyecto); ``None`` = no se genera ``car.ini``.
    """
    pv = PhysicsPreview(source=source)
    log = pv.stages
    pv.resolved = _stage(log, "resolve", lambda: resolve(source.index, source.pc))
    if pv.resolved is None:
        return pv
    resolved = pv.resolved
    pv.geometry = _stage(log, "mass_geometry", lambda: compute_mass_geometry(resolved))
    # G2.9: el adaptador se elige por el TIPO del dispositivo motor (no por la familia
    # ni por la versión del exportador). ``combustionEngine`` va a su adaptador de solo
    # lectura (sin borrador de AC); cualquier otro caso sigue exactamente el camino
    # anterior (``build_engine_model``, que solo admite ``camsoEngine`` y da el error).
    try:
        engine_type = find_engine_device(resolved)[0]
    except EngineDataError:
        engine_type = None
    if engine_type == COMBUSTION_ENGINE:
        pv.combustion_engine = _stage(
            log,
            "engine_model",
            lambda: read_combustion_engine(resolved, source.resource_reader),
        )
        log["engine_model"]["adapter"] = COMBUSTION_ENGINE
    else:
        pv.engine = _stage(
            log,
            "engine_model",
            lambda: build_engine_model(
                resolved, source.lua_sha256.get(CAMSO_ENGINE_LUA, "")
            ),
        )
    if pv.engine is not None:
        engine = pv.engine
        pv.engine_checks = (
            _stage(
                log,
                "engine_info_checks",
                lambda: compare_with_info(engine, source.info),
            )
            or []
        )
        pv.coast = _stage(
            log, "engine_coast_fit", lambda: ac_engine.fit_coast_ref(engine)
        )
        text = _stage(
            log,
            "render_engine_ini",
            lambda: ac_engine.render_engine_ini(engine, pv.coast),
        )
        if text:
            pv.rendered["engine.ini"] = text
        lut = _stage(
            log, "render_power_lut", lambda: ac_engine.render_power_lut(engine)
        )
        if lut:
            pv.rendered["power.lut"] = lut
    if pv.geometry is not None:
        # G2.7 (G2.6d): el motor es opcional; sin él no hay velocidades por marcha
        # ni valores derivados del motor (ni se construye uno: build_engine=False)
        geometry, engine = pv.geometry, pv.engine
        pv.drivetrain = _stage(
            log,
            "drivetrain",
            lambda: ac_drivetrain.analyse(
                resolved,
                geometry=geometry,
                engine=engine,
                info=source.info,
                build_engine=False,
            ),
        )
        if pv.drivetrain is not None:
            dt = pv.drivetrain
            text = _stage(
                log,
                "render_drivetrain_ini",
                lambda: ac_drivetrain.render_drivetrain_ini(dt),
            )
            if text:
                pv.rendered["drivetrain.ini"] = text
    else:
        log["drivetrain"] = {
            "ok": False,
            "error_type": "Skipped",
            "error": "falta masa",
        }
    pv.chassis = _stage(log, "chassis", lambda: ac_chassis.analyse(resolved))
    if pv.chassis is not None:
        ch = pv.chassis
        for name, fn in (
            ("suspensions.ini", ac_chassis.render_suspensions_ini),
            ("brakes.ini", ac_chassis.render_brakes_ini),
            ("tyres.ini", ac_chassis.render_tyres_ini),
        ):
            text = _stage(log, f"render_{name}", lambda fn=fn: fn(ch))
            if text:
                pv.rendered[name] = text
    pv.fuel = fuel_values(resolved)
    if pv.geometry is not None and pv.fuel.get("ok") and driver_mass_kg is not None:
        pv.rendered["car.ini"] = render_car_ini(
            source.model_info.get("Name", source.vehicle_id),
            pv.geometry.total_mass + driver_mass_kg,
            pv.fuel["startingFuelCapacity"],
            pv.fuel["fuelCapacity"],
            pv.geometry.total_mass,
            driver_mass_kg,
        )
    pv.rendered["setup.ini"] = render_setup_ini()
    return pv


def parse_ini_text(text: str) -> dict[tuple[str, str], tuple[str, str]]:
    """Misma lógica que ``car_validate.read_ini``, sobre texto y conservando el
    comentario."""
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


def rendered_keys(pv: PhysicsPreview) -> dict[str, dict[str, str]]:
    """``{archivo: {"SECCIÓN/CLAVE": valor}}`` de los INI generados en memoria."""
    return {
        f: {f"{s}/{k}": v for (s, k), (v, _c) in parse_ini_text(t).items()}
        for f, t in pv.rendered.items()
        if f.endswith(".ini")
    }
