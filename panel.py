"""The tray popup -- Windows stand-in for the macOS NSPopover.

A frameless always-on-top `Qt.Tool` window anchored to the tray icon. `Qt.Popup`
would give the transient dismiss behaviour for free, but keyboard focus inside a
popup is unreliable and this panel holds text fields, so it hides on
`WindowDeactivate` instead.

Two layouts share the card: the idle form (interval / break / presets / start /
settings) and the running view (countdown / skip / stop), swapped from the
session's `state_changed`.
"""

from PySide6.QtCore import QEasingCurve, QEvent, QRect, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIntValidator, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
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
import icon
import startup
from parsers import PRESET_COLORS, UNIT_SECONDS, best_unit, format_mmss, parse_color, rgb255
import version
from theme import DARK, LIGHT

CARD_WIDTH = 300
# Room for the drop shadow: the outer widget is transparent, the card floats in it.
SHADOW_MARGIN = 18
# Gap between the tray icon and the panel.
ANCHOR_GAP = 8
SIDE_PADDING = 20
# Matches the 0.15s easeInOut the macOS popover animates its settings with.
SETTINGS_ANIM_MS = 150
# QWIDGETSIZE_MAX; PySide does not export it.
_UNBOUNDED = 16777215
# Windows logo in the about line, sized to sit with 11px text.
LOGO_PX = 12


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


def _caption(text):
    label = QLabel(text)
    label.setObjectName("secondary")
    return label


class _DisclosureRow(QWidget):
    """Label left, chevron right, whole row clickable -- a plain QPushButton
    centres its text and cannot separate the two."""

    clicked = Signal()

    def __init__(self, text):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SIDE_PADDING, 8, SIDE_PADDING, 8)
        layout.addWidget(_caption(text))
        layout.addStretch()
        self._chevron = _caption("›")
        layout.addWidget(self._chevron)

    def set_open(self, is_open):
        self._chevron.setText("⌄" if is_open else "›")

    def mousePressEvent(self, event):
        self.clicked.emit()


class _Switch(QCheckBox):
    """Windows 11 style toggle. Qt has no switch widget and QSS cannot draw the
    knob, so it is painted here; the QCheckBox underneath handles the clicking."""

    WIDTH, HEIGHT = 34, 18

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.apply_theme(True)

    def apply_theme(self, dark):
        self._off = QColor("#48484A" if dark else "#C8C6C4")
        self._on = QColor("#0A84FF" if dark else "#0067C0")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._on if self.isChecked() else self._off)
        painter.drawRoundedRect(self.rect(), self.HEIGHT / 2, self.HEIGHT / 2)
        size = self.HEIGHT - 4
        x = self.WIDTH - size - 2 if self.isChecked() else 2
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(x, 2, size, size)


class _Swatch(QPushButton):
    """One overlay colour. The fill is the value itself, so it is set inline --
    the one case the stylesheet cannot cover."""

    def __init__(self, name):
        super().__init__()
        self.name = name
        self._rgb = rgb255(parse_color(name))
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(name)
        self.apply_theme(True)

    def apply_theme(self, dark):
        ring = "#FFFFFF" if dark else "#1B1B1F"
        r, g, b = self._rgb
        self.setStyleSheet(
            f"QPushButton {{ background: rgb({r}, {g}, {b});"
            f" border: 1px solid rgba(128, 128, 128, 0.4); border-radius: 11px; }}"
            f"QPushButton:checked {{ border: 2px solid {ring}; }}"
        )


