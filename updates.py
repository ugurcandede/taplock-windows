"""Checks GitHub for a release newer than the running build -- port of
taplock-app's UpdateChecker.swift.

Unauthenticated GitHub API calls are limited to 60 an hour per IP; the app
checks at launch and once a day, far below that. A dismissed version stays
hidden until a newer one is published.

The request runs on a daemon thread (urllib, like analytics: Python's ssl is
what PyInstaller bundles reliably) and the result comes back as a Qt signal,
which Qt delivers on the UI thread.
"""

import json
import threading
import urllib.request
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

import config
import version

REPO = "ugurcandede/taplock-windows"
DOWNLOAD_URL = f"https://github.com/{REPO}/releases/latest/download/TapLock.exe"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_TIMEOUT = 10
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


class UpdateChecker(QObject):
    found = Signal(object)  # Update, or None when up to date / unreachable

    def check(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        request = urllib.request.Request(_API, headers={"Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                data = response.read()
        except OSError:
            return  # keep whatever was shown; the daily check tries again
        self.found.emit(parse_release(data, version.VERSION, dismissed_version()))
