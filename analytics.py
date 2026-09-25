"""Anonymous usage analytics over the GA4 Measurement Protocol -- port of
taplock-app's Analytics.swift, same event names and parameters.

Events carry a random install id, the app version and what was used -- never
keystrokes, input data or device names. Everything is off when "send anonymous
usage stats" is unchecked. The GA property is shared with the macOS app;
`app_name` tells the two apart.

Events are queued in the state file and sent one request each, stamped with the
time they happened, so nothing is lost offline. GA accepts timestamps up to 72
hours old; older queued events are dropped.

Kept free of Qt so it can be tested without a display. Sending runs on a daemon
thread: a slow or dead network must never stall the tray.
"""

import json
import os
import platform
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
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
MAX_EVENT_AGE = 72 * 3600
MAX_QUEUE_LENGTH = 500
# Short enough that Realtime feels live, long enough to send a burst together.
FLUSH_DELAY = 5
# Realtime shows the last 30 minutes, so a running session reports in just inside that.
HEARTBEAT_SECONDS = 25 * 60

# Every state-file read-modify-write happens under this; the send thread and
# the UI thread both touch the queue.
_lock = threading.Lock()

_enabled = lambda: True  # noqa: E731 -- replaced by start()
_started = False
_flushing = False
_flush_timer = None
_launch = time.time()
_session_id = str(int(_launch))
_last_heartbeat = _launch
_app_user_properties = {}


# ---- state file ------------------------------------------------------------


