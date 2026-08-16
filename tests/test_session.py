from datetime import datetime, timedelta, timezone

import pytest

import session as session_module
from config import RelaxConfig
from session import BREAK, IDLE, WAITING, RelaxSession


class Clock:
    """Hand-cranked monotonic + wall clock so the whole loop runs without Qt."""

    def __init__(self):
        self.mono = 1000.0
        self.wall = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)

    def jump_monotonic(self, seconds):
        """Time passes for the monotonic clock only -- what a suspended machine
        looks like if Windows keeps the counter running."""
        self.mono += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def events(monkeypatch):
    """Capture stats writes instead of touching %APPDATA%."""
    captured = []
    monkeypatch.setattr(
        session_module.stats, "append", lambda kind, moment, **fields: captured.append((kind, moment, fields))
    )
    return captured


@pytest.fixture
def relax(clock, events):
    return RelaxSession(monotonic=lambda: clock.mono, wall=lambda: clock.wall)


def config(**overrides):
    base = dict(interval=100, break_duration=20, show_posture_reminder=False, silent=True)
    base.update(overrides)
    return RelaxConfig(**base)


def run_for(relax, clock, seconds, step=1):
    for _ in range(int(seconds / step)):
        clock.advance(step)
        relax.tick()


# ---- lifecycle -----------------------------------------------------------


def test_starts_idle(relax):
    assert relax.state == IDLE
    assert not relax.running
    assert relax.remaining == 0


def test_start_enters_waiting_with_full_interval(relax, clock):
    relax.start(config())
    assert relax.state == WAITING
    assert relax.remaining == 100


def test_start_is_ignored_while_running(relax, clock):
    relax.start(config())
    relax.start(config(interval=999))
    assert relax.config.interval == 100


def test_interval_elapses_into_a_break(relax, clock):
    relax.start(config())
    run_for(relax, clock, 99)
    assert relax.state == WAITING
    run_for(relax, clock, 1)
    assert relax.state == BREAK
    assert relax.remaining == 20


def test_break_elapses_back_into_the_interval(relax, clock):
    relax.start(config())
    run_for(relax, clock, 120)
    assert relax.state == WAITING
    assert relax.remaining == 100


def test_skip_ends_the_break_and_restarts_the_interval(relax, clock):
    relax.start(config())
    run_for(relax, clock, 100)
    assert relax.state == BREAK
    clock.advance(5)
    relax.skip_break()
    assert relax.state == WAITING
    assert relax.remaining == 100


def test_skip_outside_a_break_does_nothing(relax, clock):
    relax.start(config())
    relax.skip_break()
    assert relax.state == WAITING


def test_stop_returns_to_idle(relax, clock):
    relax.start(config())
    run_for(relax, clock, 10)
    relax.stop()
    assert relax.state == IDLE
    assert relax.remaining == 0


def test_tick_while_idle_is_a_no_op(relax, clock):
    relax.tick()
    assert relax.state == IDLE


# ---- signals -------------------------------------------------------------


def test_break_signals(relax, clock):
    seen = []
    relax.break_started.connect(lambda: seen.append("start"))
    relax.break_ended.connect(lambda: seen.append("end"))
    relax.start(config())
    run_for(relax, clock, 120)
    assert seen == ["start", "end"]


def test_silent_makes_no_sound(relax, clock):
    sounds = []
    relax.play_sound.connect(sounds.append)
    relax.start(config(silent=True))
    run_for(relax, clock, 120)
    assert sounds == []


def test_nothing_sounds_before_a_break(relax, clock):
    """macOS chimes ten seconds ahead; this build only marks the boundaries."""
    sounds = []
    relax.play_sound.connect(sounds.append)
    relax.start(config(silent=False))
    run_for(relax, clock, 99)
    assert sounds == []


def test_break_sounds(relax, clock):
    sounds = []
    relax.play_sound.connect(sounds.append)
    relax.start(config(interval=12, break_duration=5, silent=False))
    run_for(relax, clock, 17)
    assert sounds == ["start", "end"]


# ---- posture reminder ----------------------------------------------------


def test_posture_fires_at_half_the_interval(relax, clock):
    seen = []
    relax.posture_due.connect(lambda: seen.append("due"))
    relax.start(config(show_posture_reminder=True))
    run_for(relax, clock, 49)
    assert seen == []
    run_for(relax, clock, 1)
    assert seen == ["due"]


