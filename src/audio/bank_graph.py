"""SPIKE: modelo de grafo de un banco FMOD de motor para clonar/borrar ranuras sin FMOD.

Formato (decodificado con ``fmodstudio64.dll`` y con bancos construidos por FMOD local; ver
``output/spike_self_transformations_2026-11-25/informe.md``):

* El ``LIST PROJ`` es un árbol de chunks; los objetos se enlazan por GUID de 16 bytes
  (``uuid.bytes_le``). Cada lista lleva un ``LCNT`` (u32) con su número de elementos.
* Matrices contadas: ``u16 = 2n+1``, ``u16 = tamaño de elemento``, ``n`` elementos
  (``PMLB`` desde el offset 48, ``CTRO`` desde 0, ``CURV`` desde 32).
* Una **ranura** (unidad de audio) es un ``WAV`` y las ``WAIT`` que lo usan (una por evento
  de motor, exterior e interior). Cada ``WAIT`` = ``WAIB`` (id; ``WAV`` en @16), ``INST``
  (rpm raíz del autopitch como float en @69) y ``PRPS``→``PROP`` (propiedades automatizadas:
  ``CTRL`` en @28 y, si hay dos, @44). Cada ``CTRL`` (dueño ``WAIB`` en @16, su propio id en
  @0 y @48) tiene un ``CURV`` con el mismo id: dos puntos ``(x, y, forma, 0)``.
* El módulo de sonidos (``PMLB``) lista cada ``WAIB`` con ``(inicio, longitud)`` en rpm
  (entrada de 24 bytes: GUID + 2 float) y el ``CTRO`` de su ``PMLO`` lista los ``CTRL``.

Límites: pensado para los bancos que genera ``src.audio.pipeline`` (plantilla del SDK de AC).
No hay garantía de que AC acepte lo que se produzca sin una sesión medida.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from typing import Iterator

from . import bank_patch as bp

RPM_AT = 69  # offset del float de rpm raíz dentro de INST
ENTRY = 24  # PMLB: GUID + inicio + longitud
PMLB_ARR = 48  # offset de la matriz contada en PMLB
WAV_IDX = 22  # offset del u16 de índice de muestra FSB en WAV


def _t(c: bp.Chunk) -> bytes:
    return c.cid if c.form is None else c.form


def _find(c: bp.Chunk, cid: bytes) -> bp.Chunk | None:
    for ch in c.children:
        if _t(ch) == cid:
            return ch
    return None


def _all(c: bp.Chunk, cid: bytes) -> list[bp.Chunk]:
    return [ch for ch in c.children if _t(ch) == cid]


def _walk(c: bp.Chunk) -> Iterator[bp.Chunk]:
    yield c
    for ch in c.children:
        yield from _walk(ch)


def _new_guid() -> bytes:
    return uuid.uuid4().bytes_le


def _replace(data: bytes, mapping: dict[bytes, bytes]) -> bytes:
    out = bytearray(data)
    for old, new in mapping.items():
        i = data.find(old)
        while i >= 0:
            out[i : i + 16] = new
            i = data.find(old, i + 1)
    return bytes(out)


def _clone(c: bp.Chunk, mapping: dict[bytes, bytes]) -> bp.Chunk:
    return bp.Chunk(
        c.cid,
        _replace(c.data, mapping),
        c.form,
        [_clone(ch, mapping) for ch in c.children],
        c.pad,
    )


@dataclass
class Slot:
    """Una ranura: su ``WAV`` y las ``WAIT`` que lo usan."""

    wav: bp.Chunk
    waits: list[bp.Chunk]
    index: int  # índice de muestra FSB
    name: str
    rpm: float


class EngineBank:
    """Banco de motor editable (clonar / borrar / reajustar ranuras)."""

    def __init__(self, raw: bytes, events: dict[bytes, str] | None = None) -> None:
        """``events``: GUID (bytes_le) de cada ``EVTB`` -> nombre (``engine_ext``…), de GUIDs.txt."""
        self.events = events or {}
        self.bank = bp.parse_bank(raw)
        self.snd = bp.bank_fsb(self.bank)
        self.proj = next(c for c in self.bank.chunks if _t(c) == b"PROJ")
        self.lists = {_t(c): c for c in self.proj.children if c.form is not None}

    # ------------------------------------------------------------------ consulta
    def _items(self, form: bytes, cid: bytes) -> list[bp.Chunk]:
        return _all(self.lists[form], cid)

    def _wait_lists(self) -> list[bp.Chunk]:
        return _all(self.lists[b"WAIS"], b"WAIT")

    @staticmethod
    def wait_parts(w: bp.Chunk) -> tuple[bp.Chunk, bp.Chunk]:
        waib, inst = _find(w, b"WAIB"), _find(w, b"INST")
        assert waib is not None and inst is not None
        return waib, inst

    def slots(self) -> list[Slot]:
        wavs = {bytes(c.data[:16]): c for c in self._items(b"WAVS", b"WAV ")}
        by_wav: dict[bytes, list[bp.Chunk]] = {}
        for w in self._wait_lists():
            waib, _ = self.wait_parts(w)
            by_wav.setdefault(bytes(waib.data[16:32]), []).append(w)
        out = []
        for g, ws in by_wav.items():
            wav = wavs[g]
            idx = struct.unpack_from("<H", wav.data, WAV_IDX)[0]
            rpm = struct.unpack_from("<f", self.wait_parts(ws[0])[1].data, RPM_AT)[0]
            out.append(Slot(wav, ws, idx, self.snd.fsb.samples[idx].name, rpm))
        return sorted(out, key=lambda s: s.index)

    def _ctrls_of(self, waib_ids: set[bytes]) -> list[bp.Chunk]:
        return [
            c for c in self._items(b"CTRS", b"CTRL") if bytes(c.data[16:32]) in waib_ids
        ]

    def _curv(self) -> dict[bytes, bp.Chunk]:
        return {bytes(c.data[:16]): c for c in self._items(b"CRVS", b"CURV")}

    # ------------------------------------------------------------------ contadores
    def _count(self, form: bytes) -> None:
        lst = self.lists[form]
        n = sum(1 for c in lst.children if _t(c) != b"LCNT")
        lcnt = _find(lst, b"LCNT")
        assert lcnt is not None
        lcnt.data = struct.pack("<I", n)

    @staticmethod
    def _bump(data: bytes, base: int, elem: int, add: int) -> bytes:
        """Ajusta la cabecera ``u16 2n+1`` de una matriz contada en ``base``."""
        h, size = struct.unpack_from("<HH", data, base)
        assert size == elem, (size, elem)
        n = (h - 1) // 2 + add
        return data[:base] + struct.pack("<HH", 2 * n + 1, elem) + data[base + 4 :]

    def _module_lists(self) -> list[tuple[bp.Chunk, bp.Chunk, bp.Chunk | None]]:
        out = []
        for pmlo in self._items(b"PMLS", b"PMLO"):
            pmlb, ctro = _find(pmlo, b"PMLB"), _find(pmlo, b"CTRO")
            assert pmlb is not None
            out.append((pmlo, pmlb, ctro))
        return out

    # ------------------------------------------------------------------ clonar
    def clone_slot(self, src: Slot, audio: bytes | None = None) -> Slot:
        """Añade una ranura copia de ``src`` (GUID nuevos; muestra FSB nueva al final)."""
        mapping: dict[bytes, bytes] = {}
        new_wav_id = _new_guid()
        mapping[bytes(src.wav.data[:16])] = new_wav_id
        waib_ids = set()
        for w in src.waits:
            waib, _ = self.wait_parts(w)
            mapping[bytes(waib.data[:16])] = _new_guid()
            waib_ids.add(bytes(waib.data[:16]))
        ctrls = self._ctrls_of(waib_ids)
        curv = self._curv()
        for c in ctrls:
            mapping[bytes(c.data[:16])] = _new_guid()
        # muestra FSB nueva
        samples = self.snd.fsb.samples
        s0 = samples[src.index]
        new_idx = len(samples)
        samples.append(
            bp.Fsb5Sample(
                s0.freq_idx,
                s0.channels,
                s0.samples,
                list(s0.extras),
                s0.name,
                audio if audio is not None else s0.data,
            )
        )
        wav = _clone(src.wav, mapping)
        wav.data = (
            wav.data[:WAV_IDX] + struct.pack("<H", new_idx) + wav.data[WAV_IDX + 2 :]
        )
        self.lists[b"WAVS"].children.append(wav)
        new_waits = []
        for w in src.waits:
            nw = _clone(w, mapping)
            self.lists[b"WAIS"].children.append(nw)
            new_waits.append(nw)
        for c in ctrls:
            self.lists[b"CTRS"].children.append(_clone(c, mapping))
            self.lists[b"CRVS"].children.append(
                _clone(curv[bytes(c.data[:16])], mapping)
            )
        # contenedores: PMLB (entradas) y CTRO (GUID de CTRL)
        for _, pmlb, ctro in self._module_lists():
            d = pmlb.data
            n = (struct.unpack_from("<H", d, PMLB_ARR)[0] - 1) // 2
            add = b""
            for k in range(n):
                o = PMLB_ARR + 4 + k * ENTRY
                if bytes(d[o : o + 16]) in waib_ids:
                    add += _replace(d[o : o + ENTRY], mapping)
            if add:
                pmlb.data = self._bump(d, PMLB_ARR, ENTRY, len(add) // ENTRY) + add
            if ctro is not None and len(ctro.data) > 4:
                cd = ctro.data
                m = (struct.unpack_from("<H", cd, 0)[0] - 1) // 2
                ids = [bytes(cd[4 + 16 * k : 20 + 16 * k]) for k in range(m)]
                extra = b"".join(mapping[i] for i in ids if i in mapping)
                if extra:
                    ctro.data = self._bump(cd, 0, 16, len(extra) // 16) + extra
        for f in (b"WAVS", b"WAIS", b"CTRS", b"CRVS"):
            self._count(f)
        return Slot(wav, new_waits, new_idx, s0.name, src.rpm)

    # ------------------------------------------------------------------ borrar
    def delete_slot(self, slot: Slot) -> None:
        """Quita una ranura y sus registros; reindexa las muestras FSB posteriores."""
        waib_ids = {bytes(self.wait_parts(w)[0].data[:16]) for w in slot.waits}
        ctrls = self._ctrls_of(waib_ids)
        ctrl_ids = {bytes(c.data[:16]) for c in ctrls}
        gone = {id(x) for x in slot.waits} | {id(c) for c in ctrls} | {id(slot.wav)}
        gone |= {id(v) for k, v in self._curv().items() if k in ctrl_ids}
        for lst in self.lists.values():
            lst.children = [c for c in lst.children if id(c) not in gone]
        for _, pmlb, ctro in self._module_lists():
            d = pmlb.data
            n = (struct.unpack_from("<H", d, PMLB_ARR)[0] - 1) // 2
            ents = [
                d[PMLB_ARR + 4 + k * ENTRY : PMLB_ARR + 4 + (k + 1) * ENTRY]
                for k in range(n)
            ]
            keep = [e for e in ents if bytes(e[:16]) not in waib_ids]
            if len(keep) != n:
                head = self._bump(d[: PMLB_ARR + 4], PMLB_ARR, ENTRY, len(keep) - n)
                pmlb.data = head + b"".join(keep)
            if ctro is not None and len(ctro.data) > 4:
                cd = ctro.data
                m = (struct.unpack_from("<H", cd, 0)[0] - 1) // 2
                ids = [bytes(cd[4 + 16 * k : 20 + 16 * k]) for k in range(m)]
                keep_ids = [i for i in ids if i not in ctrl_ids]
                if len(keep_ids) != m:
                    ctro.data = self._bump(cd[:4], 0, 16, len(keep_ids) - m) + b"".join(
                        keep_ids
                    )
        # muestra FSB y reindexado de los WAV posteriores
        del self.snd.fsb.samples[slot.index]
        for w in self._items(b"WAVS", b"WAV "):
            i = struct.unpack_from("<H", w.data, WAV_IDX)[0]
            if i > slot.index:
                w.data = (
                    w.data[:WAV_IDX] + struct.pack("<H", i - 1) + w.data[WAV_IDX + 2 :]
                )
        for f in (b"WAVS", b"WAIS", b"CTRS", b"CRVS"):
            self._count(f)

    # ------------------------------------------------------------------ fundidos
    PROP_ARR = (
        24  # PROP: u32 4, u32 4, GUID del MAP, matriz contada de GUID de CTRL, u16 1
    )

    def _prop(self, wait: bp.Chunk) -> bp.Chunk:
        prps = _find(wait, b"PRPS")
        assert prps is not None
        return _all(prps, b"PROP")[0]

    def _wait_ctrls(self, wait: bp.Chunk) -> list[bytes]:
        d = self._prop(wait).data
        m = (struct.unpack_from("<H", d, self.PROP_ARR)[0] - 1) // 2
        return [bytes(d[28 + 16 * k : 44 + 16 * k]) for k in range(m)]

    def _kind(self, ctrl_id: bytes) -> str:
        """``in`` (0→1) o ``out`` (1→0) según el primer punto de su ``CURV``."""
        y0 = struct.unpack_from("<f", self._curv()[ctrl_id].data, 40)[0]
        return "out" if y0 >= 0.5 else "in"

    def set_fades(self, slot: Slot, want_in: bool, want_out: bool) -> None:
        """Deja en cada ``WAIT`` de la ranura solo los fundidos pedidos (orden: entrada, salida)."""
        assert want_in or want_out, "una ranura sin fundidos no está soportada"
        curv = self._curv()
        ctrls = {bytes(c.data[:16]): c for c in self._items(b"CTRS", b"CTRL")}
        donors: dict[str, bytes] = (
            {}
        )  # solo fundidos de sonidos (no curvas de otros usos)
        for w in self._wait_lists():
            for cid in self._wait_ctrls(w):
                donors.setdefault(self._kind(cid), cid)
        for w in slot.waits:
            waib = bytes(self.wait_parts(w)[0].data[:16])
            have = {self._kind(i): i for i in self._wait_ctrls(w)}
            keep: dict[str, bytes] = {}
            for kind, want in (("in", want_in), ("out", want_out)):
                if want and kind in have:
                    keep[kind] = have[kind]
                elif want:
                    new = _new_guid()
                    m = {donors[kind]: new}
                    nc = _clone(ctrls[donors[kind]], m)
                    nc.data = nc.data[:16] + waib + self._module_of(waib) + nc.data[48:]
                    nv = _clone(curv[donors[kind]], m)
                    self.lists[b"CTRS"].children.append(nc)
                    self.lists[b"CRVS"].children.append(nv)
                    keep[kind] = new
                    self._ctro_add(waib, new)
                elif kind in have:
                    self._ctro_del(have[kind])
                    gone = {id(ctrls[have[kind]]), id(curv[have[kind]])}
                    for f in (b"CTRS", b"CRVS"):
                        lst = self.lists[f]
                        lst.children = [c for c in lst.children if id(c) not in gone]
            ids = [keep[k] for k in ("in", "out") if k in keep]
            prop = self._prop(w)
            d = prop.data
            head = d[: self.PROP_ARR] + struct.pack("<HH", 2 * len(ids) + 1, 16)
            prop.data = head + b"".join(ids) + d[-2:]
        for f in (b"CTRS", b"CRVS"):
            self._count(f)

    def _module_of(self, waib: bytes) -> bytes:
        """Id (``PMLB``) del módulo de sonidos que lista a ese ``WAIB``."""
        for _, pmlb, _ in self._module_lists():
            d = pmlb.data
            if len(d) <= PMLB_ARR + 4:
                continue
            n = (struct.unpack_from("<H", d, PMLB_ARR)[0] - 1) // 2
            for k in range(n):
                o = PMLB_ARR + 4 + k * ENTRY
                if bytes(d[o : o + 16]) == waib:
                    return bytes(d[:16])
        raise ValueError("WAIB sin módulo")

    def _ctro_add(self, waib: bytes, ctrl_id: bytes) -> None:
        """Añade el CTRL al CTRO del módulo que lista a ese WAIB."""
        for _, pmlb, ctro in self._module_lists():
            d = pmlb.data
            n = (
                (struct.unpack_from("<H", d, PMLB_ARR)[0] - 1) // 2
                if len(d) > 52
                else 0
            )
            if any(
                bytes(d[PMLB_ARR + 4 + k * ENTRY : PMLB_ARR + 20 + k * ENTRY]) == waib
                for k in range(n)
            ):
                assert ctro is not None
                ctro.data = self._bump(ctro.data, 0, 16, 1) + ctrl_id
                return
        raise ValueError("WAIB sin módulo")

    def _ctro_del(self, ctrl_id: bytes) -> None:
        for _, _, ctro in self._module_lists():
            if ctro is None or len(ctro.data) <= 4:
                continue
            cd = ctro.data
            m = (struct.unpack_from("<H", cd, 0)[0] - 1) // 2
            ids = [bytes(cd[4 + 16 * k : 20 + 16 * k]) for k in range(m)]
            if ctrl_id in ids:
                ids.remove(ctrl_id)
                ctro.data = self._bump(cd[:4], 0, 16, -1) + b"".join(ids)
                return

    # ------------------------------------------------------------------ ajustar valores
    def set_rpm(self, slot: Slot, rpm: float) -> None:
        for w in slot.waits:
            inst = self.wait_parts(w)[1]
            inst.data = (
                inst.data[:RPM_AT] + struct.pack("<f", rpm) + inst.data[RPM_AT + 4 :]
            )
        slot.rpm = rpm

    def module_event(self, pmlb: bp.Chunk) -> str:
        return self.events.get(bytes(pmlb.data[32:48]), "")

    def set_placement(self, slot: Slot, place: dict[str, dict]) -> None:
        """Coloca la ranura: ``place[evento] = {start, length, fade_in, fade_out}``.

        El largo de la última ranura depende del evento (``rpms_max`` exterior 20000 e
        interior 15000), por eso se da por evento. Puntos de fundido como ``(x0, x1)``.
        """
        waib_of = {bytes(self.wait_parts(w)[0].data[:16]): w for w in slot.waits}
        ev_of: dict[bytes, str] = {}
        for _, pmlb, _ in self._module_lists():
            d = bytearray(pmlb.data)
            if len(d) <= PMLB_ARR + 4:
                continue
            ev = self.module_event(pmlb)
            n = (struct.unpack_from("<H", d, PMLB_ARR)[0] - 1) // 2
            for k in range(n):
                o = PMLB_ARR + 4 + k * ENTRY
                g = bytes(d[o : o + 16])
                if g in waib_of:
                    ev_of[g] = ev
                    p = place[ev]
                    struct.pack_into("<2f", d, o + 16, p["start"], p["length"])
            pmlb.data = bytes(d)
        curv = self._curv()
        for g, w in waib_of.items():
            p = place[ev_of[g]]
            for cid in self._wait_ctrls(w):
                cv = curv[cid]
                d = bytearray(cv.data)
                pts = p["fade_out"] if self._kind(cid) == "out" else p["fade_in"]
                struct.pack_into("<f", d, 36, pts[0])
                struct.pack_into("<f", d, 52, pts[1])
                cv.data = bytes(d)

    def set_rpms_max(self, event: str, value: float) -> None:
        """Máximo del parámetro ``rpms`` del evento (``PRMB`` de 'rpms': float en @31)."""
        guid = next((g for g, n in self.events.items() if n == event), None)
        if guid is None:
            raise ValueError(f"evento desconocido: {event}")
        params = {
            bytes(c.data[:16]): c
            for pm in self._items(b"PRMS", b"PARM")
            for c in _all(pm, b"PRMB")
        }
        for _, pmlb, _ in self._module_lists():
            if len(pmlb.data) > PMLB_ARR + 4 and bytes(pmlb.data[32:48]) == guid:
                prm = params[bytes(pmlb.data[:16])]
                assert b"rpms" in prm.data
                prm.data = prm.data[:31] + struct.pack("<f", value) + prm.data[35:]
                return
        raise ValueError(f"sin módulo de sonidos para {event}")

    # ------------------------------------------------------------------ salida
    def to_bytes(self) -> bytes:
        bp.finalize_snd(self.bank, self.snd)
        return bp.rebuild_bank(self.bank)


def check_consistency(raw: bytes) -> list[str]:
    """Comprobaciones estructurales de un banco de motor (lista de problemas)."""
    eb = EngineBank(raw)
    issues: list[str] = []
    pos = 12
    for c in eb.bank.chunks:
        if _t(c) == b"SND ":
            break
        pos += len(bp._emit(c))
    start = pos + 8 + len(eb.snd.prefix)
    (sndh,) = bp.find_leaf(eb.bank.chunks, b"SNDH")
    _, off, size = struct.unpack("<III", sndh.data)
    if start % 32 or off != start or size != len(bp.build_fsb5(eb.snd.fsb)):
        issues.append(
            f"SNDH: desplazamiento {off} != FSB5 en {start} (o sin alinear a 32)"
        )
    for f in (b"WAVS", b"WAIS", b"CTRS", b"CRVS"):
        lst = eb.lists[f]
        n = sum(1 for c in lst.children if _t(c) != b"LCNT")
        lc = struct.unpack("<I", _find(lst, b"LCNT").data)[0]  # type: ignore[union-attr]
        if n != lc:
            issues.append(f"{f.decode()}: LCNT {lc} != {n}")
    ids = {bytes(c.data[:16]) for c in _walk(eb.proj) if len(c.data) >= 16}
    nsamples = len(eb.snd.fsb.samples)
    for w in eb._items(b"WAVS", b"WAV "):
        if struct.unpack_from("<H", w.data, WAV_IDX)[0] >= nsamples:
            issues.append("WAV con índice FSB fuera de rango")
    for w in eb._wait_lists():
        waib, _ = eb.wait_parts(w)
        if bytes(waib.data[16:32]) not in ids:
            issues.append("WAIB sin WAV")
    for c in eb._items(b"CTRS", b"CTRL"):
        if bytes(c.data[16:32]) not in ids:
            issues.append("CTRL con dueño inexistente")
    curv = eb._curv()
    for c in eb._items(b"CTRS", b"CTRL"):
        if bytes(c.data[:16]) not in curv:
            issues.append("CTRL sin CURV")
    for _, pmlb, ctro in eb._module_lists():
        d = pmlb.data
        if len(d) > PMLB_ARR + 4:
            n = (struct.unpack_from("<H", d, PMLB_ARR)[0] - 1) // 2
            if len(d) != PMLB_ARR + 4 + n * ENTRY:
                issues.append("PMLB: tamaño != cabecera")
        if ctro is not None and len(ctro.data) > 4:
            m = (struct.unpack_from("<H", ctro.data, 0)[0] - 1) // 2
            if len(ctro.data) != 4 + 16 * m:
                issues.append("CTRO: tamaño != cabecera")
    return issues
