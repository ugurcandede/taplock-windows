"""Theme and posture previews against a running session, driven through TrayApp
with fake overlays and panel so nothing is shown or written to disk."""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import main
import session as session_module
from config import RelaxConfig
from schedule import RelaxSchedule
from session import BREAK, WAITING, RelaxSession


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.wall = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


class FakeOverlays(QObject):
    skipped = Signal()
    posture_dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = None
        self.remaining = None
        self.posture = False

    @property
    def visible(self):
        return self.theme is not None

    def open(self, config):
        self.theme = config.theme
        self.remaining = None

    def set_remaining(self, seconds):
        self.remaining = seconds

    def close(self):
        self.theme = None

    def open_posture(self):
        self.posture = True

    def close_posture(self):
        self.posture = False


class FakePanel(QObject):
    preview_theme = Signal()
    preview_posture = Signal()
    open_statistics = Signal()
    install_update = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()

    def __getattr__(self, name):
        return lambda *args: None  # apply_theme, set_update, ...


class FakeUpdates(QObject):
    found = Signal(object)
    installed = Signal()
    install_failed = Signal()

    def check(self):
        pass

    def install(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cfg():
    return RelaxConfig(interval=100, break_duration=20, silent=True, show_posture_reminder=True)


@pytest.fixture
def tray(qapp, clock, cfg, monkeypatch):
    monkeypatch.setattr(session_module.stats, "append", lambda *a, **k: None)
    monkeypatch.setattr(main, "RelaxSession", lambda: RelaxSession(monotonic=lambda: clock.mono, wall=lambda: clock.wall, tz=timezone.utc))
    monkeypatch.setattr(main, "OverlayController", FakeOverlays)
    monkeypatch.setattr(main, "Panel", FakePanel)
    monkeypatch.setattr(main, "UpdateChecker", FakeUpdates)
    monkeypatch.setattr(main.updates, "can_self_update", lambda: False)
    monkeypatch.setattr(main.config, "load", lambda: cfg)
    monkeypatch.setattr(main.config, "was_running", lambda: False)
    monkeypatch.setattr(main.config, "set_running", lambda running: None)
    for name in ("start", "set_user_properties", "track", "set_last_mode", "heartbeat", "app_will_terminate", "ping_if_due"):
        monkeypatch.setattr(main.analytics, name, lambda *a, **k: None)
    app = main.TrayApp(qapp)
    yield app
    app._tray.hide()
    for timer in (app._timer, app._analytics_timer, app._heartbeat_timer, app._update_timer):
        timer.stop()


def run_for(tray, clock, seconds):
    for _ in range(seconds):
        clock.advance(1)
        tray._tick()


def expire(timer):
    assert timer.isActive()
    timer.stop()
    timer.timeout.emit()


# ---- previews while waiting ------------------------------------------------


def test_theme_preview_shows_while_waiting(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_theme.emit()
    assert tray._overlays.theme == cfg.theme
    expire(tray._preview_timer)
    assert not tray._overlays.visible


def test_theme_preview_keeps_break_length_while_ticking(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_theme.emit()
    run_for(tray, clock, 3)
    assert tray._overlays.remaining == 20  # not the 97 s left until the break


def test_posture_preview_shows_while_waiting(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_posture.emit()
    assert tray._overlays.posture
    expire(tray._posture_preview_timer)
    assert not tray._overlays.posture


def test_previews_show_outside_schedule_hours(tray, clock, cfg):
    # 09:00 with hours 22:00-23:00: the session waits, paused.
    cfg.schedule = RelaxSchedule(active_start=22 * 60, active_end=23 * 60)
    tray._session.start(cfg)
    assert tray._session.paused_until is not None
    tray._panel.preview_theme.emit()
    tray._panel.preview_posture.emit()
    assert tray._overlays.visible and tray._overlays.posture


def test_previews_still_show_when_idle(tray):
    tray._panel.preview_theme.emit()
    tray._panel.preview_posture.emit()
    assert tray._overlays.visible and tray._overlays.posture


# ---- refused during a break -------------------------------------------------


def test_previews_are_refused_during_a_break(tray, clock, cfg):
    tray._session.start(cfg)
    tray._session.start_break_now()
    tray._overlays.close_posture()
    tray._panel.preview_theme.emit()
    tray._panel.preview_posture.emit()
    assert not tray._preview_timer.isActive()
    assert not tray._posture_preview_timer.isActive()
    assert not tray._overlays.posture


# ---- the real thing arriving mid-preview -----------------------------------


def test_break_mid_theme_preview_takes_over(tray, clock, cfg):
    cfg.show_posture_reminder = False
    tray._session.start(cfg)
    run_for(tray, clock, 97)
    tray._panel.preview_theme.emit()
    run_for(tray, clock, 3)
    assert tray._session.state == BREAK
    assert not tray._preview_timer.isActive()
    assert tray._overlays.remaining == 20
    run_for(tray, clock, 5)
    assert tray._overlays.visible  # the preview's 5 s did not close the break
    assert tray._overlays.remaining == 15


def test_skip_after_break_took_over_skips_the_break(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_theme.emit()
    tray._session.start_break_now()
    tray._overlays.skipped.emit()
    assert tray._session.state == WAITING
    assert not tray._overlays.visible


def test_break_mid_posture_preview_closes_it(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_posture.emit()
    tray._session.start_break_now()
    assert not tray._overlays.posture
    assert not tray._posture_preview_timer.isActive()


def test_real_posture_mid_theme_preview_outlives_it(tray, clock, cfg):
    tray._session.start(cfg)
    run_for(tray, clock, 48)
    tray._panel.preview_theme.emit()
    run_for(tray, clock, 2)  # halfway: the real reminder appears
    assert tray._overlays.posture
    expire(tray._preview_timer)
    assert tray._overlays.posture
    run_for(tray, clock, 10)  # the session's own 10 s auto-dismiss
    assert not tray._overlays.posture


def test_real_posture_mid_posture_preview_keeps_its_full_time(tray, clock, cfg):
    tray._session.start(cfg)
    run_for(tray, clock, 48)
    tray._panel.preview_posture.emit()
    run_for(tray, clock, 2)
    assert not tray._posture_preview_timer.isActive()
    run_for(tray, clock, 9)
    assert tray._overlays.posture
    run_for(tray, clock, 1)
    assert not tray._overlays.posture


# ---- previews leave the session alone ---------------------------------------


def test_skip_on_theme_preview_does_not_touch_the_session(tray, clock, cfg):
    tray._session.start(cfg)
    run_for(tray, clock, 10)
    tray._panel.preview_theme.emit()
    tray._overlays.skipped.emit()
    assert not tray._overlays.visible
    assert tray._session.state == WAITING
    assert tray._session.remaining == 90


def test_got_it_on_posture_preview_keeps_the_real_reminder_planned(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_posture.emit()
    tray._overlays.posture_dismissed.emit()
    assert not tray._overlays.posture
    assert not tray._posture_preview_timer.isActive()
    run_for(tray, clock, 50)
    assert tray._overlays.posture


def test_stopping_mid_preview_lets_it_run_out(tray, clock, cfg):
    tray._session.start(cfg)
    tray._panel.preview_theme.emit()
    tray._session.stop()
    assert tray._overlays.visible
    expire(tray._preview_timer)
    assert not tray._overlays.visible