def test_posture_auto_dismisses_after_ten_seconds(relax, clock):
    seen = []
    relax.posture_dismissed.connect(lambda: seen.append("gone"))
    relax.start(config(show_posture_reminder=True))
    run_for(relax, clock, 50)
    assert seen == []
    run_for(relax, clock, 10)
    assert seen == ["gone"]


def test_posture_is_dismissed_when_a_break_starts(relax, clock):
    seen = []
    relax.posture_dismissed.connect(lambda: seen.append("gone"))
    # Posture lands at t=48, the break at t=96 -- but the reminder is still up
    # only if its 10s window has not passed, so use a short interval.
    relax.start(config(interval=16, break_duration=5, show_posture_reminder=True))
    run_for(relax, clock, 8)
    run_for(relax, clock, 8)
    assert relax.state == BREAK
    assert seen == ["gone"]


def test_posture_disabled(relax, clock):
    seen = []
    relax.posture_due.connect(lambda: seen.append("due"))
    relax.start(config(show_posture_reminder=False))
    run_for(relax, clock, 100)
    assert seen == []


def test_no_posture_on_very_short_intervals(relax, clock):
    seen = []
    relax.posture_due.connect(lambda: seen.append("due"))
    relax.start(config(interval=10, break_duration=3, show_posture_reminder=True))
    run_for(relax, clock, 10)
    assert seen == []


# ---- sleep / resume ------------------------------------------------------


def test_long_gap_restarts_the_interval_instead_of_firing_a_stale_break(relax, clock):
    relax.start(config())
    run_for(relax, clock, 10)
    clock.jump_monotonic(7200)  # laptop lid closed for two hours
    relax.tick()
    assert relax.state == WAITING
    assert relax.remaining == 100


def test_short_gap_still_fires_the_break(relax, clock):
    relax.start(config())
    run_for(relax, clock, 90)
    clock.jump_monotonic(20)  # below MISSED_TICK_LIMIT
    relax.tick()
    assert relax.state == BREAK


def test_gap_during_a_break_ends_it(relax, clock):
    relax.start(config())
    run_for(relax, clock, 100)
    assert relax.state == BREAK
    clock.jump_monotonic(7200)
    relax.tick()
    assert relax.state == WAITING


# ---- stats ---------------------------------------------------------------


def test_session_start_event(relax, clock, events):
    relax.start(config())
    kind, moment, fields = events[0]
    assert kind == "relax_session_started"
    assert fields == {"interval_seconds": 100, "break_seconds": 20, "theme": "breathing"}
    assert moment == clock.wall


def test_completed_break_event(relax, clock, events):
    relax.start(config())
    run_for(relax, clock, 120)
    kind, _, fields = events[1]
    assert kind == "relax_break"
    assert fields["planned_seconds"] == 20
    assert fields["actual_seconds"] == 20
    assert fields["skipped_early"] is False


def test_skipped_break_event_records_the_short_duration(relax, clock, events):
    relax.start(config())
    run_for(relax, clock, 100)
    clock.advance(6)
    relax.skip_break()
    kind, _, fields = events[1]
    assert kind == "relax_break"
    assert fields["actual_seconds"] == 6
    assert fields["skipped_early"] is True


def test_slept_break_duration_is_clamped_to_planned(relax, clock, events):
    relax.start(config())
    run_for(relax, clock, 100)
    clock.advance(7200)  # wall clock moved too, as it would across a suspend
    relax.tick()
    _, _, fields = events[1]
    assert fields["actual_seconds"] == 20


def test_session_end_event_counts_breaks(relax, clock, events):
    relax.start(config())
    run_for(relax, clock, 240)  # two full break cycles
    relax.stop()
    kind, _, fields = events[-1]
    assert kind == "relax_session_ended"
    assert fields["breaks_taken"] == 2
    assert fields["duration_seconds"] == 240


def test_stopping_during_a_break_logs_it_as_skipped(relax, clock, events):
    relax.start(config())
    run_for(relax, clock, 100)
    clock.advance(3)
    relax.stop()
    kinds = [e[0] for e in events]
    assert kinds == ["relax_session_started", "relax_break", "relax_session_ended"]
    assert events[1][2]["skipped_early"] is True


def test_stop_while_idle_writes_nothing(relax, events):
    relax.stop()
    assert events == []
