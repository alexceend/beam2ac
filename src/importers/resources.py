"""Recursos y pinturas de un vehículo BeamNG → IR (genérico, lee archivos).

* recursos por rol (``src.pipeline.source_inventory``) → ``resources.files_by_role``;
  archivos sin regla → ``unknown``;
* pinturas (``src.extract.paints``: ``info.json``, ``defaultPaintName``, ``.pc``,
  ``paints.meta``) → ``resources.paint_1``; lo no interpretable → ``unknown``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from src.ir.param import Param
from src.ir.vehicle import UnknownItem, VehicleIR


def _listing(source: Any, vehicle_dir: Path | None) -> tuple[list[str], Any]:
    """(rutas relativas, lector de bytes) del vehículo en carpeta o zip."""
    if vehicle_dir is not None:
        files = sorted(
            p.relative_to(vehicle_dir).as_posix()
            for p in vehicle_dir.rglob("*")
            if p.is_file()
        )

        def read(rel: str) -> bytes | None:
            p = vehicle_dir / rel
            return p.read_bytes() if p.is_file() else None

        return files, read
    zpath, _, prefix = source.description.partition("!/")
    with zipfile.ZipFile(zpath) as zf:
        names = [n for n in zf.namelist() if n.startswith(prefix) and n[-1] != "/"]
    files = sorted(n[len(prefix) :] for n in names)

    def read_zip(rel: str) -> bytes | None:
        with zipfile.ZipFile(zpath) as zf:
            try:
                return zf.read(prefix + rel)
            except KeyError:
                return None

    return files, read_zip


def import_resources(
    ir: VehicleIR, source: Any, vehicle_dir: Path | None = None
) -> None:
    from src.extract.paints import read_paints
    from src.jbeam import lax_json
    from src.pipeline.source_inventory import inventory

    from src.attrs import schema as S

    info_ref = S.EvidenceRef(type="file", path=source.description, locator="info.json")
    files, read = _listing(source, vehicle_dir)
    root_mats = [f for f in files if f.endswith(".materials.json") and "/" not in f]
    preferred = f"{source.variant}.materials.json"
    mat_name = (
        preferred
        if preferred in root_mats
        else (root_mats[0] if len(root_mats) == 1 else None)
    )
    mats = None
    if mat_name:
        raw = read(mat_name)
        mats = lax_json.loads(raw.decode("utf-8", "replace")).value if raw else None
    inv = inventory(files, mats if isinstance(mats, dict) else None, mat_name)
    for f in inv["files"]:
        ir.resources.files_by_role.setdefault(f["role"], []).append(f["path"])
    for path in inv["unknown"]:
        ir.unknown.append(
            UnknownItem(path, "", None, "archivo sin regla de inventario")
        )
    ir.resources.materials_file = Param.of(
        mat_name, "text", "extracted", [info_ref], source_key="*.materials.json"
    )
    ir.resources.audio_present = Param.of(
        any(f.startswith("art/sound") or f.endswith((".wav", ".ogg")) for f in files),
        "enum",
        "extracted",
    )

    pc_raw = read(f"{source.variant}.pc")
    pc_data = (
        lax_json.loads(pc_raw.decode("utf-8", "replace")).value if pc_raw else None
    )
    paints = read_paints(
        source.model_info,
        source.info,
        pc_data if isinstance(pc_data, dict) else None,
        read("paints.meta"),
        config_info_name=f"info_{source.variant}.json",
        pc_name=f"{source.variant}.pc",
    )
    sel = paints.selected(1)
    if sel is not None and sel.paint is not None:
        ir.resources.paint_1 = Param.of(
            sel.name, "text", "extracted", note=sel.origin, source_key="paint1"
        )
    else:
        ir.resources.paint_1 = Param.missing(
            "text", sel.origin if sel else "sin selección de pintura"
        )
    for where, raw in paints.raw_unparsed.items():
        ir.unknown.append(UnknownItem(where, "", raw, "pintura no interpretada"))
