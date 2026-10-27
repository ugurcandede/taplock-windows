"""Advanced relax schedule -- port of TapLockCore/RelaxSchedule.swift.

Optional wall-clock rules for a relax session: active days and hours, a quiet
window (e.g. lunch) and fixed break times. A session without a schedule keeps
the plain behavior: a break every `interval` seconds after the last one.

Weekdays use the Swift numbering (1 = Sunday ... 7 = Saturday), not
`isoweekday()`, because the config file is shared with macOS. Times are minutes
from midnight; ranges never cross midnight and an end may be 24 * 60.

Instants are aware UTC datetimes, so adding seconds is exact. Clock times are
built in local time -- the system zone, or `tz` in tests -- by setting the hour
and minute rather than adding, so a DST day keeps them.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DAY = 24 * 60

# Why a schedule holds the next break back; RelaxPauseReason in Swift.
OUTSIDE_HOURS = "outside_hours"  # an off day, or before or after the hours
QUIET_HOURS = "quiet_hours"
BEFORE_QUIET_HOURS = "before_quiet_hours"  # no full interval fits before quiet hours
DONE_FOR_TODAY = "done_for_today"  # no full interval fits before the hours end


@dataclass
class RelaxSchedule:
    weekdays: set = field(default_factory=lambda: set(range(1, 8)))
    active_start: int = 0
    active_end: int = DAY
    quiet_start: int | None = None
    quiet_end: int | None = None
    align_to_clock: bool = False

    # ---- config ----------------------------------------------------------

    def to_json(self):
        """Same keys as the Swift Codable; quiet keys are left out when unset."""
        raw = {
            "weekdays": sorted(self.weekdays),
            "activeStart": self.active_start,
            "activeEnd": self.active_end,
            "alignToClock": self.align_to_clock,
        }
        if self.quiet_start is not None and self.quiet_end is not None:
            raw["quietStart"] = self.quiet_start
            raw["quietEnd"] = self.quiet_end
        return raw

    @classmethod
    def from_json(cls, raw):
        """None for anything malformed or invalid, so `config.load()` can fall back."""
        if not isinstance(raw, dict):
            return None

        def minute(key, default=None):
            value = raw.get(key, default)
            if value is None or (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= DAY):
                return value
            raise ValueError(key)

        try:
            days = raw.get("weekdays")
            if not isinstance(days, list) or not all(isinstance(d, int) and not isinstance(d, bool) for d in days):
                return None
            schedule = cls(
                weekdays=set(days),
                active_start=minute("activeStart", 0),
                active_end=minute("activeEnd", DAY),
                quiet_start=minute("quietStart"),
                quiet_end=minute("quietEnd"),
                align_to_clock=bool(raw.get("alignToClock", False)),
            )
        except ValueError:
            return None
        return None if schedule.validation_error() else schedule

    def validation_error(self):
        """Why the schedule can't be used, or None. It can still allow no break
        at all (e.g. interval longer than the hours); `next_break` tells."""
        if not self.weekdays:
            return "Pick at least one day"
        if not self.weekdays <= set(range(1, 8)):
            return "Weekdays must be 1 (Sunday) to 7 (Saturday)"
        if not (0 <= self.active_start < self.active_end <= DAY):
            return "Hours must start before they end"
        if (self.quiet_start is None) != (self.quiet_end is None):
            return "Quiet hours need a start and an end"
        if self.quiet_start is not None:
            if self.quiet_start >= self.quiet_end:
                return "Quiet hours must start before they end"
            if self.quiet_start < self.active_start or self.quiet_end > self.active_end:
                return "Quiet hours must be within active hours"
        return None

    # ---- scheduling ------------------------------------------------------

    def next_break(self, after, interval, break_duration, tz=None):
        """When the next break should start for a wait that began at `after`.
        A wait that runs into an off day, the end of the active window or the
        quiet window restarts in full when that block ends. None if no break can
        ever fire."""
        if self.validation_error():
            return None
        resume = after.astimezone(timezone.utc)
        # Each pass either returns or jumps to the start or end of a blocked
        # range; at most four per day over a week bounds the loop.
        for _ in range(64):
            until = self._blocked_until(resume, tz)
            if until is not None:
                resume = until
                continue
            if self.align_to_clock:
                candidate = self._grid_point(resume, interval, break_duration, tz)
            else:
                candidate = resume + timedelta(seconds=interval)
            block_start = self._next_block_start(resume, tz)
            if block_start is None or candidate < block_start:
                return candidate
            resume = block_start
        return None

    def pause_reason(self, at, tz=None):
        """Why breaks are held back at `at`, for a session whose next break is
        more than an interval away."""
        local = _local(at, tz)
        minute = _second_of_day(local) // 60
        if _weekday(local) not in self.weekdays or not self.active_start <= minute < self.active_end:
            return OUTSIDE_HOURS
        if self.quiet_start is not None:
            if self.quiet_start <= minute < self.quiet_end:
                return QUIET_HOURS
            if minute < self.quiet_start:
                return BEFORE_QUIET_HOURS
        return DONE_FOR_TODAY

    def active_seconds(self, start, end, tz=None):
        """Seconds of [start, end) inside active, non-quiet time."""
        if self.validation_error() or start >= end:
            return 0
        ranges = [(self.active_start, self.active_end)]
        if self.quiet_start is not None:
            ranges = [(self.active_start, self.quiet_start), (self.quiet_end, self.active_end)]
        total = 0.0
        day = _midnight(_local(start, tz))
        while _instant(day, tz) < end:
            if _weekday(day) in self.weekdays:
                for lo_minute, hi_minute in ranges:
                    lo = max(start, _at(lo_minute * 60, day, tz))
                    hi = min(end, _at(hi_minute * 60, day, tz))
                    if lo < hi:
                        total += (hi - lo).total_seconds()
            day += timedelta(days=1)
        return int(total)

    def _grid_point(self, moment, interval, break_duration, tz):
        """First grid point strictly after `moment`. The grid is anchored to the
        day's active start so the rhythm matches simple mode, just pinned to the
        clock. Computed in wall-clock seconds so a DST change doesn't shift it."""
        local = _local(moment, tz)
        anchor = self.active_start * 60 + interval
        now = _second_of_day(local)
        if now < anchor:
            return _at(anchor, local, tz)
        period = interval + break_duration
        point = anchor + ((now - anchor) // period + 1) * period
        if point < DAY * 60:
            return _at(point, local, tz)
        return _at(anchor, _midnight(local) + timedelta(days=1), tz)

    def _next_block_start(self, moment, tz):
        """Start of the first blocked range after an unblocked `moment`: the
        quiet window if it is still ahead, otherwise the end of the active
        window. With 0:00-24:00 hours, midnight is only a block if the next day
        is off. None if nothing ever blocks."""
        day = _local(moment, tz)
        minute = _second_of_day(day) // 60
        for _ in range(8):
            # `moment` is unblocked, so on its own day minute == quiet_start can't happen.
            if self.quiet_start is not None and minute <= self.quiet_start:
                return _at(self.quiet_start * 60, day, tz)
            tomorrow = _midnight(day) + timedelta(days=1)
            end = _at(self.active_end * 60, day, tz)
            if not (self.active_start == 0 and self.active_end == DAY and _weekday(tomorrow) in self.weekdays):
                return end
            day = tomorrow
            minute = 0
        return None

    def _blocked_until(self, moment, tz):
        """None if a break may fire at `moment`; otherwise when the block ends."""
        local = _local(moment, tz)
        minute = _second_of_day(local) // 60
        active_today = _weekday(local) in self.weekdays

        if active_today and minute < self.active_start:
            return _at(self.active_start * 60, local, tz)
        if not active_today or minute >= self.active_end:
            day = _midnight(local)
            # validation_error() guarantees an active day within the week.
            while True:
                day += timedelta(days=1)
                if _weekday(day) in self.weekdays:
                    return _at(self.active_start * 60, day, tz)
        if self.quiet_start is not None and self.quiet_start <= minute < self.quiet_end:
            return _at(self.quiet_end * 60, local, tz)
        return None


# ---- local time ------------------------------------------------------------
# Local values are naive datetimes; naive arithmetic is wall-clock arithmetic.


def _local(moment, tz):
    """Naive local wall time of an instant. astimezone(None) is the system zone."""
    return moment.astimezone(tz).replace(tzinfo=None)


def _instant(local, tz):
    """UTC instant of a naive local wall time. A naive astimezone() reads it as
    system local time, with that date's DST rules."""
    aware = local.replace(tzinfo=tz) if tz is not None else local.astimezone()
    return aware.astimezone(timezone.utc)


def _midnight(local):
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _second_of_day(local):
    return local.hour * 3600 + local.minute * 60 + local.second


def _weekday(local):
    """Swift numbering: 1 = Sunday ... 7 = Saturday."""
    return local.isoweekday() % 7 + 1


def _at(second, day, tz):
    """Instant `second` seconds after midnight on `day`'s local date; 24:00 is
    the next midnight."""
    midnight = _midnight(day)
    if second >= DAY * 60:
        return _instant(midnight + timedelta(days=1), tz)
    return _instant(midnight.replace(hour=second // 3600, minute=second % 3600 // 60, second=second % 60), tz)
