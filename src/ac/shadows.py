"""Sombras de contacto falsas de AC: ``data/ambient_shadows.ini`` + ``body_shadow.png`` + ``tyre_N_shadow.png``.

AC carga los PNG por nombre fijo desde la raíz del coche (``CarFakeShadow``, g253 §3) y ``ambient_shadows.ini``
solo lleva ``[SETTINGS] WIDTH/LENGTH`` (AE86 y R8 de Kunos: 1.0 / 2.8; terceros 0.95-1.11 / 2.4-2.8).

Los PNG se GENERAN aquí (sin copiar los de Kunos): bloque redondeado desenfocado, gris claro sobre negro,
512×512 el del cuerpo y 64×64 el de cada rueda (tamaños observados en ``ks_toyota_ae86``) [obs]. ``WIDTH`` = 1,0 (el valor común
a los dos Kunos leídos) y ``LENGTH`` = batalla + 0,4 (AE86: 2,4 + 0,4 = 2,8; el R8 daría 3,05 frente a 2,8) [hip, sin probar en AC].
"""

from __future__ import annotations

import math
import struct
import zlib

BODY_PX = 512
TYRE_PX = 64
GREY = 205  # gris del bloque del AE86 (≈200-210)


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    raw = b"".join(b"\x00" + r for r in rows)  # filtro 0 por fila
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8 bit, gris
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _soft_rect(
    px: int, half_w: float, half_h: float, radius: float, blur: float
) -> bytes:
    """Rectángulo redondeado (semiejes en fracción del lado) con borde suave, gris sobre negro."""
    rows = []
    for j in range(px):
        y = (j + 0.5) / px * 2 - 1
        row = bytearray()
        for i in range(px):
            x = (i + 0.5) / px * 2 - 1
            qx, qy = abs(x) - (half_w - radius), abs(y) - (half_h - radius)
            d = (
                math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius
            )  # distancia con signo
            t = min(max(0.5 - d / blur, 0.0), 1.0)
            row.append(round(GREY * t * t * (3 - 2 * t)))
        rows.append(bytes(row))
    return b"".join(rows)


def body_shadow_png() -> bytes:
    px = BODY_PX
    data = _soft_rect(px, 0.62, 0.9, 0.3, 0.25)
    return _png(px, px, [data[i * px : (i + 1) * px] for i in range(px)])


def tyre_shadow_png() -> bytes:
    px = TYRE_PX
    data = _soft_rect(px, 0.5, 0.8, 0.5, 0.5)
    return _png(px, px, [data[i * px : (i + 1) * px] for i in range(px)])


def ambient_shadows_ini(wheelbase_m: float) -> str:
    return f"[SETTINGS]\nWIDTH=1.0\nLENGTH={wheelbase_m + 0.4:.2f}\n"


def shadow_files(wheelbase_m: float) -> dict[str, bytes]:
    """``{ruta relativa al coche: bytes}``."""
    out = {
        "data/ambient_shadows.ini": ambient_shadows_ini(wheelbase_m).encode("utf-8"),
        "body_shadow.png": body_shadow_png(),
    }
    for n in range(4):
        out[f"tyre_{n}_shadow.png"] = tyre_shadow_png()
    return out
