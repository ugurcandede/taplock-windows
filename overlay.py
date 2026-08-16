"""Break overlays -- port of TapLockCore/RelaxingWindow.swift.

Frameless, always-on-top `Qt.Tool` windows with a translucent background. They
never block input: relax mode only asks for attention, so nothing here needs a
keyboard hook or elevated rights.

`breathing` covers every screen. Only the window on the screen holding the
cursor takes focus, which is what makes Esc work; the rest are shown without
activating so they cannot steal the caret.
"""

import math

from PySide6.QtCore import QElapsedTimer, QLocale, QObject, QRect, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

import icon
from parsers import format_countdown, parse_color, rgb255

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

        self._takes_focus = takes_focus
        self._wash = QColor(0, 0, 0, round(config.opacity * 255))
        self._sprite = icon.glow_sprite(accent)
        self._pulse = 0.0
        self._remaining_text = ""

        self.setGeometry(screen.geometry())
        self._build()

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

        hint = QLabel("Press Esc to skip" if self._takes_focus else "")
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


# Minimal and mini land in the next step; until then every theme shows the
# breathing overlay rather than nothing at all.
_THEMES = {"breathing": BreathingOverlay}


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
        for screen in QGuiApplication.screens():
            window = overlay_class(screen, config, accent, takes_focus=screen is focus_screen)
            window.skipped.connect(self.skipped)
            self._windows.append(window)
            window.show()
            window.raise_()
            if screen is focus_screen:
                window.activateWindow()

    def set_remaining(self, seconds):
        for window in self._windows:
            window.set_remaining(seconds)

    def close(self):
        for window in self._windows:
            window.close()
        self._windows.clear()
