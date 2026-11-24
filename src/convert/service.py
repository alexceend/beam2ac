"""Servicio de conversión: el flujo nuevo de extremo a extremo, sin lógica de interfaz.

    importador (origen) ──► IR ──► arquitectura + capacidades
                            │
                            └──► hechos ──► motor de reglas (catálogo + decisiones)
                                              │
                                              └──► exportador AC (modelo de destino)

Pensado para que una interfaz (escritorio o web) solo llame a estos métodos:
importar, ver sistemas y parámetros (``ir``), consultar reglas aplicadas
(:meth:`explain`), limitaciones (``capabilities``), resolver y generar el destino. Las
decisiones se leen del catálogo y, si se da, de la carpeta ``decisions/`` de un proyecto
(solo lectura: aquí no se escriben).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.attrs.engine import ResolutionEngine, Resolution
from src.attrs.facts import Fact
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.exporters import capabilities as caps
from src.exporters.assetto_corsa import ACTargetModel, build_target
from src.ir.architecture import classify
from src.ir.legacy_facts import ir_to_facts
from src.ir.vehicle import VehicleIR


def architecture_facts(arch: dict[str, Any]) -> dict[str, Fact]:
    """Hechos ``ir.architecture.*`` para que las reglas puedan condicionarse a la
    arquitectura (además de a los rasgos concretos)."""
    out = {}
    if arch.get("vehicle"):
        out["ir.architecture.vehicle"] = Fact(
            "ir.architecture.vehicle",
            arch["vehicle"],
            "enum",
            "computed",
            note="catalog/architectures.json (src/ir/architecture.py)",
        )
    for comp in arch.get("components", []):
        fid = f"ir.architecture.component.{comp}"
        out[fid] = Fact(fid, True, "enum", "computed")
    return out


@dataclass
class ConversionSession:
    ir: VehicleIR
    catalog: Catalog
    results: list[Resolution] = field(default_factory=list)
    _target: ACTargetModel | None = None

    # --- creación ----------------------------------------------------------------
    @classmethod
    def from_ir(
        cls, ir: VehicleIR, project_root: str | Path | None = None
    ) -> "ConversionSession":
        extra = [project_root] if project_root else []
        catalog = Catalog.load(REPO_CATALOG, *extra)
        classify(ir)
        return cls(ir, catalog)

    @classmethod
    def from_source(
        cls,
        path: str | Path,
        pc: str | None = None,
        common_zip: str | Path | None = None,
        driver_mass_kg: float | None = 70.0,
        project_root: str | Path | None = None,
    ) -> "ConversionSession":
        from src.importers.beamng import import_vehicle

        return cls.from_ir(
            import_vehicle(path, pc, common_zip, driver_mass_kg), project_root
        )

    # --- motor de conversión -------------------------------------------------------
    def facts(self) -> dict[str, Fact]:
        facts = ir_to_facts(self.ir)
        facts.update(architecture_facts(self.ir.architecture))
        return facts

    def resolve(self) -> list[Resolution]:
        vid = self.ir.identity.vehicle_id.value
        variant = self.ir.identity.variant.value
        ctx = context_from_facts(vid, variant, self.facts(), self.catalog)
        engine = ResolutionEngine(self.catalog, ctx)
        self.results = engine.resolve_all(self.catalog.targets["ac_physics"])
        self._target = None
        return self.results

    def target(self) -> ACTargetModel:
        if not self.results:
            self.resolve()
        if self._target is None:
            self._target = build_target(
                self.ir, [r.to_dict() for r in self.results], self.catalog
            )
        return self._target

    # --- consulta (para una interfaz) -----------------------------------------------
    def capabilities(self) -> list[caps.Finding]:
        return caps.assess(self.ir)

    def explain(self, target: str) -> dict[str, Any]:
        """Cómo se obtuvo (o por qué no) un atributo de destino: resolución, regla o
        decisión, entradas y su procedencia en el IR."""
        if not self.results:
            self.resolve()
        r = next((x for x in self.results if x.target == target), None)
        if r is None:
            raise KeyError(target)
        return {
            "resolution": r.to_dict(),
            "inputs": r.inputs,
        }

    def summary(self) -> dict[str, Any]:
        if not self.results:
            self.resolve()
        counts: dict[str, int] = {}
        for r in self.results:
            key = "propuesta" if r.source == "proposal" else r.status
            counts[key] = counts.get(key, 0) + 1
        t = self.target()
        return {
            "vehicle": t.vehicle,
            "architecture": self.ir.architecture,
            "resolution": counts,
            "capabilities": caps.summary(self.capabilities())["by_level"],
            "target_complete": t.complete,
            "why_incomplete": t.why_incomplete,
            "unknown_items": len(self.ir.unknown),
        }
