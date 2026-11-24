"""Lectura de configuraciones ``.pc``.

Formato verificado (``format: 2``, muestra ``35b84.pc``)::

    {"format": 2, "parts": {"<slotType>": "<pieza>" | ""}, "vars": {"$var": valor}}

``""`` en ``parts`` significa slot vacío. Otros formatos (sin ``format`` o con un
número distinto de 2) se leen igual, pero se avisa de que no están verificados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lax_json import Leniency, load, loads
from .sources import ZipMember

VERIFIED_PC_FORMATS = {2}


class PcFormatError(ValueError):
    """El .pc no tiene la estructura mínima esperada."""


@dataclass
class PcConfig:
    path: Path | ZipMember | None
    format: Any
    parts: dict[str, str]
    vars: dict[str, Any]
    raw: dict[str, Any]
    leniencies: list[Leniency] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def parse_pc(data: Any, path: Path | ZipMember | None = None) -> PcConfig:
    if not isinstance(data, dict):
        raise PcFormatError("el .pc debe ser un objeto")
    issues: list[str] = []
    fmt = data.get("format")
    if fmt not in VERIFIED_PC_FORMATS:
        issues.append(
            f"formato de .pc {fmt!r} no verificado (solo {VERIFIED_PC_FORMATS})"
        )
    parts = data.get("parts", {})
    if not isinstance(parts, dict):
        raise PcFormatError("'parts' debe ser un objeto slotType -> pieza")
    for slot, part in parts.items():
        if not isinstance(part, str):
            raise PcFormatError(f"'parts.{slot}' debe ser una cadena, no {part!r}")
    variables = data.get("vars", {})
    if not isinstance(variables, dict):
        raise PcFormatError("'vars' debe ser un objeto")
    for name in variables:
        if not name.startswith("$"):
            issues.append(f"variable de .pc sin '$': {name!r}")
    return PcConfig(
        path=path,
        format=fmt,
        parts=dict(parts),
        vars=dict(variables),
        raw=data,
        issues=issues,
    )


def load_pc(path: str | Path | ZipMember) -> PcConfig:
    """Lee un ``.pc`` de una carpeta o de un zip (``ZipMember``), sin escribir."""
    if isinstance(path, ZipMember):
        # Saltos de línea universales, como Path.read_text en la rama de carpetas.
        text = path.read_text().replace("\r\n", "\n").replace("\r", "\n")
        doc = loads(text, source=str(path))
        config = parse_pc(doc.value, path)
        config.leniencies = doc.leniencies
        return config
    doc = load(path)
    config = parse_pc(doc.value, Path(path))
    config.leniencies = doc.leniencies
    return config
