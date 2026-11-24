"""Manifiesto de proyecto (``project.json``) y huellas de entrada.

El manifiesto solo describe el ORIGEN y las opciones; no guarda resultados (están en
``stages/``) ni decisiones (``decisions/``). Se crea una vez y no se reescribe: cambiar
el origen es crear otro proyecto.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.safe_paths import PROJECT_ROOT, ensure_safe_output_path, new_output_dir

SCHEMA = "beam2ac.project/1"


class ProjectError(ValueError):
    """Proyecto inválido o petición imposible (se informa, no se corrige)."""


@dataclass
class SourceSpec:
    kind: str  # "dir" | "zip"
    path: str  # carpeta del vehículo o zip
    pc: str | None = None  # nombre del .pc (zip: opcional si solo hay uno)
    common_zip: str | None = None

    def validate(self) -> None:
        if self.kind not in ("dir", "zip"):
            raise ProjectError(f"tipo de origen desconocido: {self.kind!r}")
        p = Path(self.path)
        if self.kind == "dir":
            if not p.is_dir():
                raise ProjectError(f"no existe la carpeta del vehículo: {p}")
            if not self.pc:
                raise ProjectError("un origen de carpeta necesita el .pc")
            if not (p / self.pc).is_file():
                raise ProjectError(f"no existe {p / self.pc}")
        else:
            if not p.is_file():
                raise ProjectError(f"no existe el zip: {p}")
            if self.common_zip and not Path(self.common_zip).is_file():
                raise ProjectError(f"no existe common.zip: {self.common_zip}")
            configs = list_zip_configs(p)
            if self.pc and self.pc not in configs:
                raise ProjectError(
                    f"{self.pc!r} no está en {p.name}; configuraciones: {configs}"
                )
            if not self.pc and len(configs) != 1:
                raise ProjectError(
                    f"{p.name} tiene {len(configs)} configuraciones: indica --pc "
                    f"(una de {configs})"
                )


def list_zip_configs(vehicle_zip: str | Path) -> list[str]:
    """Nombres de los ``.pc`` del vehículo del zip (solo lectura)."""
    from src.jbeam import ZipSource, vehicle_dirs_in_zip

    prefixes = vehicle_dirs_in_zip(Path(vehicle_zip))
    if len(prefixes) != 1:
        raise ProjectError(f"el zip contiene {len(prefixes)} vehículos: {prefixes}")
    return sorted(
        m.name for m in ZipSource(Path(vehicle_zip), prefixes[0]).members(".pc")
    )


@dataclass
class Project:
    id: str
    source: SourceSpec
    created: str
    driver_mass_kg: float = 70.0
    catalog_root: str = "catalog"
    notes: str = ""
    # carpeta data/ de referencia (solo lectura) para comparar los INI resueltos
    reference_data: str | None = None
    schema: str = SCHEMA
    root: Path = field(default=Path("."), repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("root")
        return d

    @property
    def decisions_dir(self) -> Path:
        return self.root / "decisions"

    @property
    def catalog_path(self) -> Path:
        p = Path(self.catalog_root)
        return p if p.is_absolute() else PROJECT_ROOT / p


def create_project(
    out: str | Path,
    source: SourceSpec,
    driver_mass_kg: float = 70.0,
    notes: str = "",
    catalog_root: str = "catalog",
    reference_data: str | Path | None = None,
) -> Project:
    """Crea la carpeta del proyecto. Se niega si ya existe (nunca reescribe)."""
    source.validate()
    if reference_data and not Path(reference_data).is_dir():
        raise ProjectError(f"no existe la carpeta de referencia: {reference_data}")
    root = new_output_dir(Path(out))
    (root / "decisions").mkdir()
    (root / "stages").mkdir()
    proj = Project(
        id=root.name,
        source=source,
        created=_dt.datetime.now().isoformat(timespec="seconds"),
        driver_mass_kg=driver_mass_kg,
        catalog_root=catalog_root,
        notes=notes,
        reference_data=str(reference_data) if reference_data else None,
        root=root,
    )
    ensure_safe_output_path(root / "project.json").write_text(
        json.dumps(proj.to_dict(), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return proj


def load_project(root: str | Path) -> Project:
    root = Path(root)
    path = root / "project.json"
    if not path.is_file():
        raise ProjectError(f"no es un proyecto (falta project.json): {root}")
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA:
        raise ProjectError(f"esquema no soportado: {d.get('schema')!r}")
    d["source"] = SourceSpec(**d["source"])
    return Project(**d, root=root)


# --- huellas -----------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FingerprintCache:
    """sha256 de archivos, recordado por (ruta, tamaño, fecha) en el proyecto.

    Evita releer zips grandes (p. ej. ``common.zip``) en cada ejecución. Si el
    tamaño o la fecha cambian, se vuelve a calcular.
    """

    def __init__(self, project: Project) -> None:
        self.path = project.root / "fingerprints.json"
        try:
            self.data: dict[str, Any] = json.loads(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def file(self, path: str | Path) -> str:
        p = Path(path)
        st = p.stat()
        key = str(p.resolve())
        hit = self.data.get(key)
        if hit and hit["size"] == st.st_size and hit["mtime_ns"] == st.st_mtime_ns:
            return hit["sha256"]
        digest = _sha256_file(p)
        self.dirty = True
        self.data[key] = {
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "sha256": digest,
        }
        return digest

    def tree(self, root: str | Path) -> str:
        """Huella de una carpeta: rutas relativas y sha256 de cada archivo."""
        r = Path(root)
        h = hashlib.sha256()
        for p in sorted(x for x in r.rglob("*") if x.is_file()):
            h.update(p.relative_to(r).as_posix().encode())
            h.update(self.file(p).encode())
        return h.hexdigest()

    def save(self) -> None:
        ensure_safe_output_path(self.path).write_text(
            json.dumps(self.data, indent=1) + "\n", encoding="utf-8"
        )


def source_fingerprint(project: Project, fp: FingerprintCache) -> dict[str, str]:
    s = project.source
    out = {"pc": s.pc or ""}
    if s.kind == "dir":
        out["vehicle_dir"] = fp.tree(s.path)
    else:
        out["vehicle_zip"] = fp.file(s.path)
        if s.common_zip:
            out["common_zip"] = fp.file(s.common_zip)
    return out


def rules_fingerprint(project: Project, fp: FingerprintCache) -> dict[str, str]:
    """Catálogo del repositorio + decisiones del proyecto (lo que lee el motor)."""
    out = {"catalog": fp.tree(project.catalog_path)}
    out["project_decisions"] = (
        fp.tree(project.decisions_dir) if project.decisions_dir.is_dir() else ""
    )
    return out


def reference_fingerprint(project: Project, fp: FingerprintCache) -> str:
    """Huella de la carpeta de referencia (vacía si el proyecto no tiene)."""
    ref = project.reference_data
    return fp.tree(ref) if ref and Path(ref).is_dir() else ""
