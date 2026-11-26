"""Diagnóstico de la aplicación de escritorio: análisis previo y estado del entorno.

Solo lectura. No contiene reglas de conversión: usa el servicio del conversor
(:class:`src.convert.service.ConversionSession`), la clasificación de cobertura
(:mod:`src.attrs.coverage`), las capacidades del destino y la lista de claves
críticas del exportador.
Nada de aquí instala, exporta ni abre juegos.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from src.attrs import coverage

from . import mass_choice as MC
from .workspace import WorkspaceError, parse_driver_mass

# componentes que se muestran al usuario: (etiqueta, ruta del IR)
COMPONENTS = (
    ("Motor", "engine.device_type"),
    ("Sobrealimentación", "engine.forced_induction"),
    ("Caja de cambios", "drivetrain.gearbox.kind"),
    ("Marchas", "drivetrain.gearbox.forward_count"),
    ("Tracción", "drivetrain.layout"),
    ("Diferencial delantero", "drivetrain.differentials.front.kind"),
    ("Diferencial trasero", "drivetrain.differentials.rear.kind"),
    ("Diferencial central", "drivetrain.differentials.center.kind"),
    ("Suspensión delantera", "suspension.axles.front.linkage"),
    ("Suspensión trasera", "suspension.axles.rear.linkage"),
    ("Dirección", "steering.mechanism"),
    ("ABS delantero", "brakes.axles.front.abs"),
    ("Aerodinámica", "aero.model"),
)
IDENTITY = (
    ("name", "identity.name"),
    ("source_tool", "identity.source_tool"),
    ("author", "identity.author"),
    ("exporter_version", "formats.exporter_version"),
    ("source_format", "formats.source_format"),
)


# qué hace falta para resolver un bloqueo, por subsistema (texto para el usuario; el
# detalle y la evidencia están en docs/research/g227_consolidacion_b0_2026-11-11.md §7)
NEXT_STEP = {
    "neumático": "medir el agarre en el banco de neumáticos de BeamNG (como en el "
    "200409); sin medida no hay modelo de neumático",
    "aerodinámica": "medir la aerodinámica en el banco de BeamNG; el modelo del origen "
    "no tiene fórmula equivalente en AC",
    "suspensión y masas": "la geometría de este eje no encaja en un tipo de AC (eje "
    "rígido o enlaces sobredeterminados): hace falta una decisión en «Atributos»",
    "frenos": "frenos con codo de pedal: el par máximo necesita una decisión",
    "transmisión": "caja o convertidor sin equivalente directo: revisa las propuestas "
    "en «Atributos y decisiones»",
    "diferenciales": "tracción o diferencial sin regla para esta variante",
}
DRIVER_STEP = (
    "hay regla, pero solo se aplica con piloto 0 kg (el origen no declara la posición "
    "del piloto): pon 0 en «Piloto (kg)» o decide el valor en «Atributos»"
)
DEFAULT_STEP = (
    "sin regla para esta variante: decisión manual en «Atributos y decisiones»"
)


def find_vehicle_dir(path: str | Path) -> Path:
    """La carpeta del vehículo: la dada si tiene ``*.pc``, o la única
    ``vehicles/<id>`` que cuelgue de ella (raíz de un mod descomprimido). Cualquier
    otra cosa es un error explicado.
    """
    p = Path(str(path).strip().strip('"'))
    if not str(path).strip():
        raise WorkspaceError("indica la carpeta del vehículo")
    if p.is_file() and p.suffix.lower() == ".zip":
        raise WorkspaceError("es un zip, no una carpeta")
    if not p.is_dir():
        raise WorkspaceError(f"no es una carpeta: {p}")
    if any(p.glob("*.pc")):
        return p
    cands = [
        d for d in sorted(p.glob("vehicles/*")) if d.is_dir() and any(d.glob("*.pc"))
    ]
    if len(cands) == 1:
        return cands[0]
    if cands:
        names = ", ".join(d.name for d in cands)
        raise WorkspaceError(f"hay varios vehículos en {p.name}: elige uno ({names})")
    raise WorkspaceError(
        "no parece un vehículo de BeamNG: no hay ninguna configuración .pc "
        "en la carpeta "
        "(ni en vehicles/<id>/)"
    )


def find_source(path: str | Path) -> tuple[str, Path, str, list[str]]:
    """``(tipo, ruta, vehículo, configuraciones)`` de una carpeta o de un zip (G2.29;
    el zip se lee sin extraer y debe contener UN vehículo en ``vehicles/<id>/``)."""
    p = Path(str(path).strip().strip('"'))
    if str(path).strip() and p.is_file() and p.suffix.lower() == ".zip":
        import zipfile

        from src.jbeam import vehicle_dirs_in_zip
        from src.project.manifest import ProjectError, list_zip_configs

        try:
            configs = list_zip_configs(p)
            vehicle = vehicle_dirs_in_zip(p)[0].strip("/").split("/")[-1]
        except (ProjectError, zipfile.BadZipFile, OSError) as exc:
            raise WorkspaceError(f"zip no utilizable ({p.name}): {exc}") from None
        if not configs:
            raise WorkspaceError(f"el zip {p.name} no tiene ninguna configuración .pc")
        return "zip", p, vehicle, configs
    vd = find_vehicle_dir(path)
    return "dir", vd, vd.name, sorted(x.name for x in vd.glob("*.pc"))


def _param(ir: Any, path: str) -> dict[str, Any]:
    try:
        p = ir.get(path)
    except Exception:  # ruta que este IR no tiene
        return {"status": "missing", "value": None}
    v = p.value
    return {
        "status": p.status,
        "value": v if isinstance(v, (str, int, float, bool)) else None,
    }


def car_id_status(car_id: str) -> dict[str, Any]:
    """¿Puede usarse este ``car_id``? (G2.29.1; solo lectura). Misma comprobación que
    la exportación (``export.check_car_id`` con la instalación local de AC): aquí solo
    se adelanta al análisis; el bloqueo sigue en ``Workspace.convert`` y el exportador.
    """
    from src.project import export as E

    car_id = car_id.strip()
    ac = E.default_ac_root()
    out: dict[str, Any] = {
        "car_id": car_id,
        "ok": True,
        "checked": ac is not None,  # sin AC localizado no se puede saber
        "ac_root": str(ac) if ac else None,
        "installed_at": None,
        "reason": "",
        "suggestion": None,
    }
    try:
        E.check_car_id(car_id, ac)
    except ValueError as exc:
        out.update(ok=False, reason=str(exc))
        hit = E.installed_car_collision(car_id, ac)
        if hit is not None:
            out["installed_at"] = str(hit)
            out["reason"] = (
                f"ya hay un coche instalado con ese identificador en {hit}. La carpeta "
                "de un coche en Assetto Corsa es su car_id: instalar este paquete "
                "sobrescribiría ese coche, y beam2ac nunca sobrescribe vehículos "
                "existentes. Elige otro identificador."
            )
            out["suggestion"] = next(
                c
                for c in (f"{car_id}_{n}" for n in range(2, 1000))
                if E.installed_car_collision(c, ac) is None
            )
    return out


def _tolerance_reason(r: Any) -> str:
    """G2.30: la regla existe pero la descarta la tolerancia del ajuste (p. ej. SCTM
    del neumático):
    decirlo con la medida en vez de «sin regla ni decisión»."""
    for x in r.rejected:
        if "fuera de tolerancia" in x.get("reason", ""):
            return f"regla {x['rule']} rechazada: {x['reason'][:240]}"
    return ""


def _audio(kind: str, vd: Any) -> dict[str, Any]:
    """Estado del audio del origen. De un zip se copia ``art/sound`` a ``carpeta temporal (beam2ac_audio_probe)``
    (caché por zip, tamaño y fecha). Mismo plan y misma escalera de estados que la exportación; no
    genera nada (eso lo hace «Convertir»).
    """
    if kind == "zip":
        import hashlib

        from src.audio.pipeline import extract_zip_audio
        import tempfile

        st = Path(vd).stat()
        key = hashlib.sha256(f"{vd}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
        try:
            vd = extract_zip_audio(
                vd, Path(tempfile.gettempdir()) / "beam2ac_audio_probe" / key[:16]
            )
        except (OSError, ValueError) as exc:
            return {"present": False, "note": f"audio del zip no legible: {exc}"}
    from src.audio.pipeline import ladder
    from src.audio.plan import build_plan
    from src.extract.audio_sources import scan_audio

    scan = scan_audio(vd)
    plan = build_plan(scan, "beam2ac_x")
    series = [
        {k: v for k, v in s.items() if k != "points"}
        for b in scan["blends"]
        for s in b["series"]
    ]
    return {
        "present": scan["present"],
        "problems": scan["problems"],
        "series": series,
        "status": plan["status"],
        "warnings": plan.get("warnings", []),
        "rpm_range": plan.get("rpm_range"),
        "series_used": plan.get("series_used"),
        "dropped": plan.get("dropped", []),
        **ladder(plan["status"]),
    }


def _bench(kind: str, vd: Any, pc: str | None, blockers: list[dict]) -> dict[str, Any]:
    """Bancos de BeamNG que el análisis pide (G2.46; ``src.bench.service``).

    Un zip se lee sin extraer (G2.47); solo al medir se extrae, de forma segura, a
    ``output/bench_jobs/zips``."""
    from src.bench import service

    try:
        measures = []
        for k in service.KINDS:
            if k == "aero" and not service.uses_triangle_aero(vd, pc):
                continue
            st = service.status(k, vd, pc)
            measures.append(
                {x: st[x] for x in ("kind", "reuse", "measurement", "stale")}
            )
        return {
            "needs": service.needs(vd, pc, blockers),
            "measures": measures,
            "error": "",
        }
    except Exception as exc:  # el diagnóstico explica, no revienta
        return {"needs": [], "error": f"{type(exc).__name__}: {exc}"}


def _tyre_source(ir: Any) -> dict[str, Any]:
    """Origen del neumático: medido en el banco de BeamNG o ESTIMADO sin banco
    (``src/ac/tyre_estimate.py``, con los avisos de rango de calibración)."""
    out: dict[str, Any] = {"source": "sin datos", "warnings": []}
    for pos, axle in ir.tyres.axles.items():
        p = axle.force_slip_samples
        if not p.present:
            continue
        out["source"] = "estimado" if p.kind == "estimated" else "medido"
        if p.kind == "estimated":
            out["warnings"] += [
                f"{pos}: {w}"
                for w in p.note.split("; ")[2:]
                if "rango" in w or "familia" in w
            ]
    return out


def inspect_vehicle(
    path: str,
    pc: str | None = None,
    driver_mass: Any = 0.0,
    common_zip: str | None = None,
    project_root: str | Path | None = None,
    mass_choice: Any = None,
) -> dict[str, Any]:
    """Análisis previo (sin crear proyecto ni escribir nada): identidad, componentes,
    compatibilidad
    con el destino, cobertura por subsistema y lo que hoy impediría exportar."""
    from src.convert.service import ConversionSession
    from src.attrs import schema as S
    from src.project.export import (
        ZERO_IS_WRONG_KEYS,
        _blocker_severity,
        sctm_targets,
        default_car_id,
    )

    driver_mass = parse_driver_mass(driver_mass)
    choice = MC.parse_choice(mass_choice)
    kind, vd, vehicle, configs = find_source(path)
    if pc and pc not in configs:
        raise WorkspaceError(
            f"la configuración {pc!r} no está en "
            f"{'el zip' if kind == 'zip' else 'la carpeta'} "
            f"(¿otra versión del juego?); hay: {', '.join(configs[:12])}"
        )
    if kind == "zip" and not pc and len(configs) > 1:
        pc = configs[0]  # el formulario permite elegir otra y reanaliza
    try:
        s = ConversionSession.from_source(vd, pc, common_zip, driver_mass, project_root)
        results = s.resolve()
        # conflicto de masa con la ficha de Automation: lo decide el usuario (mass_choice.py)
        found = MC.conflict(s, results)
        mass_decisions = MC.decisions(s, found, choice) if found and choice else []
        if mass_decisions:
            s.catalog.decisions.extend(mass_decisions)
            results = s.resolve()
    except Exception as exc:  # el diagnóstico explica, no revienta
        return {
            "ok": False,
            "path": str(vd),
            "vehicle": vehicle,
            "configs": configs,
            "error": f"{type(exc).__name__}: {exc}",
            "hint": "el lector no pudo interpretar esta configuración; revisa que sea "
            "una exportación de Automation a BeamNG (el conversor no cubre vehículos "
            "originales de BeamNG)",
        }
    identity = {k: _param(s.ir, p)["value"] for k, p in IDENTITY}
    components = [
        dict(label=label, path=p, **_param(s.ir, p)) for label, p in COMPONENTS
    ]
    findings = [
        {"feature": f.feature, "level": f.level, "reason": f.reason}
        for f in s.capabilities()
    ]
    # misma clasificación que la exportación (claves que AC necesita para cargar o
    # conducir); aquí sin las decisiones del proyecto, que pueden resolver alguna
    attrs = {k: S._to_plain(a) for k, a in s.catalog.attributes.items()}
    sctm = sctm_targets([{"target": r.target, "value": r.value} for r in results])
    blockers = [
        {
            "target": r.target,
            "why": ZERO_IS_WRONG_KEYS.get(
                r.target, "clave que AC necesita para cargar o conducir"
            ),
            # G2.29.3: si la regla existe y solo la descarta la masa del piloto, decirlo
            "next": (
                DRIVER_STEP
                if any("ir.mass.driver" in x.get("reason", "") for x in r.rejected)
                else NEXT_STEP.get(coverage.subsystem(r.target), DEFAULT_STEP)
            ),
            "state": (
                # G2.29: el exportador no escribe un valor en conflicto (p. ej. una
                # validación fallida de este vehículo): también bloquea aquí
                "en conflicto: " + ("; ".join(map(str, r.open_issues)) or "sin detalle")
                if r.status == "ambiguous"
                else _tolerance_reason(r)
                or (
                    "propuesta sin aprobar (regla de otro ámbito)"
                    if r.source == "proposal"
                    else "sin regla ni decisión"
                )
            ),
        }
        for r in results
        if (r.value is None or r.source == "proposal" or r.status == "ambiguous")
        and r.status not in ("not_applicable", "format_requirement")
        and _blocker_severity({"target": r.target}, attrs, sctm) == "error"
    ]
    cov = coverage.by_subsystem(results, s.catalog)
    totals = {k: sum(c[k] for c in cov.values()) for k in coverage.CLASSES}
    in_scope = identity.get("source_tool") == "Automation"
    return {
        "ok": True,
        "kind": kind,
        "path": str(vd),
        "vehicle": vehicle,
        "configs": configs,
        "config": pc or (configs[0] if len(configs) == 1 else None),
        # G2.29.2: la masa que el análisis usó DE VERDAD (leída del IR, no el argumento)
        "driver_mass": _param(s.ir, "mass.driver")["value"],
        "identity": identity,
        "default_car_id": default_car_id(vehicle),
        "car_id_check": car_id_status(default_car_id(vehicle)),
        "in_scope": in_scope,
        "scope_note": (
            ""
            if in_scope
            else "no declara Automation como origen: fuera del alcance del conversor"
        ),
        "components": components,
        "capabilities": findings,
        "coverage": cov,
        "coverage_totals": totals,
        "blockers": blockers,
        "mass_conflict": MC.public(found, choice) if found else None,
        "mass_decisions": [S.to_dict(d) for d in mass_decisions],
        "bench": _bench(
            kind, vd, pc or (configs[0] if len(configs) == 1 else None), blockers
        ),
        "tyres": _tyre_source(s.ir),
        "audio": _audio(kind, vd),
        # estático: sin bloqueos NO significa física validada en el juego
        "exportable_today": in_scope and not blockers,
        "note": "cobertura = de dónde sale cada clave de AC (medida, referencia, …); "
        "no es una medida "
        "de fidelidad física. «Exportable» no es «validado en Assetto Corsa».",
    }


def environment() -> dict[str, Any]:
    """Estado del entorno (solo lectura): instalación de AC y lo que beam2ac tiene
    dentro, Steam y catálogo. No lanza ni modifica nada."""
    from src.attrs.store import REPO_CATALOG, Catalog
    from src.project.export import default_ac_root

    out: dict[str, Any] = {}
    ac = default_ac_root()
    info: dict[str, Any] = {"found": ac is not None, "root": str(ac) if ac else None}
    if ac:
        for key, rel in (
            ("cars", "content/cars"),
            ("tracks", "content/tracks"),
            ("apps", "apps/lua"),
        ):
            d = ac / rel
            info[f"beam2ac_{key}"] = (
                sorted(x.name for x in d.glob("beam2ac_*") if x.is_dir())
                if d.is_dir()
                else []
            )
        info["csp"] = (ac / "extension" / "config").is_dir()
        info["acs_exe"] = (ac / "acs.exe").is_file()
    out["assetto_corsa"] = info
    out["steam"] = _steam()
    out["python_tools"] = {
        "black": bool(shutil.which("black")),
        "flake8": bool(shutil.which("flake8")),
    }
    try:
        cat = Catalog.load(REPO_CATALOG)
        out["catalog"] = {
            "ok": True,
            "rules": len(cat.rules),
            "attributes": len(cat.attributes),
        }
    except Exception as exc:
        out["catalog"] = {"ok": False, "error": str(exc)}
    out["version"] = _version()
    return out


def _steam() -> dict[str, Any]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess"
        ) as k:
            user = winreg.QueryValueEx(k, "ActiveUser")[0]
            pid = winreg.QueryValueEx(k, "pid")[0]
        # ActiveUser y pid siguen en el registro al cerrar Steam: indicio, no prueba
        return {
            "installed": True,
            "active_user_in_registry": bool(user),
            "last_pid": pid,
        }
    except (ImportError, OSError):
        return {"installed": False}


def _version() -> str:
    import re

    t = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text("utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', t, re.M)
    return m.group(1) if m else "?"