def _read():
    try:
        state = json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write(state):
    path = Path(STATE_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass  # analytics is best effort


def _update(**fields):
    with _lock:
        state = _read()
        state.update(fields)
        _write(state)


def _day(moment):
    return moment.strftime("%Y-%m-%d")


# ---- public API ------------------------------------------------------------


def start(enabled, now=None):
    """Call once at launch. `enabled` is read on every call, so turning usage
    stats off stops sending at once."""
    global _enabled, _started
    if _started:
        return
    _enabled = enabled
    _started = True
    now = now or datetime.now().astimezone()

    with _lock:
        state = _read()
        first_launch = "clientId" not in state
        state.setdefault("clientId", str(uuid.uuid4()))
        state.setdefault("firstLaunch", now.isoformat())
        previous_version = state.get("lastVersion")
        state["lastVersion"] = version.VERSION
        _write(state)

    if first_launch:
        track("app_first_launch")
    elif previous_version and previous_version != version.VERSION:
        track("app_update", {"from_version": previous_version})
    track("app_launch")
    ping_if_due(now)


def track(name, params=None, engagement_ms=100):
    """Queue an event. Values should be str, int, float or bool; GA truncates
    strings past 100 characters. No-op before `start()`, which keeps tests off
    the network."""
    if not _started or not _enabled():
        return
    _enqueue(name, params or {}, time.time(), engagement_ms)
    _schedule_flush()


def set_user_properties(properties):
    """App-specific user properties, merged into the built-in ones on every request."""
    _app_user_properties.update(_stringify_bools(properties))


def heartbeat(params):
    """Send a heartbeat while a session runs; `params` is None when idle. The
    engagement time is the time since the previous one, so GA's engagement
    figures reflect time spent in sessions."""
    global _last_heartbeat
    if params is None:
        return
    now = time.time()
    track("heartbeat", params, engagement_ms=int((now - _last_heartbeat) * 1000))
    _last_heartbeat = now


def app_will_terminate():
    """Record the quit and give it a moment to go out. Whatever does not make it
    stays queued and is sent on next launch."""
    track("app_quit", {"uptime_sec": int(time.time() - _launch)})
    worker = threading.Thread(target=flush, daemon=True)
    worker.start()
    worker.join(timeout=2)


def disabled():
    """Usage stats were switched off: drop anything still waiting."""
    _update(queue=[])


# ---- daily ping --------------------------------------------------------------


def unsent_dates(now, last_ping):
    """Days missed offline are backfilled for the two previous days -- older
    ones are past GA's 72 h limit."""
    if last_ping is None:
        return [now]
    dates = [now - timedelta(days=offset) for offset in (2, 1)]
    return [d for d in dates if _day(d) > last_ping] + [now]


def ping_if_due(now=None):
    """One `daily_ping` per day keeps daily actives honest for an app that can
    run for weeks between launches."""
    if not _started or not _enabled():
        return
    now = now or datetime.now().astimezone()
    with _lock:
        state = _read()
    last_ping = state.get("lastPing")
    if last_ping == _day(now):
        return
    mode = state.get("lastMode", "none")
    for moment in unsent_dates(now, last_ping):
        ping_type = "live" if moment == now else "backfill"
        _enqueue("daily_ping", {"ping_type": ping_type, "mode": mode}, moment.timestamp())
    # The queue is persistent, so the day counts as sent once it is queued.
    _update(lastPing=_day(now))
    _schedule_flush()


def set_last_mode(mode):
    """Mode is not a persistent setting, so the daily ping reports the mode of
    the last started session -- "none" until one has run."""
    _update(lastMode=mode)


# ---- queue and sending -------------------------------------------------------


def _stringify_bools(values):
    # GA documents parameter values as strings or numbers.
    return {k: ("true" if v else "false") if isinstance(v, bool) else v for k, v in values.items()}


def _enqueue(name, params, timestamp, engagement_ms=100):
    event = _stringify_bools(params)
    event.update(
        app_name=APP_NAME,
        app_version=version.VERSION,
        session_id=_session_id,
        # Required for the event to count toward active users.
        engagement_time_msec=max(1, engagement_ms),
    )
    record = {"id": str(uuid.uuid4()), "name": name, "params": event, "ts": int(timestamp * 1_000_000)}
    with _lock:
        state = _read()
        state["queue"] = (state.get("queue", []) + [record])[-MAX_QUEUE_LENGTH:]
        _write(state)


def _schedule_flush():
    global _flush_timer
    if _flush_timer is not None and _flush_timer.is_alive():
        return
    _flush_timer = threading.Timer(FLUSH_DELAY, flush)
    _flush_timer.daemon = True
    _flush_timer.start()


def flush():
    """Send queued events oldest first, one request each. Stops at the first
    failure; the rest wait for the next flush."""
    global _flushing
    with _lock:
        if _flushing:
            return
        _flushing = True
    try:
        while _enabled():
            cutoff = int((time.time() - MAX_EVENT_AGE) * 1_000_000)
            with _lock:
                state = _read()
                queue = [e for e in state.get("queue", []) if e.get("ts", 0) > cutoff]
                state["queue"] = queue
                _write(state)
                client_id = state.get("clientId", "")
                first_launch = state.get("firstLaunch")
            if not queue:
                return
            event = queue[0]
            status = _post(_ENDPOINT.format(MEASUREMENT_ID, API_SECRET), request_body(event, client_id, first_launch))
            if status is None or not 200 <= status < 300:
                return
            with _lock:
                state = _read()
                state["queue"] = [e for e in state.get("queue", []) if e.get("id") != event["id"]]
                _write(state)
    finally:
        with _lock:
            _flushing = False


def request_body(event, client_id, first_launch=None):
    return {
        "client_id": client_id,
        "timestamp_micros": event["ts"],
        "user_properties": {k: {"value": v} for k, v in user_properties(first_launch).items()},
        "events": [{"name": event["name"], "params": event["params"]}],
    }


def user_properties(first_launch=None):
    props = {
        "app_name": APP_NAME,
        "app_version": version.VERSION,
        "platform": "windows",
        "os_version": _os_version(),
        "arch": _arch(),
    }
    if first_launch:
        # ISO week of the first launch, e.g. 2026-W39 -- for retention cohorts.
        year, week, _ = datetime.fromisoformat(first_launch).isocalendar()
        props["install_week"] = f"{year}-W{week:02d}"
    props.update(_app_user_properties)
    return props


def _arch():
    # Same spelling as the macOS build reports.
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine or "unknown")


def _os_version():
    if hasattr(sys, "getwindowsversion"):
        v = sys.getwindowsversion()
        return f"{v.major}.{v.minor}.{v.build}"
    return platform.release() or "unknown"


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
