"""Esquema versionado del modelo de atributos (fuente de verdad: JSON).

Cada entidad se serializa como un objeto JSON con ``"schema":
"beam2ac.<entidad>/<versión>"``. La lectura es **estricta**: campos desconocidos, tipos
erróneos, valores fuera de los enumerados o una versión más nueva que la del código son
:class:`SchemaError`. Las versiones antiguas pasan por :data:`MIGRATIONS` (función pura
``dict -> dict`` de la versión ``n`` a ``n + 1``) antes de validarse.

Entidades (ver ``docs/research/modelo_atributos.md``):

* :class:`AttributeDef` — atributo de origen o destino, con unidad y documentación.
* :class:`Rule` — correspondencia origen→destino: entradas, transformación, ámbito,
  condiciones, excepciones, reglas incompatibles, supuestos, evidencia e historial.
* :class:`Decision` — decisión manual (valor, referencia, transformación o «sin
  correspondencia»), con justificación obligatoria e historial.
* :class:`Validation` — comprobación de un valor contra una referencia o una prueba.
* :class:`Family` — familia de vehículos con criterios de pertenencia comprobables.
"""

from __future__ import annotations

import dataclasses
import functools
import re
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Union

from . import units

# --- enumerados --------------------------------------------------------------

EVIDENCE_KINDS = (
    "extracted",
    "computed",
    "measured",
    "inferred",
    "estimated",
    "manual",
    "format",
)
EVIDENCE_REF_TYPES = (
    "file",
    "code",
    "doc",
    "validation",
    "reference_vehicle",
    "decision",
    "report",
)
SCOPE_LEVELS = ("variant", "vehicle", "family", "architecture", "generic")
CONDITION_OPS = ("eq", "ne", "in", "not_in", "gt", "ge", "lt", "le", "exists", "absent")
RULE_KINDS = ("direct", "computed", "measured", "format", "reference", "applicability")
RULE_STATUS = ("pending", "provisional", "validated", "rejected", "superseded")
DECISION_STATUS = RULE_STATUS
DECISION_ACTIONS = (
    "manual_value",
    "select_reference",
    "transform",
    "no_correspondence",
    "defer",
)
VALIDATION_METHODS = (
    "info_reference",  # valor declarado por el exportador/juego (info_*.json)
    "in_game_functional",  # el paquete carga y se conduce (NO valida precisión)
    "in_game_value",  # valor medido en el juego
    "reference_vehicles",  # comparación con referencias independientes
    "code_review",  # semántica leída en el código fuente
)
VALIDATION_RESULTS = ("pass", "fail", "inconclusive")
GAMES = ("beamng", "assettocorsa", "project")

# Estados de resolución de un atributo para un vehículo (salida del motor).
RESOLUTION_STATUS = (
    "known",
    "inferred",
    "ambiguous",
    "not_applicable",
    "format_requirement",
    "manual_required",
)

SCHEMA_PREFIX = "beam2ac."
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SchemaError(ValueError):
    """Documento que no cumple el esquema."""


