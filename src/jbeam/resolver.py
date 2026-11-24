"""Resolución de una configuración: qué piezas están activas y por qué.

Algoritmo (verificado con la muestra ``35b84``: 47 piezas, masa total coherente):

1. Raíz: la pieza indicada en ``pc.parts["main"]`` si existe; si no, la única
   pieza con ``slotType == "main"``.
2. Recorrido en profundidad de la sección ``slots`` (tabla
   ``["type", "default", "description"]`` + opciones), en el orden del archivo.
3. Para cada slot: si el ``.pc`` tiene la clave ``type`` se usa su valor; si no,
   ``default``. ``""`` = slot vacío.
4. La pieza elegida debe existir y tener ``slotType == type``; si no, se
   registra en ``missing`` y no se carga.
5. Entradas del ``.pc`` que ningún slot recorrido usa → ``unreachable_pc``.

``nodeOffset`` (verificado): un slot con ``nodeOffset`` en sus opciones lo fija
para su pieza y descendientes; un slot hijo con su propio ``nodeOffset`` lo
REEMPLAZA (no lo suma). Aquí solo se registra el valor leído y de qué slot viene;
evaluarlo y aplicarlo a los nodos es trabajo de la fase 2.

Lo que este módulo NO hace (documentado en ``docs/research/jbeam_lector.md``):

* ``slots2`` (formato nuevo de BeamNG): no hay muestra; se avisa y se ignora.
* Fusionar secciones entre piezas (``mainEngine`` repartido en varias piezas,
  claves ``"$+x"``/``"$*x"``): el orden y la semántica exactos no se pueden
  verificar con las muestras. :meth:`ResolvedConfig.section_contributions` da
  las contribuciones por separado, en orden de recorrido, sin mezclarlas.
* Interpretar campos físicos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import PcConfig
from .parts import Part, PartIndex
from .tables import TableFormatError, parse_table

SLOTS_HEADER = ("type", "default", "description")


class ResolveError(ValueError):
    """No se puede determinar la raíz de la configuración."""


@dataclass(frozen=True)
class ActivePart:
    part: Part
    slot_type: str  # tipo del slot que ocupa ("main" para la raíz)
    parent: str | None  # nombre de la pieza padre
    depth: int
    chosen_by: str  # "pc" | "default" | "root"
    slot_options: dict[str, Any]  # opciones leídas del slot (sin evaluar)
    node_offset: dict[str, Any] | None  # nodeOffset efectivo leído (sin evaluar)
    node_offset_from: str | None  # "padre/slotType" que define ese nodeOffset

    @property
    def name(self) -> str:
        return self.part.name


@dataclass(frozen=True)
class MissingPart:
    slot_type: str
    part_name: str
    parent: str
    chosen_by: str
    reason: str


@dataclass(frozen=True)
class VariableInfo:
    name: str
    default: Any
    declared_in: str | None  # pieza activa que la declara (None: solo en el .pc)
    pc_value: Any = None
    in_pc: bool = False

    @property
    def value(self) -> Any:
        return self.pc_value if self.in_pc else self.default


@dataclass
class ResolvedConfig:
    root: str
    active: list[ActivePart] = field(default_factory=list)
    missing: list[MissingPart] = field(default_factory=list)
    unreachable_pc: list[str] = field(default_factory=list)
    variables: dict[str, VariableInfo] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def active_names(self) -> list[str]:
        return [a.name for a in self.active]

    def get(self, name: str) -> ActivePart | None:
        for item in self.active:
            if item.name == name:
                return item
        return None

    def variable_values(self) -> dict[str, Any]:
        """``{"$nombre": valor}`` listo para :func:`src.jbeam.expr.evaluate`."""
        return {name: info.value for name, info in self.variables.items()}

    def section_contributions(self, section: str) -> list[tuple[ActivePart, Any]]:
        """Valor leído de ``section`` en cada pieza activa, en orden de recorrido."""
        return [
            (a, a.part.data[section]) for a in self.active if section in a.part.data
        ]


def _find_root(index: PartIndex, pc: PcConfig) -> tuple[Part, str]:
    chosen = pc.parts.get("main")
    if chosen:
        part = index.get(chosen)
        if part is None:
            raise ResolveError(f"la pieza raíz del .pc '{chosen}' no existe")
        return part, "pc"
    mains = index.by_slot_type("main")
    if len(mains) == 1:
        return mains[0], "root"
    if not mains:
        raise ResolveError("no hay ninguna pieza con slotType 'main'")
    names = ", ".join(sorted(p.name for p in mains))
    raise ResolveError(f"varias piezas 'main' y el .pc no elige ninguna: {names}")


def resolve(index: PartIndex, pc: PcConfig) -> ResolvedConfig:
    root, how = _find_root(index, pc)
    result = ResolvedConfig(root=root.name)
    used_pc_keys: set[str] = {"main"} if "main" in pc.parts else set()
    visiting: set[str] = set()

    def visit(
        part: Part,
        slot_type: str,
        parent: str | None,
        depth: int,
        chosen_by: str,
        options: dict[str, Any],
        offset: dict[str, Any] | None,
        offset_from: str | None,
    ) -> None:
        if part.name in visiting or result.get(part.name):
            result.warnings.append(
                f"pieza '{part.name}' alcanzada otra vez (slot {slot_type}); se ignora"
            )
            return
        visiting.add(part.name)
        result.active.append(
            ActivePart(
                part, slot_type, parent, depth, chosen_by, options, offset, offset_from
            )
        )
        if "slots2" in part.data:
            result.warnings.append(
                f"'{part.name}' usa 'slots2' (formato no soportado, sin muestra); "
                "se ignora"
            )
        slots = part.data.get("slots")
        if slots is not None:
            try:
                table = parse_table(slots)
            except TableFormatError as exc:
                result.warnings.append(f"'{part.name}'.slots ilegible: {exc}")
                table = None
            if table is not None:
                if table.header[:3] != SLOTS_HEADER:
                    result.warnings.append(
                        f"'{part.name}'.slots con cabecera inesperada {table.header}"
                    )
                for issue in table.issues:
                    result.warnings.append(f"'{part.name}'.slots {issue}")
                for row in table.rows:
                    child_type = row.get("type")
                    if not isinstance(child_type, str) or not child_type:
                        result.warnings.append(f"'{part.name}': slot sin 'type' válido")
                        continue
                    if child_type in pc.parts:
                        child_name, child_by = pc.parts[child_type], "pc"
                        used_pc_keys.add(child_type)
                    else:
                        child_name, child_by = row.get("default", ""), "default"
                    if not child_name:
                        continue  # slot vacío
                    child_opts = dict(row.inline)
                    if "nodeOffset" in child_opts:
                        c_off = child_opts["nodeOffset"]
                        c_from = f"{part.name}/{child_type}"
                    else:
                        c_off, c_from = offset, offset_from
                    child = index.get(child_name)
                    if child is None:
                        result.missing.append(
                            MissingPart(
                                child_type, child_name, part.name, child_by, "no existe"
                            )
                        )
                        continue
                    if child.slot_type != child_type:
                        result.missing.append(
                            MissingPart(
                                child_type,
                                child_name,
                                part.name,
                                child_by,
                                f"slotType de la pieza es {child.slot_type!r}",
                            )
                        )
                        continue
                    visit(
                        child,
                        child_type,
                        part.name,
                        depth + 1,
                        child_by,
                        child_opts,
                        c_off,
                        c_from,
                    )
        visiting.discard(part.name)

    visit(root, "main", None, 0, how, {}, None, None)

    result.unreachable_pc = sorted(k for k in pc.parts if k not in used_pc_keys)
    for key in result.unreachable_pc:
        result.warnings.append(
            f".pc: '{key}' = '{pc.parts[key]}' no corresponde a ningún slot "
            "recorrido; se ignora"
        )
    for m in result.missing:
        result.warnings.append(
            f"pieza '{m.part_name}' para el slot '{m.slot_type}' de '{m.parent}' "
            f"({m.chosen_by}): {m.reason}"
        )
    _collect_variables(result, pc)
    return result


def _collect_variables(result: ResolvedConfig, pc: PcConfig) -> None:
    for active in result.active:
        section = active.part.data.get("variables")
        if section is None:
            continue
        try:
            table = parse_table(section)
        except TableFormatError as exc:
            result.warnings.append(f"'{active.name}'.variables ilegible: {exc}")
            continue
        for row in table.rows:
            name = row.get("name")
            if not isinstance(name, str) or not name.startswith("$"):
                result.warnings.append(
                    f"'{active.name}': variable con nombre inválido {name!r}"
                )
                continue
            if name in result.variables:
                first = result.variables[name].declared_in
                result.warnings.append(
                    f"variable {name} declarada otra vez en '{active.name}' "
                    f"(se mantiene la de '{first}')"
                )
                continue
            result.variables[name] = VariableInfo(name, row.get("default"), active.name)
    for name, value in pc.vars.items():
        info = result.variables.get(name)
        if info is None:
            result.variables[name] = VariableInfo(name, None, None, value, True)
        else:
            result.variables[name] = VariableInfo(
                name, info.default, info.declared_in, value, True
            )
