"""Lectura y validación de WAV sin dependencias (``wave`` no admite PCM float, que es lo que
exporta Automation). Solo lee: nunca modifica, normaliza ni convierte el audio."""

from __future__ import annotations

import hashlib
import struct
from array import array
from pathlib import Path
from typing import Any

_FLOAT, _PCM, _EXT = 3, 1, 0xFFFE


def wav_info(path: Path | str) -> dict[str, Any]:
    """Metadatos y comprobaciones de un WAV. ``ok`` False ⇒ ``problems`` dice por qué."""
    p = Path(path)
    out: dict[str, Any] = {"file": str(p), "ok": False, "problems": []}
    try:
        raw = p.read_bytes()
    except OSError as exc:
        out["problems"].append(f"ilegible: {exc.__class__.__name__}")
        return out
    out["sha256"] = hashlib.sha256(raw).hexdigest()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        out["problems"].append("no es RIFF/WAVE")
        return out
    fmt = data = None
    pos = 12
    while pos + 8 <= len(raw):
        cid, size = raw[pos : pos + 4], struct.unpack_from("<I", raw, pos + 4)[0]
        body = raw[pos + 8 : pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            fmt = struct.unpack_from("<HHIIHH", body)
            if (
                fmt[0] == _EXT and len(body) >= 26
            ):  # subformato real en los 2 primeros bytes del GUID
                fmt = (struct.unpack_from("<H", body, 24)[0], *fmt[1:])
        elif cid == b"data":
            data = body
            if pos + 8 + size > len(raw):
                out["problems"].append("data truncado")
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        out["problems"].append("faltan los bloques fmt/data")
        return out
    tag, ch, rate, _, _, bits = fmt
    out.update(format=tag, channels=ch, sample_rate=rate, bits=bits)
    kind = (
        "f"
        if tag == _FLOAT and bits == 32
        else (
            "h"
            if tag == _PCM and bits == 16
            else "i" if tag == _PCM and bits == 32 else None
        )
    )
    frames = len(data) // max(1, ch * bits // 8)
    out["duration_s"] = round(frames / rate, 6) if rate else 0.0
    if not (ch and rate and frames):
        out["problems"].append("sin muestras")
        return out
    if kind:
        a = array(kind)
        a.frombytes(data[: len(data) // a.itemsize * a.itemsize])
        scale = {"f": 1.0, "h": 32768.0, "i": 2147483648.0}[kind]
        peak = max(max(a), -min(a)) / scale
        out["peak"] = round(peak, 6)
        out["rms"] = round((sum(x * x for x in a) / len(a)) ** 0.5 / scale, 6)
        if peak != peak or peak == 0:
            out["problems"].append("silencio o NaN")
        elif peak > 1.0:
            out["problems"].append(f"pico {peak:.3f} > 1 (recorte)")
    else:  # 24 bit u otros: se aceptan por cabecera, sin nivel
        out["peak"] = out["rms"] = None
    out["ok"] = not [x for x in out["problems"] if not x.startswith("pico")]
    return out
