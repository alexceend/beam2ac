"""Clasificación de arquitecturas del vehículo sobre el IR (catalog/architectures.json).

Separa tres niveles que no deben confundirse:

* **arquitectura del vehículo** (``level: vehicle``): tracción + tipo de caja. Como
  mucho una; si encajan dos, es un error del catálogo; si no encaja ninguna, se dice
  (``vehicle = None``) en vez de suponer una;
* **rasgos de componentes** (``level: component``): p. ej. un diferencial
  ``torqueVectoring`` o LSD; se acumulan;
* **configuración concreta**: los valores del IR (relaciones, precargas…), que no forman
  parte de la arquitectura.

Que dos vehículos compartan arquitectura NO implica que se comporten igual: solo que las
reglas condicionadas a ella pueden evaluarse (y cada regla comprueba sus entradas).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .conditions import all_hold, values
from .param import IRError
from .vehicle import VehicleIR

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "architectures.json"


def load(path: Path = CATALOG) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    archs = data["architectures"]
    ids = [a["id"] for a in archs]
    if len(ids) != len(set(ids)):
        raise IRError("ids de arquitectura repetidos")
    for a in archs:
        if a["level"] not in ("vehicle", "component") or not a["conditions"]:
            raise IRError(f"arquitectura mal definida: {a['id']}")
    return archs


def classify(ir: VehicleIR, archs: list[dict[str, Any]] | None = None) -> dict:
    archs = archs if archs is not None else load()
    vehicle = [
        a["id"]
        for a in archs
        if a["level"] == "vehicle" and all_hold(ir, a["conditions"])
    ]
    if len(vehicle) > 1:
        raise IRError(f"varias arquitecturas de vehículo a la vez: {vehicle}")
    components = [
        a["id"]
        for a in archs
        if a["level"] == "component" and all_hold(ir, a["conditions"])
    ]
    result = {
        "vehicle": vehicle[0] if vehicle else None,
        "components": components,
        "layout": (values(ir, "drivetrain.layout") or [None])[0],
        "gearbox": (values(ir, "drivetrain.gearbox.kind") or [None])[0],
        "differentials": {
            role: (values(ir, f"drivetrain.differentials.{role}.kind") or [None])[0]
            for role in sorted(ir.drivetrain.differentials)
        },
        "unclassified_reason": (
            None
            if vehicle
            else "ninguna arquitectura de vehículo del catálogo encaja (o falta la "
            "tracción o la caja en el IR)"
        ),
    }
    ir.architecture = result
    return result
