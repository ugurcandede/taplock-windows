"""The tray popup -- Windows stand-in for the macOS NSPopover.

A frameless always-on-top `Qt.Tool` window anchored to the tray icon. `Qt.Popup`
would give the transient dismiss behaviour for free, but keyboard focus inside a
popup is unreliable and this panel holds text fields, so it hides on
`WindowDeactivate` instead.

Two layouts share the card: the idle form (interval / break / presets / start)
and the running view (countdown / skip / stop), swapped from the session's
`state_changed`.
"""

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import config
from parsers import UNIT_SECONDS, best_unit, format_mmss

CARD_WIDTH = 300
# Room for the drop shadow: the outer widget is transparent, the card floats in it.
SHADOW_MARGIN = 18
# Gap between the tray icon and the panel.
ANCHOR_GAP = 8
SIDE_PADDING = 20


def _mono(pixel_size, weight=QFont.Weight.ExtraLight):
    """Cascadia Mono ships with Windows 11 / Terminal; Consolas is the fallback."""
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def _divider():
    line = QFrame()
    line.setObjectName("divider")
    return line


class _UnitPicker(QWidget):
    """Captioned s / m / h selector."""

    def __init__(self, caption):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label = QLabel(caption)
        label.setObjectName("secondary")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(2)
        group = QButtonGroup(self)
        self._buttons = {}
        for unit in ("s", "m", "h"):
            button = QPushButton(unit)
            button.setObjectName("unit")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            group.addButton(button)
            row.addWidget(button)
            self._buttons[unit] = button
        layout.addLayout(row)
        self.set_unit("m")

    def unit(self):
        return next(u for u, b in self._buttons.items() if b.isChecked())

    def set_unit(self, unit):
        self._buttons[unit].setChecked(True)


