"""Hecho que consume el motor de reglas (``src.attrs.engine``).

Los hechos de un vehículo se obtienen del IR (``src.ir.legacy_facts``); antes de G1 los
construía directamente ``facts_bng`` desde las estructuras de BeamNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import schema as S


@dataclass
class Fact:
    id: str
    value: Any
    unit: str
    kind: str  # schema.EVIDENCE_KINDS
    refs: list[S.EvidenceRef] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "value": self.value,
            "unit": self.unit,
            "kind": self.kind,
            "refs": [S._to_plain(r) for r in self.refs],
            "note": self.note,
        }
