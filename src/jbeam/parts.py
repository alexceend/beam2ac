"""Índice de piezas: todas las piezas definidas en los ``.jbeam`` de un vehículo.

Un ``.jbeam`` es un objeto ``{"<nombre_pieza>": {...}, ...}``. Este módulo solo
lee y cataloga; no resuelve slots ni interpreta secciones.

Reglas:

* Se recorren las carpetas de forma recursiva y en orden alfabético, para que el
  resultado sea reproducible.
* Un archivo con error de sintaxis no detiene la carga: queda en ``file_errors``.
* Un valor de primer nivel que no es un objeto queda en ``issues`` y no se indexa.
* Nombre de pieza repetido en dos archivos: se conserva la PRIMERA aparición
  (orden de carga) y se registra el conflicto. Qué versión usa BeamNG en ese caso
  no se ha podido verificar con las muestras (no hay ninguno).
* Fuentes (``from_sources``): carpetas, ``.zip`` enteros o ``ZipSource(zip, prefijo)``,
  en el orden dado (p. ej. el vehículo antes que ``common.zip``). Los zip se leen en
  memoria y en solo lectura (``sources.py``). Un archivo que no es UTF-8 válido queda
  en ``file_errors`` en lugar de detener la carga.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .lax_json import JBeamSyntaxError, Leniency, loads
from .sources import SourceFile, ZipSource, as_source


@dataclass(frozen=True)
class Part:
    name: str
    file: SourceFile
    data: dict[str, Any]

    @property
    def slot_type(self) -> Any:
        """Valor leído de ``slotType`` (sin interpretar)."""
        return self.data.get("slotType")

    def section(self, name: str, default: Any = None) -> Any:
        return self.data.get(name, default)


@dataclass
class PartIndex:
    parts: dict[str, Part] = field(default_factory=dict)
    files: list[SourceFile] = field(default_factory=list)
    file_errors: dict[SourceFile, JBeamSyntaxError] = field(default_factory=dict)
    leniencies: dict[SourceFile, list[Leniency]] = field(default_factory=dict)
    duplicates: list[tuple[str, SourceFile, SourceFile]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def __contains__(self, name: str) -> bool:
        return name in self.parts

    def __getitem__(self, name: str) -> Part:
        return self.parts[name]

    def get(self, name: str) -> Part | None:
        return self.parts.get(name)

    def by_slot_type(self, slot_type: str) -> list[Part]:
        return [p for p in self.parts.values() if p.slot_type == slot_type]

    @classmethod
    def from_directories(cls, *directories: str | Path) -> PartIndex:
        index = cls()
        for directory in directories:
            root = Path(directory)
            if not root.is_dir():
                index.issues.append(f"no es una carpeta: {root}")
                continue
            for path in sorted(root.rglob("*.jbeam")):
                index._load_file(path)
        return index

    @classmethod
    def from_sources(cls, *sources: str | Path | ZipSource) -> PartIndex:
        """Índice de carpetas y zip (solo lectura), en el orden dado."""
        index = cls()
        for item in sources:
            source = as_source(item)
            if isinstance(source, ZipSource):
                if not source.zip_path.is_file():
                    index.issues.append(f"no existe el zip: {source.zip_path}")
                    continue
                for member, data in source.read_all(".jbeam"):
                    index._load_bytes(member, data)
            elif source.is_dir():
                for path in sorted(source.rglob("*.jbeam")):
                    index._load_file(path)
            else:
                index.issues.append(f"no es una carpeta ni un zip: {source}")
        return index

    def _load_file(self, path: Path) -> None:
        self._load_bytes(path, path.read_bytes())

    def _load_bytes(self, path: SourceFile, data: bytes) -> None:
        self.files.append(path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            self.file_errors[path] = JBeamSyntaxError(
                f"no es UTF-8 válido ({exc.reason})", 1, exc.start + 1, str(path)
            )
            return
        # Igual que Path.read_text (saltos de línea universales), para que líneas y
        # columnas de errores y tolerancias no cambien respecto al lector anterior.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        try:
            doc = loads(text, source=str(path))
        except JBeamSyntaxError as exc:
            self.file_errors[path] = exc
            return
        self.leniencies[path] = doc.leniencies
        if not isinstance(doc.value, dict):
            self.issues.append(f"{path}: el valor raíz no es un objeto")
            return
        for name, data in doc.value.items():
            if not isinstance(data, dict):
                self.issues.append(f"{path}: '{name}' no es un objeto; no se indexa")
                continue
            if name in self.parts:
                self.duplicates.append((name, self.parts[name].file, path))
                continue
            self.parts[name] = Part(name=name, file=path, data=data)
