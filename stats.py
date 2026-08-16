"""Append-only event log -- port of TapLockCore/StatsStore.swift.

`%APPDATA%\\taplock\\events.jsonl`, one JSON object per line. The record shape
and timestamp format match the macOS version exactly, so a log can move between
machines. The file is never rewritten and malformed lines are skipped, which
also means `lock_completed` records carried over from macOS survive untouched
even though this build has no lock mode.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DATA_DIR

EVENTS_PATH = DATA_DIR / "events.jsonl"

PERIODS = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_year",
    "last_year",
    "all_time",
    "custom",
)

# Periods shown in the compact panel dropdown; the rest live in the stats window.
PANEL_PERIODS = ("today", "this_week", "last_week")


def _iso(moment):
    """Swift's `.iso8601` strategy emits whole seconds with a `Z` suffix and its
    decoder rejects fractional seconds -- match it exactly or macOS cannot read
    a log this build wrote."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append(event_type, moment, path=EVENTS_PATH, **fields):
    """Append one event. Fire-and-forget: a failed write must never take the
    session down. Writing a single short line is sub-millisecond, so unlike the
    Swift version there is no background queue."""
    record = {"type": event_type, "timestamp": _iso(moment)}
    record.update(fields)
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def read_all(path=EVENTS_PATH):
    """Every parseable event, oldest first, with `timestamp` as an aware datetime."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []

    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            record["timestamp"] = datetime.fromisoformat(record["timestamp"])
        except (ValueError, KeyError, TypeError):
            continue
        events.append(record)
    return events


# ---- periods -------------------------------------------------------------


def _start_of_day(moment):
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _add_months(moment, count):
    month = moment.month - 1 + count
    return moment.replace(year=moment.year + month // 12, month=month % 12 + 1, day=1)


def resolve_period(kind, now, custom_start=None, custom_end=None):
    """Half-open `(start, end)` interval, or None for `all_time` (no filter).

    Weeks start on Monday (ISO 8601). macOS uses `Calendar.current`, so its week
    boundary is locale-dependent -- a deliberate divergence, not a port bug.
    """
    today = _start_of_day(now)

    if kind == "today":
        return today, today + timedelta(days=1)
    if kind == "yesterday":
        return today - timedelta(days=1), today
    if kind == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7)
    if kind == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=7)
    if kind == "this_month":
        start = today.replace(day=1)
        return start, _add_months(start, 1)
    if kind == "last_month":
        end = today.replace(day=1)
        return _add_months(end, -1), end
    if kind == "this_year":
        start = today.replace(month=1, day=1)
        return start, start.replace(year=start.year + 1)
    if kind == "last_year":
        end = today.replace(month=1, day=1)
        return end.replace(year=end.year - 1), end
    if kind == "all_time":
        return None
    if kind == "custom":
        start = _start_of_day(custom_start)
        end = _start_of_day(custom_end) + timedelta(days=1)
        # A single-day range must still be non-empty.
        return (start, start + timedelta(days=1)) if end <= start else (start, end)
    raise ValueError(f"unknown period: {kind}")


def events_in(events, interval):
    if interval is None:
        return list(events)
    start, end = interval
    return [e for e in events if start <= e["timestamp"] < end]


# ---- summary -------------------------------------------------------------


@dataclass
class RelaxSummary:
    sessions: int = 0
    session_seconds: int = 0
    breaks: int = 0
    break_seconds: int = 0
    skipped_early: int = 0

    @property
    def skip_rate(self):
        """Share of breaks dismissed before their timer ran out, 0-100."""
        return round(self.skipped_early / self.breaks * 100) if self.breaks else 0


def summarise(events):
    summary = RelaxSummary()
    for event in events:
        kind = event.get("type")
        if kind == "relax_break":
            summary.breaks += 1
            summary.break_seconds += event.get("actual_seconds", 0)
            if event.get("skipped_early"):
                summary.skipped_early += 1
        elif kind == "relax_session_ended":
            summary.sessions += 1
            summary.session_seconds += event.get("duration_seconds", 0)
    return summary
