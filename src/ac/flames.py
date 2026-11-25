"""``data/flames.ini`` desde las salidas de escape del JBeam (``isExhaust`` en las vigas, G2.55).

Fuente: las vigas con ``isExhaust`` forman cadenas ``exhaust_0 … exhaust_N`` (JBeam del exportador de Automation);
la salida es el extremo más trasero (mayor ``y`` de BeamNG: −Y es adelante) de cada cadena [hip: puede fallar
con escapes laterales o delanteros]; la dirección es el último tramo (extremo − nodo anterior) si apunta hacia atrás (z ≤ −0,7) y, si no, la convencional
(0, 0,1, −1) de Kunos.

Marco: se asume el marco del KN5 (Celica ST185 ``POSITION=-0.516,0.267,-2.27`` y R8 ``y=0.48``: alturas sobre el
origen del KN5) [hip, sin probar en AC]. BeamNG → KN5: ``(x, y, z) → (x, z + dy, −y)`` (rotación de CLAUDE.md más
un desplazamiento vertical ``dy`` que se mide con las ruedas: ``y`` del nulo ``WHEEL_xx`` − ``z`` del centro de rueda;
comprobado en turbot_aster: x y z coinciden a 2 mm).

Formato [obs, R8 LMS y Celica de Kunos]: ``[HEADER]`` y un ``[FLAME_n]`` por salida con ``POSITION``,
``DIRECTION``, ``VSIZE_START``, ``VSIZE_END``, ``LSIZE``, ``IS_LEFT`` (+X = izquierda), ``SIZE_MULT``, ``GROUP``.
Los tamaños son los de la Celica (heurística).
"""

from __future__ import annotations

import math
import struct
from typing import Any

Vec3 = tuple[float, float, float]
WHEEL_NAMES = {"FL": "LF", "FR": "RF", "RL": "LR", "RR": "RR"}

HEADER = (
    "[HEADER]\nEDIT_MODE=0\nINTENSITY=24.0\nEDIT_BIG=1\nEDIT_STATE=2\n"
    "BURN_FUEL_MULT=4\nFLASH_THRESHOLD=1.5\n"
)
# FLASH_THRESHOLD (ingeniería inversa de acs.exe, nivel de confianza: medio; manejador del evento de disparo): con el combustible acumulado del escape
# (1..10) <= umbral AC solo dibuja un DESTELLO breve; por encima, llama sostenida. Kunos usa 4-8 (casi todo destellos);
# aquí 1,5 para que se vea llama. EDIT_STATE (solo en modo de edición: consola ``set observeFlames 1``): 2 = llama en bucle.


def exhaust_outlets(
    positions: dict[str, Vec3], exhaust_beams: list[tuple[str, str]]
) -> list[tuple[Vec3, Vec3]]:
    """``[(posición, dirección)]`` en ejes BeamNG: una por cadena de escape conectada."""
    adj: dict[str, set[str]] = {}
    for a, b in exhaust_beams:
        if a in positions and b in positions:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    seen: set[str] = set()
    out = []
    for start in sorted(adj):
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend(adj[n] - comp)
        seen |= comp
        ends = [n for n in comp if len(adj[n]) == 1]
        if not ends:
            continue
        # una cadena puede bifurcarse (dos colas a la vez): todas las salidas a ≤ 0,3 m de la más trasera
        y_max = max(positions[n][1] for n in ends)
        for tail in sorted(n for n in ends if positions[n][1] >= y_max - 0.3):
            prev = next(iter(adj[tail]))
            p, q = positions[tail], positions[prev]
            d = tuple(p[i] - q[i] for i in range(3))
            norm = math.sqrt(sum(c * c for c in d)) or 1.0
            out.append((p, tuple(c / norm for c in d)))  # type: ignore[arg-type]
    return out


