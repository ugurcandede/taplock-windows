"""One anonymous ping per day to Google Analytics (Measurement Protocol) --
port of taplock-app's Analytics.swift.

A random install id, the app version and the last used mode, nothing else. Off
when "send anonymous usage stats" is unchecked. The GA property is shared with
the macOS app; `app_name` tells the two apart.

Kept free of Qt so it can be tested without a display. The request runs on a
daemon thread: a slow or dead network must never stall the tray.
"""

import json
import os
import threading
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path

import config
import version

# GA4 -> Admin -> Data streams -> Measurement Protocol API secrets.
MEASUREMENT_ID = "G-DCYDCWCN8V"
API_SECRET = "RzYiYu4ISFCAwL6K4ZuiUA"
APP_NAME = "taplock-windows"

STATE_PATH = config.DATA_DIR / "analytics.json"
_ENDPOINT = "https://www.google-analytics.com/mp/collect?measurement_id={}&api_secret={}"
_TIMEOUT = 10

# The send thread and the UI thread both read-modify-write the state file.
_lock = threading.Lock()


def _read(path):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write(path, state):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass  # analytics is best effort; the next tick tries again


def _day(moment):
    return moment.strftime("%Y-%m-%d")


def set_last_mode(mode, path=STATE_PATH):
    """Mode is not a persistent setting, so the ping reports the mode of the
    last started session -- "none" until one has run."""
    with _lock:
        state = _read(path)
        state["lastMode"] = mode
        _write(path, state)


def _client_id(path):
    """Random id minted on first use -- the only identifier analytics sends."""
    with _lock:
        state = _read(path)
        if "clientId" not in state:
            state["clientId"] = str(uuid.uuid4())
            _write(path, state)
        return state["clientId"]


def unsent_dates(now, last_ping):
    """Days missed offline are backfilled with a backdated timestamp, which GA
    accepts up to 72 hours into the past -- so at most the two previous days are
    recoverable; older gaps stay lost."""
    if last_ping is None:
        return [now]
    dates = [now - timedelta(days=offset) for offset in (2, 1)]
    return [d for d in dates if _day(d) > last_ping] + [now]


def payload(moment, backdated, client_id, mode):
    body = {
        "client_id": client_id,
        "events": [
            {
                "name": "daily_ping",
                "params": {
                    "app_name": APP_NAME,
                    # "backfill" means the day was spent offline and the ping
                    # was recovered later; "live" went out same-day.
                    "ping_type": "backfill" if backdated else "live",
                    "mode": mode,
                    "app_version": version.VERSION,
                    # session_id and engagement_time_msec are required for the
                    # ping to count as an active user in GA4, not just an event.
                    "session_id": str(int(moment.timestamp())),
                    "engagement_time_msec": 100,
                },
            }
        ],
    }
    if backdated:
        body["timestamp_micros"] = int(moment.timestamp() * 1_000_000)
    return body


def _post(url, body):
    """HTTP status, or None when the network is unreachable."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.status
    except OSError:  # URLError, timeouts and HTTP errors all derive from it
        return None


def _in_background(work):
    threading.Thread(target=work, daemon=True).start()


def ping_if_due(now, path=STATE_PATH, post=_post, spawn=_in_background):
    """Send today's ping (plus any recoverable missed days) unless it went out.

    The caller checks the enabled setting. A day is marked sent only once
    Google answers, so an unreachable network leaves the state untouched and
    the next tick retries and backfills what it can.
    """
    with _lock:
        state = _read(path)
    last_ping = state.get("lastPing")
    if last_ping == _day(now):
        return

    client_id = _client_id(path)
    mode = state.get("lastMode", "none")
    url = _ENDPOINT.format(MEASUREMENT_ID, API_SECRET)
    for moment in unsent_dates(now, last_ping):
        body = payload(moment, moment != now, client_id, mode)
        sent_day = _day(moment)

        def send(body=body, sent_day=sent_day):
            status = post(url, body)
            if status is None or not 200 <= status < 300:
                return
            with _lock:
                current = _read(path)
                if sent_day > current.get("lastPing", ""):
                    current["lastPing"] = sent_day
                    _write(path, current)

        spawn(send)
