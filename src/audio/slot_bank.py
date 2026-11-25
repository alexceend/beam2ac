"""SPIKE: banco FMOD de un coche SIN FMOD, rellenando las ranuras de otro banco ya construido.

Todos los bancos que genera ``src.audio.pipeline`` comparten la misma rejilla de rpm de
las muestras de Automation (803, 859, 919, …) y el mismo grafo de eventos (plantilla del SDK
de AC): cambian el número de ranuras y el audio. Un banco "molde" construido antes (por
FMOD, de otro coche) ya contiene el grafo; aquí solo se sustituye el audio de cada ranura
(``EXH_<rpm>`` = coasting, ``EXH_<rpm>_P`` = carga) por el del coche nuevo.

Límites: el molde fija el número de ranuras y sus rpm; si al coche le faltan rpm se usa la
muestra más cercana remuestreada (cambio de tono = cambio de velocidad). Sin validación
acústica fuera de AC. Los GUID de los eventos son los del molde (``reguid`` los cambia).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import bank_patch as bp

SLOT = re.compile(r"EXH_(\d+)(_P)?")


def spec_files(spec: dict[str, Any]) -> dict[tuple[int, bool], str]:
    """(rpm, es_carga) -> ruta del WAV, tomado de las pistas de motor del spec."""
    out: dict[tuple[int, bool], str] = {}
    for ev in spec["engine"].values():
        for tr in ev["tracks"]:
            for s in tr["sounds"]:
                m = SLOT.search(Path(s["file"].replace("\\", "/")).name)
                if m:
                    out[(int(m.group(1)), bool(m.group(2)))] = s["file"]
    return out


def slot_wavs(
    names: list[str], files: dict[tuple[int, bool], str]
) -> tuple[dict[int, Path], list[str]]:
    """Índice de muestra del molde -> WAV del coche nuevo y avisos."""
    notes: list[str] = []
    by_kind: dict[bool, list[int]] = {False: [], True: []}
    for rpm, load in files:
        by_kind[load].append(rpm)
    out: dict[int, Path] = {}
    for i, name in enumerate(names):
        m = SLOT.search(name)
        if not m:
            continue
        rpm, load = int(m.group(1)), bool(m.group(2))
        if (rpm, load) in files:
            out[i] = Path(files[(rpm, load)])
            continue
        if not by_kind[load]:
            raise ValueError(
                f"el coche no tiene muestras de {'carga' if load else 'coast'}"
            )
        near = min(by_kind[load], key=lambda r: abs(r - rpm))
        out[i] = Path(files[(near, load)])
        notes.append(f"{name}: sin muestra a {rpm} rpm; se usa la de {near}")
    return out, notes


def fill_bank(
    donor: bytes, files: dict[tuple[int, bool], str]
) -> tuple[bytes, list[str]]:
    """Banco del molde con el audio de ``files`` en sus ranuras de motor."""
    bank = bp.parse_bank(donor)
    snd = bp.bank_fsb(bank)
    wavs, notes = slot_wavs([s.name for s in snd.fsb.samples], files)
    # el tono de una muestra a otra rpm: velocidad = rpm_ranura / rpm_muestra
    for i, path in wavs.items():
        s = snd.fsb.samples[i]
        m = SLOT.search(s.name)
        assert m is not None
        want = int(m.group(1))
        src = int(SLOT.search(path.name).group(1))  # type: ignore[union-attr]
        rate, pcm = bp.read_wav_mono16(path)
        pcm = bp.resample_linear(pcm, rate, s.rate)
        if want != src:
            pcm = bp.resample_linear(pcm, want, src)  # más rpm = más corta
        s.samples = len(pcm) // 2
        s.data = pcm + bytes(32 + bp._pad(len(pcm), 32))
    bp.rehash(snd.fsb)
    bp.finalize_snd(bank, snd)
    return bp.rebuild_bank(bank), notes


def make_prebuilt(
    donor_dir: Path,
    donor_id: str,
    files: dict[tuple[int, bool], str],
    out_dir: Path,
    new_key: str,
) -> list[str]:
    """Escribe ``<out_dir>/<donor_id>.bank`` y ``GUIDs.txt`` (formato de ``load_prebuilt``).

    Los GUID de los eventos y del banco del coche se sustituyen por otros nuevos, derivados
    de ``new_key`` (uuid5), para no colisionar con el coche del molde si ambos se cargan.
    El nombre del banco sigue siendo el del molde: ``export --audio-source-id <donor_id>``
    lo reasigna al coche nuevo como con cualquier banco precompilado.
    """
    import uuid

    from src.safe_paths import ensure_safe_output_path

    donor_dir = Path(donor_dir)
    bank, notes = fill_bank((donor_dir / f"{donor_id}.bank").read_bytes(), files)
    lines = bp.parse_guids((donor_dir / "GUIDs.txt").read_text(encoding="utf-8"))
    mine = [
        (g.strip("{}"), n)
        for g, n in lines
        if n.startswith((f"bank:/{donor_id}", f"event:/cars/{donor_id}/"))
    ]
    ns = uuid.uuid5(uuid.NAMESPACE_URL, "beam2ac/" + new_key)
    mapping = {g: str(uuid.uuid5(ns, g)) for g, _ in mine}
    bank, counts = bp.reguid(bank, mapping)
    miss = [g for g, c in counts.items() if c == 0]
    if miss:
        notes.append(f"GUID sin aparecer en el banco: {miss[:3]}")
    text = "".join(
        f"{{{mapping.get(g.strip('{}'), g.strip('{}'))}}} {n}\n" for g, n in lines
    )
    out = ensure_safe_output_path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    (out / f"{donor_id}.bank").write_bytes(bank)
    (out / "GUIDs.txt").write_text(text, encoding="utf-8")
    return notes