class Panel(QWidget):
    def __init__(self, relax_config, session, on_quit):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._config = relax_config
        self._session = session
        self._on_quit = on_quit
        self._anchor = QRect()
        self._layout_key = None

        self._build()
        self._load_config()
        session.state_changed.connect(self._refresh)
        self._refresh()

    # ---- construction ----------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)

        card = QWidget()
        self._card = card
        card.setObjectName("card")
        card.setFixedWidth(CARD_WIDTH)
        self._shadow = QGraphicsDropShadowEffect(card)
        self._shadow.setBlurRadius(32)
        self._shadow.setOffset(0, 6)
        card.setGraphicsEffect(self._shadow)
        outer.addWidget(card)

        body = QVBoxLayout(card)
        body.setContentsMargins(0, 10, 0, 4)
        body.setSpacing(0)

        self._error = QLabel()
        self._error.setObjectName("error")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.hide()
        body.addWidget(self._error)

        self._idle = self._build_idle()
        body.addWidget(self._idle)

        self._active = self._build_active()
        body.addWidget(self._active)

        body.addWidget(_divider())

        quit_button = QPushButton("quit taplock")
        quit_button.setObjectName("plain")
        quit_button.setFocusPolicy(Qt.NoFocus)
        quit_button.setCursor(Qt.PointingHandCursor)
        quit_button.clicked.connect(self._on_quit)
        body.addWidget(quit_button)

    def _build_idle(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SIDE_PADDING, 6, SIDE_PADDING, 10)
        layout.setSpacing(8)

        entry = QHBoxLayout()
        entry.setSpacing(4)
        entry.addStretch()
        self._interval_edit = self._duration_edit(80, Qt.AlignRight | Qt.AlignVCenter)
        entry.addWidget(self._interval_edit)
        slash = QLabel("/")
        slash.setObjectName("slash")
        slash.setFont(_mono(32))
        entry.addWidget(slash)
        self._break_edit = self._duration_edit(60, Qt.AlignLeft | Qt.AlignVCenter)
        entry.addWidget(self._break_edit)
        entry.addStretch()
        layout.addLayout(entry)

        units = QHBoxLayout()
        units.setSpacing(16)
        self._interval_unit = _UnitPicker("every")
        self._break_unit = _UnitPicker("break")
        units.addWidget(self._interval_unit)
        units.addWidget(self._break_unit)
        layout.addLayout(units)

        start = QPushButton("start")
        start.setObjectName("primary")
        start.setFocusPolicy(Qt.NoFocus)
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self._start)
        layout.addWidget(start)

        presets = QHBoxLayout()
        presets.setSpacing(0)
        for interval, break_minutes in config.PRESETS:
            button = QPushButton(f"{interval}/{break_minutes}")
            button.setObjectName("preset")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _=False, i=interval, b=break_minutes: self._apply_preset(i, b)
            )
            presets.addWidget(button)
        layout.addLayout(presets)

        return page

    def _duration_edit(self, width, alignment):
        edit = QLineEdit()
        edit.setObjectName("duration")
        edit.setFont(_mono(44))
        edit.setFixedWidth(width)
        edit.setAlignment(alignment)
        edit.setValidator(QIntValidator(1, 999, self))
        edit.returnPressed.connect(self._start)
        return edit

    def _build_active(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SIDE_PADDING, 14, SIDE_PADDING, 12)
        layout.setSpacing(8)

        self._countdown = QLabel()
        self._countdown.setFont(_mono(52))
        self._countdown.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._countdown)

        self._phase = QLabel()
        self._phase.setObjectName("secondary")
        self._phase.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._phase)

        layout.addSpacing(6)

        self._skip = QPushButton("skip")
        self._skip.setObjectName("warning")
        self._skip.setFocusPolicy(Qt.NoFocus)
        self._skip.setCursor(Qt.PointingHandCursor)
        self._skip.clicked.connect(self._session.skip_break)
        layout.addWidget(self._skip)

        stop = QPushButton("stop")
        stop.setObjectName("danger")
        stop.setFocusPolicy(Qt.NoFocus)
        stop.setCursor(Qt.PointingHandCursor)
        stop.clicked.connect(self._session.stop)
        layout.addWidget(stop)

        return page

    def apply_theme(self, dark):
        """The stylesheet handles colours; the shadow is set in code, and the
        heavy one that reads well on a dark card is a smudge on a light one."""
        self._shadow.setColor(QColor(0, 0, 0, 150 if dark else 55))

    # ---- config <-> form -------------------------------------------------

    def _load_config(self):
        interval, unit = best_unit(self._config.interval)
        self._interval_edit.setText(str(interval))
        self._interval_unit.set_unit(unit)
        value, unit = best_unit(self._config.break_duration)
        self._break_edit.setText(str(value))
        self._break_unit.set_unit(unit)

    def _apply_preset(self, interval_minutes, break_minutes):
        self._interval_edit.setText(str(interval_minutes))
        self._interval_unit.set_unit("m")
        self._break_edit.setText(str(break_minutes))
        self._break_unit.set_unit("m")
        self._set_error(None)

    def _seconds(self, edit, picker):
        text = edit.text().strip()
        if not text.isdigit() or int(text) <= 0:
            return None
        return int(text) * UNIT_SECONDS[picker.unit()]

    def _start(self):
        interval = self._seconds(self._interval_edit, self._interval_unit)
        if interval is None:
            return self._set_error("Invalid interval")
        break_duration = self._seconds(self._break_edit, self._break_unit)
        if break_duration is None:
            return self._set_error("Invalid break duration")
        if interval <= break_duration:
            return self._set_error("Interval must be longer than break")

        self._set_error(None)
        self._config.interval = interval
        self._config.break_duration = break_duration
        config.save(self._config)
        self._session.start(self._config)
        self.hide()  # macOS closes the popover once a session starts

    def _set_error(self, message):
        self._error.setText(message or "")
        self._error.setVisible(bool(message))

    # ---- refresh ---------------------------------------------------------

    def _refresh(self):
        running = self._session.running
        on_break = self._session.on_break

        if running:
            self._countdown.setText(format_mmss(self._session.remaining))
            self._phase.setText("break time!" if on_break else "next break in...")

        key = (running, on_break)
        if key == self._layout_key:
            return  # countdown text only; leave geometry alone
        self._layout_key = key

        self._idle.setVisible(not running)
        self._active.setVisible(running)
        self._skip.setVisible(on_break)
        if not running:
            self._set_error(None)
        self._reflow()

    def _fit(self):
        # Hiding a page only posts a LayoutRequest, which is handled on the next
        # event-loop pass -- until then both pages are still in the size hint and
        # adjustSize() would size the window to their sum. Invalidating the card
        # layout first drops those cached hints so the resize is correct in the
        # same call, with no intermediate frame at the wrong height.
        for layout in (self._card.layout(), self.layout()):
            layout.invalidate()
            layout.activate()
        self.adjustSize()

    def _reflow(self):
        self._fit()
        if self.isVisible():
            self.show_at(self._anchor)

    # ---- show / hide -----------------------------------------------------

    def toggle(self, anchor):
        if self.isVisible():
            self.hide()
        else:
            self.show_at(anchor)

    def show_at(self, anchor):
        self._anchor = anchor
        self._fit()
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

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def event(self, e):
        # Transient dismiss: clicking anywhere else closes the panel.
        if e.type() == QEvent.WindowDeactivate:
            self.hide()
        return super().event(e)