def _meta(
    *,
    choices_: tuple[str, ...] | None = None,
    unit: bool = False,
    date: bool = False,
    nonempty: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if choices_:
        out["choices"] = choices_
    if unit:
        out["unit"] = True
    if date:
        out["date"] = True
    if nonempty:
        out["nonempty"] = True
    return out


def f(
    default: Any = dataclasses.MISSING,
    *,
    default_factory: Any = dataclasses.MISSING,
    **meta: Any,
):
    """``dataclasses.field`` con metadatos de validación (choices, unit, date,
    nonempty)."""
    md = _meta(
        choices_=meta.get("choices"),
        unit=meta.get("unit", False),
        date=meta.get("date", False),
        nonempty=meta.get("nonempty", False),
    )
    if default_factory is not dataclasses.MISSING:
        return field(default_factory=default_factory, metadata=md)
    if default is not dataclasses.MISSING:
        return field(default=default, metadata=md)
    return field(metadata=md)


# --- entidades -----------------------------------------------------------------


@dataclass(kw_only=True)
class EvidenceRef:
    type: str = f(choices=EVIDENCE_REF_TYPES)
    path: str = f(nonempty=True)
    locator: str = ""
    sha256: str = ""
    note: str = ""


@dataclass(kw_only=True)
class Condition:
    """Predicado sobre un hecho del vehículo (``fact``), nunca sobre nombres."""

    fact: str = f(nonempty=True)
    op: str = f(choices=CONDITION_OPS)
    value: Any = None


@dataclass(kw_only=True)
class Scope:
    level: str = f(choices=SCOPE_LEVELS)
    vehicle: str = ""
    variant: str = ""
    family: str = ""
    conditions: list[Condition] = f(default_factory=list)  # arquitectura


@dataclass(kw_only=True)
class RuleException:
    scope: Scope
    reason: str = f(nonempty=True)


@dataclass(kw_only=True)
class Transform:
    id: str = f(nonempty=True)
    params: dict[str, Any] = f(default_factory=dict)
    input_units: dict[str, str] = f(default_factory=dict)
    output_unit: str = f("1", unit=True)
    formula: str = ""


@dataclass(kw_only=True)
class HistoryEntry:
    date: str = f(date=True)
    author: str = f(nonempty=True)
    action: str = f(nonempty=True)
    reason: str = f(nonempty=True)
    status_from: str = ""
    status_to: str = ""


@dataclass(kw_only=True)
class Validation:
    id: str = f(nonempty=True)
    vehicle: str = f(nonempty=True)
    method: str = f(choices=VALIDATION_METHODS)
    covers: list[str] = f(default_factory=list)
    result: str = f("inconclusive", choices=VALIDATION_RESULTS)
    variant: str = ""
    expected: Any = None
    observed: Any = None
    tolerance: Any = None
    evidence: list[EvidenceRef] = f(default_factory=list)
    date: str = f("2026-10-26", date=True)
    note: str = ""


@dataclass(kw_only=True)
class AttributeDef:
    id: str = f(nonempty=True)
    game: str = f(choices=GAMES)
    unit: str = f("undocumented", unit=True)
    dtype: str = f(
        "number", choices=("number", "integer", "text", "enum", "table", "vector")
    )
    description: str = ""
    doc: list[EvidenceRef] = f(default_factory=list)
    required_for_load: bool = False


@dataclass(kw_only=True)
class Family:
    id: str = f(nonempty=True)
    description: str = f(nonempty=True)
    membership: list[Condition] = f(default_factory=list)
    evidence: list[EvidenceRef] = f(default_factory=list)


@dataclass(kw_only=True)
class Rule:
    id: str = f(nonempty=True)
    version: int = 1
    target: str = f(nonempty=True)
    kind: str = f(choices=RULE_KINDS)
    transform: Transform = None  # type: ignore[assignment]
    scope: Scope = None  # type: ignore[assignment]
    status: str = f("provisional", choices=RULE_STATUS)
    inputs: dict[str, str] = f(
        default_factory=dict
    )  # nombre -> hecho (fact id) o atributo
    conditions: list[Condition] = f(default_factory=list)
    applies_when: list[Condition] = f(default_factory=list)
    exceptions: list[RuleException] = f(default_factory=list)
    incompatible_with: list[str] = f(default_factory=list)
    assumptions: list[str] = f(default_factory=list)
    open_issues: list[str] = f(default_factory=list)
    source_doc: list[EvidenceRef] = f(default_factory=list)
    target_doc: list[EvidenceRef] = f(default_factory=list)
    evidence: list[EvidenceRef] = f(default_factory=list)
    validations: list[str] = f(default_factory=list)  # ids de Validation
    supersedes: str = ""
    history: list[HistoryEntry] = f(default_factory=list)
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(kw_only=True)
class Decision:
    id: str = f(nonempty=True)
    target: str = f(nonempty=True)
    vehicle: str = f(nonempty=True)
    action: str = f(choices=DECISION_ACTIONS)
    justification: str = f(nonempty=True)
    author: str = f(nonempty=True)
    date: str = f(date=True)
    scope: Scope = None  # type: ignore[assignment]
    status: str = f("provisional", choices=DECISION_STATUS)
    variant: str = ""
    value: Any = None
    unit: str = f("undocumented", unit=True)
    reference: dict[str, Any] = f(default_factory=dict)
    transform: Union[Transform, None] = None
    inputs: dict[str, str] = f(default_factory=dict)
    evidence: list[EvidenceRef] = f(default_factory=list)
    supersedes: str = ""
    history: list[HistoryEntry] = f(default_factory=list)


ENTITIES: dict[str, type] = {
    "attribute": AttributeDef,
    "rule": Rule,
    "decision": Decision,
    "validation": Validation,
    "family": Family,
}
# rule/2 (2026-10-26): tipo ``applicability`` y condiciones sobre atributos de destino.
# Los documentos rule/1 son válidos sin cambios (migración identidad).
CURRENT_VERSION: dict[str, int] = {name: 1 for name in ENTITIES} | {"rule": 2}

# (entidad, versión origen) -> migración a versión origen + 1
MIGRATIONS: dict[tuple[str, int], Callable[[dict[str, Any]], dict[str, Any]]] = {
    ("rule", 1): lambda doc: doc,  # rule/1 -> rule/2: solo se añadieron valores
}


# --- (de)serialización ---------------------------------------------------------


def _check_meta(value: Any, meta: Any, where: str) -> None:
    if "choices" in meta and value not in meta["choices"]:
        raise SchemaError(f"{where}: {value!r} no está en {list(meta['choices'])}")
    if meta.get("unit"):
        try:
            units.validate_unit(value)
        except units.UnitError as exc:
            raise SchemaError(f"{where}: {exc}") from None
    if meta.get("date") and not (isinstance(value, str) and ISO_DATE.match(value)):
        raise SchemaError(f"{where}: fecha ISO AAAA-MM-DD esperada, no {value!r}")
    if meta.get("nonempty") and (not isinstance(value, str) or not value.strip()):
        raise SchemaError(f"{where}: no puede estar vacío")


def _coerce(tp: Any, value: Any, where: str) -> Any:
    if tp is str:  # el caso más frecuente: sin mirar tipos genéricos
        if not isinstance(value, str):
            raise SchemaError(f"{where}: se esperaba una cadena")
        return value
    origin = typing.get_origin(tp)
    if tp is Any:
        return value
    if origin in (Union, types.UnionType):
        args = typing.get_args(tp)
        if value is None and type(None) in args:
            return None
        for arg in args:
            if arg is type(None):
                continue
            return _coerce(arg, value, where)
    if origin is list:
        if not isinstance(value, list):
            raise SchemaError(f"{where}: se esperaba una lista")
        (item,) = typing.get_args(tp)
        return [_coerce(item, v, f"{where}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        if not isinstance(value, dict):
            raise SchemaError(f"{where}: se esperaba un objeto")
        _, vt = typing.get_args(tp)
        return {str(k): _coerce(vt, v, f"{where}.{k}") for k, v in value.items()}
    if dataclasses.is_dataclass(tp):
        return _from_plain(tp, value, where)
    if tp is bool:
        if not isinstance(value, bool):
            raise SchemaError(f"{where}: se esperaba un booleano")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{where}: se esperaba un entero")
        return value
    return value


@functools.lru_cache(maxsize=None)
def _hints(cls: type) -> dict:
    # get_type_hints re-evalúa las anotaciones en cada llamada (~3 s al cargar)
    return typing.get_type_hints(cls)


@functools.lru_cache(maxsize=None)
def _fields(cls: type) -> dict:
    return {fd.name: fd for fd in dataclasses.fields(cls)}


def _from_plain(cls: type, data: Any, where: str) -> Any:
    if not isinstance(data, dict):
        raise SchemaError(f"{where}: se esperaba un objeto para {cls.__name__}")
    hints = _hints(cls)
    fields = _fields(cls)
    unknown = set(data) - set(fields) - {"schema"}
    if unknown:
        raise SchemaError(f"{where}: campos desconocidos {sorted(unknown)}")
    kwargs = {}
    for name, fd in fields.items():
        if name not in data:
            if (
                fd.default is dataclasses.MISSING
                and fd.default_factory is dataclasses.MISSING
            ):
                raise SchemaError(f"{where}: falta el campo obligatorio '{name}'")
            if fd.default is None and dataclasses.is_dataclass(hints[name]):
                # dataclass sin "| None": obligatorio aunque el default técnico sea None
                raise SchemaError(f"{where}: falta el campo obligatorio '{name}'")
            continue
        value = _coerce(hints[name], data[name], f"{where}.{name}")
        if value is not None:
            _check_meta(value, fd.metadata, f"{where}.{name}")
        kwargs[name] = value
    obj = cls(**kwargs)
    for name, fd in fields.items():
        if name not in data and fd.metadata:
            default = getattr(obj, name)
            if fd.metadata.get("nonempty") or fd.metadata.get("date"):
                _check_meta(default, fd.metadata, f"{where}.{name}")
    return obj


def _to_plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            fd.name: _to_plain(getattr(value, fd.name))
            for fd in dataclasses.fields(value)
        }
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    return value


def entity_name(obj: Any) -> str:
    for name, cls in ENTITIES.items():
        if isinstance(obj, cls):
            return name
    raise SchemaError(f"no es una entidad del esquema: {type(obj).__name__}")


def to_dict(obj: Any) -> dict[str, Any]:
    name = entity_name(obj)
    return {
        "schema": f"{SCHEMA_PREFIX}{name}/{CURRENT_VERSION[name]}",
        **_to_plain(obj),
    }


def parse_schema_tag(tag: Any) -> tuple[str, int]:
    if not isinstance(tag, str) or not tag.startswith(SCHEMA_PREFIX) or "/" not in tag:
        raise SchemaError(f"etiqueta 'schema' inválida: {tag!r}")
    name, _, ver = tag[len(SCHEMA_PREFIX) :].partition("/")
    if name not in ENTITIES or not ver.isdigit():
        raise SchemaError(f"entidad o versión desconocida: {tag!r}")
    return name, int(ver)


def from_dict(data: dict[str, Any], expected: str | None = None) -> Any:
    """Valida (y migra si hace falta) un documento y devuelve la entidad."""
    if not isinstance(data, dict):
        raise SchemaError("se esperaba un objeto JSON")
    name, version = parse_schema_tag(data.get("schema"))
    if expected and name != expected:
        raise SchemaError(f"se esperaba '{expected}' y el documento es '{name}'")
    current = CURRENT_VERSION[name]
    if version > current:
        raise SchemaError(
            f"{name}/{version} es más nuevo que el código ({name}/{current}); "
            "actualizar beam2ac"
        )
    doc = dict(data)
    while version < current:
        migrate = MIGRATIONS.get((name, version))
        if migrate is None:
            raise SchemaError(
                f"no hay migración de {name}/{version} a {name}/{version + 1}"
            )
        doc = migrate(dict(doc))
        version += 1
        doc["schema"] = f"{SCHEMA_PREFIX}{name}/{version}"
    return _from_plain(ENTITIES[name], doc, data.get("id", name))
