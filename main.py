"""Entry point: tray-resident QApplication.

The app has no main window -- it lives in the tray and shows a popup panel, the
Windows equivalent of the macOS menu bar item. There is no async work anywhere,
so unlike lumea this runs a plain Qt event loop with no qasync.

The 1 Hz timer that drives `RelaxSession` is owned here rather than by the
session, which keeps the state machine testable without Qt.
"""

import sys
import winsound

from PySide6.QtCore import QSharedMemory, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import icon
import theme
from overlay import OverlayController
from panel import Panel
from parsers import format_mmss
from session import RelaxSession

# Shared-memory segment whose existence means "an instance is already running".
# Creating it is atomic, so two launches milliseconds apart cannot both win.
_GUARD_KEY = "TapLock-single-instance"
# Named pipe, used only to tell that running instance to surface its panel.
_IPC_KEY = "TapLock-ipc"

TICK_MS = 1000

# How long a theme preview stays up, matching the macOS popover.
PREVIEW_MS = 5000

# Windows system sounds standing in for macOS's Pop / Blow / Glass. Volume is
# not settable from the API, which is fine: the user's sound scheme wins, and a
# scheme set to "No Sounds" correctly produces silence.
_SOUNDS = {
    "pre": "Notification.Default",
    "start": "Notification.Reminder",
    "end": "SystemAsterisk",
}


def _play(kind):
    try:
        winsound.PlaySound(
            _SOUNDS[kind],
            # SND_NODEFAULT matters: without it a missing alias falls back to
            # the generic Windows ding.
            winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except RuntimeError:
        pass  # the scheme can be emptied; a missing sound is not an error here


class TrayApp:
    def __init__(self, app):
        self._app = app
        self._config = config.load()
        self._session = RelaxSession()
        self._panel = Panel(self._config, self._session, on_quit=self.quit)

        self._tray = QSystemTrayIcon()
        self._tray.setToolTip("TapLock")
        self._tray_shows_active = None  # forces the first icon render
        menu = QMenu()
        menu.addAction("Show / Hide", self.toggle_panel)
        # Only meaningful mid-session; hidden the rest of the time.
        self._stop_action = menu.addAction("Stop session", self._session.stop)
        self._stop_action.setVisible(False)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

        self._overlays = OverlayController(app)
        self._overlays.skipped.connect(self._on_skip)
        self._session.break_started.connect(self._open_overlay)
        self._session.break_ended.connect(self._overlays.close)
        self._session.play_sound.connect(_play)
        self._session.posture_due.connect(self._overlays.open_posture)
        self._session.posture_dismissed.connect(self._overlays.close_posture)
        self._overlays.posture_dismissed.connect(self._on_posture_dismissed)

        # Previews reuse the break overlays; settings are only reachable while
        # idle, so there is never a real break to collide with.
        self._preview_timer = QTimer(app)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._end_preview)
        self._panel.preview_theme.connect(self._show_preview)
        self._panel.preview_posture.connect(self._show_posture_preview)

        self._timer = QTimer(app)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._session.state_changed.connect(self._refresh_tray)
        app.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.apply_theme()  # also paints the first tray icon
        self._tray.show()

    def _tick(self):
        self._session.tick()
        if self._overlays.visible:
            self._overlays.set_remaining(self._session.remaining)

    def _open_overlay(self):
        self._overlays.open(self._session.config)
        self._overlays.set_remaining(self._session.remaining)

    def _show_preview(self):
        if self._session.running:
            return
        self._overlays.open(self._config)
        self._overlays.set_remaining(self._config.break_duration)
        self._preview_timer.start(PREVIEW_MS)

    def _end_preview(self):
        self._overlays.close()
        self._overlays.close_posture()

    def _show_posture_preview(self):
        if self._session.running:
            return
        self._overlays.open_posture()
        self._preview_timer.start(PREVIEW_MS)

    def _on_skip(self):
        # The same Skip button dismisses a preview and skips a real break.
        if self._preview_timer.isActive():
            self._preview_timer.stop()
            self._overlays.close()
        else:
            self._session.skip_break()

    def _on_posture_dismissed(self):
        # "Got it" ends a preview and a real reminder alike; the session call is
        # a no-op when there is no session running.
        self._preview_timer.stop()
        self._overlays.close_posture()
        self._session.dismiss_posture()

    # ---- tray ------------------------------------------------------------

    def apply_theme(self):
        """Follow the Windows app theme: panel, tray menu and statistics window
        all come from the one stylesheet.

        The tray glyph is deliberately not decided here -- it is painted against
        the taskbar, whose theme is a separate Windows setting (`icon.py` reads
        it). The two are normally switched together, so this is still the right
        moment to re-render the glyph in the other colour.
        """
        dark = self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self._app.setStyleSheet(theme.stylesheet(dark))
        self._panel.apply_theme(dark)
        self._tray_shows_active = None
        self._refresh_tray()

    def _refresh_tray(self):
        running = self._session.running
        # The tooltip changes every second; the icon and menu only on a switch.
        if running != self._tray_shows_active:
            self._tray_shows_active = running
            self._tray.setIcon(icon.tray_icon(running))
            self._stop_action.setVisible(running)
        if running:
            phase = "break" if self._session.on_break else "next break"
            self._tray.setToolTip(f"TapLock — {phase} in {format_mmss(self._session.remaining)}")
        else:
            self._tray.setToolTip("TapLock")

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_panel()

    def toggle_panel(self):
        self._panel.toggle(self._anchor())

    def show_panel(self):
        self._panel.show_at(self._anchor())

    def _anchor(self):
        # Empty when the icon sits in the tray overflow flyout; Panel falls back
        # to the tray corner in that case.
        return self._tray.geometry()

    def quit(self):
        self._session.stop()  # records the session-ended event
        self._tray.hide()
        self._app.quit()


def _claim_single_instance():
    """The guard segment, or None if another instance already holds it.

    Not `QLocalServer.listen()`: on Windows a pipe name accepts several server
    instances, so listen() succeeds for every launch and detects nothing.
    Creating a shared-memory segment is atomic and the segment dies with its
    owner, so there is no stale lock to clean up either.
    """
    guard = QSharedMemory(_GUARD_KEY)
    return guard if guard.create(1) else None


def _wake_running_instance():
    """Best effort: connect to the running instance so it shows its panel."""
    socket = QLocalSocket()
    socket.connectToServer(_IPC_KEY)
    if socket.waitForConnected(200):
        socket.disconnectFromServer()


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("ugurcandede")
    app.setApplicationName("TapLock")
    app.setWindowIcon(icon.app_icon())
    # No main window: closing the panel must not end the process.
    app.setQuitOnLastWindowClosed(False)

    # Held for the process lifetime: releasing it would let a second instance in.
    guard = _claim_single_instance()
    if guard is None:
        _wake_running_instance()
        sys.exit(0)

    server = QLocalServer()
    server.listen(_IPC_KEY)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.exit("No system tray available.")

    tray_app = TrayApp(app)

    def surface_existing():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.close()
        tray_app.show_panel()

    # A second launch connects to our socket instead of starting up.
    server.newConnection.connect(surface_existing)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
