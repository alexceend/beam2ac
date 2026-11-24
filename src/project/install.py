"""Instalación explícita de un coche exportado en ``content/cars`` de Assetto Corsa.

Es la ÚNICA escritura del proyecto dentro de una instalación de AC y solo ocurre si el usuario marca la casilla
«Instalar en Assetto Corsa y sobrescribir…» al exportar. Límites: solo ``car_id`` propios (``beam2ac_*``), destino
exactamente ``<ac_root>/content/cars/<car_id>`` y, si ya existía, la carpeta anterior se MUEVE a
``output/_papelera`` (recuperable) antes de copiar; nunca se borra.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path
from typing import Any

from src.safe_paths import DEFAULT_OUTPUT_DIR, ensure_safe_output_path

CAR_ID = re.compile(r"^beam2ac_[a-z0-9_]+$")


class InstallError(ValueError):
    pass


def install_to_ac(
    car_dir: Path, ac_root: Path | str | None, car_id: str, overwrite: bool
) -> dict[str, Any]:
    if not CAR_ID.match(car_id or ""):
        raise InstallError("solo se instalan coches propios: car_id «beam2ac_…»")
    if not ac_root:
        raise InstallError("falta la ruta de Assetto Corsa (configúrala en Ajustes)")
    cars = Path(ac_root) / "content" / "cars"
    if not cars.is_dir():
        raise InstallError(f"no existe {cars}: revisa la ruta de Assetto Corsa")
    car_dir = Path(car_dir)
    if not (car_dir / "data").is_dir():
        raise InstallError(f"{car_dir} no parece un coche exportado (sin data/)")
    dest = cars / car_id
    if dest.resolve().parent != cars.resolve():
        raise InstallError("destino fuera de content/cars")
    # Se copia primero a una carpeta temporal contigua; solo si la copia termina se aparta el coche anterior
    # (a la papelera, recuperable) y se renombra la nueva. Si algo falla, el coche anterior sigue en su sitio.
    if dest.exists() and not overwrite:
        raise InstallError(f"ya existe {dest}: marca «sobrescribir» para reemplazarlo")
    tmp = cars / f".beam2ac_tmp_{car_id}"
    if tmp.exists():
        shutil.rmtree(tmp)  # resto de una instalación interrumpida (nuestro, con prefijo propio)
    backup = None
    try:
        shutil.copytree(car_dir, tmp)
        if dest.exists():
            backup = ensure_safe_output_path(
                DEFAULT_OUTPUT_DIR / "_papelera" / f"ac_{car_id}_{_dt.datetime.now():%Y%m%d_%H%M%S}"
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(backup))
        try:
            tmp.rename(dest)
        except OSError:
            if backup is not None:  # no se pudo poner la nueva: se devuelve la anterior
                shutil.move(str(backup), str(dest))
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"installed_to": str(dest), "replaced_backup": str(backup) if backup else None}
