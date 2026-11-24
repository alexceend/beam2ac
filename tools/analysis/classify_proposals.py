"""Clasifica las propuestas (reglas del Ibiza en otro vehículo) por riesgo y evidencia.

    python tools/analysis/classify_proposals.py
        --report output/<…>/report.json --out output/<nueva>

Solo lee el informe de resolución y el catálogo; NO aprueba ni aplica nada. Para cada
propuesta indica:

* ``risk``: ``bajo`` (sin efecto físico o geometría/lectura directa con semántica de AC
  documentada), ``medio`` (valor físico leído o medido cuya correspondencia tiene un
  supuesto declarado), ``alto`` (equivalencia de comportamiento entre motores físicos
  distintos o problema abierto);
* ``validation``: ``estática`` (se puede contrastar con datos del propio vehículo, sin
  ejecutar nada), ``prueba_específica`` (necesita una prueba del vehículo en AC o en
  BeamNG), ``decisión`` (hay que elegir entre interpretaciones);
* ``static_checks``: las comprobaciones estáticas posibles y su resultado si ya existen.

La tabla de criterios por regla está escrita aquí (``CRITERIA``) con su motivo; no se
infiere por nombres.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

# id de regla -> (riesgo, validación, motivo, comprobaciones estáticas posibles)
CRITERIA: dict[str, tuple[str, str, str, list[str]]] = {
    "car.screen_name": (
        "bajo",
        "estática",
        "texto de la interfaz, sin física",
        ["info.json Name"],
    ),
    "car.fuel": (
        "bajo",
        "estática",
        "lectura directa de mainTank (L); AC FUEL en L",
        ["startingFuelCapacity ≤ fuelCapacity"],
    ),
    "car.max_fuel": (
        "bajo",
        "estática",
        "lectura directa de mainTank (L)",
        ["fuelCapacity > 0"],
    ),
    "dt.gear_count": (
        "bajo",
        "estática",
        "número de relaciones leídas del gearbox",
        ["coincide con info 'Transmission' (manual) y con el número de relaciones"],
    ),
    "dt.gear_r": (
        "medio",
        "estática",
        "relación leída; signo y significado documentados",
        ["relación < 0"],
    ),
    **{
        f"dt.gear_{k}": (
            "medio",
            "estática",
            "relación de marcha leída del gearbox",
            ["velocidad punta teórica frente a info 'Top Speed'"],
        )
        for k in range(1, 9)
    },
    "dt.final": (
        "medio",
        "estática",
        "gearRatio del diferencial en el camino motriz",
        ["velocidad punta teórica frente a info 'Top Speed'"],
    ),
    "susp.wheelbase": (
        "bajo",
        "estática",
        "medida en los nodos; semántica de AC confirmada",
        ["coherencia con el plan de mallas (centros de rueda)"],
    ),
    "susp.track_front": (
        "bajo",
        "estática",
        "medida en los nodos",
        ["simetría izquierda/derecha"],
    ),
    "susp.track_rear": (
        "bajo",
        "estática",
        "medida en los nodos",
        ["simetría izquierda/derecha"],
    ),
    "tyre.front.rim_radius": (
        "bajo",
        "estática",
        "hubRadius leído",
        ["etiqueta del neumático (pulgadas)"],
    ),
    "tyre.rear.rim_radius": (
        "bajo",
        "estática",
        "hubRadius leído",
        ["etiqueta del neumático (pulgadas)"],
    ),
    "tyre.front.width": (
        "bajo",
        "estática",
        "tireWidth leído",
        ["etiqueta del neumático (mm)"],
    ),
    "tyre.rear.width": (
        "bajo",
        "estática",
        "tireWidth leído",
        ["etiqueta del neumático (mm)"],
    ),
    "tyre.front.pressure_static": (
        "medio",
        "prueba_específica",
        "pressurePSI leída; AC usa la presión en su modelo "
        "de neumático (sin equivalente)",
        [],
    ),
    "tyre.rear.pressure_static": ("medio", "prueba_específica", "ídem", []),
    "tyre.front.radius": (
        "medio",
        "estática",
        "radio de nodos SIN carga (supuesto)",
        ["etiqueta del neumático", "velocidad punta teórica"],
    ),
    "tyre.rear.radius": (
        "medio",
        "estática",
        "radio de nodos SIN carga (supuesto)",
        ["etiqueta del neumático", "velocidad punta teórica"],
    ),
    "eng.minimum": (
        "medio",
        "prueba_específica",
        "supuesto MINIMUM = idleRPM (ralentí en AC)",
        [],
    ),
    "eng.limiter": (
        "alto",
        "prueba_específica",
        "supuesto LIMITER ≈ techo de rpm; corte en AC",
        [],
    ),
    "eng.inertia": (
        "alto",
        "prueba_específica",
        "falta la inercia del embrague (additionalEngineInertia); respuesta del motor",
        [],
    ),
    "eng.coast_rpm": (
        "alto",
        "prueba_específica",
        "modelo de freno motor distinto (lineal desde 0)",
        [],
    ),
    "eng.coast_torque": ("alto", "prueba_específica", "ídem", []),
    "eng.coast_nonlinearity": ("alto", "prueba_específica", "ídem", []),
    "dt.preload": (
        "medio",
        "estática",
        "unidad: differential.lua:57 suma lsdPreload a un par ⇒ N·m (evidencia nueva); "
        "el modelo LSD de AC (PRELOAD + POWER/COAST) no es el de BeamNG",
        ["dimensión por el código (hecha)"],
    ),
    "dt.clutch_max_torque": (
        "alto",
        "prueba_específica",
        "fórmula de lockTorque verificada; equivalencia con MAX_TORQUE de AC supuesta",
        [],
    ),
    "brk.front_share": (
        "alto",
        "decisión",
        "semántica de AC confirmada en AC (fracción de MAX_TORQUE por rueda "
        "delantera; convenciones_fisicas_2026-10-26.md §2), pero AC no modela el codo "
        "de brakeInputSplit: hay que elegir el régimen de pedal",
        ["reparto T_f/(T_f+T_r) por eje sin codo (pedal completo)"],
    ),
}


def static_checks(vehicle_zip: Path, common_zip: Path | None) -> dict[str, Any]:
    """Comprobaciones estáticas con datos del propio vehículo (sin ejecutar nada)."""
    from src.ac.chassis import chassis_report
    from src.pipeline.physics_preview import load_vehicle_zip, preview_physics

    pv = preview_physics(load_vehicle_zip(vehicle_zip, common_zip))
    out: dict[str, Any] = {"preview_ok": pv.ok}
    if pv.chassis is not None:
        out["tyre_label"] = chassis_report(pv.chassis)["tyres"]["label_checks"]
    if pv.drivetrain is not None:
        out["drivetrain_info"] = [
            {
                "quantity": c.quantity,
                "reference": c.reference,
                "computed": c.computed,
                "explanation": c.explanation,
            }
            for c in pv.drivetrain.checks
        ]
    return out


def classify(
    report: dict[str, Any], measured: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    facts = {k: v["value"] for k, v in report["facts"].items()}
    rows = []
    for r in report["results"]:
        if not r.get("requires_approval"):
            continue
        rid = r["rule"].split("@")[0]
        risk, validation, why, checks = CRITERIA.get(
            rid,
            ("sin clasificar", "decisión", "regla sin criterio escrito: revisar", []),
        )
        done: dict[str, Any] = {}
        m = measured or {}
        if rid in (
            "dt.final",
            "tyre.front.radius",
            "tyre.rear.radius",
        ) or rid.startswith("dt.gear_"):
            done["velocidad punta"] = [
                c for c in m.get("drivetrain_info", []) if c["quantity"] == "Top Speed"
            ] or "no calculada"
        if rid.startswith("tyre.") and m.get("tyre_label"):
            axle = "F" if ".front." in rid else "R"
            done["etiqueta"] = [
                c for c in m["tyre_label"] if c["wheel"].startswith(axle)
            ][:1]
        if rid == "car.fuel":
            done["start ≤ capacity"] = facts.get("bng.fuel.start", 0) <= facts.get(
                "bng.fuel.capacity", -1
            )
        if rid == "dt.gear_r":
            done["relación < 0"] = (
                isinstance(r["value"], (int, float)) and r["value"] < 0
            )
        rows.append(
            {
                "target": r["target"],
                "rule": r["rule"],
                "value": r["value"],
                "unit": r["unit"],
                "status": r["status"],
                "confidence": r["confidence"],
                "risk": risk,
                "validation": validation,
                "why": why,
                "assumptions": r["assumptions"],
                "open_issues": r["open_issues"],
                "static_checks_possible": checks,
                "static_checks_done": done,
                "approved": False,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--vehicle-zip", type=Path, help="para las comprobaciones estáticas"
    )
    ap.add_argument("--common-zip", type=Path)
    args = ap.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    checks = (
        static_checks(args.vehicle_zip, args.common_zip) if args.vehicle_zip else {}
    )
    rows = classify(report, checks)
    out = new_output_dir(args.out)
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        summary.setdefault(row["risk"], {}).setdefault(row["validation"], 0)
        summary[row["risk"]][row["validation"]] += 1
    data = {
        "schema": "beam2ac.proposal_review/1",
        "vehicle": report["vehicle"],
        "variant": report["variant"],
        "source_report": str(args.report),
        "count": len(rows),
        "summary": summary,
        "static_checks": checks,
        "proposals": rows,
        "note": "Clasificación; NADA se aprueba ni se aplica.",
    }
    ensure_safe_output_path(out / "proposals.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"count": len(rows), "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
