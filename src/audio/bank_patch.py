"""Spike: leer, reconstruir y parchear bancos FMOD de AC (``.bank``) sin FMOD.

Solo biblioteca estándar. Se basa exclusivamente en el análisis de archivos de datos
``.bank`` locales y en la descripción PÚBLICA del contenedor FSB5 (cabecera, cabeceras de
muestra de 8 bytes, tabla de nombres, datos alineados a 16 bytes). No se ha abierto ni
desensamblado ningún binario de FMOD y no se copia código de terceros.

Estructura observada de un banco (FMOD Studio 1.08):

* RIFF ``FEV `` con los trozos ``FMT `` (8 bytes), ``LIST PROJ`` (grafo de eventos,
  buses y metadatos; tamaño ~100 KB) y un único ``SND `` que contiene
  ``relleno de ceros`` + un FSB5 (el relleno alinea el FSB5 a 32 bytes del archivo).
* El FSB5 (versión 1) tiene cabecera de 60 bytes; el modo de códec 2 es PCM16.

Uso: ``python -m src.audio.bank_patch inspect <banco>`` y
``python -m src.audio.bank_patch patch <banco> --out <salida.bank> --wav i=ruta ...``.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import struct
import sys
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- RIFF


@dataclass
class Chunk:
    """Trozo RIFF. ``form`` no es None si es un ``LIST`` (entonces ``children``)."""

    cid: bytes
    data: bytes = b""
    form: bytes | None = None
    children: list[Chunk] = field(default_factory=list)
    pad: bytes = b""  # byte de relleno tras un tamaño impar (se conserva tal cual)

    def find(self, cid: bytes) -> Chunk | None:
        for c in self.children:
            if c.cid == cid:
                return c
        return None


@dataclass
class Bank:
    """Banco ya troceado: forma RIFF, trozos de primer nivel y bytes sobrantes."""

    form: bytes
    chunks: list[Chunk]
    tail: bytes = b""

    def find(self, cid: bytes) -> Chunk | None:
        for c in self.chunks:
            if c.cid == cid:
                return c
        return None


def _walk(buf: bytes, pos: int, end: int) -> list[Chunk]:
    out: list[Chunk] = []
    while pos < end:
        if pos + 8 > end:
            raise ValueError(f"trozo truncado en {pos}")
        cid = bytes(buf[pos : pos + 4])
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        body = pos + 8
        if body + size > end:
            raise ValueError(f"trozo {cid!r} excede su contenedor en {pos}")
        pad = bytes(buf[body + size : body + size + 1]) if size & 1 else b""
        if cid in (b"LIST", b"RIFF"):
            form = bytes(buf[body : body + 4])
            kids = _walk(buf, body + 4, body + size)
            out.append(Chunk(cid, form=form, children=kids, pad=pad))
        else:
            out.append(Chunk(cid, data=bytes(buf[body : body + size]), pad=pad))
        pos = body + size + len(pad)
    return out


def parse_bank(buf: bytes) -> Bank:
    """Trocea un banco (RIFF ``FEV ``) incluyendo los ``LIST`` anidados."""
    if buf[:4] != b"RIFF":
        raise ValueError("no es RIFF")
    size = struct.unpack_from("<I", buf, 4)[0]
    end = 8 + size
    if end > len(buf):
        raise ValueError("RIFF más largo que el archivo")
    return Bank(bytes(buf[8:12]), _walk(buf, 12, end), bytes(buf[end:]))


def _emit(c: Chunk) -> bytes:
    if c.form is not None:
        body = c.form + b"".join(_emit(k) for k in c.children)
    else:
        body = c.data
    return (
        c.cid + struct.pack("<I", len(body)) + body + (c.pad if len(body) & 1 else b"")
    )


def rebuild_bank(bank: Bank) -> bytes:
    """Serializa recalculando todos los tamaños (idéntico al original si no cambia)."""
    body = bank.form + b"".join(_emit(c) for c in bank.chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body + bank.tail


def iter_chunks(chunks: list[Chunk], path: str = ""):
    """Recorre el árbol: devuelve ``(ruta, trozo)`` en profundidad."""
    for c in chunks:
        name = c.cid.decode("latin1") + (
            f"[{c.form.decode('latin1')}]" if c.form else ""
        )
        yield f"{path}/{name}", c
        yield from iter_chunks(c.children, f"{path}/{name}")


# --------------------------------------------------------------------------- FSB5

FREQS = {
    0: 4000, 1: 8000, 2: 11000, 3: 11025, 4: 16000,
    5: 22050, 6: 24000, 7: 32000, 8: 44100, 9: 48000,
}  # fmt: skip
MODE_PCM16 = 2
HEADER_SIZE = 60


@dataclass
class Fsb5Sample:
    """Una muestra: campos del u64, trozos extra, nombre y datos (con su relleno)."""

    freq_idx: int
    channels: int
    samples: int  # número de muestras (por canal)
    extras: list[tuple[int, bytes]]  # (tipo, datos) de los trozos tras la cabecera
    name: str
    data: bytes  # bytes del bloque de datos, incluido el relleno a 16

    @property
    def rate(self) -> int:
        return FREQS[self.freq_idx]

    @property
    def pcm_len(self) -> int:
        return self.samples * self.channels * 2

    def pcm(self) -> bytes:
        return self.data[: self.pcm_len]


@dataclass
class Fsb5:
    """FSB5 troceado. ``hash24`` son los 24 bytes opacos (hash + ``dummy``) 36..60."""

    version: int
    mode: int
    zero8: bytes
    hash24: bytes
    samples: list[Fsb5Sample]


def _pad(n: int, align: int) -> int:
    return (-n) % align


def parse_fsb5(buf: bytes) -> Fsb5:
    """Decodifica un FSB5 (solo PCM16; otros códecs dan ``ValueError``)."""
    if buf[:4] != b"FSB5":
        raise ValueError("no es FSB5")
    ver, n, shdr, nmsz, dsz, mode = struct.unpack_from("<6I", buf, 4)
    if mode != MODE_PCM16:
        raise ValueError(f"códec {mode} no soportado (solo PCM16 = 2)")
    zero8 = bytes(buf[28:36])
    hash24 = bytes(buf[36:60])
    pos = HEADER_SIZE
    rows = []
    for _ in range(n):
        raw = struct.unpack_from("<Q", buf, pos)[0]
        pos += 8
        extras: list[tuple[int, bytes]] = []
        more = raw & 1
        while more:
            h = struct.unpack_from("<I", buf, pos)[0]
            size = (h >> 1) & 0xFFFFFF
            extras.append((h >> 25, bytes(buf[pos + 4 : pos + 4 + size])))
            pos += 4 + size
            more = h & 1
        rows.append((raw, extras))
    if pos != HEADER_SIZE + shdr:
        raise ValueError("tamaño de cabeceras de muestra incoherente")
    names_at = pos
    offs = struct.unpack_from(f"<{n}I", buf, names_at) if nmsz else (0,) * n
    data_at = names_at + nmsz
    dstart = [(((r >> 6) & 0xFFFFFFF) << 4) for r, _ in rows]
    dend = dstart[1:] + [dsz]
    out = []
    for i, (raw, extras) in enumerate(rows):
        a = names_at + offs[i]
        name = bytes(buf[a : buf.index(b"\0", a)]).decode("latin1") if nmsz else ""
        out.append(
            Fsb5Sample(
                (raw >> 1) & 0xF,
                ((raw >> 5) & 1) + 1,
                (raw >> 34) & 0x3FFFFFFF,
                extras,
                name,
                bytes(buf[data_at + dstart[i] : data_at + dend[i]]),
            )
        )
    if data_at + dsz != len(buf):
        raise ValueError("bytes sobrantes tras los datos del FSB5")
    return Fsb5(ver, mode, zero8, hash24, out)


def build_fsb5(f: Fsb5) -> bytes:
    """Reconstruye el FSB5 desde sus piezas (offsets y tamaños recalculados)."""
    hdrs = bytearray()
    off = 0
    for s in f.samples:
        raw = (
            (1 if s.extras else 0)
            | (s.freq_idx << 1)
            | ((s.channels - 1) << 5)
            | ((off >> 4) << 6)
            | (s.samples << 34)
        )
        hdrs += struct.pack("<Q", raw)
        for k, (typ, d) in enumerate(s.extras):
            last = k == len(s.extras) - 1
            hdrs += struct.pack("<I", (0 if last else 1) | (len(d) << 1) | (typ << 25))
            hdrs += d
        off += len(s.data)
    table = bytearray()
    strings = bytearray()
    base = 4 * len(f.samples)
    for s in f.samples:
        table += struct.pack("<I", base + len(strings))
        strings += s.name.encode("latin1") + b"\0"
    names = bytes(table + strings) if any(s.name for s in f.samples) else b""
    # la tabla de nombres se rellena con ceros para que los datos empiecen a 32 bytes
    names += bytes(_pad(HEADER_SIZE + len(hdrs) + len(names), 32))
    data = b"".join(s.data for s in f.samples)
    head = (
        b"FSB5"
        + struct.pack(
            "<6I", f.version, len(f.samples), len(hdrs), len(names), len(data), f.mode
        )
        + f.zero8
        + f.hash24
    )
    return head + bytes(hdrs) + names + data


# --------------------------------------------------------------------------- SND


@dataclass
class SndChunk:
    """Contenido de ``SND ``: relleno inicial + FSB5 + posibles bytes finales."""

    prefix: bytes
    fsb: Fsb5
    suffix: bytes


def parse_snd(data: bytes) -> SndChunk:
    i = data.find(b"FSB5")
    if i < 0:
        raise ValueError("SND sin FSB5")
    _, n, shdr, nmsz, dsz, _ = struct.unpack_from("<6I", data, i + 4)
    total = HEADER_SIZE + shdr + nmsz + dsz
    return SndChunk(
        bytes(data[:i]), parse_fsb5(bytes(data[i : i + total])), data[i + total :]
    )


def build_snd(s: SndChunk) -> bytes:
    return s.prefix + build_fsb5(s.fsb) + s.suffix


def bank_fsb(bank: Bank) -> SndChunk:
    c = bank.find(b"SND ")
    if c is None:
        raise ValueError("banco sin trozo SND ")
    return parse_snd(c.data)


# --------------------------------------------------------------------------- patch


def read_wav_mono16(path: Path) -> tuple[int, bytes]:
    """Lee un WAV PCM16 (mono, o estéreo mezclado a mono). Devuelve (Hz, PCM16)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: solo PCM16")
        ch, rate, raw = w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())
    a = array.array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    if ch > 1:
        a = array.array("h", [sum(a[i : i + ch]) // ch for i in range(0, len(a), ch)])
    if sys.byteorder == "big":
        a.byteswap()
    return rate, a.tobytes()


def resample_linear(pcm: bytes, src: int, dst: int) -> bytes:
    """Remuestreo lineal simple de PCM16 mono (sin filtro: solo para el spike)."""
    if src == dst:
        return pcm
    a = array.array("h")
    a.frombytes(pcm)
    n = max(1, round(len(a) * dst / src))
    out = array.array("h")
    for i in range(n):
        x = i * src / dst
        j = int(x)
        k = min(j + 1, len(a) - 1)
        j = min(j, len(a) - 1)
        out.append(round(a[j] + (a[k] - a[j]) * (x - j)))
    return out.tobytes()


def patch_samples(snd: SndChunk, wavs: dict[int, Path]) -> None:
    """Sustituye los datos de las muestras ``índice -> WAV`` (mismo número y orden).

    Mantiene frecuencia, nombre y extras de cada muestra (el audio se remuestrea a la
    frecuencia de la muestra original). Los puntos de bucle (extras) no se tocan: si el
    nuevo audio es más corto que ``loop end`` hay que ajustarlos (no se hace aquí).
    """
    for idx, path in wavs.items():
        if not 0 <= idx < len(snd.fsb.samples):
            raise ValueError(f"índice de muestra fuera de rango: {idx}")
        s = snd.fsb.samples[idx]
        if s.channels != 1:
            raise ValueError(f"muestra {idx} no es mono")
        rate, pcm = read_wav_mono16(path)
        pcm = resample_linear(pcm, rate, s.rate)
        s.samples = len(pcm) // 2
        s.data = pcm + bytes(32 + _pad(len(pcm), 32))  # FMOD: >= 32 bytes, a 32
    rehash(snd.fsb)


def rehash(f: Fsb5) -> None:
    """Clave de identidad nueva (16 bytes en 36..52) para un FSB5 modificado.

    IDA (fmod64.dll, códec FSB5): el cargador NO valida ese campo contra los datos, pero lo
    usa como clave de una caché de FSB ya cargados: dos FSB con la misma clave comparten
    cabeceras y muestras en la misma sesión. Un FSB parcheado con la clave del original
    sonaría con el audio del original si ambos están cargados. Se usa sha256 del contenido
    (determinista y único por contenido); los 8 bytes finales no se tocan.
    """
    h = hashlib.sha256(b"".join(s.data for s in f.samples))
    h.update(b"".join(s.name.encode("latin1") for s in f.samples))
    f.hash24 = h.digest()[:16] + f.hash24[16:]


def patch_bank(buf: bytes, wavs: dict[int, Path]) -> bytes:
    """Devuelve el banco con el audio de las muestras indicadas sustituido."""
    bank = parse_bank(buf)
    snd = bank_fsb(bank)
    patch_samples(snd, wavs)
    c = bank.find(b"SND ")
    assert c is not None
    c.data = build_snd(snd)
    update_sndh(bank, len(build_fsb5(snd.fsb)))
    return rebuild_bank(bank)


def find_leaf(chunks: list[Chunk], cid: bytes) -> list[Chunk]:
    """Todos los trozos hoja ``cid`` del árbol (en orden de aparición)."""
    return [c for _, c in iter_chunks(chunks) if c.cid == cid and c.form is None]


def update_sndh(bank: Bank, fsb_size: int) -> None:
    """Actualiza ``SNDH`` de PROJ: ``(0x80003, desplazamiento del FSB5, tamaño)``.

    El desplazamiento (absoluto en el archivo) no cambia porque PROJ y el relleno de
    ``SND `` no se tocan; el tamaño del FSB5 sí. Comprobado en 14 bancos.
    """
    (h,) = find_leaf(bank.chunks, b"SNDH")
    tag, off, _ = struct.unpack("<III", h.data)
    h.data = struct.pack("<III", tag, off, fsb_size)


# --------------------------------------------------------------------------- GUID


def guid_variants(g: str) -> dict[str, bytes]:
    """Codificaciones de 16 bytes de un GUID textual."""
    u = uuid.UUID(g)
    b = u.bytes
    return {
        "le": u.bytes_le,
        "be": b,
        "rev": b[::-1],
        "d1d2d3-le_d4-rev": u.bytes_le[:8] + b[8:][::-1],
    }


def parse_guids(text: str) -> list[tuple[str, str]]:
    out = []
    for ln in text.splitlines():
        if ln.strip():
            g, name = ln.split(None, 1)
            out.append((g, name.strip()))
    return out


def find_all(buf: bytes, needle: bytes) -> list[int]:
    out, i = [], buf.find(needle)
    while i >= 0:
        out.append(i)
        i = buf.find(needle, i + 1)
    return out


def reguid(bank_bytes: bytes, mapping: dict[str, str]) -> tuple[bytes, dict[str, int]]:
    """Sustituye cada aparición de los GUID (``bytes_le``) por el GUID nuevo.

    Mismo tamaño en cada sustitución, así los largos de trozo no cambian. Devuelve
    el banco nuevo y el número de apariciones sustituidas por GUID viejo. Se busca en
    todo el banco (como hace ``load_prebuilt``), incluido el audio; con 16 bytes
    aleatorios una coincidencia fortuita en PCM es despreciable (~2^-128).
    """
    out = bytearray(bank_bytes)
    counts: dict[str, int] = {}
    olds = {uuid.UUID(o).bytes_le: uuid.UUID(n).bytes_le for o, n in mapping.items()}
    for old, new in olds.items():
        hits = find_all(bank_bytes, old)
        counts[str(uuid.UUID(bytes_le=old))] = len(hits)
        for h in hits:
            out[h : h + 16] = new
    return bytes(out), counts


# --------------------------------------------------------------------------- CLI


def inspect(path: Path) -> str:
    buf = path.read_bytes()
    bank = parse_bank(buf)
    lines = [f"{path.name}: {len(buf)} bytes, forma {bank.form!r}"]
    for p, c in iter_chunks(bank.chunks):
        if p.count("/") <= 2:
            lines.append(f"  {p} ({len(c.data)} bytes)")
    snd = bank_fsb(bank)
    f = snd.fsb
    lines.append(
        f"SND prefijo {len(snd.prefix)} bytes; FSB5 v{f.version} modo {f.mode}"
    )
    for i, s in enumerate(f.samples):
        ex = ",".join(f"t{t}:{len(d)}" for t, d in s.extras)
        lines.append(
            f"  [{i}] {s.name} {s.rate} Hz x{s.channels} {s.samples} muestras"
            f" extras[{ex}]"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("inspect")
    pi.add_argument("bank", type=Path)
    pp = sub.add_parser("patch")
    pp.add_argument("bank", type=Path)
    pp.add_argument("--out", type=Path, required=True, help="banco de salida (output/)")
    pp.add_argument("--wav", action="append", required=True, metavar="IDX=RUTA.wav")
    a = ap.parse_args(argv)
    if a.cmd == "inspect":
        print(inspect(a.bank))
        return 0
    from src.safe_paths import ensure_safe_output_path

    wavs = {int(k): Path(v) for k, v in (w.split("=", 1) for w in a.wav)}
    out = ensure_safe_output_path(a.out)
    if out.exists():
        print(f"{out} ya existe: no se sobrescribe", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(patch_bank(a.bank.read_bytes(), wavs))
    print(f"escrito {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
