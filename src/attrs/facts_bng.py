"""Hechos de un vehículo BeamNG para el motor de reglas, a través del IR (G1).

Antes de G1 este módulo leía directamente las estructuras de extracción de BeamNG. Ahora
el flujo es::

    PhysicsPreview ──► src.importers.beamng.import_preview ──► VehicleIR (src.ir)
                   ──► src.ir.legacy_facts.ir_to_facts ──► hechos bng.* ──► motor

Los hechos son idénticos a los anteriores (identificadores, valores, unidades, clase,
notas y procedencia): ``tests/test_ir_equivalence.py``. Se conserva la interfaz
(``Fact``, ``build_facts``) para no cambiar a sus usuarios.
"""

from __future__ import annotations

from src.pipeline.physics_preview import PhysicsPreview

from .facts import Fact

__all__ = ["Fact", "build_facts"]


def build_facts(
    pv: PhysicsPreview, driver_mass_kg: float | None = None
) -> dict[str, Fact]:
    """Hechos disponibles. Los que no se pueden obtener simplemente no aparecen
    (el motor los trata como ausentes y lo explica)."""
    from src.importers.beamng import import_preview
    from src.ir.legacy_facts import ir_to_facts

    return ir_to_facts(import_preview(pv, driver_mass_kg))
