"""Lector JBeam (fase 1): lectura tolerante, tablas, índice de piezas y
resolución de configuraciones. No interpreta datos físicos."""

from .config import PcConfig, PcFormatError, load_pc, parse_pc
from .expr import ExpressionError, evaluate, is_dynamic
from .lax_json import JBeamSyntaxError, LaxDocument, Leniency, load, loads
from .parts import Part, PartIndex
from .sources import ZipMember, ZipSource, vehicle_dirs_in_zip
from .resolver import (
    ActivePart,
    MissingPart,
    ResolveError,
    ResolvedConfig,
    VariableInfo,
    resolve,
)
from .tables import JBeamTable, TableFormatError, TableRow, is_header_table, parse_table

__all__ = [
    "ActivePart",
    "ExpressionError",
    "JBeamSyntaxError",
    "JBeamTable",
    "LaxDocument",
    "Leniency",
    "MissingPart",
    "Part",
    "PartIndex",
    "PcConfig",
    "PcFormatError",
    "ResolveError",
    "ResolvedConfig",
    "TableFormatError",
    "TableRow",
    "VariableInfo",
    "ZipMember",
    "ZipSource",
    "evaluate",
    "is_dynamic",
    "is_header_table",
    "load",
    "load_pc",
    "loads",
    "parse_pc",
    "parse_table",
    "resolve",
    "vehicle_dirs_in_zip",
]
