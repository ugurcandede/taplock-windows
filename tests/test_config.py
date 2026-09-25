import json

import config
from config import RelaxConfig


def test_round_trip(tmp_path):
    path = tmp_path / "relax-config.json"
    original = RelaxConfig(interval=2700, break_duration=600, theme="mini", color="blue", opacity=0.5, silent=True)
    config.save(original, path)
    assert config.load(path) == original


def test_uses_macos_key_names(tmp_path):
    path = tmp_path / "relax-config.json"
    config.save(RelaxConfig(), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["breakDuration"] == 300
    assert raw["showPostureReminder"] is True
    assert "break_duration" not in raw


def test_missing_file_returns_defaults(tmp_path):
    assert config.load(tmp_path / "absent.json") == RelaxConfig()


def test_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "relax-config.json"
    path.write_text("{not json", encoding="utf-8")
    assert config.load(path) == RelaxConfig()


def test_unknown_keys_are_ignored(tmp_path):
    """A config written by a newer macOS build must still load."""
    path = tmp_path / "relax-config.json"
    path.write_text(json.dumps({"interval": 60, "somethingNew": 42}), encoding="utf-8")
    loaded = config.load(path)
    assert loaded.interval == 60
    assert loaded.break_duration == RelaxConfig.break_duration


def test_bad_values_fall_back_per_field(tmp_path):
    path = tmp_path / "relax-config.json"
    path.write_text(json.dumps({"interval": "nope", "theme": "sparkles", "opacity": 5.0}), encoding="utf-8")
    loaded = config.load(path)
    assert loaded.interval == RelaxConfig.interval
    assert loaded.theme == "breathing"
    assert loaded.opacity == 1.0


def test_save_creates_directory(tmp_path):
    path = tmp_path / "nested" / "relax-config.json"
    config.save(RelaxConfig(), path)
    assert path.exists()


def test_posture_interval_round_trip(tmp_path):
    path = tmp_path / "relax-config.json"
    config.save(RelaxConfig(posture_interval=600), path)
    assert json.loads(path.read_text(encoding="utf-8"))["postureInterval"] == 600
    assert config.load(path).posture_interval == 600


def test_posture_interval_defaults_to_none(tmp_path):
    path = tmp_path / "relax-config.json"
    path.write_text(json.dumps({"postureInterval": None}), encoding="utf-8")
    assert config.load(path).posture_interval is None
    path.write_text(json.dumps({"postureInterval": "ten"}), encoding="utf-8")
    assert config.load(path).posture_interval is None


def test_running_marker(tmp_path):
    marker = tmp_path / "nested" / "relax-running"
    assert not config.was_running(marker)
    config.set_running(True, marker)
    assert config.was_running(marker)
    config.set_running(False, marker)
    assert not config.was_running(marker)


def test_tooltip_timer_stays_on_for_existing_configs(tmp_path):
    """Files written before this setting carry showTimerInTray: false; that
    must not switch the tooltip countdown off."""
    path = tmp_path / "relax-config.json"
    path.write_text(json.dumps({"interval": 60, "showTimerInTray": False}), encoding="utf-8")
    assert config.load(path).show_timer_in_tooltip is True


def test_usage_stats_default_on(tmp_path):
    assert config.load(tmp_path / "absent.json").send_usage_stats is True
