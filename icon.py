"""App and tray icons.

The window and taskbar icon is the macOS TapLock artwork, so the two platforms
look the same in the places that matter. The tray needs something else: that
artwork carries its own dark rounded-square background, which at 16px reads as a
smudge next to Windows' own transparent monochrome glyphs. The tray therefore
uses a leaf, mirroring the macOS menu bar item -- outline when idle, filled and
green while a session runs, the same distinction SF Symbols draws between `leaf`
and `leaf.fill`.

Pillow is kept isolated in this module -- it is the only place image generation
happens, so the dependency stays easy to find.
"""

import io
import winreg
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtGui import QIcon, QPixmap

_ASSETS = Path(__file__).resolve().parent / "assets"
_ICO = _ASSETS / "icon.ico"
_LEAF_IDLE = _ASSETS / "leaf.png"
_LEAF_ACTIVE = _ASSETS / "leaf-filled.png"

# Windows picks a tray render by DPI. Each size is rendered separately so the
# outline can be thickened only where it needs it.
_TRAY_SIZES = (16, 20, 24, 32, 48)

# A running session is always this green, whatever the overlay accent is set to:
# the tray has to mean "running" at a glance, not carry the overlay's theming.
ACTIVE_GREEN = (48, 209, 88)
_GLYPH_ON_DARK = (255, 255, 255)
_GLYPH_ON_LIGHT = (32, 32, 34)

_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

_SUPERSAMPLE = 8


def _to_pixmap(image: Image.Image) -> QPixmap:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


def taskbar_is_light() -> bool:
    """Whether a white glyph would be invisible in the tray.

    Windows keeps the taskbar theme (`SystemUsesLightTheme`) separate from the
    app theme (`AppsUseLightTheme`) and Qt only exposes the latter, so read the
    setting that actually decides this.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False


@lru_cache(maxsize=2)
def _leaf_alpha(path: Path) -> Image.Image:
    # The source glyphs are black on transparent, so the alpha channel is
    # already the mask, antialiasing included.
    return Image.open(path).convert("RGBA").getchannel("A")


def _fit(mask: Image.Image, n: int, margin: float = 0.02) -> Image.Image:
    """Crop to the ink and scale it to fill the cell, still supersampled so the
    downsample happens exactly once."""
    box = mask.getbbox()
    if box:
        mask = mask.crop(box)
    target = max(1, round(n * (1 - 2 * margin)))
    scale = target / max(mask.size)
    mask = mask.resize((max(1, round(mask.width * scale)), max(1, round(mask.height * scale))), Image.LANCZOS)
    canvas = Image.new("L", (n, n), 0)
    canvas.paste(mask, ((n - mask.width) // 2, (n - mask.height) // 2))
    return canvas


@lru_cache(maxsize=32)
def _tray_pixmap(size: int, active: bool, colour: tuple[int, int, int]) -> QPixmap:
    n = size * _SUPERSAMPLE
    mask = _leaf_alpha(_LEAF_ACTIVE if active else _LEAF_IDLE).resize((n, n), Image.LANCZOS)

    if not active and size <= 20:
        # The outline stroke is under a pixel wide at these sizes and greys out
        # into a smudge; dilate it slightly before the downsample. The filled
        # glyph has no thin strokes and needs none.
        mask = mask.filter(ImageFilter.MaxFilter(_SUPERSAMPLE | 1))

    mask = _fit(mask, n)
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (n, n), (*colour, 255)), (0, 0), mask)
    return _to_pixmap(image.resize((size, size), Image.LANCZOS))


def app_icon() -> QIcon:
    """Window / taskbar icon."""
    return QIcon(str(_ICO))


def tray_icon(active: bool) -> QIcon:
    """Outline leaf when idle, filled green leaf while a session runs."""
    colour = ACTIVE_GREEN if active else (_GLYPH_ON_LIGHT if taskbar_is_light() else _GLYPH_ON_DARK)
    icon = QIcon()
    for size in _TRAY_SIZES:
        icon.addPixmap(_tray_pixmap(size, active, colour))
    return icon


# ---- breathing overlay ---------------------------------------------------

# SwiftUI draws a 300pt circle at 30% alpha under a 60pt blur. A Gaussian that
# wide reaches roughly 3 sigma past the edge, so the visible disc spans about
# 300 + 2*3*60 design units. Keeping those three numbers lets the overlay scale
# the sprite in the same units SwiftUI animates in.
GLOW_CIRCLE = 300
GLOW_BLUR = 60
GLOW_SPAN = GLOW_CIRCLE + 6 * GLOW_BLUR

_GLOW_RESOLUTION = 512


@lru_cache(maxsize=4)
def glow_sprite(accent: tuple[int, int, int]) -> QPixmap:
    """The pulsing disc, rendered once per accent colour.

    Each frame only scales and fades this pixmap: running a Gaussian blur inside
    a paint event would not hold a frame rate.
    """
    n = _GLOW_RESOLUTION
    scale = n / GLOW_SPAN
    radius = GLOW_CIRCLE * scale / 2
    centre = n / 2

    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius],
        fill=(*accent, 77),  # 0.30 alpha, as in the SwiftUI original
    )
    return _to_pixmap(image.filter(ImageFilter.GaussianBlur(GLOW_BLUR * scale)))
