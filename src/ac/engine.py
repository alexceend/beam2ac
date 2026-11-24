"""Representación del motor para Assetto Corsa (borrador de la fase 3).

Decisión 1 del proyecto: ``power.lut`` = par NETO a gas a fondo de BeamNG, que
YA incluye el boost del turbo; por eso el ``engine.ini`` generado no tiene
ninguna sección ``[TURBO_n]`` (AC multiplicaría el par por ``1 + boost`` y el
turbo se aplicaría dos veces). El retraso del turbo queda fuera de alcance.

Equivalencias BeamNG → AC usadas y su justificación (docs/research/motor.md §4):

=================  ============================  ==================================
Clave AC           Fuente                        Estado de la equivalencia
=================  ============================  ==================================
POWER_CURVE        curva neta WOT                derivada (decisión 1)
LIMITER            techo de rpm del Lua          hipótesis: LIMITER = corte de rpm
MINIMUM            idleRPM                       hipótesis: MINIMUM = ralentí
INERTIA            inertia del motor             hipótesis: misma magnitud; falta
                                                 la inercia del embrague
COAST_REF          freno motor del Lua           hipótesis: forma lineal desde 0
                                                 (comentario del ini de muestra)
=================  ============================  ==================================

No se escriben (pendientes): ``LIMITER_HZ``, ``ALTITUDE_SENSITIVITY``,
``[DAMAGE]``. Este archivo es un BORRADOR para la fase de ensamblado (F7).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.ac.coast_ref import CoastRefFit, coast_ref_linear
from src.extract.engine_camso import (
    HP_W,
    EngineModel,
    InfoCheck,
    build_engine_model,
    compare_with_info,
)
from src.jbeam import PartIndex, load, load_pc, resolve
from src.ac.ini_format import sanitize_ini_comments
from src.safe_paths import ensure_safe_output_path


def fit_coast_ref(model: EngineModel) -> CoastRefFit | None:
    """COAST_REF con NON_LINEARITY = 0: freno(rpm) = TORQUE · rpm / RPM.

    La forma "lineal desde cero" sale del comentario del engine.ini de muestra
    ("coast engine brake from ZERO to TORQUE value at rpm with linear (0)…").
    Se ancla en el techo de rpm y se informa del error; no se optimiza. El cálculo es
    ``src.ac.coast_ref.coast_ref_linear`` (compartido con la transformación
    ``ac_coast_ref`` de las reglas).
    """
    return coast_ref_linear(model.coast, model.rpm_ceiling)


def _fmt(x: float) -> str:
    return f"{int(x)}" if float(x).is_integer() else f"{x:g}"


def render_power_lut(model: EngineModel, decimals: int = 2) -> str:
    lines = [
        f"{_fmt(p.rpm)}|{p.net:.{decimals}f}"
        for p in model.curve
        if p.rpm <= model.rpm_ceiling
    ]
    return "\n".join(lines) + "\n"


def render_engine_ini(model: EngineModel, coast: CoastRefFit | None) -> str:
    raw = model.raw
    lines = [
        "; BORRADOR generado por beam2ac (fase 3). Ver docs/research/motor.md",
        f"; Fuente: {raw.device_type} '{raw.device_name}' de "
        f"{', '.join(raw.source_parts)}",
        "; Sin seccion [TURBO_n]: power.lut ya incluye el boost (decision 1).",
        "",
        "[HEADER]",
        "VERSION=1",
        "POWER_CURVE=power.lut\t\t; par neto WOT (throttle 99 - frictionTorque)",
        "COAST_CURVE=FROM_COAST_REF",
        "",
        "[ENGINE_DATA]",
        f"INERTIA={raw.inertia:g}\t\t; hipotesis: solo motor, "
        "falta inercia del embrague",
        f"LIMITER={_fmt(model.rpm_ceiling)}\t\t; hipotesis de equivalencia; "
        "techo de rpm derivado del Lua",
        f"MINIMUM={_fmt(raw.idle_rpm)}\t\t; hipotesis: MINIMUM = idleRPM",
        "; PENDIENTE: LIMITER_HZ, ALTITUDE_SENSITIVITY (sin equivalente verificado)",
    ]
    if coast:
        lines += [
            "",
            "[COAST_REF]",
            f"RPM={_fmt(coast.rpm)}",
            f"TORQUE={coast.torque:.1f}\t\t; freno motor BeamNG en RPM",
            "NON_LINEARITY=0\t\t; hipotesis: forma lineal desde 0 "
            f"(error max {coast.max_abs_error:.1f} Nm a {_fmt(coast.worst_rpm)} rpm)",
        ]
    lines += [
        "",
        "; PENDIENTE [DAMAGE]: BeamNG empieza el dano por sobrerregimen a "
        f"{_fmt(model.overrev_damage_rpm)} rpm; "
        "equivalencia con RPM_THRESHOLD sin verificar",
        "",
    ]
    text = "\n".join(lines)
    if has_turbo_section(text):
        raise AssertionError(
            "el engine.ini no debe tener [TURBO_n] (boost ya en power.lut)"
        )
    return sanitize_ini_comments(text)


def has_turbo_section(ini_text: str) -> bool:
    """True si el ini define alguna sección [TURBO_n] (ignora comentarios)."""
    return any(
        line.strip().upper().startswith("[TURBO") for line in ini_text.splitlines()
    )


def engine_report(
    model: EngineModel, checks: list[InfoCheck], coast: CoastRefFit | None
) -> dict[str, Any]:
    raw = model.raw
    return {
        "device": {"type": raw.device_type, "name": raw.device_name},
        "source_parts": raw.source_parts,
        "raw": {
            "torque_rows": len(raw.torque_rows),
            "throttle_levels": sorted({r.throttle for r in raw.torque_rows}),
            "friction_rows": raw.friction_rows,
            "idleRPM": raw.idle_rpm,
            "maxRPM": raw.max_rpm,
            "maxPhysicalRPM": raw.max_physical_rpm,
            "inertia": raw.inertia,
            "hasRevLimiter_candidates": raw.rev_limiter_candidates,
            "revLimiterRPM": raw.rev_limiter_rpm,
            "revLimiterType": raw.rev_limiter_type,
            "revLimiterRPMDrop": raw.rev_limiter_rpm_drop,
            "turbo_section": raw.turbo_section_name,
            "turbo": {k: v for k, v in raw.turbo.items() if not isinstance(v, list)},
            "unused_keys": raw.other_keys,
        },
        "derived": {
            "wot_throttle": model.wot_throttle,
            "peak_torque": {"rpm": model.peak_torque.rpm, "Nm": model.peak_torque.net},
            "peak_power": {
                "rpm": model.peak_power.rpm,
                "kW": model.peak_power.power_w / 1000,
                "hp": model.peak_power.power_w / HP_W,
            },
            "rpm_ceiling": model.rpm_ceiling,
            "rpm_ceiling_basis": model.rpm_ceiling_basis,
            "has_cut_limiter": model.has_cut_limiter,
            "overrev_damage_rpm": model.overrev_damage_rpm,
            "max_boost_bar_gauge": model.max_boost_bar,
            "full_boost_rpm": model.full_boost_rpm,
            "curve": [
                {**asdict(p), "na_equivalent": p.na_equivalent} for p in model.curve
            ],
            "coast": model.coast,
        },
        "ac_candidates": {
            "power_lut": "par neto WOT con boost; sin [TURBO_n]",
            "coast_ref": asdict(coast) if coast else None,
        },
        "info_checks": [asdict(c) for c in checks],
        "status": model.status,
        "duplicate_rpms": model.duplicate_rpms,
        "issues": model.issues,
    }


def write_engine_files(
    model: EngineModel,
    out_dir: str | Path,
    checks: list[InfoCheck] | None = None,
) -> dict[str, Path]:
    """Escribe power.lut, engine.ini (borrador) y engine_report.json."""
    out = ensure_safe_output_path(out_dir)
    coast = fit_coast_ref(model)
    files = {
        "power.lut": render_power_lut(model),
        "engine.ini": render_engine_ini(model, coast),
        "engine_report.json": json.dumps(
            engine_report(model, checks or [], coast), indent=2, ensure_ascii=False
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
        prog="python -m src.ac.engine",
        description="Motor BeamNG (camsoEngine) -> borrador power.lut/engine.ini. "
        "Sin --out no escribe nada.",
    )
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("--out", help="carpeta de salida (fuera de sample_*)")
    args = parser.parse_args(argv)

    resolved = resolve(PartIndex.from_directories(args.vehicle_dir), load_pc(args.pc))
    model = build_engine_model(resolved)
    info_path = Path(args.vehicle_dir) / f"info_{Path(args.pc).stem}.json"
    checks = (
        compare_with_info(model, load(info_path).value) if info_path.is_file() else []
    )

    t, p = model.peak_torque, model.peak_power
    print(
        f"Motor {model.raw.device_type} '{model.raw.device_name}' "
        f"({len(model.curve)} puntos)"
    )
    print(f"Par neto máx.: {t.net:.2f} Nm @ {_fmt(t.rpm)} rpm")
    print(
        f"Potencia neta máx.: {p.power_w / 1000:.2f} kW = "
        f"{p.power_w / HP_W:.2f} hp @ {_fmt(p.rpm)} rpm"
    )
    print(f"Techo de rpm: {_fmt(model.rpm_ceiling)} ({model.rpm_ceiling_basis})")
    print(f"Boost máx.: {model.max_boost_bar} bar desde {model.full_boost_rpm} rpm")
    for c in checks:
        print(
            f"  {c.quantity}: ref {c.reference:g} / calc {c.computed:.2f} "
            f"-> {c.explanation}"
        )
    for issue in model.issues:
        print(f"  aviso: {issue}")
    if args.out:
        for name, path in write_engine_files(model, args.out, checks).items():
            print(f"Escrito {name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