def vertical_shift(
    kn5_wheels: dict[str, Vec3], bng_wheel_centres: dict[str, Vec3]
) -> float | None:
    """``dy`` = media de (``y`` del nulo del KN5 − ``z`` del centro de rueda de BeamNG)."""
    diffs = [
        kn5_wheels[WHEEL_NAMES[n]][1] - c[2]
        for n, c in bng_wheel_centres.items()
        if n in WHEEL_NAMES and WHEEL_NAMES[n] in kn5_wheels
    ]
    return sum(diffs) / len(diffs) if diffs else None


def render_flames_ini(
    outlets: list[tuple[Vec3, Vec3]], dy: float
) -> tuple[str, list[dict[str, Any]]]:
    out = [HEADER]
    info = []
    for i, (p, d) in enumerate(outlets):
        pos = (p[0], p[2] + dy, -p[1])
        dire = (d[0], d[2], -d[1])
        if (
            dire[2] > -0.7
        ):  # el último tramo no apunta atrás (codo lateral): dirección convencional de Kunos
            dire = (0.0, 0.1, -1.0)
        out.append(
            f"[FLAME_{i}]\nPOSITION={pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f}\n"
            f"DIRECTION={dire[0]:.3f},{dire[1]:.3f},{dire[2]:.3f}\n"
            "VSIZE_START=0.08\nVSIZE_END=0.1\nLSIZE=0.2\n"
            f"IS_LEFT={1 if pos[0] > 0 else 0}\nSIZE_MULT=1.2\nGROUP=0\n"
        )
        info.append(
            {
                "position_kn5": [round(c, 3) for c in pos],
                "direction": [round(c, 3) for c in dire],
            }
        )
    return "\n".join(out), info


# ``flame_presets.ini``: aspecto de la llama (arranque, fin, bucle y destello). Estructura de los de Kunos
# (``[PRESET_START|END|LOOP|FLASH_n]`` con ``TRIGGER``, ``OFFSET``, ``SIZE``, ``TYPE``, ``RGB=r,g,b,alfa``);
# los valores son propios [hip], en la escala de los de Kunos. Sin este archivo AC no tiene aspecto para dibujar
# las llamas de ``flames.ini`` (no probado en AC).
_PRESETS: dict[str, list[tuple[int, str, float, int, str]]] = {
    "START": [
        (0, "0.0,0.0,0.03", 0.05, 0, "255,170,20,0.5"),
        (1, "0.0,0.0,0.1", 0.1, 1, "255,120,20,0.3"),
        (2, "0.0,0.0,0.08", 0.07, 0, "255,120,20,0.3"),
    ],
    "END": [
        (4, "0.0,0.0,0.02", 0.06, 1, "255,190,0,0.4"),
        (2, "0.0,0.0,0.06", 0.04, 1, "255,190,0,0.3"),
        (0, "0.0,0.0,0.1", 0.04, 2, "255,190,0,0.25"),
    ],
    "LOOP": [
        (0, "0.0,-0.01,0.18", 0.13, 1, "200,80,0,0.3"),
        (1, "0.0,-0.04,0.26", 0.13, 1, "200,80,0,0.25"),
        (2, "0.0,-0.06,0.35", 0.15, 2, "200,80,0,0.2"),
    ],
    "FLASH": [
        (2, "0.0,0.0,0.02", 0.06, 2, "255,160,20,0.5"),
        (1, "0.0,0.0,0.06", 0.05, 2, "255,160,20,0.35"),
    ],
}


def render_flame_presets_ini() -> str:
    out = ["[HEADER]\nSIZE_MULT=1\n"]
    for kind, rows in _PRESETS.items():
        for i, (trigger, offset, size, typ, rgb) in enumerate(rows):
            out.append(
                f"[PRESET_{kind}_{i}]\nTRIGGER={trigger}\nOFFSET={offset}\n"
                f"SIZE={size}\nTYPE={typ}\nRGB={rgb}\n"
            )
    return "\n".join(out)


