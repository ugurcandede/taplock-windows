"""The tray popup -- Windows stand-in for the macOS NSPopover.

A frameless always-on-top `Qt.Tool` window anchored to the tray icon. `Qt.Popup`
would give the transient dismiss behaviour for free, but keyboard focus inside a
popup is unreliable and this panel holds text fields, so it hides on
`WindowDeactivate` instead.

Two layouts share the card: the idle form (interval / break / advanced
schedule / start / presets / stats) and the running view (countdown / break now / restart / skip / stop),
swapped from the session's `state_changed`. Settings sit below both, so they can
be changed mid-session; the session reads the same config object.
"""

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QLocale, QPoint, QRect, QRectF, QSize, Qt, QTime, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QIntValidator, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from datetime import datetime, timedelta, timezone

import analytics
import config
import icon
import startup
import stats
import updates
from parsers import (
    PRESET_COLORS,
    UNIT_SECONDS,
    best_unit,
    format_duration,
    format_mmss,
    format_time_range,
    parse_color,
    parse_time_range,
    rgb255,
)
from schedule import RelaxSchedule
import version
from theme import DARK, LIGHT

CARD_WIDTH = 300
# Room for the drop shadow: the outer widget is transparent, the card floats in it.
SHADOW_MARGIN = 18
# Gap between the tray icon and the panel.
ANCHOR_GAP = 8
SIDE_PADDING = 20
# Matches the 0.15s easeInOut the macOS popover animates its sections with.
SECTION_ANIM_MS = 150
# The s / m / h pill slides a little quicker; it travels a much shorter way.
SEGMENT_ANIM_MS = 140
# Long enough for the count to read as a count rather than a flicker.
COUNT_ANIM_MS = 220
# Windows logo in the about line, sized to sit with 11px text.
LOGO_PX = 12
# Every settings row is this tall, whatever it contains. With the 4px spacing
# below it that gives a 30px pitch, close to the macOS section.
SETTINGS_ROW_HEIGHT = 26
SETTINGS_ROW_SPACING = 4

# Advanced schedule. Monday-first display order of the Swift weekday numbers the
# config uses (1 = Sunday ... 7 = Saturday).
WEEKDAY_ORDER = (2, 3, 4, 5, 6, 7, 1)
WORKDAYS = {2, 3, 4, 5, 6}
HOURS_INFO = (
    "Breaks only happen between these times. Outside them the timer waits, "
    "and starts counting again when they begin."
)
QUIET_INFO = "No breaks in this window, e.g. lunch. The timer starts counting again when it ends."
FIXED_INFO = (
    "Breaks land on the same clock times every day, counted from the start of your hours "
    "(50/10 → 9:50, 10:50, 11:50), instead of from when you press start."
)


def _mono(pixel_size, weight=QFont.Weight.DemiBold):
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


def _day_name(day, format_type):
    """Locale day name of a Swift weekday number; Qt counts 1 = Monday."""
    return QLocale.system().dayName((day + 5) % 7 + 1, format_type)


def _clock(moment):
    """Local short time of an instant, with the day name when it isn't today."""
    local = moment.astimezone()
    text = QLocale.system().toString(QTime(local.hour, local.minute), QLocale.ShortFormat)
    if local.date() != datetime.now().date():
        text = f"{_day_name(local.isoweekday() % 7 + 1, QLocale.ShortFormat)} {text}"
    return text


def _days_text(days):
    if days == set(range(1, 8)):
        return "every day"
    if days == WORKDAYS:
        return "weekdays"
    if days == {1, 7}:
        return "weekends"
    if not days:
        return "no days"
    return " ".join(_day_name(d, QLocale.ShortFormat) for d in WEEKDAY_ORDER if d in days)


class _DisclosureRow(QWidget):
    """Label left, chevron right, whole row clickable -- a plain QPushButton
    centres its text and cannot separate the two."""

    clicked = Signal()

    def __init__(self, text):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SIDE_PADDING, 8, SIDE_PADDING, 8)
        self._label = _caption(text)
        layout.addWidget(self._label)
        layout.addStretch()
        self._chevron = _caption("›")
        layout.addWidget(self._chevron)

    def set_text(self, text):
        self._label.setText(text)

    def set_open(self, is_open):
        self._chevron.setText("⌄" if is_open else "›")

    def mousePressEvent(self, event):
        self.clicked.emit()


