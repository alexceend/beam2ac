"""Suspensión, frenos, neumáticos y aero para Assetto Corsa (borradores de la fase 5).

Solo se escriben claves cuya correspondencia está respaldada por los comentarios
de los ini de AC de la muestra o por la definición de la magnitud
(docs/research/suspension_frenos_neumaticos_aero.md §5):

* suspensions.ini: ``WHEELBASE``; ``TRACK`` ("Track width in meters (from pivot
  3D placement of the 3d model of a wheel)" = distancia entre centros de rueda);
  ``TYPE=STRUT`` solo si la pieza se etiqueta MacPherson/Strut.
* brakes.ini: ``FRONT_SHARE`` ("Percentance of brake torque at front axis") a
  pedal 100 %. Es el cociente entre ejes, igual tanto si ``MAX_TORQUE`` es por
  rueda como si es total.
* tyres.ini: ``RADIUS`` (radio nominal sin carga; la muestra de AC lo cumple:
  235/40/17 → 0,30988), ``RIM_RADIUS`` ("use 1 inch more than nominal"; en
  BeamNG ``hubRadius`` = nominal + 1"), ``WIDTH`` (anchura nominal),
  ``PRESSURE_STATIC`` (psi en ambos juegos).

Todo lo demás queda pendiente y se explica en el informe JSON. ``aero.ini`` no se
escribe: la fórmula de fuerza de las alas de AC no está documentada en la muestra.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.extract.aero import Aero, AeroDataError, aero_model, build_aero
from src.extract.steering import SteeringDataError, SteeringSystem, build_steering
from src.extract.brakes_tyres import (
    BrakesTyres,
    BrakeTyreDataError,
    build_brakes_tyres,
    front_share,
)
from src.extract.mass_geometry import MassGeometry, compute_mass_geometry
from src.extract.suspension import Suspension, build_suspension
from src.jbeam import PartIndex, ResolvedConfig, load_pc, resolve
from src.ac.ini_format import sanitize_ini_comments
from src.safe_paths import ensure_safe_output_path

PENDING = {
    "suspensions.ini": [
        "CG_LOCATION: semántica (¿fracción delantera?) no documentada en la muestra",
        "BASEY: fórmula del comentario ambigua; además depende de la altura en reposo",
        "SPRING_RATE, DAMP_*: son valores en rueda; requieren la relación de palanca "
        "(solo hay hipótesis)",
        "BUMP_STOP_*, BUMPSTOP_UP/DN, PACKER_RANGE: recorridos en rueda "
        "(relación de palanca)",
        "puntos de anclaje (STRUT_*, WB*): sistema de referencia de AC no documentado",
        "STATIC_CAMBER/TOE_OUT: AC los aplica sobre su propia geometría (pendiente)",
        "HUB_MASS: falta la partición suspendida/no suspendida",
        "[ARB]: unidad ('in Nm') y modelo de AC no verificados",
        "[REAR] TYPE: eje torsional sin tipo equivalente en AC",
        "[HEADER] VERSION: significado no documentado",
    ],
    "brakes.ini": [
        "MAX_TORQUE: no se sabe si AC lo entiende por rueda o total",
        "HANDBRAKE_TORQUE: misma ambigüedad (BeamNG: parkingTorque por rueda)",
        "AC no modela el codo de freno de BeamNG (reparto 0,818 → 0,857)",
    ],
    "tyres.ini": [
        "modelo de agarre (DX/DY, LS_EXP*, FZ0…): incompatible con el de BeamNG",
        "ANGULAR_INERTIA: estimación sin disco (hipótesis de la fase 2)",
        "RATE/DAMP del neumático: requieren simular el neumático de presión",
        "[HEADER] VERSION y compuestos: elección de modelo de AC",
        "térmico y desgaste: sin equivalente",
    ],
    "aero.ini": [
        "no se escribe: la fórmula de las alas de AC y sus LUT no están "
        "documentadas en la muestra (ver informe: Cd·A, Cl·A y puntos)",
    ],
    "car.ini": [
        "TOTALMASS: decisión 2 pendiente de verificar si AC incluye al piloto",
        "INERTIA (caja), dirección, combustible: fuera del alcance pedido en la fase 5",
    ],
}


class ChassisWriteError(ValueError):
    """No se puede generar un borrador coherente."""


@dataclass
class ChassisResult:
    geometry: MassGeometry
    suspension: Suspension
    brakes_tyres: BrakesTyres
    aero: Aero | None
    aero_error: str | None
    front_wheels: list[str]
    rear_wheels: list[str]
    # G2.15: dirección (src/extract/steering.py); None con el motivo si no se reconoce
    steering: SteeringSystem | None = None
    steering_error: str | None = None
    aero_model: str | None = None  # G2.15: cefaero | triangles | none


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def analyse(resolved: ResolvedConfig) -> ChassisResult:
    geometry = compute_mass_geometry(resolved)
    if len(geometry.axles) != 2:
        raise ChassisWriteError("se necesitan exactamente 2 ejes")
    try:
        aero, aero_error = build_aero(resolved), None
    except AeroDataError as exc:
        aero, aero_error = None, str(exc)
    try:
        steering, steering_error = build_steering(resolved), None
    except SteeringDataError as exc:
        steering, steering_error = None, str(exc)
    return ChassisResult(
        aero_model=aero_model(resolved),
        steering=steering,
        steering_error=steering_error,
        geometry=geometry,
        suspension=build_suspension(resolved, geometry),
        brakes_tyres=build_brakes_tyres(resolved),
        aero=aero,
        aero_error=aero_error,
        front_wheels=list(geometry.axles[0].wheels),
        rear_wheels=list(geometry.axles[1].wheels),
    )


def _same(values: list[Any], what: str) -> Any:
    if not values or any(v != values[0] for v in values):
        raise ChassisWriteError(
            f"{what}: valores distintos entre ruedas del eje {values}"
        )
    return values[0]


def render_suspensions_ini(res: ChassisResult) -> str:
    g = res.geometry
    lines = [
        "; BORRADOR generado por beam2ac (fase 5). Ver docs/research/"
        "suspension_frenos_neumaticos_aero.md",
        "",
        "[BASIC]",
        f"WHEELBASE={_fmt(g.wheelbase)}\t\t; distancia entre ejes (fase 2)",
    ]
    for axle, section in zip(res.suspension.axles, ("[FRONT]", "[REAR]")):
        lines += ["", section]
        if axle.ac_type:
            lines.append(f"TYPE={axle.ac_type}\t\t; etiqueta: {axle.label}")
        else:
            lines.append(f"; PENDIENTE TYPE: '{axle.label}' sin tipo AC respaldado")
        track = g.axles[axle.index].track
        if track is None:
            raise ChassisWriteError(f"eje {axle.index} sin vía")
        lines.append(f"TRACK={_fmt(track)}\t\t; distancia entre centros de rueda")
    lines += (
        ["", "; PENDIENTE:"] + [f";  - {p}" for p in PENDING["suspensions.ini"]] + [""]
    )
    return sanitize_ini_comments("\n".join(lines))


def _split_brakes(res: ChassisResult):
    brakes = {b.wheel: b for b in res.brakes_tyres.brakes}
    missing = [w for w in res.front_wheels + res.rear_wheels if w not in brakes]
    if missing:
        raise ChassisWriteError(f"ruedas sin freno: {missing}")
    return [brakes[w] for w in res.front_wheels], [brakes[w] for w in res.rear_wheels]


def render_brakes_ini(res: ChassisResult) -> str:
    front, rear = _split_brakes(res)
    share = front_share(front, rear, 1.0)
    lines = (
        [
            "; BORRADOR generado por beam2ac (fase 5)",
            "",
            "[DATA]",
            f"FRONT_SHARE={share:.4f}\t\t; reparto del par entre ejes a pedal 100 %",
            "; PENDIENTE:",
        ]
        + [f";  - {p}" for p in PENDING["brakes.ini"]]
        + [""]
    )
    return sanitize_ini_comments("\n".join(lines))


def render_tyres_ini(res: ChassisResult) -> str:
    tyres = {t.wheel: t for t in res.brakes_tyres.tyres}
    lines = ["; BORRADOR generado por beam2ac (fase 5)", ""]
    for section, wheels in (("[FRONT]", res.front_wheels), ("[REAR]", res.rear_wheels)):
        ts = [tyres[w] for w in wheels]
        lines += [
            section,
            f"RADIUS={_fmt(_same([t.radius for t in ts], 'radius'))}"
            "\t\t; radio sin carga",
            f"RIM_RADIUS={_fmt(_same([t.hub_radius for t in ts], 'hubRadius'))}"
            "\t\t; hubRadius (= nominal + 1 pulgada)",
        ]
        width = _same([t.width for t in ts], "tireWidth")
        if width is not None:
            lines.append(f"WIDTH={_fmt(width)}")
        psi = _same([t.pressure_psi for t in ts], "pressurePSI")
        if psi is not None:
            lines.append(f"PRESSURE_STATIC={_fmt(psi)}\t\t; psi")
        lines.append("")
    lines += ["; PENDIENTE:"] + [f";  - {p}" for p in PENDING["tyres.ini"]] + [""]
    return sanitize_ini_comments("\n".join(lines))


def chassis_report(res: ChassisResult) -> dict[str, Any]:
    front, rear = _split_brakes(res)
    corners = []
    for axle in res.suspension.axles:
        for c in axle.corners:
            d = asdict(c)
            d["preload_vs_sprung_load_hyp"] = (
                None
                if c.preload_at_wheel_hyp is None
                else c.preload_at_wheel_hyp / c.sprung_corner_load
            )
            corners.append(d)
    tyre_checks = []
    for t in res.brakes_tyres.tyres:
        if t.nominal:
            tyre_checks.append(
                {
                    "wheel": t.wheel,
                    "label": t.label,
                    "nominal_radius": t.nominal.radius,
                    "node_radius": t.radius,
                    "radius_difference": t.radius - t.nominal.radius,
                    "nominal_rim_plus_1in": t.nominal.rim_radius + 0.0254,
                    "hub_radius": t.hub_radius,
                }
            )
    return {
        "suspension": {
            "axles": [
                {
                    "index": a.index,
                    "label": a.label,
                    "ac_type": a.ac_type,
                    "torsionbars": a.torsionbars,
                }
                for a in res.suspension.axles
            ],
            "corners": corners,
            "status": res.suspension.status,
            "issues": res.suspension.issues,
        },
        "brakes": {
            "wheels": [asdict(b) for b in res.brakes_tyres.brakes],
            "front_share_pedal_100": front_share(front, rear, 1.0),
            "front_share_below_knee": front_share(front, rear, 0.5),
        },
        "tyres": {
            "wheels": [asdict(t) for t in res.brakes_tyres.tyres],
            "label_checks": tyre_checks,
            "issues": res.brakes_tyres.issues,
        },
        "aero": asdict(res.aero) if res.aero else {"error": res.aero_error},
        "pending": PENDING,
    }


def write_chassis_files(res: ChassisResult, out_dir: str | Path) -> dict[str, Path]:
    out = ensure_safe_output_path(out_dir)
    files = {
        "suspensions.ini": render_suspensions_ini(res),
        "brakes.ini": render_brakes_ini(res),
        "tyres.ini": render_tyres_ini(res),
        "chassis_report.json": json.dumps(
            chassis_report(res), indent=2, ensure_ascii=False
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
        prog="python -m src.ac.chassis",
        description="Suspensión/frenos/neumáticos/aero -> borradores AC. "
        "Sin --out no escribe.",
    )
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("--out", help="carpeta de salida (fuera de sample_*)")
    args = parser.parse_args(argv)
    res = analyse(
        resolve(PartIndex.from_directories(args.vehicle_dir), load_pc(args.pc))
    )
    for axle in res.suspension.axles:
        print(f"Eje {axle.index}: {axle.label} -> TYPE {axle.ac_type or 'pendiente'}")
        for c in axle.corners:
            ratio = (
                c.preload_at_wheel_hyp / c.sprung_corner_load
                if c.preload_at_wheel_hyp
                else float("nan")
            )
            print(
                f"  {c.wheel}: muelle {c.spring_rate:g} N/m, incl. "
                f"{c.spring_inclination_deg:.2f}°, caída {c.camber_deg:.2f}°, "
                f"conv. {c.toe_deg:.2f}°, precarga/carga {ratio:.3f} (hip.)"
            )
    for b in res.brakes_tyres.brakes:
        print(f"  freno {b.wheel}: máx {b.max_torque:.1f} Nm (tasa {b.rate:.1f})")
    if res.aero:
        a = res.aero
        print(
            f"Aero: CdA {a.cda} m², ClA del. {a.cla_front:.4f} / "
            f"tras. {a.cla_rear:.4f} m²"
        )
    else:
        print(f"Aero: {res.aero_error}")
    for issue in res.suspension.issues + res.brakes_tyres.issues:
        print(f"  aviso: {issue}")
    if args.out:
        try:
            for name, path in write_chassis_files(res, args.out).items():
                print(f"Escrito {name}: {path}")
        except (ChassisWriteError, BrakeTyreDataError) as exc:
            print(f"ERROR: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
