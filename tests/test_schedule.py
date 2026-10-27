"""Parity with Tests/TapLockTests/RelaxScheduleTests.swift: same cases, same answers."""

from datetime import datetime, timezone

import pytest

from schedule import RelaxSchedule

UTC = timezone.utc
WEEKDAYS = {2, 3, 4, 5, 6}  # Mon-Fri, Swift numbering


def at(hour, minute, day=19):
    """2026-10-19 is a Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def next_break(schedule, after, interval=3000, break_duration=600, tz=UTC):
    return schedule.next_break(after, interval, break_duration, tz=tz)


def test_default_schedule_matches_plain_interval():
    assert next_break(RelaxSchedule(), at(10, 0)) == at(10, 50)


def test_inside_active_hours_is_plain_interval():
    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=9 * 60, active_end=18 * 60)
    assert next_break(s, at(10, 0)) == at(10, 50)


def test_before_active_hours_waits_from_start():
    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=9 * 60, active_end=18 * 60)
    assert next_break(s, at(7, 30)) == at(9, 50)


def test_past_active_end_moves_to_next_day():
    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=9 * 60, active_end=18 * 60)
    assert next_break(s, at(17, 40)) == at(9, 50, day=20)


def test_friday_evening_skips_weekend():
    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=9 * 60, active_end=18 * 60)
    assert next_break(s, at(17, 40, day=23)) == at(9, 50, day=26)


def test_quiet_hours_restart_wait_afterwards():
    s = RelaxSchedule(quiet_start=12 * 60, quiet_end=13 * 60)
    assert next_break(s, at(11, 30)) == at(13, 50)


def test_starting_inside_quiet_hours_waits_from_their_end():
    s = RelaxSchedule(quiet_start=12 * 60, quiet_end=13 * 60)
    assert next_break(s, at(12, 30)) == at(13, 50)


def test_wait_spanning_quiet_hours_restarts():
    s = RelaxSchedule(quiet_start=12 * 60, quiet_end=13 * 60)
    # 11:30 + 90m would be 13:00, but the wait crossed lunch.
    assert next_break(s, at(11, 30), interval=5400) == at(14, 30)


def test_align_to_clock_uses_grid_from_active_start():
    s = RelaxSchedule(active_start=9 * 60, active_end=18 * 60, align_to_clock=True)
    # 50/10 grid: 09:50, 10:50, 11:50 ...
    assert next_break(s, at(10, 17)) == at(10, 50)
    assert next_break(s, at(10, 50)) == at(11, 50)
    assert next_break(s, at(8, 0)) == at(9, 50)


def test_align_to_clock_pomodoro():
    s = RelaxSchedule(active_start=9 * 60, active_end=18 * 60, align_to_clock=True)
    # 25/5 grid: 09:25, 09:55, 10:25 ...
    assert next_break(s, at(9, 30), interval=1500, break_duration=300) == at(9, 55)


def test_no_active_days_never_fires():
    assert next_break(RelaxSchedule(weekdays=set()), at(10, 0)) is None


def test_all_day_wait_crosses_midnight_without_restarting():
    # 0:00-24:00 every day: midnight isn't a block, so this is the plain interval.
    assert next_break(RelaxSchedule(), at(23, 30)) == at(0, 20, day=20)


def test_all_day_wait_restarts_before_off_day():
    # Sunday 23:30 with Monday off: the wait restarts Tuesday at midnight.
    s = RelaxSchedule(weekdays={1, 3, 4, 5, 6, 7})
    assert next_break(s, at(23, 30, day=18)) == at(0, 50, day=20)


def test_all_day_quiet_on_next_day_still_blocks():
    s = RelaxSchedule(quiet_start=0, quiet_end=60)
    # 23:30 + 50m runs into tomorrow's 0:00-1:00 quiet window.
    assert next_break(s, at(23, 30)) == at(1, 50, day=20)


def test_aligned_grid_rolls_to_tomorrows_anchor():
    s = RelaxSchedule(align_to_clock=True)
    # 0:50, 1:50 ... 23:50, then tomorrow's 0:50, not midnight.
    assert next_break(s, at(23, 55)) == at(0, 50, day=20)


def test_interval_longer_than_hours_never_fires():
    s = RelaxSchedule(active_start=9 * 60, active_end=10 * 60)
    assert next_break(s, at(8, 0), interval=2 * 3600) is None


def test_keeps_clock_time_across_dst():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        tz = zoneinfo.ZoneInfo("America/New_York")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database (Windows without the tzdata package)")
    # Clocks spring forward on 2026-03-08 at 2:00.
    saturday_evening = datetime(2026, 3, 7, 20, 0, tzinfo=tz)
    expected = datetime(2026, 3, 8, 9, 50, tzinfo=tz)
    s = RelaxSchedule(active_start=9 * 60, active_end=18 * 60)
    assert next_break(s, saturday_evening, tz=tz) == expected
    aligned = RelaxSchedule(active_start=9 * 60, active_end=18 * 60, align_to_clock=True)
    assert next_break(aligned, saturday_evening, tz=tz) == expected


def test_validation():
    assert RelaxSchedule().validation_error() is None
    assert RelaxSchedule(weekdays=set()).validation_error()
    assert RelaxSchedule(weekdays={0}).validation_error()
    assert RelaxSchedule(active_start=600, active_end=600).validation_error()
    assert RelaxSchedule(active_start=22 * 60, active_end=2 * 60).validation_error()
    assert RelaxSchedule(quiet_start=720).validation_error()
    assert RelaxSchedule(quiet_start=780, quiet_end=720).validation_error()
    assert RelaxSchedule(active_start=9 * 60, active_end=18 * 60, quiet_start=8 * 60, quiet_end=13 * 60).validation_error()
    assert RelaxSchedule(active_start=9 * 60, active_end=18 * 60, quiet_start=12 * 60, quiet_end=13 * 60).validation_error() is None


def test_active_seconds_skips_blocked_time():
    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=9 * 60, active_end=18 * 60, quiet_start=12 * 60, quiet_end=13 * 60)
    # Monday 8:00 to Monday 14:00: 9-12 and 13-14.
    assert s.active_seconds(at(8, 0), at(14, 0), tz=UTC) == 4 * 3600
    # Friday 17:00 to Monday 10:00: 1h Friday, nothing on the weekend, 1h Monday.
    assert s.active_seconds(at(17, 0, day=23), at(10, 0, day=26), tz=UTC) == 2 * 3600
    # A full week: 5 days x 8h.
    assert s.active_seconds(at(0, 0), at(0, 0, day=26), tz=UTC) == 40 * 3600


# ---- config shape, shared with macOS ----------------------------------------


def test_json_round_trip_uses_swift_keys():
    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=540, active_end=1080, quiet_start=720, quiet_end=780, align_to_clock=True)
    raw = s.to_json()
    assert raw == {
        "weekdays": [2, 3, 4, 5, 6],
        "activeStart": 540,
        "activeEnd": 1080,
        "quietStart": 720,
        "quietEnd": 780,
        "alignToClock": True,
    }
    assert RelaxSchedule.from_json(raw) == s


def test_json_leaves_out_unset_quiet_hours():
    assert "quietStart" not in RelaxSchedule().to_json()
    # What Swift writes for nil quiet hours: the keys are absent.
    assert RelaxSchedule.from_json({"weekdays": [1], "activeStart": 0, "activeEnd": 1440, "alignToClock": False}) == RelaxSchedule(weekdays={1})


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "weekdays",
        {"weekdays": "mon"},
        {"weekdays": [8]},
        {"weekdays": [True]},
        {"weekdays": [2], "activeStart": "9:00"},
        {"weekdays": [2], "activeStart": 600, "activeEnd": 540},
        {"weekdays": [2], "quietStart": 720},
    ],
)
def test_bad_json_is_none(raw):
    assert RelaxSchedule.from_json(raw) is None


def test_pause_reasons():
    from schedule import BEFORE_QUIET_HOURS, DONE_FOR_TODAY, OUTSIDE_HOURS, QUIET_HOURS

    s = RelaxSchedule(weekdays=WEEKDAYS, active_start=9 * 60, active_end=18 * 60, quiet_start=12 * 60, quiet_end=13 * 60)
    assert s.pause_reason(at(7, 30), tz=UTC) == OUTSIDE_HOURS
    assert s.pause_reason(at(18, 0), tz=UTC) == OUTSIDE_HOURS
    assert s.pause_reason(at(10, 0, day=24), tz=UTC) == OUTSIDE_HOURS  # Saturday
    assert s.pause_reason(at(12, 30), tz=UTC) == QUIET_HOURS
    assert s.pause_reason(at(11, 30), tz=UTC) == BEFORE_QUIET_HOURS
    assert s.pause_reason(at(17, 30), tz=UTC) == DONE_FOR_TODAY