class _Reveal(QWidget):
    """Slides `page` open and shut by animating its own height.

    The page keeps its full height and is only clipped. Shrinking the page
    itself (by its maximum height) squeezed its rows on top of each other part
    way through, since a layout overlaps what it has no room for.
    """

    resized = Signal()

    def __init__(self, page):
        super().__init__()
        self._page = page
        page.setParent(self)
        self._open = False
        self._height = 0  # None once open and settled: follow the page
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.hide()

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SECTION_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.valueChanged.connect(self._step)
        self._anim.finished.connect(self._settled)

    @property
    def animating(self):
        return self._anim.state() == QAbstractAnimation.Running

    def _page_height(self, width):
        # Word-wrapped labels (the phase line, the schedule error) need the
        # height for the actual width; their plain size hint is one line.
        if self._page.hasHeightForWidth():
            return self._page.heightForWidth(width)
        return self._page.sizeHint().height()

    def _width(self):
        # Never laid out while hidden; the parent is the closest real width.
        return self.width() if self.isVisible() else self.parentWidget().width()

    def sizeHint(self):
        if self._height is None:
            height = self._page_height(self._width())
        else:
            height = self._height
        return QSize(self._page.sizeHint().width(), height)

    def minimumSizeHint(self):
        return self.sizeHint()

    def resizeEvent(self, event):
        self._page.setGeometry(0, 0, self.width(), self._page_height(self.width()))

    def set_open(self, is_open, animate=True):
        if not animate:
            self._anim.stop()
            self._open = is_open
            self._settled()
            return
        if is_open == self._open:
            return
        self._open = is_open
        start = self.height() if self.isVisible() else 0
        self.show()
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(self._page_height(self._width()) if is_open else 0)
        self._anim.start()

    def _step(self, height):
        # Setting the end value on a stopped animation reports it straight away,
        # which would show the end state for a frame before the slide starts.
        if not self.animating:
            return
        self._height = int(height)
        self.updateGeometry()
        self.resized.emit()

    def _settled(self):
        self._height = None if self._open else 0
        self.setVisible(self._open)  # hidden, it takes no layout spacing either
        self.updateGeometry()
        self.resized.emit()


class _Section(QWidget):
    """A disclosure row with a page that slides open under it."""

    opened = Signal()
    resized = Signal()

    def __init__(self, caption, page):
        super().__init__()
        self._open = False

        self._row = _DisclosureRow(caption)
        self._row.clicked.connect(self.toggle)
        self._reveal = _Reveal(page)
        self._reveal.resized.connect(self.resized)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._row)
        layout.addWidget(self._reveal)

    @property
    def is_open(self):
        return self._open

    def set_caption(self, text):
        self._row.set_text(text)

    def toggle(self):
        self._open = not self._open
        self._row.set_open(self._open)
        if self._open:
            self.opened.emit()  # before measuring: stats fills its page here
        self._reveal.set_open(self._open)


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

    def hitButton(self, pos):
        # QCheckBox only takes clicks on its style's small indicator, which is
        # not where the painted track is.
        return self.rect().contains(pos)

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


class _CountingEdit(QLineEdit):
    """A number field that counts to its new value rather than swapping to it.

    Only the preset buttons drive this. Typing goes straight in, and a keystroke
    cancels a running count so the field never fights the person using it.
    """

    def __init__(self):
        super().__init__()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(COUNT_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda value: self.setText(str(int(value))))
        self.textEdited.connect(self._anim.stop)

    def count_to(self, value):
        self._anim.stop()
        try:
            start = int(self.text())
        except ValueError:
            self.setText(str(value))
            return
        if start == value:
            return
        self._anim.setStartValue(start)
        self._anim.setEndValue(value)
        self._anim.start()


class _UnitPicker(QWidget):
    """Captioned s / m / h selector.

    The selection is a pill painted behind the buttons rather than a `:checked`
    background on each, so it can slide from one to the next.
    """

    changed = Signal()

    def __init__(self, caption):
        super().__init__()
        self._slide_x = None  # until the first move, follow the checked button
        self.apply_theme(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label = _caption(caption)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons = {}
        for unit in ("s", "m", "h"):
            button = QPushButton(unit)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            self._group.addButton(button)
            row.addWidget(button)
            self._buttons[unit] = button
        layout.addLayout(row)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SEGMENT_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.valueChanged.connect(self._on_slide)
        self._group.buttonClicked.connect(lambda _: self._slide_to_checked())
        self._group.buttonClicked.connect(lambda _: self.changed.emit())

        self.set_unit("m")

    def apply_theme(self, dark):
        self._pill = QColor(10, 132, 255, 41) if dark else QColor(0, 103, 192, 36)
        self.update()

    def unit(self):
        return next(u for u, b in self._buttons.items() if b.isChecked())

    def set_unit(self, unit, animate=False):
        """Jumps by default -- loading a config happens before the panel is even
        on screen. A preset button is different: the user is looking straight at
        the picker, so that path asks for the slide.
        """
        if animate and unit != self._selected:
            self._buttons[unit].setChecked(True)
            self._slide_to_checked()
            return
        self._buttons[unit].setChecked(True)
        self._selected = unit
        self._slide_x = None
        self.update()

    def _slide_to_checked(self):
        # The button is already checked by the time this runs, so the departure
        # point comes from the unit we were on -- or from wherever a previous
        # slide had got to, if one is still in flight.
        start = self._slide_x
        if start is None:
            start = float(self._buttons[self._selected].x())
        self._selected = self.unit()

        self._anim.stop()
        self._anim.setStartValue(float(start))
        self._anim.setEndValue(float(self._buttons[self._selected].x()))
        self._anim.start()

    def _on_slide(self, x):
        self._slide_x = x
        self.update()

    def paintEvent(self, event):
        checked = self._buttons[self.unit()]
        box = checked.geometry()
        x = self._slide_x if self._slide_x is not None else box.x()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._pill)
        painter.drawRoundedRect(QRectF(x, box.y(), box.width(), box.height()), 5, 5)


