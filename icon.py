"""App and tray icons.

Everything derives from the macOS TapLock artwork (`assets/icon.png`) so the two
platforms stay visually identical. Pillow is kept isolated in this module -- it
is the only place image generation happens, so the dependency stays easy to find.

The tray icon carries no separate "relax" glyph: macOS uses an SF Symbols leaf,
which is a system resource we cannot redistribute. Session state is shown as an
accent dot badge on the corner of the app icon instead.
"""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtGui import QIcon, QPixmap

_ASSETS = Path(__file__).resolve().parent / "assets"
_ICO = _ASSETS / "icon.ico"
_PNG = _ASSETS / "icon.png"

# Windows picks a tray render by DPI. Each size is drawn separately rather than
# scaled from one bitmap: the badge needs a larger proportion at 16px or it
# vanishes, so a single scaled source would not survive.
_TRAY_SIZES = (16, 20, 24, 32, 48)

# icon.png's own background, reused as the badge ring so the dot reads as a
# badge rather than a blob merging into the padlock.
_TILE_BG = (28, 28, 30)

_SUPERSAMPLE = 4


@lru_cache(maxsize=1)
def _source() -> Image.Image:
    return Image.open(_PNG).convert("RGBA")


def _to_pixmap(image: Image.Image) -> QPixmap:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


def _badge_fraction(size: int) -> float:
    """Badge diameter as a fraction of the icon. Hand-tuned: small renders need a
    disproportionately large dot to stay legible in the tray."""
    if size <= 16:
        return 0.30
    if size <= 24:
        return 0.24
    return 0.19


def _tray_pixmap(size: int, active: bool, accent: tuple[int, int, int]) -> QPixmap:
    n = size * _SUPERSAMPLE
    image = _source().resize((n, n), Image.LANCZOS)

    if active:
        d = _badge_fraction(size) * n
        margin = 0.04 * n
        box = [n - margin - d, n - margin - d, n - margin, n - margin]
        ring = max(_SUPERSAMPLE, round(size * 0.035) * _SUPERSAMPLE)
        ImageDraw.Draw(image).ellipse(box, fill=(*accent, 255), outline=(*_TILE_BG, 255), width=ring)

    return _to_pixmap(image.resize((size, size), Image.LANCZOS))


def app_icon() -> QIcon:
    """Window / taskbar icon."""
    return QIcon(str(_ICO))


def tray_icon(active: bool, accent: tuple[int, int, int]) -> QIcon:
    """Tray icon; `active` adds the running-session badge in `accent`."""
    icon = QIcon()
    for size in _TRAY_SIZES:
        icon.addPixmap(_tray_pixmap(size, active, accent))
    return icon
