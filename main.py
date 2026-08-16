"""Entry point: tray-resident QApplication.

The app has no main window -- it lives in the tray and shows a popup panel, the
Windows equivalent of the macOS menu bar item. There is no async work anywhere,
so unlike lumea this runs a plain Qt event loop with no qasync.

The 1 Hz timer that drives `RelaxSession` is owned here rather than by the
session, which keeps the state machine testable without Qt.
"""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import icon
from panel import Panel
from parsers import format_mmss, parse_color, rgb255
from session import RelaxSession
from theme import STYLESHEET

# Named pipe used to detect an already-running instance.
_IPC_KEY = "TapLock-single-instance"

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
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._refresh_tray()
        self._tray.show()

        self._timer = QTimer(app)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._session.tick)
        self._timer.start()

        self._session.state_changed.connect(self._refresh_tray)

    # ---- tray ------------------------------------------------------------

    def _accent(self):
        return rgb255(parse_color(self._config.color) or parse_color("green"))

    def _refresh_tray(self):
        running = self._session.running
        # The tooltip changes every second; the icon only when the badge does.
        if running != self._tray_shows_active:
            self._tray_shows_active = running
            self._tray.setIcon(icon.tray_icon(running, self._accent()))
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


def _ping_running_instance():
    """True if another instance already holds the socket (connection succeeds)."""
    socket = QLocalSocket()
    socket.connectToServer(_IPC_KEY)
    connected = socket.waitForConnected(200)
    if connected:
        socket.disconnectFromServer()
    return connected


def _claim_instance_socket():
    QLocalServer.removeServer(_IPC_KEY)  # clears a socket left behind by a crash
    server = QLocalServer()
    server.listen(_IPC_KEY)
    return server


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("ugurcandede")
    app.setApplicationName("TapLock")
    app.setWindowIcon(icon.app_icon())
    app.setStyleSheet(STYLESHEET)
    # No main window: closing the panel must not end the process.
    app.setQuitOnLastWindowClosed(False)

    if _ping_running_instance():
        sys.exit(0)
    server = _claim_instance_socket()

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