class Panel(QWidget):
    preview_theme = Signal()
    preview_posture = Signal()
    open_statistics = Signal()
    install_update = Signal()

    def __init__(self, relax_config, session, on_quit):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._config = relax_config
        self._session = session
        self._on_quit = on_quit
        self._anchor = QRect()
        self._layout_key = None
        self._loading = False
        self._update = None
        self._update_state = "idle"  # idle | updating | failed
        self._banner_tracked_version = None

        self._build()
        self._load_config()
        session.state_changed.connect(self._refresh)
        self._refresh()

    # ---- construction ----------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN, SHADOW_MARGIN)
        # Only _reflow sizes the window. The default constraint also resized it
        # whenever the layout changed, as a second, unpositioned update.
        outer.setSizeConstraint(QLayout.SetNoConstraint)

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

        self._banner_reveal = self._revealed(self._build_update_banner())
        body.addWidget(self._banner_reveal)

        self._error = QLabel()
        self._error.setObjectName("error")
        self._error.setAlignment(Qt.AlignCenter)
        self._error_reveal = self._revealed(self._error)
        body.addWidget(self._error_reveal)

        # Start and stop slide one view shut as the other opens.
        self._idle_reveal = self._revealed(self._build_idle())
        body.addWidget(self._idle_reveal)
        self._active_reveal = self._revealed(self._build_active())
        body.addWidget(self._active_reveal)

        body.addWidget(_divider())

        self._settings_section = _Section("settings", self._build_settings())
        self._settings_section.resized.connect(self._reflow)
        self._settings_section.opened.connect(lambda: analytics.track("settings_opened", {"mode": "relax"}))
        body.addWidget(self._settings_section)

        body.addWidget(_divider())

        quit_button = QPushButton("quit taplock")
        quit_button.setObjectName("plain")
        quit_button.setFocusPolicy(Qt.NoFocus)
        quit_button.setCursor(Qt.PointingHandCursor)
        quit_button.clicked.connect(self._on_quit)
        body.addWidget(quit_button)

    def _build_update_banner(self):
        self._banner = QWidget()
        outer = QHBoxLayout(self._banner)
        outer.setContentsMargins(12, 0, 12, 6)
        box = QFrame()
        box.setObjectName("banner")
        outer.addWidget(box)
        row = QHBoxLayout(box)
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(6)
        self._banner_label = QLabel()
        self._banner_label.setObjectName("bannerText")
        row.addWidget(self._banner_label)
        row.addStretch()
        self._update_button = QPushButton()
        self._dismiss_button = QPushButton("×")
        self._dismiss_button.setToolTip("Hide until the next version")
        for button, slot in ((self._update_button, self._start_update), (self._dismiss_button, self._dismiss_update)):
            button.setObjectName("bannerAction")
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            row.addWidget(button)
        return self._banner

    def _build_idle(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(SIDE_PADDING, 6, SIDE_PADDING, 0)
        form_layout.setSpacing(8)

        self._advanced_switch = _Switch()
        self._advanced_switch.toggled.connect(self._show_schedule)
        advanced = self._row("advanced", self._advanced_switch)
        advanced.setToolTip("Active days and hours, quiet hours, fixed break times")
        form_layout.addWidget(advanced)

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
        for edit in (self._interval_edit, self._break_edit):
            edit.textChanged.connect(self._refresh_schedule)
        for picker in (self._interval_unit, self._break_unit):
            picker.changed.connect(self._refresh_schedule)
        layout.addWidget(form)

        # Between the durations and start, like the macOS popover; hidden unless
        # advanced is on, so the plain form looks as it always has.
        self._schedule_block = self._build_schedule()
        self._schedule_reveal = self._revealed(self._schedule_block)
        layout.addWidget(self._schedule_reveal)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(SIDE_PADDING, 8, SIDE_PADDING, 10)
        form_layout.setSpacing(8)

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

        self._stats_section = _Section("stats", self._build_stats())
        # Opening measures the page right after this, so no slide inside it.
        self._stats_section.opened.connect(lambda: self._refresh_stats(animate=False))
        self._stats_section.opened.connect(lambda: analytics.track("stats_opened", {"mode": "relax"}))
        self._stats_section.resized.connect(self._reflow)
        layout.addWidget(self._stats_section)

        return page

    def _build_schedule(self):
        """Collapsed to a summary ("weekdays · 09:00–18:00 · quiet · fixed") with
        the upcoming break times under it; the summary opens the editor. The
        preview is the main way the rules explain themselves."""
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(_divider())

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(SIDE_PADDING, 0, SIDE_PADDING, 4)
        editor_layout.setSpacing(SETTINGS_ROW_SPACING)

        days = QHBoxLayout()
        days.setSpacing(2)
        self._day_chips = {}
        for day in WEEKDAY_ORDER:
            chip = QPushButton(_day_name(day, QLocale.ShortFormat))
            chip.setObjectName("unit")
            chip.setCheckable(True)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setCursor(Qt.PointingHandCursor)
            chip.toggled.connect(self._refresh_schedule)
            days.addWidget(chip)
            self._day_chips[day] = chip
        editor_layout.addLayout(days)

        self._hours_edit = self._range_edit("09:00-18:00")
        editor_layout.addWidget(self._info_row("hours", HOURS_INFO, self._hours_edit))

        self._quiet_switch = _Switch()
        self._quiet_switch.toggled.connect(self._refresh_schedule)
        editor_layout.addWidget(self._info_row("quiet hours", QUIET_INFO, self._quiet_switch))

        self._quiet_edit = self._range_edit("12:00-13:00")
        self._quiet_row = self._row("from–to", self._quiet_edit)
        self._quiet_row.layout().setContentsMargins(12, 0, 0, 0)
        self._quiet_reveal = self._revealed(self._quiet_row)
        editor_layout.addWidget(self._quiet_reveal)

        self._align_switch = _Switch()
        self._align_switch.toggled.connect(self._refresh_schedule)
        editor_layout.addWidget(self._info_row("fixed break times", FIXED_INFO, self._align_switch))

        self._schedule_section = _Section("", editor)
        self._schedule_section.resized.connect(self._reflow)
        layout.addWidget(self._schedule_section)

        self._schedule_preview = QLabel()
        self._schedule_preview.setObjectName("hint")
        self._schedule_preview.setFont(_mono(10, QFont.Weight.Normal))
        self._schedule_preview.setContentsMargins(SIDE_PADDING, 0, SIDE_PADDING, 8)
        self._schedule_preview_reveal = self._revealed(self._schedule_preview)
        layout.addWidget(self._schedule_preview_reveal)

        self._schedule_error = QLabel()
        self._schedule_error.setObjectName("error")
        self._schedule_error.setWordWrap(True)
        self._schedule_error.setContentsMargins(SIDE_PADDING, 0, SIDE_PADDING, 8)
        self._schedule_error_reveal = self._revealed(self._schedule_error)
        layout.addWidget(self._schedule_error_reveal)

        layout.addWidget(_divider())
        return block

    def _revealed(self, widget):
        reveal = _Reveal(widget)
        reveal.resized.connect(self._reflow)
        return reveal

    def _info_row(self, caption, tooltip, widget):
        """A settings row with an info icon after the caption. "ⓘ" has no glyph
        in Segoe UI and fell back to a blurry bitmap font; the icon font's Info
        glyph is drawn instead."""
        row = self._row(caption, widget)
        row.setToolTip(tooltip)
        icon = QLabel("\ue946")
        icon.setObjectName("info")
        row.layout().insertWidget(1, icon)
        return row

    def _range_edit(self, placeholder):
        edit = QLineEdit()
        edit.setObjectName("setting")
        edit.setPlaceholderText(placeholder)
        edit.setFixedWidth(96)
        edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        edit.setFocusPolicy(Qt.ClickFocus)
        edit.textChanged.connect(self._refresh_schedule)
        return edit

    def _show_schedule(self, visible, animate=True):
        self._schedule_reveal.set_open(visible, animate)
        self._refresh_schedule()

    def _form_schedule(self):
        """(schedule, error) from the form; (None, None) while advanced is off."""
        if not self._advanced_switch.isChecked():
            return None, None
        hours = parse_time_range(self._hours_edit.text())
        if hours is None:
            return None, "Hours must look like 09:00-18:00, within one day"
        quiet = (None, None)
        if self._quiet_switch.isChecked():
            quiet = parse_time_range(self._quiet_edit.text())
            if quiet is None:
                return None, "Quiet hours must look like 12:00-13:00, within one day"
        schedule = RelaxSchedule(
            weekdays={day for day, chip in self._day_chips.items() if chip.isChecked()},
            active_start=hours[0],
            active_end=hours[1],
            quiet_start=quiet[0],
            quiet_end=quiet[1],
            align_to_clock=self._align_switch.isChecked(),
        )
        return schedule, schedule.validation_error()

    def _refresh_schedule(self):
        """Summary, preview and quiet row after any change to the form."""
        if self._loading:
            return
        self._quiet_reveal.set_open(self._quiet_switch.isChecked())

        days = {day for day, chip in self._day_chips.items() if chip.isChecked()}
        parts = [_days_text(days), self._hours_edit.text().strip().replace("-", "–")]
        if self._quiet_switch.isChecked():
            parts.append("quiet")
        if self._align_switch.isChecked():
            parts.append("fixed")
        self._schedule_section.set_caption(" · ".join(parts))

        schedule, error = self._form_schedule()
        preview = ""
        interval = self._seconds(self._interval_edit, self._interval_unit)
        break_duration = self._seconds(self._break_edit, self._break_unit)
        if schedule is not None and error is None and interval and break_duration:
            breaks = []
            moment = datetime.now(timezone.utc)
            while len(breaks) < 3:
                moment = schedule.next_break(moment, interval, break_duration)
                if moment is None:
                    break
                breaks.append(moment)
                moment += timedelta(seconds=break_duration)
            if breaks:
                preview = "breaks: " + " · ".join(_clock(b) for b in breaks) + " …"
            else:
                error = "no breaks with these settings"
        # Text is only replaced, never cleared, so a closing line slides out
        # with what it said.
        if preview:
            self._schedule_preview.setText(preview)
        self._schedule_preview_reveal.set_open(bool(preview))
        if error:
            self._schedule_error.setText(error.lower())
        self._schedule_error_reveal.set_open(bool(error))
        if self._schedule_block.isVisible():
            self._reflow()

    def _build_stats(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SIDE_PADDING, 4, SIDE_PADDING, 10)
        layout.setSpacing(SETTINGS_ROW_SPACING)

        self._period = QComboBox()
        for kind in stats.PANEL_PERIODS:
            self._period.addItem(stats.PERIOD_LABELS[kind], kind)
        self._period.setFocusPolicy(Qt.NoFocus)
        self._period.setCursor(Qt.PointingHandCursor)
        self._period.currentIndexChanged.connect(lambda _: self._refresh_stats())
        self._period.currentIndexChanged.connect(
            lambda _: analytics.track("stats_period_changed", {"period": self._period.currentData()})
        )
        layout.addWidget(self._row("period", self._period))

        self._metrics = {}
        for key, caption in (
            ("sessions", "sessions"),
            ("session_time", "session time"),
            ("breaks", "breaks"),
            ("break_time", "break time"),
            ("skipped", "skipped early"),
        ):
            value = QLabel("—")
            value.setObjectName("metric")
            self._metrics[key] = value
            row = self._row(caption, value)
            # The macOS dropdown only shows this one once something was skipped.
            if key == "skipped":
                self._skipped_reveal = self._revealed(row)
                row = self._skipped_reveal
            layout.addWidget(row)

        view_all = QPushButton("view all statistics  ↗")
        view_all.setObjectName("plain")
        view_all.setFocusPolicy(Qt.NoFocus)
        view_all.setCursor(Qt.PointingHandCursor)
        view_all.clicked.connect(self.open_statistics)
        layout.addWidget(view_all)

        return page

    def _refresh_stats(self, animate=True):
        interval = stats.resolve_period(self._period.currentData(), datetime.now().astimezone())
        summary = stats.summarise(stats.events_in(stats.read_all(), interval))
        self._metrics["sessions"].setText(str(summary.sessions))
        self._metrics["session_time"].setText(format_duration(summary.session_seconds))
        self._metrics["breaks"].setText(str(summary.breaks))
        self._metrics["break_time"].setText(format_duration(summary.break_seconds))
        self._metrics["skipped"].setText(str(summary.skipped_early))
        self._skipped_reveal.set_open(bool(summary.skipped_early), animate)

    def _duration_edit(self, width, alignment):
        edit = _CountingEdit()
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
        layout.setSpacing(SETTINGS_ROW_SPACING)

        self._theme_box = QComboBox()
        self._theme_box.addItems(config.THEMES)
        self._theme_box.setFocusPolicy(Qt.NoFocus)
        self._theme_box.setCursor(Qt.PointingHandCursor)
        self._theme_box.currentTextChanged.connect(self._save_settings)
        layout.addWidget(
            self._row(
                "theme",
                self._preview_button("Show this theme for 5 seconds", self.preview_theme),
                self._theme_box,
            )
        )

        self._swatches = QButtonGroup(self)
        for name in PRESET_COLORS:
            self._swatches.addButton(_Swatch(name))
        self._swatches.buttonClicked.connect(self._save_settings)
        layout.addWidget(self._row("color", *self._swatches.buttons(), spacing=6))

        self._alphas = QButtonGroup(self)
        for label, value in config.TRANSPARENCY:
            pill = QPushButton(str(label))
            pill.setObjectName("unit")
            pill.setCheckable(True)
            pill.setFocusPolicy(Qt.NoFocus)
            pill.setCursor(Qt.PointingHandCursor)
            pill.opacity = value
            self._alphas.addButton(pill)
        self._alphas.buttonClicked.connect(self._save_settings)
        layout.addWidget(self._row("transparency", *self._alphas.buttons(), spacing=2))

        self._launch_switch = _Switch()
        self._resume_switch = _Switch()
        self._silent_switch = _Switch()
        self._tooltip_timer_switch = _Switch()
        self._usage_stats_switch = _Switch()
        self._posture_switch = _Switch()
        for switch in self._switches():
            switch.toggled.connect(self._save_settings)

        layout.addWidget(
            self._row(
                "posture reminder",
                self._preview_button("Show the reminder for 5 seconds", self.preview_posture),
                self._posture_switch,
            )
        )

        self._posture_every = QLineEdit()
        self._posture_every.setObjectName("setting")
        self._posture_every.setPlaceholderText("auto")
        self._posture_every.setFixedWidth(44)
        self._posture_every.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._posture_every.setValidator(QIntValidator(1, 999, self))
        self._posture_every.setFocusPolicy(Qt.ClickFocus)
        self._posture_every.textChanged.connect(self._save_settings)
        self._posture_every_row = self._row("every", self._posture_every, _caption("min"))
        self._posture_every_row.layout().setContentsMargins(12, 0, 0, 0)
        self._posture_switch.toggled.connect(self._show_posture_every)
        self._posture_every_reveal = self._revealed(self._posture_every_row)
        layout.addWidget(self._posture_every_reveal)

        layout.addWidget(_divider())

        layout.addWidget(self._row("launch at login", self._launch_switch))
        layout.addWidget(self._row("resume after restart", self._resume_switch))
        layout.addWidget(self._row("silent", self._silent_switch))
        layout.addWidget(self._row("show timer in tooltip", self._tooltip_timer_switch))
        layout.addWidget(self._row("send anonymous usage stats", self._usage_stats_switch))

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

    def _row(self, caption, *widgets, spacing=8):
        """One settings row, at the same height as every other one.

        Built by hand before, each row ended up as tall as whatever it held --
        28px with a preview button, 18px with only a switch -- so the block read
        as unevenly spaced.
        """
        row = QWidget()
        row.setFixedHeight(SETTINGS_ROW_HEIGHT)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        layout.addWidget(_caption(caption))
        layout.addStretch()
        for widget in widgets:
            layout.addWidget(widget)
        return row

    def _switches(self):
        return (
            self._launch_switch,
            self._resume_switch,
            self._silent_switch,
            self._tooltip_timer_switch,
            self._usage_stats_switch,
            self._posture_switch,
        )

    def _show_posture_every(self, visible, animate=True):
        self._posture_every_reveal.set_open(visible, animate)

    def _preview_button(self, tooltip, slot):
        button = QPushButton("preview")
        button.setObjectName("preset")
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

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
        self._phase.setWordWrap(True)  # "paused until … · next break …" can need two lines
        layout.addWidget(self._phase)

        layout.addSpacing(6)

        self._waiting_actions = QWidget()
        actions = QHBoxLayout(self._waiting_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        for text, name, slot in (
            ("break now", "primary", self._session.start_break_now),
            ("restart", "warning", self._restart_countdown),
        ):
            button = QPushButton(text)
            button.setObjectName(name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            actions.addWidget(button)
        # With fixed break times a restart lands on the same grid point; hidden then.
        self._restart_button = button
        layout.addWidget(self._waiting_actions)

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
        for switch in self._switches():
            switch.apply_theme(dark)
        for swatch in self._swatches.buttons():
            swatch.apply_theme(dark)
        for picker in (self._interval_unit, self._break_unit):
            picker.apply_theme(dark)

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
        self._tooltip_timer_switch.setChecked(self._config.show_timer_in_tooltip)
        self._usage_stats_switch.setChecked(self._config.send_usage_stats)
        self._posture_switch.setChecked(self._config.show_posture_reminder)
        every = self._config.posture_interval
        self._posture_every.setText(str(every // 60) if every else "")
        self._show_posture_every(self._config.show_posture_reminder, animate=False)
        self._launch_switch.setChecked(startup.is_enabled())
        self._resume_switch.setChecked(self._config.resume_on_launch)

        schedule = self._config.schedule
        self._advanced_switch.setChecked(schedule is not None)
        shown = schedule or RelaxSchedule(weekdays=WORKDAYS, active_start=9 * 60, active_end=18 * 60)
        for day, chip in self._day_chips.items():
            chip.setChecked(day in shown.weekdays)
        self._hours_edit.setText(format_time_range(shown.active_start, shown.active_end))
        self._quiet_switch.setChecked(shown.quiet_start is not None)
        self._quiet_edit.setText(format_time_range(shown.quiet_start or 12 * 60, shown.quiet_end or 13 * 60))
        self._align_switch.setChecked(shown.align_to_clock)
        self._loading = False
        self._quiet_reveal.set_open(self._quiet_switch.isChecked(), animate=False)
        self._show_schedule(schedule is not None, animate=False)

    def _save_settings(self):
        """macOS only persists on start, so settings changed without starting a
        session were lost. Written on every change here instead."""
        if self._loading:
            return
        before = self._tracked_settings()
        self._config.theme = self._theme_box.currentText()
        checked = self._swatches.checkedButton()
        if checked is not None:
            self._config.color = checked.name
        alpha = self._alphas.checkedButton()
        if alpha is not None:
            self._config.opacity = alpha.opacity
        self._config.silent = self._silent_switch.isChecked()
        self._config.show_timer_in_tooltip = self._tooltip_timer_switch.isChecked()
        self._config.send_usage_stats = self._usage_stats_switch.isChecked()
        self._config.show_posture_reminder = self._posture_switch.isChecked()
        minutes = self._posture_every.text().strip()
        self._config.posture_interval = int(minutes) * 60 if minutes.isdigit() and int(minutes) > 0 else None
        self._config.resume_on_launch = self._resume_switch.isChecked()
        self._config.launch_at_login = self._launch_switch.isChecked()
        startup.set_enabled(self._config.launch_at_login)
        config.save(self._config)
        self._session.settings_changed()

        if not self._config.send_usage_stats:
            analytics.disabled()
        self.refresh_user_properties()  # first, so the events below carry the new values
        after = self._tracked_settings()
        for name, value in after.items():
            if before[name] != value:
                # usage_stats only lands when turned on: analytics is off otherwise.
                analytics.track("setting_changed", {"setting": name, "value": str(value).lower()})

    def _tracked_settings(self):
        """Setting names match the macOS app's setting_changed events."""
        c = self._config
        return {
            "relax_theme": c.theme,
            "relax_color": c.color,
            "relax_transparency": config.transparency_label(c.opacity),
            "relax_silent": c.silent,
            "relax_posture": c.show_posture_reminder,
            "relax_tooltip_timer": c.show_timer_in_tooltip,
            "relax_resume_on_launch": c.resume_on_launch,
            "launch_at_login": c.launch_at_login,
            "usage_stats": c.send_usage_stats,
        }

    def refresh_user_properties(self):
        """Settings worth slicing every report by."""
        analytics.set_user_properties({
            "launch_at_login": self._config.launch_at_login,
            "resume_on_launch": self._config.resume_on_launch,
            "relax_theme": self._config.theme,
        })

    def _restart_countdown(self):
        config_ = self._session.config
        if config_ is not None:
            # A schedule can hold the wait longer than the interval; don't report negative time.
            analytics.track("relax_restart", {"elapsed_sec": max(0, config_.interval - self._session.remaining)})
        self._session.restart_countdown()

    # ---- update banner ---------------------------------------------------

    def set_update(self, update):
        if self._update_state == "updating":
            return  # a daily re-check must not reset a download in flight
        self._update = update
        self._update_state = "idle"
        self._render_banner()
        self._banner_reveal.set_open(update is not None, animate=self.isVisible())

    def set_update_failed(self):
        analytics.track("update_failed", {"latest_version": self._update.version, "reason": "exe_swap"})
        self._update_state = "failed"
        self._render_banner()

    def _render_banner(self):
        if self._update is None:
            return
        version_ = self._update.version
        running = self._session.running
        text, button, tip = {
            "idle": (f"v{version_} available", "update", f"Install v{version_} and restart"),
            "updating": (f"updating to v{version_}…", "", ""),
            "failed": ("update failed", "retry", "Try the download again"),
        }[self._update_state]
        if running and self._update_state != "updating":
            # Updating restarts the app; not in the middle of a session.
            button, tip = "after session", "Available once the session ends"
        self._banner_label.setText(text)
        self._update_button.setText(button)
        self._update_button.setToolTip(tip)
        self._update_button.setVisible(bool(button))
        self._update_button.setEnabled(not running)
        self._dismiss_button.setVisible(self._update_state != "updating")

    def _start_update(self):
        if self._update is None or self._session.running or self._update_state == "updating":
            return
        if not updates.can_self_update():
            # Running from source: there is no exe to replace.
            analytics.track("update_started", {"latest_version": self._update.version, "result": "release_page"})
            QDesktopServices.openUrl(QUrl(self._update.url))
            return
        analytics.track("update_started", {"latest_version": self._update.version, "result": "exe_swap"})
        self._update_state = "updating"
        self._render_banner()
        self.install_update.emit()

    def _dismiss_update(self):
        analytics.track("update_dismissed", {"latest_version": self._update.version})
        updates.dismiss(self._update.version)
        self.set_update(None)

    def _apply_preset(self, interval_minutes, break_minutes):
        analytics.track("preset_applied", {"mode": "relax", "value": f"{interval_minutes}/{break_minutes}"})
        self._interval_edit.count_to(interval_minutes)
        self._interval_unit.set_unit("m", animate=True)
        self._break_edit.count_to(break_minutes)
        self._break_unit.set_unit("m", animate=True)
        self._set_error(None)

    def _seconds(self, edit, picker):
        text = edit.text().strip()
        if not text.isdigit() or int(text) <= 0:
            return None
        return int(text) * UNIT_SECONDS[picker.unit()]

    def _start(self):
        interval = self._seconds(self._interval_edit, self._interval_unit)
        if interval is None:
            analytics.track("relax_error", {"reason": "invalid_interval"})
            return self._set_error("Invalid interval")
        break_duration = self._seconds(self._break_edit, self._break_unit)
        if break_duration is None:
            analytics.track("relax_error", {"reason": "invalid_break"})
            return self._set_error("Invalid break duration")
        if interval <= break_duration:
            analytics.track("relax_error", {"reason": "interval_not_longer"})
            return self._set_error("Interval must be longer than break")
        schedule, error = self._form_schedule()
        if error:
            analytics.track("relax_error", {"reason": "invalid_schedule"})
            return self._set_error(error)
        if schedule is not None and schedule.next_break(datetime.now(timezone.utc), interval, break_duration) is None:
            analytics.track("relax_error", {"reason": "no_breaks"})
            return self._set_error("No breaks with these settings")

        self._set_error(None)
        self._config.interval = interval
        self._config.break_duration = break_duration
        self._config.schedule = schedule
        config.save(self._config)
        self._session.start(self._config)
        # macOS closes the popover here. Not done on Windows: pressing start and
        # having the window vanish reads as "did that work?" -- the panel stays
        # up and swaps to the countdown, and still dismisses on the next click
        # elsewhere like any other transient popup.

    def _set_error(self, message):
        if message:
            self._error.setText(message)
        self._error_reveal.set_open(bool(message))

    # ---- refresh ---------------------------------------------------------

    def _refresh(self):
        running = self._session.running
        on_break = self._session.on_break

        if running:
            self._countdown.setText(format_mmss(self._session.remaining))
            self._phase.setText(self._phase_text())

        key = (running, on_break)
        if key == self._layout_key:
            return  # countdown text only; leave geometry alone
        first = self._layout_key is None
        self._layout_key = key

        self._skip.setVisible(on_break)
        self._waiting_actions.setVisible(running and not on_break)
        schedule = self._session.config.schedule if running else None
        self._restart_button.setVisible(schedule is None or not schedule.align_to_clock)
        # After the contents above, so the slide measures the view as it will be.
        self._idle_reveal.set_open(not running, animate=not first)
        self._active_reveal.set_open(running, animate=not first)
        self._render_banner()
        if not running:
            self._set_error(None)
        self._reflow()

    def _phase_text(self):
        """Under the countdown. With a schedule it names the break time, and says
        "paused" while the schedule holds breaks back (off days, outside hours,
        quiet hours)."""
        if self._session.on_break:
            return "break time!"
        upcoming = self._session.next_break
        interval = timedelta(seconds=self._session.config.interval)
        if self._session.config.schedule is None or upcoming is None:
            return "next break in..."
        if upcoming - datetime.now(timezone.utc) > interval:
            return f"paused until {_clock(upcoming - interval)} · next break {_clock(upcoming)}"
        return f"next break at {_clock(upcoming)}"

    def _reflow(self):
        """Lay the panel out for its content and put it against the anchor.

        Size and position go in one setGeometry. As adjustSize() then move() they
        were two window updates, and Windows could show the frame in between --
        new size, old position -- which flickered every frame of an animation.
        """
        # Hiding a page only posts a LayoutRequest, which is handled on the next
        # event-loop pass -- until then both pages are still in the size hint and
        # the window would be sized to their sum. Invalidating the card layout
        # first drops those cached hints so the size is correct in the same call,
        # with no intermediate frame at the wrong height. A row shown or hidden
        # deeper down (quiet hours) is only picked up one nesting level per pass,
        # so every nested widget (and each _Reveal, which has no layout to
        # notice its page changing) drops its cached size hint as well.
        for widget in self.findChildren(QWidget):
            widget.updateGeometry()
        for layout in (self._card.layout(), self.layout()):
            layout.invalidate()
            layout.activate()
        size = self.sizeHint()
        self.setGeometry(QRect(self._position(size), size))

    # ---- show / hide -----------------------------------------------------

    def toggle(self, anchor):
        if self.isVisible():
            self.hide()
        else:
            self.show_at(anchor)

    def show_at(self, anchor):
        state = "active" if self._session.running else "idle"
        analytics.track("popover_opened", {"mode": "relax", "state": state})
        if self._update is not None and self._banner_tracked_version != self._update.version:
            self._banner_tracked_version = self._update.version
            analytics.track("update_banner_shown", {"latest_version": self._update.version})
        self._anchor = anchor
        if not self._session.running:
            self._refresh_schedule()  # the preview's times move with the clock
        self._reflow()
        self.show()
        self.raise_()
        self.activateWindow()

    def _position(self, size):
        """Top-left for a window of `size` against the anchor."""
        anchor = self._anchor
        screen = QGuiApplication.screenAt(anchor.center()) if not anchor.isEmpty() else None
        screen = screen or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        if anchor.isEmpty():
            # Icon is hidden in the tray overflow flyout and reports no geometry;
            # fall back to the corner the tray normally lives in.
            x = area.right() - size.width()
            y = area.bottom() - size.height()
        else:
            x = anchor.center().x() - size.width() // 2
            y = anchor.top() - size.height() + SHADOW_MARGIN - ANCHOR_GAP
            if y < area.top():  # taskbar docked to the top edge
                y = anchor.bottom() - SHADOW_MARGIN + ANCHOR_GAP

        x = max(area.left(), min(x, area.right() - size.width()))
        y = max(area.top(), min(y, area.bottom() - size.height()))
        return QPoint(x, y)

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
