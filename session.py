"""Relax session state machine -- port of TapLockCore/RelaxingSession.swift.

Swift schedules five separate `Timer`s (interval, break, pre-notify, posture,
posture auto-dismiss). Here one deadline set is checked by a single 1 Hz tick:
drift-free, and the panel can read `remaining` at any moment.

The session does not own that timer. It exposes `tick()` and the app wires a
QTimer to it, which keeps the whole state machine drivable by hand in tests --
no QApplication, no waiting.

Clocks are injected for the same reason: `monotonic` measures deadlines, `wall`
stamps stats events.
"""

import math
import time
from datetime import datetime, timezone

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
# whether or not the monotonic clock advances while suspended.
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

    def __init__(self, monotonic=time.monotonic, wall=None, parent=None):
        super().__init__(parent)
        self._monotonic = monotonic
        self._wall = wall or (lambda: datetime.now(timezone.utc))

        self._state = IDLE
        self._config = None
        self._deadline = 0.0
        self._wait_started = 0.0
        self._posture_at = None
        self._posture_off_at = None
        self._session_started_at = None
        self._break_started_at = None
        self._breaks_taken = 0

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
        stats.append(
            "relax_session_ended",
            ended_at,
            duration_seconds=int((ended_at - self._session_started_at).total_seconds()),
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
        self._start_break()
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
        config = self._config
        self._state = WAITING
        self._wait_started = now
        self._deadline = now + config.interval
        self._plan_posture(now)

    def _plan_posture(self, now):
        """Next posture reminder of the current wait, measured from its start so
        a mid-wait config change stays aligned with the countdown."""
        config = self._config
        self._posture_at = None
        if not config.show_posture_reminder:
            return
        every = config.posture_interval
        if every:
            # Repeats every posture_interval until the break; see tick().
            if every < config.interval:
                following = self._wait_started + (math.floor((now - self._wait_started) / every) + 1) * every
                if following < self._deadline:
                    self._posture_at = following
        elif config.interval > POSTURE_MIN_INTERVAL:
            halfway = self._wait_started + config.interval / 2
            if halfway > now:
                self._posture_at = halfway

    def _start_break(self):
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
