"""Icon artwork shared by the tray and the exe (assets/icon.ico).

Design: a YouTube-style play button with a notification-bell badge.
  idle — muted grey button, so it stays quiet in the tray
  live — red button with a pulsing amber badge
Run `python -m notifier.icons` to regenerate assets/icon.ico.
"""

import math
import os

from PIL import Image, ImageDraw

_SS = 4            # supersampling factor for smooth edges
_BASE = 64         # design grid

GREY = (92, 92, 98)
RED = (255, 0, 0)
AMBER = (255, 179, 0)
DARK = (24, 24, 24)
WHITE = (255, 255, 255)


def _bell(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, fill):
    """Bell centred on (cx, cy); `s` is the half-width."""
    d.pieslice((cx - s * 0.7, cy - s * 0.95, cx + s * 0.7, cy + s * 0.45), 180, 360, fill=fill)
    d.polygon([(cx - s * 0.7, cy - s * 0.25), (cx + s * 0.7, cy - s * 0.25),
               (cx + s, cy + s * 0.5), (cx - s, cy + s * 0.5)], fill=fill)
    d.ellipse((cx - s * 0.25, cy + s * 0.45, cx + s * 0.25, cy + s * 0.9), fill=fill)


def draw_icon(live: bool = False, pulse: float = 1.0, size: int = _BASE) -> Image.Image:
    """RGBA icon. `pulse` (0-1) drives the live badge glow."""
    k = _SS * size / _BASE
    big = size * _SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def p(*v):
        return tuple(x * k for x in v)

    # Play button
    d.rounded_rectangle(p(2, 8, 58, 46), radius=11 * k, fill=(*(RED if live else GREY), 255))
    d.polygon([p(23, 17), p(23, 37), p(40, 27)], fill=(*WHITE, 255))

    # Bell badge (bottom-right)
    bx, by, br = 46, 46, 15
    if live:
        glow = int(70 + 150 * pulse)
        d.ellipse(p(bx - br - 3, by - br - 3, bx + br + 3, by + br + 3), fill=(*AMBER, glow))
    d.ellipse(p(bx - br, by - br, bx + br, by + br), fill=(*DARK, 255))
    _bell(d, bx * k, (by - 0.5) * k, 8 * k, (*(AMBER if live else WHITE), 255))

    return img.resize((size, size), Image.LANCZOS)


def idle_icon() -> Image.Image:
    return draw_icon(live=False).convert("RGBA")


def live_frames(n: int = 12) -> list[Image.Image]:
    return [
        draw_icon(live=True, pulse=0.5 + 0.5 * math.sin(2 * math.pi * i / n)).convert("RGBA")
        for i in range(n)
    ]


def write_ico(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    draw_icon(live=True, pulse=1.0, size=256).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    write_ico(os.path.join(root, "assets", "icon.ico"))
    print("Wrote assets/icon.ico")
