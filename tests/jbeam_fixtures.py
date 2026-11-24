"""Construcción de vehículos JBeam mínimos en carpetas temporales (para tests)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from src.jbeam import PartIndex, ResolvedConfig, parse_pc, resolve
from src.safe_paths import ensure_safe_output_path

NODE_HEADER = ["id", "posX", "posY", "posZ"]
SLOT_HEADER = ["type", "default", "description"]
WHEEL_HEADER = [
    "name",
    "hubGroup",
    "group",
    "node1:",
    "node2:",
    "nodeS",
    "nodeArm:",
    "wheelDir",
]


def resolve_parts(
    parts: dict[str, dict[str, Any]],
    pc_parts: dict[str, str] | None = None,
    pc_vars: dict[str, Any] | None = None,
) -> ResolvedConfig:
    """Escribe ``parts`` como un .jbeam temporal y resuelve la configuración."""
    with tempfile.TemporaryDirectory() as tmp:
        target = ensure_safe_output_path(Path(tmp) / "vehicle.jbeam")
        target.write_text(json.dumps(parts), encoding="utf-8")
        index = PartIndex.from_directories(tmp)
    pc = parse_pc({"format": 2, "parts": pc_parts or {}, "vars": pc_vars or {}})
    return resolve(index, pc)


def wheel_props(**overrides: Any) -> dict[str, Any]:
    props = {
        "numRays": 4,
        "hubNodeWeight": 1.0,
        "nodeWeight": 0.5,
        "radius": 0.3,
        "hubRadius": 0.2,
        "tireWidth": 0.2,
        "hubWidth": 0.2,
    }
    props.update(overrides)
    return props


def simple_car(
    node_mass: float = 100.0, wheel_overrides: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Coche de 4 ruedas: batalla 2,0 (eje del. y = −1, tras. y = +1), vía 1,6,
    centros de rueda a z = 0,3 y un nodo de carrocería en el origen."""
    nodes = [NODE_HEADER, {"nodeWeight": node_mass}, ["body", 0.0, 0.0, 0.5]]
    axle_nodes = [NODE_HEADER, {"nodeWeight": 1.0}]
    wheels: list[Any] = [WHEEL_HEADER, wheel_props(**(wheel_overrides or {}))]
    for name, x, y in (
        ("FL", 0.8, -1.0),
        ("FR", -0.8, -1.0),
        ("RL", 0.8, 1.0),
        ("RR", -0.8, 1.0),
    ):
        inner = 0.7 if x > 0 else -0.7
        outer = 0.9 if x > 0 else -0.9
        axle_nodes += [[f"{name}_o", outer, y, 0.3], [f"{name}_i", inner, y, 0.3]]
        wheels.append([name, "hub", "tire", f"{name}_o", f"{name}_i", 9999, "body", 1])
    return {
        "car": {
            "slotType": "main",
            "nodes": nodes,
            "slots": [SLOT_HEADER, ["wheels", "wheel_part", "Wheels"]],
        },
        "wheel_part": {
            "slotType": "wheels",
            "nodes": axle_nodes,
            "pressureWheels": wheels,
        },
    }
