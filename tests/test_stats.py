from datetime import datetime, timedelta, timezone

import pytest

import stats

# A Wednesday, so week boundaries are unambiguous.
NOW = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)


def test_append_and_read_round_trip(tmp_path):
    path = tmp_path / "events.jsonl"
    stats.append("relax_break", NOW, path=path, planned_seconds=300, actual_seconds=112, skipped_early=True)
    events = stats.read_all(path)
    assert len(events) == 1
    assert events[0]["type"] == "relax_break"
    assert events[0]["actual_seconds"] == 112
    assert events[0]["timestamp"] == NOW


def test_timestamp_matches_swift_iso8601(tmp_path):
    """Swift's decoder rejects fractional seconds; whole seconds plus `Z` or the
    macOS build cannot read a log this one wrote."""
    path = tmp_path / "events.jsonl"
    stats.append("relax_session_ended", NOW.replace(microsecond=123456), path=path, duration_seconds=60)
    line = path.read_text(encoding="utf-8").strip()
    assert '"timestamp": "2026-08-12T14:30:00Z"' in line


def test_append_is_append_only(tmp_path):
    path = tmp_path / "events.jsonl"
    for i in range(3):
        stats.append("relax_break", NOW, path=path, actual_seconds=i)
    assert len(stats.read_all(path)) == 3


def test_malformed_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    stats.append("relax_break", NOW, path=path, actual_seconds=10)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ truncated\n\n")
        handle.write('{"type": "relax_break"}\n')  # no timestamp
    stats.append("relax_break", NOW, path=path, actual_seconds=20)
    assert [e["actual_seconds"] for e in stats.read_all(path)] == [10, 20]


def test_missing_file_reads_empty(tmp_path):
    assert stats.read_all(tmp_path / "absent.jsonl") == []


# ---- periods -------------------------------------------------------------


def test_today():
    start, end = stats.resolve_period("today", NOW)
    assert start == datetime(2026, 8, 12, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 13, tzinfo=timezone.utc)


def test_yesterday_ends_where_today_starts():
    y_start, y_end = stats.resolve_period("yesterday", NOW)
    t_start, _ = stats.resolve_period("today", NOW)
    assert y_end == t_start
    assert y_start == datetime(2026, 8, 11, tzinfo=timezone.utc)


def test_week_starts_monday():
    start, end = stats.resolve_period("this_week", NOW)
    assert start == datetime(2026, 8, 10, tzinfo=timezone.utc)  # Monday
    assert end - start == timedelta(days=7)


def test_last_week_is_the_preceding_seven_days():
    last_start, last_end = stats.resolve_period("last_week", NOW)
    this_start, _ = stats.resolve_period("this_week", NOW)
    assert last_end == this_start
    assert last_start == datetime(2026, 8, 3, tzinfo=timezone.utc)


def test_months():
    assert stats.resolve_period("this_month", NOW) == (
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert stats.resolve_period("last_month", NOW) == (
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def test_month_arithmetic_crosses_the_year():
    january = datetime(2026, 1, 15, tzinfo=timezone.utc)
    assert stats.resolve_period("last_month", january) == (
        datetime(2025, 12, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_years():
    assert stats.resolve_period("this_year", NOW) == (
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    assert stats.resolve_period("last_year", NOW) == (
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_all_time_means_no_filter():
    assert stats.resolve_period("all_time", NOW) is None


def test_custom_single_day_is_not_empty():
    day = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
    start, end = stats.resolve_period("custom", NOW, custom_start=day, custom_end=day)
    assert end - start == timedelta(days=1)
    assert start == datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_custom_end_is_inclusive_of_its_day():
    start, end = stats.resolve_period(
        "custom",
        NOW,
        custom_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        custom_end=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    assert (start, end) == (datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 8, 4, tzinfo=timezone.utc))


def test_custom_reversed_range_falls_back_to_one_day():
    start, end = stats.resolve_period(
        "custom",
        NOW,
        custom_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        custom_end=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    assert end - start == timedelta(days=1)


def test_unknown_period_raises():
    with pytest.raises(ValueError):
        stats.resolve_period("fortnight", NOW)


def test_events_in_filters_half_open():
    start, end = stats.resolve_period("today", NOW)
    events = [
        {"type": "relax_break", "timestamp": start - timedelta(seconds=1)},
        {"type": "relax_break", "timestamp": start},
        {"type": "relax_break", "timestamp": end - timedelta(seconds=1)},
        {"type": "relax_break", "timestamp": end},
    ]
    assert len(stats.events_in(events, (start, end))) == 2


def test_events_in_none_returns_everything():
    events = [{"type": "relax_break", "timestamp": NOW}]
    assert stats.events_in(events, None) == events


# ---- summary -------------------------------------------------------------


def test_summarise():
    events = [
        {"type": "relax_session_started", "timestamp": NOW, "interval_seconds": 1500},
        {"type": "relax_break", "timestamp": NOW, "actual_seconds": 300, "skipped_early": False},
        {"type": "relax_break", "timestamp": NOW, "actual_seconds": 40, "skipped_early": True},
        {"type": "relax_session_ended", "timestamp": NOW, "duration_seconds": 3600, "breaks_taken": 2},
    ]
    summary = stats.summarise(events)
    assert summary.sessions == 1
    assert summary.session_seconds == 3600
    assert summary.breaks == 2
    assert summary.break_seconds == 340
    assert summary.skipped_early == 1
    assert summary.skip_rate == 50


def test_summarise_ignores_lock_events():
    """Logs carried over from macOS contain lock records this build cannot use."""
    events = [{"type": "lock_completed", "timestamp": NOW, "actual_seconds": 120, "emergency_cancelled": True}]
    assert stats.summarise(events) == stats.RelaxSummary()


def test_skip_rate_without_breaks_is_zero():
    assert stats.RelaxSummary().skip_rate == 0
