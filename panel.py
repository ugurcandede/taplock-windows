"""The tray popup -- Windows stand-in for the macOS NSPopover.

A frameless always-on-top `Qt.Tool` window anchored to the tray icon. `Qt.Popup`
would give the transient dismiss behaviour for free, but keyboard focus inside a
popup is unreliable and this panel will hold text fields, so it hides on
`WindowDeactivate` instead.
"""

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

CARD_WIDTH = 300
# Room for the drop shadow: the outer widget is transparent, the card floats in it.
SHADOW_MARGIN = 18
# Gap between the tray icon and the panel.
ANCHOR_GAP = 8


class Panel(QWidget):
    def __init__(self, on_quit):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._on_quit = on_quit
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)

        card = QWidget()
        card.setObjectName("card")
        card.setFixedWidth(CARD_WIDTH)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 6)
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(0, 16, 0, 4)
        body.setSpacing(12)

        placeholder = QLabel("relax")
        placeholder.setAlignment(Qt.AlignCenter)
        body.addWidget(placeholder)

        divider = QFrame()
        divider.setObjectName("divider")
        body.addWidget(divider)

        quit_button = QPushButton("quit taplock")
        quit_button.setObjectName("plain")
        quit_button.setCursor(Qt.PointingHandCursor)
        quit_button.clicked.connect(self._on_quit)
        body.addWidget(quit_button)

    # ---- show / hide -----------------------------------------------------

    def toggle(self, anchor: QRect):
        if self.isVisible():
            self.hide()
        else:
            self.show_at(anchor)

    def show_at(self, anchor: QRect):
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center()) if not anchor.isEmpty() else None
        screen = screen or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        if anchor.isEmpty():
            # Icon is hidden in the tray overflow flyout and reports no geometry;
            # fall back to the corner the tray normally lives in.
            x = area.right() - self.width()
            y = area.bottom() - self.height()
        else:
            x = anchor.center().x() - self.width() // 2
            y = anchor.top() - self.height() + SHADOW_MARGIN - ANCHOR_GAP
            if y < area.top():  # taskbar docked to the top edge
                y = anchor.bottom() - SHADOW_MARGIN + ANCHOR_GAP

        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)

        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, e):
        # Transient dismiss: clicking anywhere else closes the panel.
        if e.type() == QEvent.WindowDeactivate:
            self.hide()
        return super().event(e)
