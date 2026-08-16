"""Entry point: tray-resident QApplication.

The app has no main window -- it lives in the tray and shows a popup panel, the
Windows equivalent of the macOS menu bar item. There is no async work anywhere,
so unlike lumea this runs a plain Qt event loop with no qasync.

The 1 Hz timer that drives `RelaxSession` is owned here rather than by the
session, which keeps the state machine testable without Qt.
"""

import sys

from PySide6.QtCore import QSharedMemory, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import icon
from overlay import OverlayController
from panel import Panel
from parsers import format_mmss
from session import RelaxSession
from theme import STYLESHEET

# Shared-memory segment whose existence means "an instance is already running".
# Creating it is atomic, so two launches milliseconds apart cannot both win.
_GUARD_KEY = "TapLock-single-instance"
# Named pipe, used only to tell that running instance to surface its panel.
_IPC_KEY = "TapLock-ipc"

TICK_MS = 1000


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
        self._refresh_tray()
        self._tray.show()

        self._overlays = OverlayController(app)
        self._overlays.skipped.connect(self._session.skip_break)
        self._session.break_started.connect(self._open_overlay)
        self._session.break_ended.connect(self._overlays.close)

        self._timer = QTimer(app)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._session.state_changed.connect(self._refresh_tray)
        app.styleHints().colorSchemeChanged.connect(self._theme_changed)

    def _tick(self):
        self._session.tick()
        if self._overlays.visible:
            self._overlays.set_remaining(self._session.remaining)

    def _open_overlay(self):
        self._overlays.open(self._session.config)
        self._overlays.set_remaining(self._session.remaining)

    # ---- tray ------------------------------------------------------------

    def _theme_changed(self):
        # Qt reports the app theme, we render against the taskbar theme; they are
        # separate Windows settings but are normally switched together, so this
        # is the right moment to re-render the glyph in the other colour.
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
    app.setStyleSheet(STYLESHEET)
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
