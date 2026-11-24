"""Clasificación de cobertura de una resolución: de dónde sale cada clave de AC.

No mide fidelidad física: dice si el valor procede del origen (``medida``), de un valor
de referencia que no sale del origen (``referencia``), si es un requisito de formato,
si no aplica, o si falta
(``propuesta`` sin aprobar o ``sin_regla``). Lo usan la matriz de cobertura
(``tools/analysis/coverage_matrix.py``) y el diagnóstico de la aplicación
(``src/app/diagnostics.py``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from .store import Catalog

CLASSES = ("medida", "referencia", "formato", "no_aplica", "propuesta", "sin_regla")

# (archivo, prefijo de sección) -> subsistema; gana el primero que casa
SUBSYSTEMS = {
    ("engine.ini", ""): "motor",
    ("power.lut", ""): "motor",
    ("drivetrain.ini", "AWD"): "diferenciales",
    ("drivetrain.ini", "DIFFERENTIAL"): "diferenciales",
    ("drivetrain.ini", ""): "transmisión",
    ("suspensions.ini", ""): "suspensión y masas",
    ("tyres.ini", "THERMAL"): "neumático: térmica",
    ("tyres.ini", ""): "neumático",
    ("brakes.ini", ""): "frenos",
    ("aero.ini", ""): "aerodinámica",
    ("car.ini", "CONTROLS"): "dirección",
    ("car.ini", ""): "masa, combustible e identidad",
}


def subsystem(target: str) -> str:
    file, _, rest = target[3:].partition("/")
    for (f, sec), name in SUBSYSTEMS.items():
        if f == file and rest.startswith(sec):
            return name
    return file


def klass(r: Any, cat: Catalog) -> str:
    """``r``: una ``Resolution`` (o cualquier objeto con status, value y rule)."""
    if r.status == "not_applicable":
        return "no_aplica"
    if r.status == "format_requirement":
        return "formato"
    if getattr(r, "source", "") == "proposal":  # trae valor, pero no se aplica
        return "propuesta"
    if r.value is None:
        return (
            "propuesta"
            if getattr(r, "requires_approval", False) or r.rule
            else "sin_regla"
        )
    rule = cat.rules.get(r.rule) if r.rule else None
    return "referencia" if rule is not None and rule.kind == "reference" else "medida"


def by_subsystem(results: Iterable[Any], cat: Catalog) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        out[subsystem(r.target)][klass(r, cat)] += 1
    return {s: {k: c.get(k, 0) for k in CLASSES} for s, c in sorted(out.items())}
