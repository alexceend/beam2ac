"""Logo por defecto «b2ac» (``ui/badge.png`` 128 px y ``skins/<skin>/livery.png`` 64 px) en PNG puro, sin Pillow.

Content Manager avisa de un coche sin ``badge.png`` ni ``livery.png``. Si el usuario no sube un logo se usa este.
"""

from __future__ import annotations

import struct
import zlib

BG, FG, ACC = (
    (30, 30, 46),
    (203, 166, 247),
    (148, 226, 213),
)  # colores de la app (logo.svg)
# fuente 5x7 solo para las 4 letras del logo
GLYPH = {
    "b": ["1....", "1....", "1111.", "1...1", "1...1", "1...1", "1111."],
    "2": [".111.", "1...1", "....1", "...1.", "..1..", ".1...", "11111"],
    "a": [".....", ".....", ".111.", "....1", ".1111", "1...1", ".1111"],
    "c": [".....", ".....", ".111.", "1...1", "1....", "1...1", ".111."],
}


def _png(w: int, h: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    raw = b"".join(b"\x00" + r for r in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))  # 8 bit RGB
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def default_logo_png(size: int = 128) -> bytes:
    """Cuadrado ``size`` × ``size`` (múltiplo de 32) con «b2ac» centrado y subrayado."""
    k = size // 32  # píxeles por celda de la fuente
    px = [[BG] * size for _ in range(size)]
    x0, y0 = (size - 23 * k) // 2, (size - 7 * k) // 2 - k
    for n, ch in enumerate("b2ac"):
        col = ACC if ch == "2" else FG
        for r, line in enumerate(GLYPH[ch]):
            for c, bit in enumerate(line):
                if bit == "1":
                    for dy in range(k):
                        for dx in range(k):
                            px[y0 + r * k + dy][x0 + (n * 6 + c) * k + dx] = col
    for dy in range(k):  # subrayado
        for x in range(x0, x0 + 23 * k):
            px[y0 + 9 * k + dy][x] = ACC
    return _png(size, size, [b"".join(bytes(p) for p in row) for row in px])


def png_size(data: bytes) -> tuple[int, int] | None:
    """(ancho, alto) si ``data`` es un PNG, si no ``None``."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])
