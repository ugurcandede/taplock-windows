"""Break overlays -- port of TapLockCore/RelaxingWindow.swift.

Frameless, always-on-top `Qt.Tool` windows with a translucent background. They
never block input: relax mode only asks for attention, so nothing here needs a
keyboard hook or elevated rights.

`breathing` covers every screen. Only the window on the screen holding the
cursor takes focus, which is what makes Esc work; the rest are shown without
activating so they cannot steal the caret.
"""

import math

from PySide6.QtCore import (
    QElapsedTimer,
    QLocale,
    QObject,
    QPoint,
    QRect,
    QRectF,
    QTime,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import icon
from parsers import format_countdown, luminance, parse_color, rgb255

# One full pulse: SwiftUI eases 4s out and 4s back with autoreverse.
PULSE_MS = 8000
FRAME_MS = 33
GLOW_MIN_SCALE = 0.8
GLOW_MAX_SCALE = 1.4
GLOW_MIN_ALPHA = 0.2
GLOW_MAX_ALPHA = 0.6


def _font(pixel_size, weight, mono=False):
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas"] if mono else ["Segoe UI Variable Display", "Segoe UI"])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def _white(alpha):
    return f"color: rgba(255, 255, 255, {alpha});"


def _rgba(colour, alpha):
    r, g, b = colour
    return f"color: rgba({r}, {g}, {b}, {alpha});"


def _snapshot(screen, rect):
    """The desktop inside `rect` (global coordinates), grabbed before we cover it."""
    full = screen.grabWindow(0)
    geometry = screen.geometry()
    # Derive the ratio from the grab rather than assuming Qt returns device or
    # logical pixels -- that differs by platform and version, and guessing wrong
    # slides the blur out from under the card on a scaled display.
    ratio = full.width() / max(1, geometry.width())
    return full.copy(
        round((rect.x() - geometry.x()) * ratio),
        round((rect.y() - geometry.y()) * ratio),
        round(rect.width() * ratio),
        round(rect.height() * ratio),
    )


def _clock_text():
    """Locale short time. macOS sets a fixed "HH:mm" but leaves the formatter's
    locale alone, so it renders as the user's convention -- match that."""
    return QLocale().toString(QTime.currentTime(), QLocale.FormatType.ShortFormat)


class BreathingOverlay(QWidget):
    """Full-screen dark wash with a softly pulsing accent disc."""

    skipped = Signal()

    def __init__(self, screen, config, accent, takes_focus):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        if not takes_focus:
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setWindowFlag(Qt.WindowDoesNotAcceptFocus)

        self.takes_focus = takes_focus
        self._wash = QColor(0, 0, 0, round(config.opacity * 255))
        self._sprite = icon.glow_sprite(accent)
        self._pulse = 0.0
        self._remaining_text = ""

        self.setGeometry(screen.geometry())
        self._build()

    def prepare(self, screen):
        """Nothing to do: this theme covers the whole screen and paints its own
        wash, so there is no backdrop to capture."""

        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self._frame)
        self._frames.start()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 50)
        layout.setSpacing(0)
        layout.addStretch()

        title = QLabel("Take a break")
        title.setFont(_font(42, QFont.Weight.Light))
        title.setStyleSheet(_white(1.0))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(30)

        self._clock = QLabel(_clock_text())
        self._clock.setFont(_font(64, QFont.Weight.Thin, mono=True))
        self._clock.setStyleSheet(_white(0.8))
        self._clock.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._clock)
        layout.addSpacing(30)

        self._remaining = QLabel()
        self._remaining.setFont(_font(22, QFont.Weight.Normal, mono=True))
        self._remaining.setStyleSheet(_white(0.5))
        self._remaining.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._remaining)

        layout.addStretch()

        skip = QPushButton("Skip")
        skip.setFont(_font(14, QFont.Weight.Medium))
        skip.setCursor(Qt.PointingHandCursor)
        skip.setFocusPolicy(Qt.NoFocus)
        skip.setStyleSheet(
            "QPushButton { background: rgba(255, 255, 255, 0.15); border: none;"
            " border-radius: 8px; padding: 8px 28px; color: rgba(255, 255, 255, 0.8); }"
            "QPushButton:hover { background: rgba(255, 255, 255, 0.24); }"
        )
        skip.clicked.connect(self.skipped)
        row = QVBoxLayout()
        row.setAlignment(Qt.AlignHCenter)
        row.addWidget(skip)
        layout.addLayout(row)
        layout.addSpacing(16)

        hint = QLabel("Press Esc to skip" if self.takes_focus else "")
        hint.setFont(_font(14, QFont.Weight.Normal))
        hint.setStyleSheet(_white(0.35))
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

    # ---- animation -------------------------------------------------------

    def _glow_rect(self):
        span = round(icon.GLOW_SPAN * GLOW_MAX_SCALE)
        centre = self.rect().center()
        return QRect(centre.x() - span // 2, centre.y() - span // 2, span, span)

    def _frame(self):
        # Qt has no autoreverse. A cosine over one 8s loop is exactly SwiftUI's
        # 4s easeInOut run in both directions, with no seam at the wrap.
        phase = (self._elapsed.elapsed() % PULSE_MS) / PULSE_MS
        self._pulse = (1 - math.cos(2 * math.pi * phase)) / 2
        self._clock.setText(_clock_text())
        self.update(self._glow_rect())

    def set_remaining(self, seconds):
        text = format_countdown(seconds)
        if text != self._remaining_text:
            self._remaining_text = text
            self._remaining.setText(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Clipped to the exposed region by Qt, so repainting only the glow does
        # not cost a full-screen fill.
        painter.fillRect(event.rect(), self._wash)

        scale = GLOW_MIN_SCALE + (GLOW_MAX_SCALE - GLOW_MIN_SCALE) * self._pulse
        span = round(icon.GLOW_SPAN * scale)
        centre = self.rect().center()
        painter.setOpacity(GLOW_MIN_ALPHA + (GLOW_MAX_ALPHA - GLOW_MIN_ALPHA) * self._pulse)
        painter.drawPixmap(
            QRect(centre.x() - span // 2, centre.y() - span // 2, span, span), self._sprite
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.skipped.emit()
            return
        super().keyPressEvent(event)


# ---- glass themes --------------------------------------------------------

SHADOW_MARGIN = 24
MINIMAL_WIDTH = 300
MINIMAL_RADIUS = 16
MINIMAL_BLUR = 24
MINI_SIZE = (340, 50)
MINI_RADIUS = 10
MINI_BLUR = 18
MINI_TOP_GAP = 12

# SwiftUI layers `.thinMaterial` under `accent.opacity(0.35)`. There is no
# material here, so a neutral scrim stands in for it and keeps the card dark
# enough for the light/dark text rule to hold whatever the desktop looks like.
# The scrim is what carries the contrast: at 0.45 the green card left its white
# body text at 3.3:1, under the threshold; 0.62 puts it at 4.7:1 and lands
# closer to the macOS card, which reads as a much darker green than the raw
# accent because its material darkens heavily.
_SCRIM = 0.62
_ACCENT_WASH = 0.35

# mini is `.ultraThinMaterial` on macOS, which follows the system appearance.
# Here it is fixed dark: the bar always carries white text, and a card that
# tracked the desktop instead could land white-on-white.
_MINI_TINT = (0, 0, 0, 184)


def _minimal_tint(accent):
    """One RGBA wash equivalent to a black scrim followed by the accent layer.

    Compositing both separately gives `0.3575*base + 0.35*accent`; a single
    layer (C, a) gives `(1-a)*base + a*C`, so a = 0.6425 and C = 0.545*accent.
    """
    keep = (1 - _SCRIM) * (1 - _ACCENT_WASH)
    alpha = 1 - keep
    return (*(round(c * _ACCENT_WASH / alpha) for c in accent), round(alpha * 255))


class _Card(QWidget):
    """Rounded panel that paints the blurred snapshot as its background."""

    def __init__(self, radius):
        super().__init__()
        self._radius = radius
        self._backdrop = None

    def set_backdrop(self, pixmap):
        self._backdrop = pixmap
        self.update()

    def paintEvent(self, event):
        if self._backdrop is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        painter.setClipPath(path)
        painter.drawPixmap(self.rect(), self._backdrop)


class _GlassOverlay(QWidget):
    """A card floating over the desktop, backed by a blurred still of what it covers.

    The window is larger than the card by `SHADOW_MARGIN` so the drop shadow has
    somewhere to fall; the same trick `panel.py` uses.
    """

    skipped = Signal()

    def __init__(self, radius, tint, blur, shadow_blur, shadow_alpha, takes_focus):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        if not takes_focus:
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.setWindowFlag(Qt.WindowDoesNotAcceptFocus)

        self.takes_focus = takes_focus
        self._tint = tint
        self._blur = blur

        self.card = _Card(radius)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(shadow_blur)
        shadow.setColor(QColor(0, 0, 0, shadow_alpha))
        shadow.setOffset(0, 4)
        self.card.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        outer.addWidget(self.card)

    def prepare(self, screen):
        """Size, position, then capture what is behind -- in that order, or the
        grab catches this window instead of the desktop."""
        self.layout().activate()
        self.adjustSize()
        self.move(self._position(screen))
        card_rect = QRect(self.pos() + QPoint(SHADOW_MARGIN, SHADOW_MARGIN), self.card.size())
        self.card.set_backdrop(icon.blurred_backdrop(_snapshot(screen, card_rect), self._blur, self._tint))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.skipped.emit()
            return
        super().keyPressEvent(event)


class MinimalOverlay(_GlassOverlay):
    """Centred glass card, no full-screen wash."""

    def __init__(self, screen, config, accent, takes_focus):
        super().__init__(
            radius=MINIMAL_RADIUS,
            tint=_minimal_tint(accent),
            blur=MINIMAL_BLUR,
            shadow_blur=40,
            shadow_alpha=90,
            takes_focus=takes_focus,
        )
        # Same rule as the Swift original: dark text on a light accent.
        text = (0, 0, 0) if luminance(tuple(c / 255 for c in accent)) > 0.5 else (255, 255, 255)
        self.card.setFixedWidth(MINIMAL_WIDTH)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        title = QLabel("Take a break")
        title.setFont(_font(20, QFont.Weight.Medium))
        title.setStyleSheet(_rgba(text, 1.0))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self._clock = QLabel(_clock_text())
        self._clock.setFont(_font(36, QFont.Weight.Thin, mono=True))
        self._clock.setStyleSheet(_rgba(text, 0.9))
        self._clock.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._clock)

        self._remaining = QLabel()
        self._remaining.setFont(_font(14, QFont.Weight.Normal, mono=True))
        self._remaining.setStyleSheet(_rgba(text, 0.5))
        self._remaining.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._remaining)

        skip = QPushButton("Skip")
        skip.setFont(_font(14, QFont.Weight.Medium))
        skip.setCursor(Qt.PointingHandCursor)
        skip.setFocusPolicy(Qt.NoFocus)
        r, g, b = text
        skip.setStyleSheet(
            f"QPushButton {{ background: rgba({r}, {g}, {b}, 0.15); border: none;"
            f" border-radius: 8px; padding: 8px 28px; color: rgba({r}, {g}, {b}, 0.85); }}"
            f"QPushButton:hover {{ background: rgba({r}, {g}, {b}, 0.24); }}"
        )
        skip.clicked.connect(self.skipped)
        layout.addWidget(skip)

    def _position(self, screen):
        area = screen.availableGeometry()
        return QPoint(
            area.center().x() - self.width() // 2,
            area.center().y() - self.height() // 2,
        )

    def set_remaining(self, seconds):
        self._clock.setText(_clock_text())
        self._remaining.setText(format_countdown(seconds))


class MiniOverlay(_GlassOverlay):
    """Small bar at the top of the screen. Never takes focus, so no Esc."""

    def __init__(self, screen, config, accent, takes_focus):
        super().__init__(
            radius=MINI_RADIUS,
            tint=_MINI_TINT,
            blur=MINI_BLUR,
            shadow_blur=24,
            shadow_alpha=70,
            takes_focus=False,
        )
        self.card.setFixedSize(*MINI_SIZE)

        layout = QHBoxLayout(self.card)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(16)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: rgb{accent}; border-radius: 4px;")
        layout.addWidget(dot)

        title = QLabel("Take a break")
        title.setFont(_font(14, QFont.Weight.Medium))
        title.setStyleSheet(_white(1.0))
        layout.addWidget(title)

        layout.addStretch()

        self._remaining = QLabel()
        self._remaining.setFont(_font(13, QFont.Weight.Normal, mono=True))
        self._remaining.setStyleSheet(_white(0.6))
        layout.addWidget(self._remaining)

        close = QPushButton("✕")
        close.setFont(_font(11, QFont.Weight.DemiBold))
        close.setCursor(Qt.PointingHandCursor)
        close.setFocusPolicy(Qt.NoFocus)
        close.setFixedSize(18, 18)
        close.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: rgba(255, 255, 255, 0.6); }"
            "QPushButton:hover { color: rgba(255, 255, 255, 1.0); }"
        )
        close.clicked.connect(self.skipped)
        layout.addWidget(close)

    def _position(self, screen):
        area = screen.availableGeometry()
        return QPoint(area.center().x() - self.width() // 2, area.top() + MINI_TOP_GAP - SHADOW_MARGIN)

    def set_remaining(self, seconds):
        self._remaining.setText(format_countdown(seconds))


_THEMES = {
    "breathing": BreathingOverlay,
    "minimal": MinimalOverlay,
    "mini": MiniOverlay,
}


class OverlayController(QObject):
    """Opens and closes the windows for one break."""

    skipped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._windows = []

    @property
    def visible(self):
        return bool(self._windows)

    def open(self, config):
        self.close()
        accent = rgb255(parse_color(config.color) or parse_color("green"))
        overlay_class = _THEMES.get(config.theme, BreathingOverlay)
        focus_screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

        if overlay_class is BreathingOverlay:
            # Full-screen theme: cover every display so the break is impossible
            # to miss, but only the one under the cursor takes focus.
            screens = QGuiApplication.screens()
        else:
            # The glass themes are small and deliberately unobtrusive; repeating
            # them on every display would just be noise.
            screens = [focus_screen]

        for screen in screens:
            window = overlay_class(screen, config, accent, takes_focus=screen is focus_screen)
            window.skipped.connect(self.skipped)
            window.prepare(screen)
            self._windows.append(window)
            window.show()
            window.raise_()
            if window.takes_focus:
                window.activateWindow()

    def set_remaining(self, seconds):
        for window in self._windows:
            window.set_remaining(seconds)

    def close(self):
        for window in self._windows:
            window.close()
        self._windows.clear()
