"""The statistics window -- port of TapLockApp/StatisticsWindow.swift.

A plain dialog rather than the frameless card the panel uses: this one is a
window the user leaves open, so it should take Windows' own title bar, snapping
and taskbar entry.

Only relax metrics are shown. The macOS window has a lock/relax switch, but
there is no lock mode here; `lock_completed` records carried over from a macOS
log are read and ignored rather than displayed.
"""

from datetime import datetime

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

import stats
from parsers import format_duration

WINDOW_SIZE = (540, 480)
COLUMNS = 2


def _mono(pixel_size, weight=QFont.Weight.Light):
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


class _PathLabel(QLabel):
    """The log path, shortened from the left so the file name stays readable --
    what matters is which file, not which drive."""

    def __init__(self, path):
        super().__init__()
        self.setObjectName("tilePath")
        self._path = str(path)
        self.setToolTip(self._path)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setText(self._path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self._path, Qt.ElideLeft, self.width()))


class _Tile(QFrame):
    """One metric: a quiet caption over a large number."""

    def __init__(self, caption):
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        label = QLabel(caption.upper())
        label.setObjectName("tileLabel")
        layout.addWidget(label)

        self._value = QLabel("—")
        self._value.setFont(_mono(28))
        layout.addWidget(self._value)

    def set_value(self, text):
        self._value.setText(text)


class StatisticsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TapLock Statistics")
        # Fixed rather than resizable: the content is a fixed grid of six tiles,
        # so there is nothing for extra space to do but stretch the gaps.
        self.setFixedSize(*WINDOW_SIZE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(16)

        layout.addLayout(self._build_filters())

        grid = QGridLayout()
        grid.setSpacing(14)
        self._tiles = {}
        captions = [
            ("sessions", "Sessions"),
            ("session_time", "Total Session Time"),
            ("breaks", "Breaks Delivered"),
            ("break_time", "Total Break Time"),
            ("skipped", "Skipped Early"),
            ("skip_rate", "Skip Rate"),
        ]
        for index, (key, caption) in enumerate(captions):
            tile = _Tile(caption)
            self._tiles[key] = tile
            grid.addWidget(tile, index // COLUMNS, index % COLUMNS)
        layout.addLayout(grid)
        layout.addStretch()

        layout.addWidget(_PathLabel(stats.EVENTS_PATH))

        self.refresh()

    def _build_filters(self):
        row = QHBoxLayout()
        row.setSpacing(10)

        caption = QLabel("period")
        caption.setObjectName("secondary")
        row.addWidget(caption)

        self._period = QComboBox()
        for kind in stats.PERIODS:
            self._period.addItem(stats.PERIOD_LABELS[kind], kind)
        self._period.currentIndexChanged.connect(self._on_period_changed)
        row.addWidget(self._period)

        today = QDate.currentDate()
        self._from = QDateEdit(today.addDays(-7))
        self._to = QDateEdit(today)
        self._arrow = QLabel("→")
        self._arrow.setObjectName("secondary")
        for edit in (self._from, self._to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("d MMM yyyy")
            edit.dateChanged.connect(self.refresh)
        row.addWidget(self._from)
        row.addWidget(self._arrow)
        row.addWidget(self._to)
        row.addStretch()
        self._set_custom_visible(False)
        return row

    def _set_custom_visible(self, visible):
        for widget in (self._from, self._arrow, self._to):
            widget.setVisible(visible)

    def _on_period_changed(self):
        self._set_custom_visible(self._period.currentData() == "custom")
        self.refresh()

    def refresh(self):
        kind = self._period.currentData()
        interval = stats.resolve_period(
            kind,
            datetime.now().astimezone(),
            custom_start=_as_datetime(self._from),
            custom_end=_as_datetime(self._to),
        )
        summary = stats.summarise(stats.events_in(stats.read_all(), interval))

        self._tiles["sessions"].set_value(str(summary.sessions))
        self._tiles["breaks"].set_value(str(summary.breaks))
        self._tiles["skipped"].set_value(str(summary.skipped_early))
        # A dash rather than "0s": nothing recorded is not the same as zero
        # seconds recorded, and the macOS window draws it the same way.
        self._tiles["session_time"].set_value(
            format_duration(summary.session_seconds) if summary.sessions else "—"
        )
        self._tiles["break_time"].set_value(
            format_duration(summary.break_seconds) if summary.breaks else "—"
        )
        self._tiles["skip_rate"].set_value(f"{summary.skip_rate}%" if summary.breaks else "—")


def _as_datetime(edit):
    """A QDateEdit's date as a local-aware datetime, so it compares against the
    UTC timestamps in the log."""
    date = edit.date()
    return datetime(date.year(), date.month(), date.day()).astimezone()
