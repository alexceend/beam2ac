"""Almacén JSON del catálogo de atributos (fuente de verdad) y de las decisiones.

Estructura de un catálogo (``catalog/`` en el repositorio, o la carpeta de un
proyecto)::

    attributes/*.json        lista de AttributeDef families.json            lista de
    Family rules/<id>@<versión>.json  una Rule por archivo; INMUTABLE (otra versión =
    otro archivo) validations/*.json       lista de Validation
    decisions/<vehículo>.jsonl  Decision, una por línea; SOLO SE AÑADE targets/*.json
    {"schema": "beam2ac.targets/1", "targets": [ids de atributo]}

Reglas de integridad (``Catalog.load`` falla si no se cumplen):
* el nombre de archivo de cada regla es ``<id>@<versión>.json``;
* ids únicos por entidad; ``supersedes``, ``incompatible_with`` y ``validations``
existen;
* cada destino de regla y decisión está definido en ``attributes``.

Escritura: nunca se sobrescribe una regla ni se reescribe un JSONL. Revertir o sustituir
una decisión = añadir otra con ``supersedes``. Solo se escribe dentro de la carpeta
``catalog/`` del repositorio o en rutas aceptadas por ``src.safe_paths`` (``output/``…).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.safe_paths import ensure_safe_output_path

from . import schema as S

REPO_CATALOG = Path(__file__).resolve().parents[2] / "catalog"


class StoreError(ValueError):
    """Catálogo incoherente o escritura no permitida."""


@dataclass
class Catalog:
    root: Path
    attributes: dict[str, S.AttributeDef] = field(default_factory=dict)
    families: dict[str, S.Family] = field(default_factory=dict)
    rules: dict[str, S.Rule] = field(default_factory=dict)  # clave id@versión
    validations: dict[str, S.Validation] = field(default_factory=dict)
    decisions: list[S.Decision] = field(default_factory=list)
    targets: dict[str, list[str]] = field(default_factory=dict)

    # --- carga -------------------------------------------------------------
    @classmethod
    def load(cls, root: str | Path, *extra_roots: str | Path) -> Catalog:
        """Carga ``root`` y, encima, las carpetas extra (p. ej. las decisiones de un
        proyecto)."""
        cat = cls(Path(root))
        for r in (Path(root), *map(Path, extra_roots)):
            cat._load_dir(r)
        cat.check()
        return cat

    def _load_dir(self, root: Path) -> None:
        if not root.is_dir():
            raise StoreError(f"no existe el catálogo {root}")
        for path in sorted((root / "attributes").glob("*.json")):
            for item in _read_list(path):
                self._add(self.attributes, S.from_dict(item, "attribute"), path)
        fam = root / "families.json"
        if fam.is_file():
            for item in _read_list(fam):
                self._add(self.families, S.from_dict(item, "family"), fam)
        for path in sorted((root / "rules").glob("*.json")):
            rule = S.from_dict(_read_json(path), "rule")
            if path.name != f"{rule.key}.json":
                raise StoreError(f"{path.name}: debe llamarse {rule.key}.json")
            if rule.key in self.rules:
                raise StoreError(f"regla duplicada {rule.key} ({path})")
            self.rules[rule.key] = rule
        for path in sorted((root / "validations").glob("*.json")):
            for item in _read_list(path):
                self._add(self.validations, S.from_dict(item, "validation"), path)
        for path in sorted((root / "decisions").glob("*.jsonl")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    try:
                        self.decisions.append(S.from_dict(json.loads(line), "decision"))
                    except (json.JSONDecodeError, S.SchemaError) as exc:
                        raise StoreError(f"{path}:{n}: {exc}") from None
        for path in sorted((root / "targets").glob("*.json")):
            data = _read_json(path)
            if data.get("schema") != "beam2ac.targets/1" or not isinstance(
                data.get("targets"), list
            ):
                raise StoreError(f"{path}: se esperaba beam2ac.targets/1 con 'targets'")
            self.targets[path.stem] = list(data["targets"])

    @staticmethod
    def _add(bucket: dict, obj: Any, path: Path) -> None:
        if obj.id in bucket:
            raise StoreError(f"id duplicado {obj.id!r} en {path}")
        bucket[obj.id] = obj

    def check(self) -> None:
        ids = {r.id for r in self.rules.values()}
        dec_ids: set[str] = set()
        for d in self.decisions:
            if d.id in dec_ids:
                raise StoreError(f"decisión duplicada {d.id!r}")
            dec_ids.add(d.id)
        for rule in self.rules.values():
            if rule.target not in self.attributes:
                raise StoreError(f"{rule.key}: destino no definido {rule.target!r}")
            if rule.supersedes and rule.supersedes not in self.rules:
                raise StoreError(
                    f"{rule.key}: supersedes inexistente {rule.supersedes!r}"
                )
            for other in rule.incompatible_with:
                if other not in ids:
                    raise StoreError(
                        f"{rule.key}: incompatible_with inexistente {other!r}"
                    )
            for vid in rule.validations:
                if vid not in self.validations:
                    raise StoreError(f"{rule.key}: validación inexistente {vid!r}")
            if rule.scope.level == "family" and rule.scope.family not in self.families:
                raise StoreError(
                    f"{rule.key}: familia inexistente {rule.scope.family!r}"
                )
        for d in self.decisions:
            if d.target not in self.attributes:
                raise StoreError(f"decisión {d.id}: destino no definido {d.target!r}")
            if d.supersedes and d.supersedes not in dec_ids:
                raise StoreError(
                    f"decisión {d.id}: supersedes inexistente {d.supersedes!r}"
                )
        for name, targets in self.targets.items():
            missing = [t for t in targets if t not in self.attributes]
            if missing:
                raise StoreError(
                    f"targets/{name}: atributos no definidos {missing[:5]}"
                )

    # --- consultas -----------------------------------------------------------
    def rules_for(self, target: str) -> list[S.Rule]:
        """Reglas vigentes (no rechazadas ni sustituidas por otra versión) de un
        destino."""
        superseded = {r.supersedes for r in self.rules.values() if r.supersedes}
        return [
            r
            for key, r in sorted(self.rules.items())
            if r.target == target
            and r.status not in ("rejected", "superseded")
            and key not in superseded
        ]

    def active_decisions(self, vehicle: str, target: str) -> list[S.Decision]:
        """Decisiones vigentes de un vehículo y destino (sin las sustituidas)."""
        superseded = {d.supersedes for d in self.decisions if d.supersedes}
        return [
            d
            for d in self.decisions
            if d.target == target
            and d.id not in superseded
            and d.status not in ("rejected", "superseded")
            and _decision_covers(d, vehicle)
        ]

    def decision_history(
        self, target: str, vehicle: str | None = None
    ) -> list[S.Decision]:
        return [
            d
            for d in self.decisions
            if d.target == target and (vehicle is None or d.vehicle == vehicle)
        ]


def _decision_covers(d: S.Decision, vehicle: str) -> bool:
    if d.scope.level in ("variant", "vehicle"):
        return d.vehicle == vehicle
    return False  # familia/arquitectura: no se aplican sin una regla aprobada


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoreError(f"{path}: JSON inválido: {exc}") from None


def _read_list(path: Path) -> list[Any]:
    data = _read_json(path)
    if not isinstance(data, list):
        raise StoreError(f"{path}: se esperaba una lista")
    return data


# --- escritura ----------------------------------------------------------------


def _writable(path: Path) -> Path:
    path = Path(path).resolve()
    try:
        path.relative_to(REPO_CATALOG.resolve())
        return path
    except ValueError:
        return ensure_safe_output_path(path)


def write_rule(root: str | Path, rule: S.Rule) -> Path:
    """Escribe una regla nueva. Nunca sobrescribe: otra versión = otro archivo."""
    S.from_dict(S.to_dict(rule))  # valida
    target = _writable(Path(root) / "rules" / f"{rule.key}.json")
    if target.exists():
        raise StoreError(f"la regla {rule.key} ya existe; crear una versión nueva")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(S.to_dict(rule), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def append_decision(
    root: str | Path, decision: S.Decision, existing: Catalog | None = None
) -> Path:
    """Añade una decisión al JSONL del vehículo (solo añadir; nunca reescribe)."""
    S.from_dict(S.to_dict(decision))  # valida
    if existing is not None:
        if any(d.id == decision.id for d in existing.decisions):
            raise StoreError(f"ya existe la decisión {decision.id!r}")
        if decision.supersedes and not any(
            d.id == decision.supersedes for d in existing.decisions
        ):
            raise StoreError(f"supersedes inexistente {decision.supersedes!r}")
        if decision.target not in existing.attributes:
            raise StoreError(f"destino no definido {decision.target!r}")
    if (
        not re.fullmatch(r"[A-Za-z0-9._-]+", decision.vehicle)
        or ".." in decision.vehicle
    ):
        raise StoreError(
            f"vehículo no válido para un nombre de archivo: {decision.vehicle!r}"
        )
    target = _writable(Path(root) / "decisions" / f"{decision.vehicle}.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(S.to_dict(decision), ensure_ascii=False, sort_keys=True)
    with open(target, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
    if existing is not None:
        existing.decisions.append(decision)
    return target
