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
from PySide6.QtCore import QBuffer
from PySide6.QtGui import QIcon, QPixmap

_ASSETS = Path(__file__).resolve().parent / "assets"
_ICO = _ASSETS / "icon.ico"
_LEAF_IDLE = _ASSETS / "leaf.png"
_LEAF_ACTIVE = _ASSETS / "leaf-filled.png"
_POSTURE = _ASSETS / "posture.png"

# Windows picks a tray render by DPI. Each size is rendered from the source art
# rather than scaled off one bitmap, so the thin outline resamples cleanly.
_TRAY_SIZES = (16, 20, 24, 32, 48)

# A running session is always green, whatever the overlay accent is set to: the
# tray has to mean "running" at a glance, not carry the overlay's theming.
#
# It cannot be one green, though. Apple's systemGreen scores 8.2:1 against the
# dark taskbar but only 1.8:1 against the light one, where it washes out
# completely; Windows' Fluent green is the reverse, 3.1:1 dark and 4.8:1 light.
# So the running colour switches with the theme, exactly like the idle glyph.
_GLYPH_ON_DARK = (255, 255, 255)
_GLYPH_ON_LIGHT = (32, 32, 34)
_ACTIVE_ON_DARK = (48, 209, 88)  # Apple systemGreen
_ACTIVE_ON_LIGHT = (16, 124, 16)  # Windows Fluent green

_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

_SUPERSAMPLE = 8


def _to_pixmap(image: Image.Image) -> QPixmap:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


def _from_pixmap(pixmap: QPixmap) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QBuffer.ReadWrite)
    pixmap.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return Image.open(io.BytesIO(data)).convert("RGBA")


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
def _glyph_alpha(path: Path) -> Image.Image:
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
    # The outline is left exactly as drawn. Dilating it at 16 and 20px was tried
    # to stop the sub-pixel stroke greying out, and it made the leaf visibly
    # heavier than the system glyphs beside it -- worse than the problem it
    # solved. Downsampling from the supersampled mask keeps the thin stroke
    # readable on its own.
    n = size * _SUPERSAMPLE
    mask = _glyph_alpha(_LEAF_ACTIVE if active else _LEAF_IDLE).resize((n, n), Image.LANCZOS)
    mask = _fit(mask, n)
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (n, n), (*colour, 255)), (0, 0), mask)
    return _to_pixmap(image.resize((size, size), Image.LANCZOS))


def app_icon() -> QIcon:
    """Window / taskbar icon."""
    return QIcon(str(_ICO))


def tray_icon(active: bool) -> QIcon:
    """Outline leaf when idle, filled green leaf while a session runs."""
    if taskbar_is_light():
        colour = _ACTIVE_ON_LIGHT if active else _GLYPH_ON_LIGHT
    else:
        colour = _ACTIVE_ON_DARK if active else _GLYPH_ON_DARK
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


@lru_cache(maxsize=16)
def posture_figure(size: int, colour: tuple[int, int, int]) -> QPixmap:
    """The upright figure on the posture reminder, tinted to the card's text.

    Same treatment as the tray leaf: an alpha mask from the source art, cropped
    to its ink and resampled once.
    """
    n = size * _SUPERSAMPLE
    mask = _fit(_glyph_alpha(_POSTURE).resize((n, n), Image.LANCZOS), n)
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (n, n), (*colour, 255)), (0, 0), mask)
    return _to_pixmap(image.resize((size, size), Image.LANCZOS))


# ---- windows logo --------------------------------------------------------

# The four panes of the Windows logo, as coordinates in an 18-unit box.
# Drawn rather than shipped as an asset so it can take the label's colour: a
# QLabel cannot load an <img src="data:..."> at all (Qt resolves src through the
# document's resource loader, which has no data: handler) and a file-backed one
# would be stuck at whatever colour it was saved in.
_WINDOWS_BOX = 18
_WINDOWS_PANES = (
    ((0, 2.5), (7, 1.5), (7, 8.5), (0, 8.5)),
    ((8, 1.5), (18, 0), (18, 8.5), (8, 8.5)),
    ((0, 9.5), (7, 9.5), (7, 16.5), (0, 15.5)),
    ((8, 9.5), (18, 9.5), (18, 18), (8, 16.5)),
)


@lru_cache(maxsize=8)
def windows_logo(size: int, colour: tuple[int, int, int]) -> QPixmap:
    n = size * _SUPERSAMPLE
    scale = n / _WINDOWS_BOX
    image = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for pane in _WINDOWS_PANES:
        draw.polygon([(x * scale, y * scale) for x, y in pane], fill=(*colour, 255))
    return _to_pixmap(image.resize((size, size), Image.LANCZOS))


# ---- glass cards ---------------------------------------------------------


@lru_cache(maxsize=8)
def card_shadow(
    width: int, height: int, margin: int, radius: int, blur: float, alpha: int, offset: int
) -> QPixmap:
    """The soft shadow under a glass card, baked once.

    Not a QGraphicsDropShadowEffect: an effect re-renders its whole source
    widget and re-blurs it every time any child repaints, so a card with an
    animation inside pays a full blur per frame.
    """
    size = (width + 2 * margin, height + 2 * margin)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(canvas).rounded_rectangle(
        [margin, margin + offset, margin + width, margin + height + offset],
        radius=radius,
        fill=(0, 0, 0, alpha),
    )
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))

    # Punch the card's own silhouette back out. The card covers exactly this
    # area, and these cards are translucent -- black left underneath seeps up
    # through the glass and dirties it.
    keep = Image.new("L", size, 255)
    ImageDraw.Draw(keep).rounded_rectangle(
        [margin, margin, margin + width, margin + height], radius=radius, fill=0
    )
    canvas.putalpha(Image.composite(canvas.getchannel("A"), Image.new("L", size, 0), keep))
    return _to_pixmap(canvas)


def blurred_backdrop(snapshot: QPixmap, blur: float, tint: tuple[int, int, int, int]) -> QPixmap:
    """SwiftUI's `.thinMaterial`, approximated.

    Qt has no live backdrop blur, so the card is backed by a still of whatever it
    covers, blurred and washed with `tint`. The still is taken once when the
    break opens and never refreshed -- acceptable because the desktop is not
    moving during a break, and the alternative is re-grabbing and re-blurring the
    screen every frame.
    """
    image = _from_pixmap(snapshot).filter(ImageFilter.GaussianBlur(blur))
    return _to_pixmap(Image.alpha_composite(image, Image.new("RGBA", image.size, tint)))
