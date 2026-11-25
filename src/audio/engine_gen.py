"""SPIKE: banco de coche por ``spec`` SIN FMOD a partir de un banco de motor ya construido.

Toma un banco "molde" (cualquier banco de motor construido por ``src.audio.pipeline``),
ajusta el número de ranuras de cada papel (carga / retención), sus rpm, colocaciones y
fundidos exactamente a los del ``spec`` y sustituye el audio (``bank_graph``), y renueva los
GUID. Frente a ``slot_bank`` (rellena ranuras fijas con la muestra más cercana) este camino
usa la rejilla de rpm del propio coche. Validado contra los bancos que FMOD construye para
los mismos ``spec`` (comparación canónica de registros, sin GUID ni orden).

Sigue haciendo falta el molde (el esqueleto de buses, efectos y eventos secundarios).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from . import bank_graph as bg
from . import bank_patch as bp

# tipos de registro cuyo primer GUID es una identidad aleatoria por construcción (FMOD los
# regenera en cada build); MAP y los BUS de GBSS son constantes de la plantilla y se conservan
_ID_TYPES = (
    b"EVTB",
    b"TLNB",
    b"PRMB",
    b"PMLB",
    b"CTRL",
    b"CURV",
    b"WAIB",
    b"WAV ",
    b"IBSB",
    b"MBSB",
    b"GBSB",
    b"BEFB",
    b"BNKI",
)


def renew_ids(
    raw: bytes, keep: set[bytes] | None = None
) -> tuple[bytes, dict[str, str]]:
    """Sustituye los GUID de identidad por otros nuevos en todo el banco (mapa viejo→nuevo)."""
    bank = bp.parse_bank(raw)
    ids: set[bytes] = set()

    def walk(c: bp.Chunk) -> None:
        t = c.cid if c.form is None else c.form
        if t in _ID_TYPES and len(c.data) >= 16:
            ids.add(bytes(c.data[:16]))
        for ch in c.children:
            walk(ch)

    for c in bank.chunks:
        walk(c)
    ids -= keep or set()
    mapping = {str(uuid.UUID(bytes_le=g)): str(uuid.uuid4()) for g in ids}
    new, _ = bp.reguid(raw, mapping)
    return new, mapping


def _role(track_name: str) -> str:
    return "load" if " load" in track_name else "coast"


def retarget(
    eb: bg.EngineBank,
    spec: dict[str, Any],
    audio: Callable[[str], tuple[int, bytes]] | None = None,
) -> None:
    """Deja ``eb`` con las ranuras, rpm, colocaciones y fundidos del ``spec``.

    ``audio(ruta)`` -> (Hz, PCM16 mono) de cada WAV; si es ``None`` se conserva el audio del
    molde (solo estructura, para validar).
    """
    # por evento y papel: {rpm: sonido del spec}; la rejilla del papel sale del exterior
    per: dict[str, dict[str, dict[float, dict]]] = {}
    for ev, d in spec["engine"].items():
        for tr in d["tracks"]:
            role = _role(tr["name"])
            rpms = [float(s["rpm"]) for s in tr["sounds"]]
            if len(set(rpms)) != len(rpms):
                raise ValueError(f"{ev}/{role}: rpm repetidas en las muestras")
            if role in per.get(ev, {}):
                raise ValueError(f"{ev}: más de una pista de {role}")
            per.setdefault(ev, {})[role] = {float(s["rpm"]): s for s in tr["sounds"]}
    ext = per["engine_ext"]
    for ev in per:
        for role in ("load", "coast"):
            if sorted(per[ev].get(role, {})) != sorted(ext[role]):
                raise ValueError(
                    f"{ev}/{role}: las rpm no coinciden con las de engine_ext "
                    "(el molde comparte cada muestra entre exterior e interior)"
                )
    for role in ("load", "coast"):
        rpms = sorted(ext[role])
        if len(rpms) < 2:
            raise ValueError(f"el papel {role} necesita al menos 2 ranuras")
        is_load = role == "load"

        def mine() -> list[bg.Slot]:
            return sorted(
                (s for s in eb.slots() if s.name.endswith("_P") == is_load),
                key=lambda s: (s.rpm, s.index),
            )

        cur = mine()
        if len(cur) < 2:
            raise ValueError(f"el molde necesita al menos 2 ranuras de {role}")
        while len(cur) < len(rpms):
            eb.clone_slot(cur[-1])
            cur = mine()
        while len(cur) > len(rpms):
            eb.delete_slot(cur[-1])
            cur = mine()
        for j, (s, r) in enumerate(zip(cur, rpms)):
            eb.set_fades(s, j > 0, j < len(rpms) - 1)
            eb.set_rpm(s, float(r))
            place = {e: per[e][role][r] for e in per}
            eb.set_placement(s, place)
            sm = eb.snd.fsb.samples[s.index]
            sm.name = f"s{s.index:03d}_EXH_{int(r)}{'_P' if is_load else ''}"
            if audio is not None:
                rate, pcm = audio(ext[role][r]["file"])
                pcm = bp.resample_linear(pcm, rate, sm.rate)
                bp.set_audio(sm, pcm)
    for ev, d in spec["engine"].items():
        eb.set_rpms_max(ev, float(d["rpms_max"]))


def generate(
    donor_raw: bytes,
    donor_guids: str,
    donor_id: str,
    spec: dict[str, Any],
    audio: Callable[[str], tuple[int, bytes]] | None = None,
) -> tuple[bytes, str]:
    """(banco, GUIDs.txt) del coche ``spec['car']``. Renueva todos los GUID."""
    lines = bp.parse_guids(donor_guids)
    events = {
        uuid.UUID(g.strip("{}")).bytes_le: n.split("/")[-1].strip()
        for g, n in lines
        if n.strip().startswith(f"event:/cars/{donor_id}/")
    }
    eb = bg.EngineBank(donor_raw, events)
    retarget(eb, spec, audio)
    bp.rehash(eb.snd.fsb)
    raw = eb.to_bytes()
    raw, mapping = renew_ids(raw)
    car = spec["car"]
    out = []
    for g, n in lines:
        gg = g.strip("{}")
        n2 = n.strip().replace(f"/{donor_id}", f"/{car}")
        out.append(f"{{{mapping.get(gg, gg)}}} {n2}\n")
    return raw, "".join(out)


# --------------------------------------------------------------------------- esqueleto versionado
DATA = Path(__file__).parent / "data"
SKELETON_ID = "beam2ac_skeleton"


def skeleton() -> tuple[bytes, str, str]:
    """(banco, GUIDs.txt, id) del esqueleto versionado (``tools/audio/make_skeleton.py``).

    Banco de plantilla del SDK de AC sin audio de nadie (2 ranuras de silencio por papel):
    el molde que permite generar bancos de coche sin FMOD ni bancos construidos antes.
    """
    return (
        (DATA / "skeleton_engine.bank").read_bytes(),
        (DATA / "skeleton_engine_guids.txt").read_text(encoding="utf-8"),
        SKELETON_ID,
    )


def generate_from_skeleton(
    spec: dict[str, Any],
    audio: Callable[[str], tuple[int, bytes]] | None = None,
) -> tuple[bytes, str]:
    """Banco y GUIDs.txt del coche ``spec['car']`` sin FMOD (molde = esqueleto versionado)."""
    raw, guids, sid = skeleton()
    return generate(raw, guids, sid, spec, audio)
