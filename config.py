"""Relax session configuration, persisted as JSON.

`%APPDATA%\\taplock\\relax-config.json`, using the same camelCase keys as the
macOS `RelaxingSessionConfig` so a config written on either platform is readable
by the other. Windows-only settings ride along in the same file: Swift's
JSONDecoder ignores keys it does not recognise.

`load()` never raises -- a missing, truncated or hand-edited file falls back to
defaults, per field where possible.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "taplock"
CONFIG_PATH = DATA_DIR / "relax-config.json"
# Exists while a session runs. Survives shutdown, logoff and crashes so the
# session can resume on next launch; removed when the session stops.
RUNNING_MARKER = DATA_DIR / "relax-running"

THEMES = ("breathing", "minimal", "mini")

# Transparency presets: label shown in the UI -> overlay opacity.
# 0% transparency is fully opaque, 90% is nearly see-through.
TRANSPARENCY = ((0, 1.0), (15, 0.85), (50, 0.50), (75, 0.25), (90, 0.10))

# Interval/break presets offered as one-tap buttons, in minutes.
PRESETS = ((25, 5), (45, 10), (50, 10))


@dataclass
class RelaxConfig:
    interval: int = 1500
    break_duration: int = 300
    theme: str = "breathing"
    color: str = "green"
    opacity: float = 0.85
    silent: bool = False
    show_posture_reminder: bool = True
    # Seconds between posture reminders while waiting; None keeps the single
    # reminder halfway through the interval. Shared with macOS.
    posture_interval: int | None = None
    # Windows-only; macOS ignores these keys.
    show_timer_in_tray: bool = False
    # Separate from show_timer_in_tray, which existing config files already carry
    # as false: the tooltip has always shown the countdown, so it stays on.
    show_timer_in_tooltip: bool = True
    launch_at_login: bool = False
    resume_on_launch: bool = False
    send_usage_stats: bool = True


# attribute -> on-disk key. The first seven must stay byte-identical to Swift.
_KEYS = {
    "interval": "interval",
    "break_duration": "breakDuration",
    "theme": "theme",
    "color": "color",
    "opacity": "opacity",
    "silent": "silent",
    "show_posture_reminder": "showPostureReminder",
    "posture_interval": "postureInterval",
    "show_timer_in_tray": "showTimerInTray",
    "show_timer_in_tooltip": "showTimerInTooltip",
    "launch_at_login": "launchAtLogin",
    "resume_on_launch": "resumeOnLaunch",
    "send_usage_stats": "sendUsageStats",
}


def load(path=CONFIG_PATH):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return RelaxConfig()
    if not isinstance(raw, dict):
        return RelaxConfig()

    config = RelaxConfig()
    for name, key in _KEYS.items():
        if key not in raw:
            continue
        if name == "posture_interval":
            # Optional: the type-of-default coercion below cannot handle None.
            value = raw[key]
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                config.posture_interval = value
            continue
        try:
            setattr(config, name, type(getattr(config, name))(raw[key]))
        except (TypeError, ValueError):
            pass

    if config.theme not in THEMES:
        config.theme = RelaxConfig.theme
    config.opacity = min(1.0, max(0.1, config.opacity))
    return config


def save(config, path=CONFIG_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: getattr(config, name) for name, key in _KEYS.items()}
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, path)  # atomic: a crash mid-write cannot truncate the config


def was_running(path=RUNNING_MARKER):
    return Path(path).exists()


def set_running(running, path=RUNNING_MARKER):
    path = Path(path)
    try:
        if running:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass  # resume simply does not happen; not worth failing a session over
