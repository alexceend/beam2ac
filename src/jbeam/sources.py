"""Fuentes de solo lectura para archivos JBeam y ``.pc``: carpetas y paquetes ``.zip``.

BeamNG distribuye los vehículos oficiales y los mods como ``.zip`` con la estructura
``vehicles/<id>/...``; las piezas compartidas viven en ``content/vehicles/common.zip``
(``vehicles/common/...``). Este módulo permite leerlos **sin extraerlos**:

* El zip se abre siempre con ``zipfile.ZipFile(path, "r")`` y se lee miembro a miembro
  en memoria. No se escribe nada, ni se extrae a disco, ni se cambia la fecha del zip.
* Cada miembro se identifica con :class:`ZipMember` (``<zip>!/<miembro>``), que ofrece
  ``name``, ``suffix`` y ``read_text()`` como un ``Path``, para que el resto del
  lector no tenga que distinguir el origen.
* El orden de recorrido es alfabético por nombre de miembro, igual que el de
  ``PartIndex.from_directories`` (``sorted(rglob)``), para que el índice sea
  reproducible.

No se interpreta nada de BeamNG aquí; solo se localizan y leen textos.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Union


@dataclass(frozen=True)
class ZipMember:
    """Archivo dentro de un zip: ruta del zip y nombre del miembro."""

    zip_path: Path
    member: str

    @property
    def name(self) -> str:
        return PurePosixPath(self.member).name

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.member).suffix

    @property
    def stem(self) -> str:
        return PurePosixPath(self.member).stem

    def read_bytes(self) -> bytes:
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            return read_member_capped(zf, self.member)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def __str__(self) -> str:
        return f"{self.zip_path}!/{self.member}"


SourceFile = Union[Path, ZipMember]

MAX_MEMBER_BYTES = 512 << 20  # un miembro mayor se rechaza (bomba de compresión)


def read_member_capped(zf: zipfile.ZipFile, name: str) -> bytes:
    """``zf.read`` que rechaza miembros cuyo tamaño declarado supera ``MAX_MEMBER_BYTES``."""
    size = zf.getinfo(name).file_size
    if size > MAX_MEMBER_BYTES:
        raise ValueError(
            f"miembro del zip demasiado grande ({size >> 20} MiB): {name!r}"
        )
    return zf.read(name)


@dataclass(frozen=True)
class ZipSource:
    """Un zip, opcionalmente limitado a los miembros bajo ``prefix``."""

    zip_path: Path
    prefix: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "zip_path", Path(self.zip_path))
        prefix = self.prefix.replace("\\", "/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        object.__setattr__(self, "prefix", prefix)

    def members(self, suffix: str) -> list[ZipMember]:
        """Miembros (no carpetas) que terminan en ``suffix`` (sin mayúsculas)."""
        suffix = suffix.lower()
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            names = [
                info.filename
                for info in zf.infolist()
                if not info.is_dir()
                and info.filename.startswith(self.prefix)
                and info.filename.lower().endswith(suffix)
            ]
        return [ZipMember(self.zip_path, name) for name in sorted(names)]

    def read_all(self, suffix: str) -> Iterator[tuple[ZipMember, bytes]]:
        """Lee los miembros con un solo ``ZipFile`` abierto (más rápido)."""
        members = self.members(suffix)
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            for member in members:
                yield member, read_member_capped(zf, member.member)

    def __str__(self) -> str:
        return f"{self.zip_path}!/{self.prefix}"


def as_source(item: Union[str, Path, ZipSource]) -> Union[Path, ZipSource]:
    """Normaliza: ``ZipSource`` tal cual; un ``.zip`` entero; o una carpeta."""
    if isinstance(item, ZipSource):
        return item
    path = Path(item)
    if path.suffix.lower() == ".zip" and path.is_file():
        return ZipSource(path)
    return path


def read_text(file: SourceFile, encoding: str = "utf-8") -> str:
    """Texto de un archivo de cualquiera de las dos fuentes."""
    if isinstance(file, ZipMember):
        return file.read_text(encoding)
    return Path(file).read_text(encoding=encoding)


def vehicle_dirs_in_zip(zip_path: str | Path) -> list[str]:
    """Carpetas ``vehicles/<id>/`` presentes en un zip (para elegir el prefijo)."""
    out: set[str] = set()
    with zipfile.ZipFile(Path(zip_path), "r") as zf:
        for name in zf.namelist():
            parts = PurePosixPath(name).parts
            if len(parts) > 2 and parts[0].lower() == "vehicles":
                out.add(f"{parts[0]}/{parts[1]}/")
    return sorted(out)
