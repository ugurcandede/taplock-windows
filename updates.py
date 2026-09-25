"""Checks GitHub for a release newer than the running build -- port of
taplock-app's UpdateChecker.swift.

Unauthenticated GitHub API calls are limited to 60 an hour per IP; the app
checks at launch and once a day, far below that. A dismissed version stays
hidden until a newer one is published.

The frozen build updates itself: it downloads the new TapLock.exe next to the
running one, renames itself out of the way (Windows refuses to delete or
overwrite a running exe but allows renaming it), moves the download into place
and hands over to a detached relauncher before quitting. A source checkout has
no exe to replace and gets the release page instead.

Network calls run on daemon threads (urllib, like analytics: Python's ssl is
what PyInstaller bundles reliably) and results come back as Qt signals, which
Qt delivers on the UI thread.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

import config
import version

REPO = "ugurcandede/taplock-windows"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/TapLock.exe"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_TIMEOUT = 10
_DOWNLOAD_TIMEOUT = 120
# Long enough for this process to exit and release the single-instance guard,
# so the new exe does not find us still running and bow out.
_RELAUNCH_DELAY_SECONDS = 2
_DISMISSED_PATH = config.DATA_DIR / "update-dismissed"


@dataclass(frozen=True)
class Update:
    version: str
    url: str


def is_newer(candidate, current):
    """Numeric dot-separated comparison: 1.10.0 is newer than 1.9.3. A
    non-numeric current version ("dev") counts as 0, so source runs see the
    banner -- handy for testing it."""

    def parts(text):
        return [int(p) if p.isdigit() else 0 for p in text.split(".")]

    a, b = parts(candidate), parts(current)
    width = max(len(a), len(b))
    return a + [0] * (width - len(a)) > b + [0] * (width - len(b))


def parse_release(data, current, dismissed=None):
    """The release in `data` if newer than `current` and not dismissed."""
    try:
        release = json.loads(data)
        tag, url = release["tag_name"], release["html_url"]
    except (ValueError, KeyError, TypeError):
        return None
    latest = tag[1:] if tag.startswith("v") else tag
    if not is_newer(latest, current) or latest == dismissed:
        return None
    return Update(latest, url)


def dismissed_version():
    try:
        return _DISMISSED_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def dismiss(latest):
    try:
        _DISMISSED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DISMISSED_PATH.write_text(latest, encoding="utf-8")
    except OSError:
        pass


def can_self_update():
    return getattr(sys, "frozen", False)


def cleanup_previous(exe=None):
    """Remove the exe a previous update renamed out of the way."""
    exe = Path(exe or sys.executable)
    try:
        exe.with_name(exe.name + ".old").unlink(missing_ok=True)
    except OSError:
        pass  # still locked or not ours to delete; try again next launch


def swap_executable(exe, new):
    """Put `new` where `exe` is, keeping the running exe as `<exe>.old`.
    Rolls back and re-raises if the second step fails."""
    exe, new = Path(exe), Path(new)
    old = exe.with_name(exe.name + ".old")
    old.unlink(missing_ok=True)
    os.rename(exe, old)
    try:
        os.rename(new, exe)
    except OSError:
        os.rename(old, exe)
        raise


def _download(url, dest):
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response:
        data = response.read()
    # A redirect to an error page must never replace the app.
    if not data.startswith(b"MZ"):
        raise OSError("download is not a Windows executable")
    Path(dest).write_bytes(data)


def _relaunch(exe):
    # ping is the usual console-free sleep; `start` detaches the new process.
    command = f'ping -n {_RELAUNCH_DELAY_SECONDS + 1} 127.0.0.1 >nul & start "" "{exe}"'
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(["cmd", "/c", command], creationflags=flags, close_fds=True)


class UpdateChecker(QObject):
    found = Signal(object)  # Update, or None when up to date / unreachable
    installed = Signal()  # the new exe is in place and relaunching; quit now
    install_failed = Signal()

    def check(self):
        threading.Thread(target=self._run, daemon=True).start()

    def install(self):
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self):
        exe = Path(sys.executable)
        new = exe.with_name(exe.name + ".new")
        try:
            _download(DOWNLOAD_URL, new)
            swap_executable(exe, new)
            _relaunch(exe)
        except OSError:
            Path(new).unlink(missing_ok=True)
            self.install_failed.emit()
            return
        self.installed.emit()

    def _run(self):
        request = urllib.request.Request(_API, headers={"Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                data = response.read()
        except OSError:
            return  # keep whatever was shown; the daily check tries again
        self.found.emit(parse_release(data, version.VERSION, dismissed_version()))
