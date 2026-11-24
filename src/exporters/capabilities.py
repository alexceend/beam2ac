"""Capacidades del destino: qué rasgos del vehículo puede representar el destino y cómo.

Datos en ``catalog/capabilities/<destino>.json``; condiciones de ``src.ir.conditions``.
:func:`assess` devuelve, para un IR, los rasgos presentes con su nivel
(``direct``, ``approximation``, ``fidelity_loss``, ``not_representable``,
``pending_research``) y el motivo. No cambia valores: explica qué se pierde.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.ir.conditions import all_hold
from src.ir.vehicle import VehicleIR

ROOT = Path(__file__).resolve().parents[2] / "catalog" / "capabilities"
LEVELS = (
    "direct",
    "approximation",
    "fidelity_loss",
    "not_representable",
    "pending_research",
)


@dataclass(frozen=True)
class Finding:
    feature: str
    level: str
    reason: str
    evidence: tuple[str, ...]


def load(target: str = "assettocorsa") -> dict[str, Any]:
    data = json.loads((ROOT / f"{target}.json").read_text(encoding="utf-8"))
    for f in data["features"]:
        if f["level"] not in LEVELS:
            raise ValueError(f"{f['id']}: nivel desconocido {f['level']!r}")
    return data


def assess(ir: VehicleIR, target: str = "assettocorsa") -> list[Finding]:
    return [
        Finding(f["id"], f["level"], f["reason"], tuple(f.get("evidence", [])))
        for f in load(target)["features"]
        if all_hold(ir, f.get("when", []))
    ]


def summary(findings: list[Finding]) -> dict[str, Any]:
    by_level: dict[str, list[str]] = {lvl: [] for lvl in LEVELS}
    for f in findings:
        by_level[f.level].append(f.feature)
    return {"by_level": by_level, "findings": [asdict(f) for f in findings]}