class _UnitPicker(QWidget):
    """Captioned s / m / h selector."""

    def __init__(self, caption):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label = _caption(caption)
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
    preview_theme = Signal()
    preview_posture = Signal()

    def __init__(self, relax_config, session, on_quit):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._config = relax_config
        self._session = session
        self._on_quit = on_quit
        self._anchor = QRect()
        self._layout_key = None
        self._loading = False
        self._settings_open = False

        self._settings_anim = QVariantAnimation(self)
        self._settings_anim.setDuration(SETTINGS_ANIM_MS)
        self._settings_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._settings_anim.valueChanged.connect(self._settings_step)
        self._settings_anim.finished.connect(self._settings_settled)

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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(SIDE_PADDING, 6, SIDE_PADDING, 10)
        form_layout.setSpacing(8)

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
        form_layout.addLayout(entry)

        units = QHBoxLayout()
        units.setSpacing(16)
        self._interval_unit = _UnitPicker("every")
        self._break_unit = _UnitPicker("break")
        units.addWidget(self._interval_unit)
        units.addWidget(self._break_unit)
        form_layout.addLayout(units)

        start = QPushButton("start")
        start.setObjectName("primary")
        start.setFocusPolicy(Qt.NoFocus)
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self._start)
        form_layout.addWidget(start)

        presets = QHBoxLayout()
        presets.setSpacing(0)
        for interval, break_minutes in config.PRESETS:
            button = QPushButton(f"{interval}/{break_minutes}")
            button.setObjectName("preset")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=interval, b=break_minutes: self._apply_preset(i, b))
            presets.addWidget(button)
        form_layout.addLayout(presets)

        layout.addWidget(form)
        layout.addWidget(_divider())

        self._settings_button = _DisclosureRow("settings")
        self._settings_button.clicked.connect(self._toggle_settings)
        layout.addWidget(self._settings_button)

        self._settings = self._build_settings()
        self._settings.hide()
        layout.addWidget(self._settings)

        return page

    def _duration_edit(self, width, alignment):
        edit = QLineEdit()
        edit.setObjectName("duration")
        edit.setFont(_mono(44))
        edit.setFixedWidth(width)
        edit.setAlignment(alignment)
        edit.setValidator(QIntValidator(1, 999, self))
        edit.returnPressed.connect(self._start)
        # Every button here is NoFocus, so with the default StrongFocus these
        # fields were the only focus candidates and Qt handed the caret to the
        # first one the moment the panel opened. ClickFocus keeps them silent
        # until they are actually clicked -- the same thing the macOS popover
        # does by clearing its first responder on appear.
        edit.setFocusPolicy(Qt.ClickFocus)
        return edit

    def _build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SIDE_PADDING, 4, SIDE_PADDING, 10)
        layout.setSpacing(10)

        # theme + preview
        theme_row = QHBoxLayout()
        theme_row.addWidget(_caption("theme"))
        theme_row.addStretch()
        preview = QPushButton("preview")
        preview.setObjectName("preset")
        preview.setFocusPolicy(Qt.NoFocus)
        preview.setCursor(Qt.PointingHandCursor)
        preview.setToolTip("Show this theme for 5 seconds")
        preview.clicked.connect(self.preview_theme)
        theme_row.addWidget(preview)
        self._theme_box = QComboBox()
        self._theme_box.addItems(config.THEMES)
        self._theme_box.setFocusPolicy(Qt.NoFocus)
        self._theme_box.setCursor(Qt.PointingHandCursor)
        self._theme_box.currentTextChanged.connect(self._save_settings)
        theme_row.addWidget(self._theme_box)
        layout.addLayout(theme_row)

        # colour swatches
        colour_row = QHBoxLayout()
        colour_row.addWidget(_caption("color"))
        colour_row.addStretch()
        colour_row.setSpacing(6)
        self._swatches = QButtonGroup(self)
        for name in PRESET_COLORS:
            swatch = _Swatch(name)
            self._swatches.addButton(swatch)
            colour_row.addWidget(swatch)
        self._swatches.buttonClicked.connect(self._save_settings)
        layout.addLayout(colour_row)

        # transparency pills
        alpha_row = QHBoxLayout()
        alpha_row.addWidget(_caption("transparency"))
        alpha_row.addStretch()
        alpha_row.setSpacing(2)
        self._alphas = QButtonGroup(self)
        for label, value in config.TRANSPARENCY:
            pill = QPushButton(str(label))
            pill.setObjectName("unit")
            pill.setCheckable(True)
            pill.setFocusPolicy(Qt.NoFocus)
            pill.setCursor(Qt.PointingHandCursor)
            pill.opacity = value
            self._alphas.addButton(pill)
            alpha_row.addWidget(pill)
        self._alphas.buttonClicked.connect(self._save_settings)
        layout.addLayout(alpha_row)

        self._launch_switch = self._switch_row(layout, "launch at login")
        self._silent_switch = self._switch_row(layout, "silent")
        self._posture_switch = self._switch_row(
            layout, "posture reminder", preview=self.preview_posture
        )

        layout.addWidget(_divider())

        # The logo is a real image, so the line is laid out rather than written
        # as rich text: QLabel cannot render an <img src="data:...">, and a
        # file-backed one could not follow the theme.
        credit = QHBoxLayout()
        credit.setSpacing(5)
        credit.addStretch()
        credit.addWidget(self._about_label("Built with ❤️ for"))
        self._windows_logo = QLabel()
        self._windows_logo.setFixedSize(LOGO_PX, LOGO_PX)
        credit.addWidget(self._windows_logo)
        credit.addWidget(self._about_label("users"))
        credit.addStretch()
        layout.addLayout(credit)

        self._links = self._about_label("")
        self._links.setAlignment(Qt.AlignCenter)
        self._links.setOpenExternalLinks(True)
        layout.addWidget(self._links)

        return page

    def _links_html(self, accent):
        """Anchors are styled inline because the stylesheet cannot reach them:
        Qt draws rich-text links with the text document's own anchor styling,
        which underlines them, and QLabel does not expose that document."""
        style = f"color: {accent}; text-decoration: none;"
        return (
            f'<a href="https://github.com/ugurcandede" style="{style}">ugurcandede</a> · '
            f'<a href="https://github.com/ugurcandede/taplock-windows/releases/latest"'
            f' style="{style}">{version.display()}</a>'
        )

    def _about_label(self, text):
        label = QLabel(text)
        label.setObjectName("about")
        return label

    def _switch_row(self, layout, label, preview=None):
        row = QHBoxLayout()
        row.addWidget(_caption(label))
        row.addStretch()
        if preview is not None:
            button = QPushButton("preview")
            button.setObjectName("preset")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip("Show the reminder for 5 seconds")
            button.clicked.connect(preview)
            row.addWidget(button)
        switch = _Switch()
        switch.toggled.connect(self._save_settings)
        row.addWidget(switch)
        layout.addLayout(row)
        return switch

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

    # ---- theme -----------------------------------------------------------

    def apply_theme(self, dark):
        """The stylesheet handles colours; these three are painted in code, and
        the values that read well on a dark card are wrong on a light one."""
        self._shadow.setColor(QColor(0, 0, 0, 150 if dark else 55))
        for switch in (self._launch_switch, self._silent_switch, self._posture_switch):
            switch.apply_theme(dark)
        for swatch in self._swatches.buttons():
            swatch.apply_theme(dark)

        palette = DARK if dark else LIGHT
        self._links.setText(self._links_html(palette["accent"]))

        # Same colour the stylesheet gives the text beside it, from the one
        # place that defines it.
        faint = QColor(palette["faint"])
        ratio = self.devicePixelRatioF()  # keeps the logo crisp on a scaled display
        logo = icon.windows_logo(round(LOGO_PX * ratio), (faint.red(), faint.green(), faint.blue()))
        logo.setDevicePixelRatio(ratio)
        self._windows_logo.setPixmap(logo)

    # ---- config <-> form -------------------------------------------------

    def _load_config(self):
        self._loading = True  # setters below would otherwise write straight back
        interval, unit = best_unit(self._config.interval)
        self._interval_edit.setText(str(interval))
        self._interval_unit.set_unit(unit)
        value, unit = best_unit(self._config.break_duration)
        self._break_edit.setText(str(value))
        self._break_unit.set_unit(unit)

        self._theme_box.setCurrentText(self._config.theme)
        for swatch in self._swatches.buttons():
            swatch.setChecked(swatch.name == self._config.color)
        nearest = min(self._alphas.buttons(), key=lambda b: abs(b.opacity - self._config.opacity))
        nearest.setChecked(True)
        self._silent_switch.setChecked(self._config.silent)
        self._posture_switch.setChecked(self._config.show_posture_reminder)
        self._launch_switch.setChecked(startup.is_enabled())
        self._loading = False

    def _save_settings(self):
        """macOS only persists on start, so settings changed without starting a
        session were lost. Written on every change here instead."""
        if self._loading:
            return
        self._config.theme = self._theme_box.currentText()
        checked = self._swatches.checkedButton()
        if checked is not None:
            self._config.color = checked.name
        alpha = self._alphas.checkedButton()
        if alpha is not None:
            self._config.opacity = alpha.opacity
        self._config.silent = self._silent_switch.isChecked()
        self._config.show_posture_reminder = self._posture_switch.isChecked()
        self._config.launch_at_login = self._launch_switch.isChecked()
        startup.set_enabled(self._config.launch_at_login)
        config.save(self._config)

    def _toggle_settings(self):
        self._settings_open = not self._settings_open
        self._settings_button.set_open(self._settings_open)

        if self._settings_open:
            # It has to be visible to be animated, so it opens at zero height.
            self._settings.setMaximumHeight(0)
            self._settings.show()
            start, end = 0, self._settings.sizeHint().height()
        else:
            start, end = self._settings.height(), 0

        self._settings_anim.stop()
        self._settings_anim.setStartValue(start)
        self._settings_anim.setEndValue(end)
        self._settings_anim.start()

    def _settings_step(self, height):
        self._settings.setMaximumHeight(int(height))
        self._fit()
        if self.isVisible():
            self._place()

    def _settings_settled(self):
        if self._settings_open:
            self._settings.setMaximumHeight(_UNBOUNDED)  # let it grow again
        else:
            self._settings.hide()
        self._fit()
        if self.isVisible():
            self._place()

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
        # macOS closes the popover here. Not done on Windows: pressing start and
        # having the window vanish reads as "did that work?" -- the panel stays
        # up and swaps to the countdown, and still dismisses on the next click
        # elsewhere like any other transient popup.

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
        # The layout's default size constraint pushes its minimum onto the
        # window, and Qt will not lower a minimum once it has been set. After the
        # panel had been shown with settings expanded, that minimum stayed at the
        # expanded height and adjustSize() could no longer shrink past it --
        # collapsing left the window its old size. Clearing it first is the fix.
        self.setMinimumSize(0, 0)
        self.adjustSize()

    def _reflow(self):
        self._fit()
        if self.isVisible():
            self._place()

    # ---- show / hide -----------------------------------------------------

    def toggle(self, anchor):
        if self.isVisible():
            self.hide()
        else:
            self.show_at(anchor)

    def show_at(self, anchor):
        self._anchor = anchor
        self._fit()
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()

    def _place(self):
        """Position against the anchor. Separate from `show_at` so the resize
        animation can reposition every frame without re-raising the window."""
        anchor = self._anchor
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
