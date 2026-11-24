"""Genera src/app/static/icon.ico (icono de la ventana nativa) con el mismo diseño que icon.svg.

Uso: python tools/gen_icon.py. Necesita Pillow y una fuente en negrita del sistema (Arial/Segoe UI).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "src" / "app" / "static" / "icon.ico"
S = 256


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=56, fill="#1e1e2e")
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 88)
            break
        except OSError:
            continue
    if font is None:
        raise SystemExit("no hay fuente en negrita disponible")
    d.text((S / 2, 112), "b2ac", font=font, fill="#cba6f7", anchor="mm")
    d.rounded_rectangle((52, 182, 204, 194), radius=6, fill="#94e2d5")
    return img


if __name__ == "__main__":
    render().save(
        OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)]
    )
    print(OUT)
