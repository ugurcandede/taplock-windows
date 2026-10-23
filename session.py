"""Relax session state machine -- port of TapLockCore/RelaxingSession.swift.

Swift schedules five separate `Timer`s (interval, break, pre-notify, posture,
posture auto-dismiss). Here one deadline set is checked by a single 1 Hz tick:
drift-free, and the panel can read `remaining` at any moment.

The session does not own that timer. It exposes `tick()` and the app wires a
QTimer to it, which keeps the whole state machine drivable by hand in tests --
no QApplication, no waiting.

Clocks are injected for the same reason: `monotonic` measures deadlines, `wall`
stamps stats events and places scheduled breaks (`config.schedule`), and `tz`
is the zone the schedule's clock times are read in (None: the system's).
"""

import math
import time
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, Signal

import stats

IDLE = "idle"
WAITING = "waiting"
BREAK = "break"

POSTURE_MIN_INTERVAL = 10
POSTURE_VISIBLE = 10  # posture reminder auto-dismiss

# Ticking at 1 Hz, a deadline is never more than a second or so overdue. Finding
# it further behind than this means we were not running at all -- system sleep,
# most likely. Firing a break hours late is worse than skipping it, so the
# interval starts over instead. This covers both possible Windows behaviours:
# whether or not the monotonic clock advances while suspended. While waiting, the
# wall clock pulling away from the monotonic one by more than this (a sleep the
# monotonic clock did not count, or the clock being set) re-places the break at
# its wall-clock time, matching the macOS wake handling.
MISSED_TICK_LIMIT = 30


