"""Duration and colour helpers -- port of TapLockCore/Parsers.swift.

Pure functions: no Qt, no I/O, no clock. Colours are kept as 0.0-1.0 triples
like the Swift original so the port stays auditable against it; `rgb255`
converts at the boundary where Qt and Pillow want bytes.
"""

import re

# DurationUnit.multiplier
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}

# OverlayColor presets offered in the UI, in the macOS order.
PRESET_COLORS = ("black", "white", "red", "blue", "green", "purple")

_NAMED_COLORS = {
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 0.8, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "orange": (1.0, 0.65, 0.0),
    "purple": (0.5, 0.0, 0.5),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
}


def parse_duration(text):
    """`"30"`, `"30s"`, `"2m"`, `"1h30m"` -> seconds. None if nothing valid parses."""
    trimmed = text.strip()
    if trimmed.isdigit():
        value = int(trimmed)
        return value if value > 0 else None

    total = 0
    matched = False
    for unit, multiplier in (("h", 3600), ("m", 60), ("s", 1)):
        found = re.search(rf"(\d+){unit}", trimmed)
        if found:
            total += int(found.group(1)) * multiplier
            matched = True
    return total if matched and total > 0 else None


def format_duration(seconds):
    """Human-readable length: `90` -> `"1m30s"`, `3600` -> `"1h"`."""
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        if minutes == 0 and secs == 0:
            return f"{hours}h"
        if secs == 0:
            return f"{hours}h{minutes}m"
        return f"{hours}h{minutes}m{secs}s"
    if seconds >= 60:
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}m" if secs == 0 else f"{minutes}m{secs}s"
    return f"{seconds}s"


def format_countdown(seconds):
    """Overlay countdown: minutes are dropped below 60s (`CountdownTimer.formattedTime`)."""
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes}:{secs:02d}" if minutes > 0 else str(secs)


def format_mmss(seconds):
    """Panel countdown: always `M:SS`, even under a minute."""
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes}:{secs:02d}"


def best_unit(seconds):
    """Largest unit that divides evenly -- how a saved config is shown in the panel."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return seconds // 3600, "h"
    if seconds >= 60 and seconds % 60 == 0:
        return seconds // 60, "m"
    return seconds, "s"


def luminance(rgb):
    """Perceived brightness of a 0.0-1.0 triple."""
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def parse_color(text):
    """Colour name or hex (`fff`, `#FF0000`) -> 0.0-1.0 triple. None if unparseable."""
    named = _NAMED_COLORS.get(text.strip().lower())
    if named is not None:
        return named

    clean = text.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return None
    try:
        value = int(clean, 16)
    except ValueError:
        return None
    return ((value >> 16 & 0xFF) / 255.0, (value >> 8 & 0xFF) / 255.0, (value & 0xFF) / 255.0)


def rgb255(rgb):
    """0.0-1.0 triple -> 0-255 ints, for Qt and Pillow."""
    return tuple(round(c * 255) for c in rgb)
