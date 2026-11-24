"""Transmisión para Assetto Corsa (borrador de la fase 4).

Solo se escriben claves cuya correspondencia está justificada
(docs/research/transmision.md §4):

* ``[TRACTION] TYPE`` ← ruedas motrices del grafo (derivado; info: "FWD").
  La tracción, el radio de la rueda motriz y el papel de cada diferencial (eje
  delantero, trasero o central y su reparto) se interpretan en el ORIGEN, en
  ``src.extract.traction`` (G2.5c); este módulo solo los consume.
* ``[GEARS] COUNT`` ← nº de marchas hacia delante (definición).
* ``GEAR_R`` ← ``gearRatios[0]``, negativo: AC también la usa negativa
  (``drivetrain.ini`` de muestra).
* ``GEAR_n`` ← ``gearRatios[n + 1]``: relación de transmisión (definición).
* ``FINAL`` ← ``gearRatio`` del diferencial: grupo final (definición).
* ``[DIFFERENTIAL] PRELOAD`` ← ``lsdPreload``: ambos son el par de bloqueo
  inicial (descripción de la variable BeamNG y comentario de AC). La unidad
  "N/m" declarada en la variable de BeamNG es incoherente con un par.

Requisito de compatibilidad con AC (no deriva de BeamNG):

* ``[GEARBOX] VALID_SHIFT_RPM_WINDOW`` = :data:`VALID_SHIFT_RPM_WINDOW`. Sin la
  clave, transmisión de AC deja a 0 el divisor de
  ``getRpmWindowStatus`` (copia la clave ausente antes de aplicar su valor por
  defecto, 500) y el indicador de transmisión del HUD parpadea en rojo
  continuamente. Confirmado en el juego con la variante ``test13vw``
  (docs/research/ensamblado.md §9.15). 800 es el valor de ``ks_toyota_celica_st185``
  y ``ks_toyota_ae86``; ``ks_audi_r8_lms`` y ``ks_abarth500_assetto_corse`` usan 1000.

No se escriben (pendientes, sin correspondencia verificada): ``[DIFFERENTIAL]
POWER/COAST`` (lsdLockCoef/lsdRevLockCoef son "par adicional proporcional al
par del motor", no se sabe si equivale al bloqueo de AC), ``[CLUTCH]``, el resto
de ``[GEARBOX]`` (tiempos de cambio, inercia, ``CONTROLS_WINDOW_GAIN``),
``[AUTOCLUTCH]``, ``[AUTOBLIP]``.

Tracción total (``Drivetrain.awd``): se escriben ``TYPE=AWD``, las marchas y ``FINAL``
(común a los dos ejes; si difieren, la extracción ya falla). La sección ``AWD`` NO se
escribe: transmisión de AC lee ``FRONT_SHARE`` (×0,01) y ``CENTRE_/FRONT_/
REAR_DIFF_POWER|COAST|PRELOAD`` [C], pero cómo los aplica ``transmisión 4WD de AC`` no
está analizado (E0), igual que ``AWD`` frente a ``AWD2``. Los valores de BeamNG van como
comentario y ``car_validate`` da error mientras falte ``FRONT_SHARE`` (AC leería 0).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.extract.drivetrain import (
    Drivetrain,
    DrivetrainDataError,
    GearSpeed,
    build_drivetrain,
    gear_speeds,
)
from src.extract.traction import (  # noqa: F401  (traction_type: compatibilidad)
    SOURCE_KEY,
    DiffSetup,
    TractionLayout,
    analyse_source_drivetrain,
    awd_axle_roles,
    classify_traction,
    traction_type,
)
from src.extract.engine_camso import EngineModel, build_engine_model
from src.extract.mass_geometry import MassGeometry, compute_mass_geometry
from src.jbeam import PartIndex, ResolvedConfig, load, load_pc, resolve
from src.ac.ini_format import sanitize_ini_comments
from src.safe_paths import ensure_safe_output_path

WRITABLE_TRACTION = {"FWD", "RWD"}
# "0-100 km/h" de info_<config>.json cuando Automation no alcanza 100 km/h (centinela)
ACCEL_NOT_REACHED = 999.0


@dataclass(frozen=True)
class InfoCheck:
    quantity: str
    reference: Any
    computed: Any
    explanation: str


@dataclass
class DrivetrainResult:
    drivetrain: Drivetrain
    traction: str
    driven_radius: float
    rpm_ceiling: float | None  # None sin modelo de motor (G2.7)
    speeds: list[GearSpeed]  # vacía sin modelo de motor
    shift_controller_raw: dict[str, Any]
    checks: list[InfoCheck]
    awd: dict[str, Any] | None = None  # reparto por eje (delantero/trasero)
    # Tracción y diferenciales interpretados en el origen (src.extract.traction).
    # ``traction``, ``driven_radius`` y ``awd`` se derivan de él (compatibilidad).
    layout: TractionLayout | None = None
    # G2.11: caja DCT o automática con convertidor (src.extract.automatic_transmission)
    transmission: Any = None


def _fmt(x: float) -> str:
    return f"{int(x)}" if float(x).is_integer() else f"{x:g}"


def analyse(
    resolved: ResolvedConfig,
    geometry: MassGeometry | None = None,
    engine: EngineModel | None = None,
    info: dict[str, Any] | None = None,
    *,
    build_engine: bool = True,
) -> DrivetrainResult:
    """Transmisión del origen + lo que necesita el destino.

    Sin ``engine``: con ``build_engine`` (por defecto, el CLI) se construye el modelo
    Camso como antes; con ``build_engine=False`` (vista previa, G2.7) el motor es
    opcional: sin velocidades por marcha, ``rpm_ceiling = None`` y sin comprobaciones
    de velocidad. El borrador ``drivetrain.ini`` no usa el motor.
    """
    # mismo orden de errores que antes: grafo, geometría, motor y tracción
    dt = build_drivetrain(resolved)
    geometry = geometry or compute_mass_geometry(resolved)
    if engine is None and build_engine:
        engine = build_engine_model(resolved)
    # Grafo, tracción, radio y papel de los diferenciales: lógica de origen.
    src = analyse_source_drivetrain(resolved, geometry, dt)
    dt, layout = src.drivetrain, src.layout
    traction, radius = layout.layout, layout.driven_radius
    if engine is not None:
        ceiling = engine.rpm_ceiling
        speeds = gear_speeds(dt, radius, ceiling, engine.peak_power.rpm)
    else:
        ceiling, speeds = None, []
    checks = compare_with_info(dt, traction, speeds, info or {})
    return DrivetrainResult(
        dt,
        traction,
        radius,
        ceiling,
        speeds,
        src.shift_controller_raw,
        checks,
        layout.awd_summary(),
        layout,
        src.transmission,
    )


def awd_axles(dt: Drivetrain, front: set[str], rear: set[str]) -> dict | None:
    """Compatibilidad: resumen de :func:`src.extract.traction.awd_axle_roles` con el
    formato histórico (la lógica vive ahora en ``src.extract.traction``)."""
    roles = awd_axle_roles(dt, front, rear)
    if roles is None:
        return None
    center, axles = roles
    return TractionLayout(
        "AWD",
        tuple(dt.driven_wheels),
        frozenset(front),
        frozenset(rear),
        0.0,
        axles,
        center,
    ).awd_summary()


def compare_with_info(
    dt: Drivetrain, traction: str, speeds: list[GearSpeed], info: dict[str, Any]
) -> list[InfoCheck]:
    checks: list[InfoCheck] = []
    if "Drivetrain" in info:
        ok = info["Drivetrain"] == traction
        checks.append(
            InfoCheck(
                "Drivetrain",
                info["Drivetrain"],
                traction,
                "coincide" if ok else "DISCREPANCIA",
            )
        )
    if "Transmission" in info:
        is_manual = dt.gearbox.type == "manualGearbox"
        ok = (info["Transmission"] == "Manual") == is_manual
        checks.append(
            InfoCheck(
                "Transmission",
                info["Transmission"],
                dt.gearbox.type,
                "coincide" if ok else "DISCREPANCIA",
            )
        )
    top = info.get("Top Speed")
    accel = info.get("0-100 km/h")
    if not speeds:  # sin modelo de motor no hay velocidades: no se compara (G2.7)
        return checks
    if (
        isinstance(top, (int, float))
        and not isinstance(top, bool)
        and accel == ACCEL_NOT_REACHED
    ):
        # La simulación de Automation no llegó a 100 km/h (centinela 999): su
        # «Top Speed» no es la punta del coche. En los 3 casos vistos vale ≈ 0,92 ×
        # la velocidad en 1.ª al techo de rpm (docs/research/
        # catalogo_motor_awd_vmax_2026-10-30.md §3). No se compara ni se ajusta.
        first = next((s for s in speeds if s.gear == "1"), None)
        ratio = top / first.speed_at_ceiling if first else None
        checks.append(
            InfoCheck(
                "Top Speed",
                top,
                None,
                "dato de Automation no comparable: '0-100 km/h' = 999 (no alcanzó "
                "100 km/h en su simulación); "
                + (
                    f"vale {ratio:.3f} × la velocidad en 1.ª al techo de rpm "
                    f"({first.speed_at_ceiling:.2f} m/s)"
                    if first
                    else "sin 1.ª marcha"
                ),
            )
        )
    elif isinstance(top, (int, float)) and not isinstance(top, bool):
        fastest = max(speeds, key=lambda s: s.speed_at_ceiling)
        diff = (top - fastest.speed_at_ceiling) / fastest.speed_at_ceiling
        rpm_needed = top / fastest.speed_at_ceiling * 1.0  # fracción del techo
        why = (
            f"unidad inferida m/s. Velocidad teórica en {fastest.gear}.ª al "
            "techo de rpm: "
            f"{fastest.speed_at_ceiling:.2f} m/s; referencia {diff:+.2%} "
            f"(equivale a {rpm_needed:.1%} del techo en esa marcha). No se ajusta: la "
            "diferencia puede venir del radio efectivo bajo carga, del deslizamiento o "
            "de un límite por resistencia; no hay datos para distinguirlo"
        )
        checks.append(InfoCheck("Top Speed", top, fastest.speed_at_ceiling, why))
    return checks


# Requisito de compatibilidad con AC (ver docstring del módulo y ensamblado.md §9.15).
VALID_SHIFT_RPM_WINDOW = 800


def _gears_lines(dt: Drivetrain, final_note: str) -> list[str]:
    lines = [
        "[GEARS]",
        f"COUNT={len(dt.forward)}",
        f"GEAR_R={dt.reverse:g}",
    ]
    lines += [f"GEAR_{i + 1}={g:g}" for i, g in enumerate(dt.forward)]
    lines.append(f"FINAL={dt.final_drive:g}\t\t; {final_note}")
    return lines


def _gearbox_lines() -> list[str]:
    return [
        "[GEARBOX]",
        f"VALID_SHIFT_RPM_WINDOW={VALID_SHIFT_RPM_WINDOW}\t\t; requisito de "
        "compatibilidad con AC, no deriva de BeamNG: sin la clave AC divide por 0 la "
        "ventana de cambio y el indicador de transmision parpadea (ensamblado.md "
        "9.15); 800 como ks_toyota_celica_st185 y ks_toyota_ae86",
        "",
        "; PENDIENTE GEARBOX tiempos/inercia/CONTROLS_WINDOW_GAIN,",
        "; CLUTCH MAX_TORQUE, AUTOCLUTCH, AUTOBLIP: sin datos o sin correspondencia",
        "; verificada (ver informe JSON)",
        "",
    ]


def _diff_desc(name: str, setup: DiffSetup) -> str:
    # Comentario con los valores del origen y su clave original (SOURCE_KEY).
    return f"{name}: " + ", ".join(
        f"{SOURCE_KEY[k]}={setup.get(k)!r}" for k in SOURCE_KEY if setup.has(k)
    )


def _layout(result: DrivetrainResult) -> TractionLayout:
    if result.layout is None:
        raise DrivetrainDataError("resultado sin tracción interpretada (layout)")
    return result.layout


def _render_awd(result: DrivetrainResult) -> str:
    dt, lay = result.drivetrain, _layout(result)
    center = lay.center
    assert center is not None
    f, r = lay.axles["front"], lay.axles["rear"]
    share = lay.front_torque_share
    assert share is not None and r.torque_share is not None
    lines = [
        "; BORRADOR generado por beam2ac (fase 4, tracción total). Ver "
        "docs/research/catalogo_motor_awd_vmax_2026-10-30.md",
        f"; Camino motriz hasta el central: "
        f"{' -> '.join(dt.path[: dt.path.index(center.diff.device) + 1])}",
        "",
        "[TRACTION]",
        "TYPE=AWD\t\t; derivado del grafo (diferencial central "
        f"'{center.diff.device}'); AWD frente a AWD2: decisión pendiente",
        "",
        *_gears_lines(
            dt,
            f"gearRatio común de '{f.diff.device}' y '{r.diff.device}'; " "central 1:1",
        ),
        "",
        "; PENDIENTE seccion AWD: transmisión de AC lee FRONT_SHARE (x0,01) y",
        "; CENTRE_, FRONT_ y REAR_DIFF_POWER, COAST, PRELOAD; su uso en",
        "; transmisión 4WD de AC no esta analizado. Valores de BeamNG",
        "; (differential.lua):",
        f"; reparto del central: delantero {100.0 * share:g} % "
        f"(salida {f.center_output}), trasero {100 * r.torque_share:g} % "
        f"(salida {r.center_output}); {center.split_source}",
        "; " + _diff_desc(f"central {center.diff.device}", center.diff),
        "; " + _diff_desc(f"delantero {f.diff.device}", f.diff),
        "; " + _diff_desc(f"trasero {r.diff.device}", r.diff),
        "; BeamNG: bloqueo = lsdPreload + lsdLockCoef x |par de entrada| con muelle",
        "; de angulo (lockSpring); el par de bloqueo y la dinamica no tienen",
        "; equivalente verificado en AC",
        "",
        *_gearbox_lines(),
    ]
    return sanitize_ini_comments("\n".join(lines))


def render_drivetrain_ini(result: DrivetrainResult) -> str:
    """Borrador ``drivetrain.ini`` (fase 4). G2.11: sin borrador con convertidor de par
    (AC no tiene convertidor; decisión D-G211-TC pendiente); con ``dctGearbox`` el
    borrador lleva una nota de lo que no se traslada."""
    dt = result.drivetrain
    if dt.converter is not None:
        raise DrivetrainDataError(
            f"caja automática con convertidor de par '{dt.converter.name}': sin "
            "borrador drivetrain.ini (AC no tiene convertidor; decisión D-G211-TC "
            "pendiente, docs/research/g211_transmissions_2026-11-02.md)"
        )
    text = _render_drivetrain_ini(result)
    if dt.gearbox.type == "dctGearbox":
        note = sanitize_ini_comments(
            "; G2.11: caja dctGearbox (doble embrague). Relaciones = gearRatios "
            "(dctGearbox.lua l. 417-445); el traspaso entre embragues (0,05-0,5 s),\n"
            "; los modos automaticos y la capacidad de los embragues no se trasladan\n"
        )
        text = note + text
    return text


def _render_drivetrain_ini(result: DrivetrainResult) -> str:
    dt = result.drivetrain
    lay = _layout(result)
    if result.traction == "AWD" and lay.center is not None:
        return _render_awd(result)
    if result.traction not in WRITABLE_TRACTION:
        raise DrivetrainDataError(
            f"tracción {result.traction}: el borrador solo cubre FWD/RWD "
            "(AWD necesita secciones adicionales sin verificar)"
        )
    role = "front" if result.traction == "FWD" else "rear"
    if role not in lay.axles:
        raise DrivetrainDataError(f"tracción {result.traction} sin diferencial de eje")
    d = lay.axles[role].diff
    lines = [
        "; BORRADOR generado por beam2ac (fase 4). Ver docs/research/transmision.md",
        f"; Camino motriz: {' -> '.join(dt.path)}",
        "",
        "[TRACTION]",
        f"TYPE={result.traction}\t\t; derivado del grafo (ruedas motrices: "
        f"{', '.join(dt.driven_wheels)})",
        "",
        "[GEARS]",
        f"COUNT={len(dt.forward)}",
        f"GEAR_R={dt.reverse:g}",
    ]
    lines += [f"GEAR_{i + 1}={g:g}" for i, g in enumerate(dt.forward)]
    lines += [
        f"FINAL={dt.final_drive:g}\t\t; gearRatio de '{d.device}'",
        "",
        "[DIFFERENTIAL]",
    ]
    dtype = d.get("kind")
    if dtype not in (None, "lsd"):
        # differential.lua l. 126-134: open/torqueVectoring comparten la actualización
        # de diferencial abierto (torqueVectoring añade un controlador externo);
        # viscous, locked, activeLock tienen la suya. Nada de eso se traduce aquí.
        lines.append(
            f"; diffType={dtype!r} en BeamNG: sin PRELOAD/POWER/COAST escritos; "
            "equivalencia en AC sin verificar"
        )
    if dtype == "lsd" and d.has("preload"):
        lines.append(
            f"PRELOAD={d.get('preload'):g}\t\t; lsdPreload (par de bloqueo inicial; "
            "unidad declarada 'N/m', se asume Nm)"
        )
    lines += [
        "; PENDIENTE POWER/COAST: BeamNG lsdLockCoef="
        f"{d.get('lock_coef')!r}, lsdRevLockCoef={d.get('rev_lock_coef')!r} "
        "(par adicional proporcional al par motor); equivalencia sin verificar",
        "",
        "[GEARBOX]",
        f"VALID_SHIFT_RPM_WINDOW={VALID_SHIFT_RPM_WINDOW}		; requisito de "
        "compatibilidad con AC, no deriva de BeamNG: sin la clave AC divide por 0 la "
        "ventana de cambio y el indicador de transmision parpadea (ensamblado.md "
        "9.15); 800 como ks_toyota_celica_st185 y ks_toyota_ae86",
        "",
        "; PENDIENTE GEARBOX tiempos/inercia/CONTROLS_WINDOW_GAIN,",
        "; CLUTCH MAX_TORQUE, AUTOCLUTCH, AUTOBLIP: sin datos o sin correspondencia",
        "; verificada (ver informe JSON)",
        "",
    ]
    return sanitize_ini_comments("\n".join(lines))


def drivetrain_report(result: DrivetrainResult) -> dict[str, Any]:
    dt = result.drivetrain
    return {
        "raw": {
            "devices": {
                n: {
                    "type": d.type,
                    "input": d.input_name,
                    "inputIndex": d.input_index,
                    "part": d.part,
                    "params": d.params,
                }
                for n, d in dt.devices.items()
            },
            "gearRatios": dt.gear_ratios_raw,
            "shift_controller": result.shift_controller_raw,
        },
        "derived": {
            "path": dt.path,
            "driven_wheels": dt.driven_wheels,
            "undriven_axles": dt.undriven_axles,
            "traction": result.traction,
            "reverse": dt.reverse,
            "forward": dt.forward,
            "final_drive": dt.final_drive,
            "driven_wheel_radius": result.driven_radius,
            "rpm_ceiling": result.rpm_ceiling,
            "gear_speeds": [asdict(s) for s in result.speeds],
        },
        "info_checks": [asdict(c) for c in result.checks],
        "status": dt.status,
        "issues": dt.issues,
    } | (
        {"transmission": asdict(result.transmission)}
        if result.transmission is not None
        else {}
    )


def write_drivetrain_files(result: DrivetrainResult, out_dir: str | Path) -> dict:
    out = ensure_safe_output_path(out_dir)
    files = {
        "drivetrain.ini": render_drivetrain_ini(result),
        "drivetrain_report.json": json.dumps(
            drivetrain_report(result), indent=2, ensure_ascii=False
        ),
    }
    written = {}
    for name, text in files.items():
        target = ensure_safe_output_path(out / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written[name] = target
    return written


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m src.ac.drivetrain",
        description="Transmisión BeamNG -> borrador drivetrain.ini. "
        "Sin --out no escribe.",
    )
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("--out", help="carpeta de salida (fuera de sample_*)")
    args = parser.parse_args(argv)
    resolved = resolve(PartIndex.from_directories(args.vehicle_dir), load_pc(args.pc))
    info_path = Path(args.vehicle_dir) / f"info_{Path(args.pc).stem}.json"
    info = load(info_path).value if info_path.is_file() else {}
    result = analyse(resolved, info=info)
    dt = result.drivetrain
    print(f"Tracción {result.traction}; camino: {' -> '.join(dt.path)}")
    print(f"R {dt.reverse:g}; marchas {dt.forward}; final {dt.final_drive:g}")
    print(
        f"Radio rueda motriz {result.driven_radius} m; "
        f"techo {_fmt(result.rpm_ceiling)} rpm"
    )
    for s in result.speeds:
        print(
            f"  {s.gear:>2}: total {s.total_ratio:.4f}  "
            f"v_techo {s.speed_at_ceiling:6.2f} m/s "
            f"({s.speed_at_ceiling * 3.6:6.1f} km/h)"
        )
    for c in result.checks:
        print(
            f"  {c.quantity}: ref {c.reference!r} / calc {c.computed!r} "
            f"-> {c.explanation}"
        )
    for issue in dt.issues:
        print(f"  aviso: {issue}")
    if args.out:
        for name, path in write_drivetrain_files(result, args.out).items():
            print(f"Escrito {name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
