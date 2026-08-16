"""Entry point: tray-resident QApplication.

The app has no main window -- it lives in the tray and shows a popup panel, the
Windows equivalent of the macOS menu bar item. There is no async work anywhere,
so unlike lumea this runs a plain Qt event loop with no qasync.
"""

import sys

from PySide6.QtCore import QRect
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import icon
from panel import Panel
from theme import STYLESHEET

# Named pipe used to detect an already-running instance.
_IPC_KEY = "TapLock-single-instance"

# Default relax accent (OverlayColor.green in the macOS app), used for the tray
# badge until the session config is wired up.
_DEFAULT_ACCENT = (0, 204, 0)


class TrayApp:
    def __init__(self, app: QApplication):
        self._app = app
        self._panel = Panel(on_quit=self.quit)

        self._tray = QSystemTrayIcon()
        self._tray.setIcon(icon.tray_icon(active=False, accent=_DEFAULT_ACCENT))
        self._tray.setToolTip("TapLock")

        menu = QMenu()
        menu.addAction("Show / Hide", self.toggle_panel)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_panel()

    def toggle_panel(self):
        self._panel.toggle(self._anchor())

    def show_panel(self):
        self._panel.show_at(self._anchor())

    def _anchor(self) -> QRect:
        # Empty when the icon sits in the tray overflow flyout; Panel falls back
        # to the tray corner in that case.
        return self._tray.geometry()

    def quit(self):
        self._tray.hide()
        self._app.quit()


def _ping_running_instance() -> bool:
    """True if another instance already holds the socket (connection succeeds)."""
    socket = QLocalSocket()
    socket.connectToServer(_IPC_KEY)
    connected = socket.waitForConnected(200)
    if connected:
        socket.disconnectFromServer()
    return connected


def _claim_instance_socket() -> QLocalServer:
    QLocalServer.removeServer(_IPC_KEY)  # clears a socket left behind by a crash
    server = QLocalServer()
    server.listen(_IPC_KEY)
    return server


def main() -> None:
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
