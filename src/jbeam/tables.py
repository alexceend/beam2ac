"""Tablas JBeam con cabecera (``nodes``, ``beams``, ``slots``, ``variables``,
``pressureWheels``, ``torque``…).

Estructura observada en las muestras::

    [
        ["id", "posX", "posY", "posZ"],          # cabecera (lista de cadenas)
        {"nodeWeight": 2.1},                     # modificador (filas siguientes)
        ["n1", 0.1, 0.2, 0.3],                   # fila
        ["n2", 0.1, 0.2, 0.3, {"group": "x"}],   # fila con propiedades propias
    ]

Este módulo solo ESTRUCTURA la tabla; no interpreta ningún campo.

Reglas:

* La cabecera es el primer elemento lista; todos sus valores deben ser cadenas.
  Se conservan tal cual (incluido el sufijo ``:`` que JBeam usa para referencias
  a nodos, p. ej. ``"id1:"``).
* Cada dict anterior a una fila actualiza el estado de modificadores
  (``dict.update``, la última clave gana). Dicts anteriores a la cabecera también
  cuentan. Un dict al final de una fila son propiedades solo de esa fila.
* ``TableRow.properties`` = modificadores vigentes + propiedades propias (estas
  ganan). Que BeamNG aplique así los modificadores se comprobó indirectamente con
  ``nodeWeight`` (masa total = 1226,3 kg, igual que la UI).
* Valores no-dict de más o columnas que faltan NO se adivinan: se guardan en
  ``extra`` / se dejan sin clave, y se registran en ``issues``.
* Si una fila tiene exactamente tantas celdas como la cabecera y la última es un
  dict, es ambiguo: se deja como valor de la última columna y se registra.
* Un modificador con valor ``False``/``""`` se conserva tal cual: su significado
  (p. ej. "anular") depende del campo y lo decide quien lo interprete.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class TableFormatError(ValueError):
    """La sección no tiene forma de tabla con cabecera."""


@dataclass(frozen=True)
class TableRow:
    index: int  # posición dentro de la lista original de la sección
    values: dict[str, Any]  # columna de cabecera -> valor leído
    inline: dict[str, Any]  # dict propio al final de la fila
    modifiers: dict[str, Any]  # estado de modificadores vigente (copia)
    extra: tuple[Any, ...] = ()  # valores no-dict sobrantes

    @property
    def properties(self) -> dict[str, Any]:
        return {**self.modifiers, **self.inline}

    def get(self, column: str, default: Any = None) -> Any:
        return self.values.get(column, default)


@dataclass
class JBeamTable:
    header: tuple[str, ...]
    rows: list[TableRow]
    final_modifiers: dict[str, Any]  # estado tras la última entrada
    issues: list[str] = field(default_factory=list)


def is_header_table(section: Any) -> bool:
    """True si la sección parece una tabla con cabecera de cadenas."""
    if not isinstance(section, list):
        return False
    for item in section:
        if isinstance(item, dict):
            continue
        return (
            isinstance(item, list)
            and len(item) > 0
            and all(isinstance(v, str) for v in item)
        )
    return False


def parse_table(
    section: Any,
    initial_modifiers: Mapping[str, Any] | None = None,
) -> JBeamTable:
    """Estructura una sección tabla. ``initial_modifiers`` permite continuar el
    estado de una tabla anterior (necesario p. ej. para ``pressureWheels``,
    cuyos modificadores pasan de una pieza a otra)."""
    if not isinstance(section, list):
        raise TableFormatError(f"se esperaba una lista, no {type(section).__name__}")
    modifiers: dict[str, Any] = dict(initial_modifiers or {})
    header: tuple[str, ...] | None = None
    rows: list[TableRow] = []
    issues: list[str] = []

    for index, item in enumerate(section):
        if isinstance(item, dict):
            modifiers.update(item)
            continue
        if not isinstance(item, list):
            raise TableFormatError(
                f"elemento {index}: se esperaba lista o dict, no {item!r}"
            )
        if header is None:
            if not item or not all(isinstance(v, str) for v in item):
                raise TableFormatError(
                    f"elemento {index}: la cabecera debe ser una lista de "
                    f"cadenas: {item!r}"
                )
            header = tuple(item)
            continue

        cells = list(item)
        inline: dict[str, Any] = {}
        if cells and isinstance(cells[-1], dict):
            if len(cells) > len(header):
                inline = cells.pop()
            elif len(cells) < len(header):
                # Fila corta acabada en dict: se toma como propiedades propias.
                inline = cells.pop()
            else:
                issues.append(
                    f"fila {index}: el último valor es un dict y ocupa la columna "
                    f"{header[-1]!r}; ambiguo (¿valor o propiedades?), "
                    "se deja como valor"
                )
        values = {name: cells[i] for i, name in enumerate(header) if i < len(cells)}
        extra = tuple(cells[len(header) :])
        if len(cells) < len(header):
            missing = ", ".join(header[len(cells) :])
            issues.append(f"fila {index}: faltan columnas ({missing})")
        if extra:
            issues.append(f"fila {index}: {len(extra)} valor(es) sin columna")
        rows.append(
            TableRow(
                index=index,
                values=values,
                inline=inline,
                modifiers=dict(modifiers),
                extra=extra,
            )
        )

    if header is None:
        # Sección sin cabecera ni filas (p. ej. "nodes": [] en
        # camso_fueltank_35b84.jbeam): tabla vacía. Si trae modificadores, su
        # estado se conserva en final_modifiers y se anota.
        if modifiers != dict(initial_modifiers or {}):
            issues.append("modificadores sin cabecera ni filas")
        return JBeamTable(header=(), rows=[], final_modifiers=modifiers, issues=issues)
    return JBeamTable(
        header=header, rows=rows, final_modifiers=modifiers, issues=issues
    )
