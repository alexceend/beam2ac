"""Parámetro del modelo intermedio (IR) con procedencia, estado y unidad validada.

Un :class:`Param` nunca inventa un valor:

* ``present``: hay valor (``value`` no es ``None``);
* ``missing``: el dato no está en el origen o no se pudo obtener (``reason`` dice
  por qué);
* ``not_applicable``: el concepto no existe en este vehículo (p. ej. diferencial
  central en un FWD). No es lo mismo que ausente;
* ``unparsed``: el dato existe en el origen pero no se pudo interpretar; ``original``
  conserva el valor crudo.

``kind`` dice cómo se obtuvo un valor presente (vocabulario común con el motor de
reglas, ``src.attrs.schema.EVIDENCE_KINDS``): ``extracted`` (leído), ``measured``
(medido en la geometría), ``computed`` (fórmula sobre otros datos), ``estimated``
(hipótesis), ``manual`` (decisión del usuario o del proyecto).

``unit`` se valida con ``src.attrs.units`` (dimensión explícita); una unidad
desconocida es un error al construir el parámetro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.attrs import schema as S
from src.attrs import units

STATUSES = ("present", "missing", "not_applicable", "unparsed")


class IRError(ValueError):
    """Parámetro o modelo intermedio inválido."""


@dataclass
class Param:
    value: Any = None
    unit: str = "text"
    status: str = "missing"
    kind: str = ""  # extracted | measured | computed | estimated | manual
    refs: list[S.EvidenceRef] = field(default_factory=list)  # procedencia
    note: str = ""
    original: Any = None  # valor tal cual en el origen (si difiere o no se interpretó)
    source_key: str = (
        ""  # identificador del dato original (p. ej. "mainEngine.idleRPM")
    )
    transforms: list[str] = field(default_factory=list)  # pasos aplicados, en orden
    reason: str = ""  # por qué falta, no aplica o no se interpretó

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise IRError(f"estado desconocido {self.status!r}")
        try:
            units.validate_unit(self.unit)
        except units.UnitError as exc:
            raise IRError(str(exc)) from None
        if self.status == "present":
            if self.value is None:
                raise IRError("un parámetro presente necesita valor")
            if self.kind not in S.EVIDENCE_KINDS:
                raise IRError(f"kind {self.kind!r} no está en {S.EVIDENCE_KINDS}")
        elif self.value is not None:
            raise IRError(f"un parámetro {self.status} no puede tener valor")
        if self.status == "unparsed" and self.original is None:
            raise IRError("un parámetro sin interpretar debe conservar el original")

    # --- constructores -----------------------------------------------------------
    @classmethod
    def of(
        cls,
        value: Any,
        unit: str,
        kind: str,
        refs: list[S.EvidenceRef] | None = None,
        note: str = "",
        source_key: str = "",
        transforms: list[str] | None = None,
    ) -> "Param":
        """Presente si ``value`` no es ``None``; si no, ``missing`` (sin inventar)."""
        if value is None:
            return cls.missing(unit, "el origen no aporta el dato", source_key)
        return cls(
            value=value,
            unit=unit,
            status="present",
            kind=kind,
            refs=list(refs or []),
            note=note,
            source_key=source_key,
            transforms=list(transforms or []),
        )

    @classmethod
    def missing(cls, unit: str = "text", reason: str = "", source_key: str = ""):
        return cls(unit=unit, status="missing", reason=reason, source_key=source_key)

    @classmethod
    def not_applicable(cls, unit: str = "text", reason: str = ""):
        return cls(unit=unit, status="not_applicable", reason=reason)

    @classmethod
    def unparsed(cls, original: Any, unit: str = "text", reason: str = "", key=""):
        return cls(
            unit=unit,
            status="unparsed",
            original=original,
            reason=reason,
            source_key=key,
        )

    @property
    def present(self) -> bool:
        return self.status == "present"

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "kind": self.kind,
            "refs": [S._to_plain(r) for r in self.refs],
            "note": self.note,
            "original": self.original,
            "source_key": self.source_key,
            "transforms": list(self.transforms),
            "reason": self.reason,
        }