# ---------------------------------------------------------------- texturas de las llamas
# Según la ingeniería inversa de acs.exe (nivel de confianza: medio), la carga de texturas lee ``content/cars/<coche>/texture/flames/*.dds``: prefijo ``f`` = destello
# (TYPE 0 de los presets), prefijo ``l`` = cola (TYPE 2) y cualquier otro = llama principal (TYPE 1). La comprobación
# de texturas provoca un cierre forzado si un preset usa un TYPE sin texturas: SIN esta carpeta las llamas no
# se dibujan bien (Eagle/Ibiza: solo se veía «humo»). Los DDS se GENERAN aquí (no se copian los de Kunos): blancos con
# alfa, porque AC los tiñe con el RGB del preset y los mezcla con el alfa del cuadrado.
TEX_PX = 64
TEX_FRAMES = 4  # la animación va rotando entre los fotogramas de cada tipo


def _dds_bgra(px: int, rows: list[bytes]) -> bytes:
    flags = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000  # CAPS|HEIGHT|WIDTH|PITCH|PIXELFORMAT
    header = struct.pack("<7I", 124, flags, px, px, px * 4, 0, 0) + bytes(44)
    pixfmt = struct.pack(
        "<8I", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000
    )
    caps = struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return b"DDS " + header + pixfmt + caps + b"".join(rows)


def _lerp(
    a: tuple[int, int, int], b: tuple[int, int, int], u: float
) -> tuple[int, int, int]:
    u = min(max(u, 0.0), 1.0)
    return tuple(round(a[k] + (b[k] - a[k]) * u) for k in range(3))  # type: ignore[return-value]


# Color en el propio sprite: el RGB de los presets (255,170,20…) llega a OpenGL sin normalizar (255 > 1 se satura a blanco),
# así que el tono de la llama sale SOLO de la textura. Con sprites blancos (G2.55, primera versión) las llamas salían como
# «humo» blanco/gris (Ibiza, ``set observeFlames 1``).
_PALETTE = {  # (centro, medio, borde) en RGB
    "body": ((255, 245, 190), (255, 150, 25), (190, 40, 0)),
    "flash": ((255, 255, 235), (255, 215, 110), (255, 140, 20)),
    "tail": ((255, 190, 60), (255, 110, 15), (150, 25, 0)),
}


def _sprite(kind: str, frame: int, px: int = TEX_PX) -> bytes:
    """Sprite con color y alfa: ``body`` = gota blanda, ``flash`` = estrella, ``tail`` = estela alargada."""
    phase = frame * math.pi / (2 * TEX_FRAMES)
    core, mid, edge = _PALETTE[kind]
    rows = []
    for j in range(px):
        y = (j + 0.5) / px * 2 - 1
        row = bytearray()
        for i in range(px):
            x = (i + 0.5) / px * 2 - 1
            if kind == "tail":
                r = math.hypot(x / (0.55 + 0.05 * frame / TEX_FRAMES), y)
                a = max(0.0, 1 - r) ** 1.4
            else:
                r = math.hypot(x, y)
                base = max(0.0, 1 - r)
                th = math.atan2(y, x) + phase
                if kind == "flash":
                    a = base**3 + 0.8 * base * abs(math.cos(4 * th)) ** 10
                else:
                    a = base**1.6 * (
                        0.9 + 0.1 * math.cos(3 * th) * min(1.0, r * 2.5)
                    )  # sin pellizco en el centro
            rr = min(r, 1.0)  # radio normalizado: centro caliente, borde rojizo
            rgb = (
                _lerp(core, mid, rr / 0.45)
                if rr < 0.45
                else _lerp(mid, edge, (rr - 0.45) / 0.55)
            )
            row += bytes((rgb[2], rgb[1], rgb[0], min(255, round(255 * a))))  # BGRA
        rows.append(bytes(row))
    return _dds_bgra(px, rows)


def flame_texture_files() -> dict[str, bytes]:
    """``{ruta relativa al coche: DDS}`` de ``texture/flames`` (prefijos: ``f`` destello, ``l`` cola, otro = llama)."""
    out = {}
    for prefix, kind in (("f_flash", "flash"), ("l_tail", "tail"), ("b_flame", "body")):
        for n in range(TEX_FRAMES):
            out[f"texture/flames/{prefix}{n}.dds"] = _sprite(kind, n)
    return out