class RelaxSession(QObject):
    state_changed = Signal()
    break_started = Signal()
    break_ended = Signal()
    posture_due = Signal()
    posture_dismissed = Signal()
    play_sound = Signal(str)  # "start" | "end"
    # macOS also chimes ten seconds before a break. Dropped here: two sounds a
    # cycle is already the most a background app should ask for.

    def __init__(self, monotonic=time.monotonic, wall=None, tz=None, parent=None):
        super().__init__(parent)
        self._monotonic = monotonic
        self._wall = wall or (lambda: datetime.now(timezone.utc))
        self._tz = tz

        self._state = IDLE
        self._config = None
        self._deadline = 0.0
        self._wait_started = 0.0
        # Wall-clock time of the pending break, and wall minus monotonic when it
        # was placed -- how tick() notices the two clocks drifting apart.
        self._next_break = None
        self._wall_offset = 0.0
        self._posture_at = None
        self._posture_off_at = None
        self._session_started_at = None
        self._break_started_at = None
        self._breaks_taken = 0
        # "manual" for break now, "timer" otherwise; read when break_started fires.
        self.break_trigger = "timer"

    # ---- public state ----------------------------------------------------

    @property
    def state(self):
        return self._state

    @property
    def running(self):
        return self._state != IDLE

    @property
    def on_break(self):
        return self._state == BREAK

    @property
    def remaining(self):
        """Seconds left in the current phase, rounded up so a fresh session
        shows its full duration."""
        if self._state == IDLE:
            return 0
        return max(0, math.ceil(self._deadline - self._monotonic()))

    @property
    def next_break(self):
        """When the pending break starts (aware UTC); None unless waiting."""
        return self._next_break if self._state == WAITING else None

    @property
    def config(self):
        return self._config

    # ---- lifecycle -------------------------------------------------------

    def start(self, config):
        if self._state != IDLE:
            return
        self._config = config
        self._session_started_at = self._wall()
        self._breaks_taken = 0
        stats.append(
            "relax_session_started",
            self._session_started_at,
            interval_seconds=config.interval,
            break_seconds=config.break_duration,
            theme=config.theme,
        )
        self._begin_waiting()
        self.state_changed.emit()

    def stop(self):
        if self._state == IDLE:
            return
        if self._state == BREAK:
            self._end_break(skipped=True)
        self._dismiss_posture()

        ended_at = self._wall()
        schedule = self._config.schedule
        # Time outside the schedule's hours isn't session time.
        if schedule is not None:
            duration = schedule.active_seconds(self._session_started_at, ended_at, tz=self._tz)
        else:
            duration = int((ended_at - self._session_started_at).total_seconds())
        stats.append(
            "relax_session_ended",
            ended_at,
            duration_seconds=duration,
            breaks_taken=self._breaks_taken,
        )

        self._state = IDLE
        self._config = None
        self._session_started_at = None
        self.state_changed.emit()

    def skip_break(self):
        if self._state != BREAK:
            return
        self._end_break(skipped=True)
        self._begin_waiting()
        self.state_changed.emit()

    def start_break_now(self):
        """Start the upcoming break instead of waiting for the interval."""
        if self._state != WAITING:
            return
        self._start_break(trigger="manual")
        self.state_changed.emit()

    def restart_countdown(self):
        """Discard the running countdown and wait a full interval again."""
        if self._state != WAITING:
            return
        self._dismiss_posture()
        self._begin_waiting()
        self.state_changed.emit()

    def dismiss_posture(self):
        self._dismiss_posture()

    def settings_changed(self):
        """Re-plan the posture reminder after the shared config was edited, so
        the change applies to the current wait rather than the next one."""
        if self._state != WAITING:
            return
        if not self._config.show_posture_reminder:
            self._dismiss_posture()
        self._plan_posture(self._monotonic())

    # ---- the tick --------------------------------------------------------

    def tick(self):
        if self._state == IDLE:
            return
        now = self._monotonic()

        if now - self._deadline > MISSED_TICK_LIMIT:
            if self._state == BREAK:
                self._end_break(skipped=False)
            self._dismiss_posture()
            self._begin_waiting()
            self.state_changed.emit()
            return

        if self._state == WAITING and abs(self._wall().timestamp() - now - self._wall_offset) > MISSED_TICK_LIMIT:
            self._resync(now)
            self.state_changed.emit()
            return

        if self._posture_off_at is not None and now >= self._posture_off_at:
            self._dismiss_posture()

        if self._posture_at is not None and now >= self._posture_at:
            every = self._config.posture_interval
            following = self._posture_at + every if every else None
            self._posture_at = following if following is not None and following < self._deadline else None
            self._posture_off_at = now + POSTURE_VISIBLE
            self.posture_due.emit()

        if now >= self._deadline:
            if self._state == WAITING:
                self._start_break()
            else:
                self._end_break(skipped=False)
                self._begin_waiting()

        self.state_changed.emit()

    # ---- phases ----------------------------------------------------------

    def _begin_waiting(self):
        now = self._monotonic()
        wall_now = self._wall()
        config = self._config
        self._state = WAITING
        self._wait_started = now
        upcoming = None
        if config.schedule is not None:
            upcoming = config.schedule.next_break(wall_now, config.interval, config.break_duration, tz=self._tz)
        # No schedule, or one that allows no break (the panel refuses to start those).
        self._next_break = upcoming or wall_now + timedelta(seconds=config.interval)
        self._place_deadline(now, wall_now)
        self._plan_posture(now)

    def _place_deadline(self, now, wall_now):
        self._deadline = now + (self._next_break - wall_now).total_seconds()
        self._wall_offset = wall_now.timestamp() - now

    def _resync(self, now):
        """Keep the break at its wall-clock time, or start a fresh wait if it
        passed while we were not running (being away counts as the break)."""
        wall_now = self._wall()
        if self._next_break > wall_now:
            self._place_deadline(now, wall_now)
            self._plan_posture(now)
        else:
            self._dismiss_posture()
            self._begin_waiting()

    def _plan_posture(self, now):
        """Next posture reminder of the current wait, measured from its start so
        a mid-wait config change stays aligned with the countdown.

        A schedule can stretch the wait over off-hours, so the wait counts from
        at most `interval` before the break -- no reminder in the middle of the
        night. Without a schedule that is the start of the wait."""
        config = self._config
        self._posture_at = None
        if not config.show_posture_reminder:
            return
        start = max(self._wait_started, self._deadline - config.interval)
        every = config.posture_interval
        if every:
            # Repeats every posture_interval until the break; see tick().
            if every < config.interval:
                following = start + max(1, math.floor((now - start) / every) + 1) * every
                if following < self._deadline:
                    self._posture_at = following
        elif config.interval > POSTURE_MIN_INTERVAL:
            halfway = start + (self._deadline - start) / 2
            if halfway > now:
                self._posture_at = halfway

    def _start_break(self, trigger="timer"):
        self.break_trigger = trigger
        self._dismiss_posture()
        self._state = BREAK
        self._break_started_at = self._wall()
        self._deadline = self._monotonic() + self._config.break_duration
        self._posture_at = None
        if not self._config.silent:
            self.play_sound.emit("start")
        self.break_started.emit()

    def _end_break(self, skipped):
        planned = self._config.break_duration
        elapsed = int((self._wall() - self._break_started_at).total_seconds())
        stats.append(
            "relax_break",
            self._break_started_at,
            planned_seconds=planned,
            # Clamped: a break the machine slept through must not report hours.
            actual_seconds=min(planned, max(0, elapsed)),
            theme=self._config.theme,
            skipped_early=skipped,
        )
        self._breaks_taken += 1
        self._break_started_at = None
        if not self._config.silent:
            self.play_sound.emit("end")
        self.break_ended.emit()

    def _dismiss_posture(self):
        if self._posture_off_at is None:
            return
        self._posture_off_at = None
        self.posture_dismissed.emit()
