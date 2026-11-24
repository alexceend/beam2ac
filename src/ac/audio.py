"""Audio del coche: banco FMOD precompilado.

Contrato: docs/research/audio_pipeline_contrato.md.

Solo la vía ``prebuilt``: copia y valida un banco ya construido. NUNCA ejecuta FMOD
(compilar un banco es un paso manual que prepara ``tools/audio/prepare_fmod_build.py``).

Requisitos de AC (AC Audio Pipeline 1.9, p. 9-11; 466/466 coches instalados):

* ``sfx/<carpeta del coche>.bank`` y ``sfx/GUIDs.txt`` con la línea ``bank:/<carpeta>``;
* los eventos del coche son ``event:/cars/<carpeta>/<nombre>``;
* ``data/sounds.ini`` ``[ENGINE] POSITION=front|rear`` (p. 5 y 8).

Cómo une AC carpeta, banco y eventos (fase C1, 2026-10-26):

* **[C]** el banco identifica el banco y sus eventos SOLO por GUID (16 bytes
  little-endian). No contiene nombres: en el banco de v16sn hay 0 apariciones del id y
  están los GUID del banco y de los 19 eventos.
* **[C]** AC (cadenas UTF-16) compone ``"/sfx/" + carpeta + ".bank"`` y
  ``"event:/cars/" + carpeta + "/engine_ext"``, lee ``/sfx/GUIDs.txt`` («AUDIO LOAD») y
  da el error «the specified bank was not present in any registered GUID file».
  ``acs.pdb``: ``Audiomotor de AC``, ``Audiomotor de AC``; importa
  ``getEventByID``.
* **[I]** AC traduce la ruta a GUID con ``GUIDs.txt`` y abre el evento por GUID. Por eso
  un banco ya construido puede **reasignarse** a otra carpeta reescribiendo las rutas de
  ``GUIDs.txt`` con los mismos GUID (``source_id``). Sin probar en el juego. Riesgo: dos
  coches con el mismo banco en una sesión cargarían dos veces los mismos GUID.
  Esto corrige la suposición previa del contrato («cambiar el id obliga a recompilar»).

``GUIDs.txt`` se recorta como en v16sn: la línea del banco, los eventos del coche y las
líneas ``bus:/``. ``common.bank`` y cualquier audio suelto nunca se copian.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_POSITIONS = ("front", "rear")
AUDIO_SUFFIXES = (".wav", ".ogg", ".mp3", ".flac")


class AudioInputError(ValueError):
    """El banco precompilado no sirve para este coche."""


@dataclass
class PrebuiltAudio:
    bank: bytes
    guids: bytes
    events: list[str]
    buses: int
    source: dict[str, str] = field(default_factory=dict)


def trim_guids(text: str, car_id: str) -> list[str]:
    return [
        ln
        for ln in text.splitlines()
        if ln.endswith(f" bank:/{car_id}")
        or f" event:/cars/{car_id}/" in ln
        or re.search(r" bus:/", ln)
    ]


def _rebind(line: str, old: str, new: str) -> str:
    if f" event:/cars/{old}/" in line:
        return line.replace(f" event:/cars/{old}/", f" event:/cars/{new}/")
    if line.endswith(f" bank:/{old}"):
        return line[: -len(old)] + new
    return line


def load_prebuilt(
    bank_dir: Path, car_id: str, source_id: str | None = None
) -> PrebuiltAudio:
    """Lee ``<bank_dir>/<id>.bank`` y ``<bank_dir>/GUIDs.txt`` y los valida.

    ``source_id``: id para el que se construyó el banco, si no es ``car_id``; las rutas
    de ``GUIDs.txt`` se reescriben a ``car_id`` con los mismos GUID.
    """
    bank_dir = Path(bank_dir)
    built_for = source_id or car_id
    bank_path = bank_dir / f"{built_for}.bank"
    guids_path = bank_dir / "GUIDs.txt"
    if built_for.lower() == "common":
        raise AudioInputError("common.bank es el banco global de Kunos: no se copia")
    if not bank_path.is_file():
        found = sorted(p.name for p in bank_dir.glob("*.bank"))
        raise AudioInputError(
            f"falta {bank_path.name} en {bank_dir} (hay {found}); indique el id "
            "para el "
            "que se construyó el banco (source_id) o constrúyalo para este coche "
            "(tools/audio/prepare_fmod_build.py)"
        )
    if not guids_path.is_file():
        raise AudioInputError(f"falta GUIDs.txt en {bank_dir}")
    bank = bank_path.read_bytes()
    raw = guids_path.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    keep = trim_guids(raw.decode("utf-8", errors="replace"), built_for)
    events = [ln.split("/")[-1] for ln in keep if f" event:/cars/{built_for}/" in ln]
    if not any(ln.endswith(f" bank:/{built_for}") for ln in keep):
        raise AudioInputError(f"GUIDs.txt sin la línea bank:/{built_for}")
    if not events:
        raise AudioInputError(f"GUIDs.txt sin eventos event:/cars/{built_for}/…")
    missing = []
    for ln in keep:
        guid, name = ln.split(None, 1)
        if name.startswith("bus:/"):
            continue  # los buses viven en el banco maestro de AC
        try:
            ok = uuid.UUID(guid).bytes_le in bank
        except ValueError:
            ok = False
        if not ok:
            missing.append(name)
    if missing:
        raise AudioInputError(
            f"{bank_path.name} no contiene los GUID de {missing[:5]}: GUIDs.txt no "
            "corresponde a este banco"
        )
    loose = sorted(
        p.name for p in bank_dir.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES
    )
    if loose:
        raise AudioInputError(f"audio suelto junto al banco (no se copia): {loose}")
    if built_for != car_id:
        keep = [_rebind(ln, built_for, car_id) for ln in keep]
    return PrebuiltAudio(
        bank=bank,
        guids=(eol.join(keep) + eol).encode("utf-8"),
        events=sorted(events),
        buses=sum(" bus:/" in ln for ln in keep),
        source={
            "bank": str(bank_path),
            "guids": str(guids_path),
            "built_for": built_for,
            "rebound_to": car_id if built_for != car_id else "",
        },
    )


def render_sounds_ini(position: str, provenance: str) -> str:
    if position not in ENGINE_POSITIONS:
        raise AudioInputError(f"posición de motor {position!r}: front o rear")
    return (
        "; beam2ac: posicion del emisor del motor (AC Audio Pipeline 1.9, p. 5 y 8). "
        f"{provenance}\n[ENGINE]\nPOSITION={position}\n"
    )
