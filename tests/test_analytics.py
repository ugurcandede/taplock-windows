import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import analytics

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class FakePost:
    def __init__(self, status=204):
        self.status = status
        self.bodies = []

    def __call__(self, url, body):
        self.bodies.append(body)
        return self.status

    def names(self):
        return [b["events"][0]["name"] for b in self.bodies]


@pytest.fixture
def post(tmp_path, monkeypatch):
    fake = FakePost()
    monkeypatch.setattr(analytics, "STATE_PATH", tmp_path / "analytics.json")
    monkeypatch.setattr(analytics, "_post", fake)
    # Send inline instead of on a timer thread.
    monkeypatch.setattr(analytics, "_schedule_flush", lambda: analytics.flush())
    monkeypatch.setattr(analytics, "_started", False)
    monkeypatch.setattr(analytics, "_app_user_properties", {})
    return fake


def state():
    return json.loads(analytics.STATE_PATH.read_text(encoding="utf-8"))


def test_track_before_start_is_a_no_op(post):
    analytics.track("anything")
    assert post.bodies == []


def test_first_launch(post):
    analytics.start(enabled=lambda: True, now=NOW)
    assert post.names() == ["app_first_launch", "app_launch", "daily_ping"]
    body = post.bodies[0]
    assert body["user_properties"]["platform"] == {"value": "windows"}
    params = body["events"][0]["params"]
    assert params["app_name"] == "taplock-windows"
    assert params["engagement_time_msec"] == 100
    assert state()["queue"] == []


def test_version_change_is_reported(post):
    analytics.STATE_PATH.write_text(json.dumps({"clientId": "x", "lastVersion": "0.0.1"}), encoding="utf-8")
    analytics.start(enabled=lambda: True, now=NOW)
    assert post.names()[0] == "app_update"
    assert post.bodies[0]["events"][0]["params"]["from_version"] == "0.0.1"


def test_disabled_sends_nothing(post):
    analytics.start(enabled=lambda: False, now=NOW)
    analytics.track("relax_start")
    assert post.bodies == []


def test_failed_send_keeps_the_queue_and_retries(post):
    post.status = None
    analytics.start(enabled=lambda: True, now=NOW)
    assert len(state()["queue"]) == 3
    post.status = 204
    analytics.flush()
    assert state()["queue"] == []


def test_disabled_clears_the_queue(post):
    post.status = None
    analytics.start(enabled=lambda: True, now=NOW)
    analytics.disabled()
    assert state()["queue"] == []


def test_events_past_72_hours_are_dropped(post, monkeypatch):
    post.status = None
    analytics.start(enabled=lambda: True, now=NOW)
    post.status = 204
    post.bodies.clear()
    real = time.time()
    monkeypatch.setattr(analytics.time, "time", lambda: real + 73 * 3600)
    analytics.flush()
    # daily_ping was stamped NOW (2026), long before the fake clock.
    assert "daily_ping" not in post.names()
    assert state()["queue"] == []


def test_bools_are_sent_as_strings(post):
    analytics.start(enabled=lambda: True, now=NOW)
    analytics.track("relax_start", {"silent": True, "interval_sec": 1500})
    params = post.bodies[-1]["events"][0]["params"]
    assert params["silent"] == "true"
    assert params["interval_sec"] == 1500


def test_heartbeat_only_while_running(post):
    analytics.start(enabled=lambda: True, now=NOW)
    sent = len(post.bodies)
    analytics.heartbeat(None)
    assert len(post.bodies) == sent
    analytics.heartbeat({"mode": "relax", "state": "waiting"})
    assert post.names()[-1] == "heartbeat"


def test_daily_ping_once_per_day(post):
    analytics.start(enabled=lambda: True, now=NOW)
    analytics.ping_if_due(NOW + timedelta(hours=6))
    assert post.names().count("daily_ping") == 1


def test_missed_days_are_backfilled_up_to_two():
    last = "2026-09-20"
    dates = analytics.unsent_dates(NOW, last)
    assert [d.strftime("%Y-%m-%d") for d in dates] == ["2026-09-23", "2026-09-24", "2026-09-25"]


def test_user_properties(post):
    analytics.set_user_properties({"launch_at_login": True, "relax_theme": "mini"})
    props = analytics.user_properties("2026-09-25T12:00:00+00:00")
    assert props["launch_at_login"] == "true"
    assert props["relax_theme"] == "mini"
    assert props["install_week"] == "2026-W39"
