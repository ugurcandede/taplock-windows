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


@pytest.fixture
def state(tmp_path):
    return tmp_path / "analytics.json"


def ping(state, post, now=NOW):
    # Run the send inline instead of on a thread.
    analytics.ping_if_due(now, path=state, post=post, spawn=lambda work: work())


def test_first_ping_sends_today_only(state):
    post = FakePost()
    ping(state, post)
    assert len(post.bodies) == 1
    params = post.bodies[0]["events"][0]["params"]
    assert params["app_name"] == "taplock-windows"
    assert params["ping_type"] == "live"
    assert params["mode"] == "none"
    assert "timestamp_micros" not in post.bodies[0]


def test_at_most_one_ping_per_day(state):
    post = FakePost()
    ping(state, post)
    ping(state, post, NOW + timedelta(hours=6))
    assert len(post.bodies) == 1


def test_failed_ping_is_retried(state):
    ping(state, FakePost(status=None))
    post = FakePost()
    ping(state, post)
    assert len(post.bodies) == 1


def test_missed_days_are_backfilled_up_to_two(state):
    ping(state, FakePost())
    post = FakePost()
    ping(state, post, NOW + timedelta(days=5))
    assert [b["events"][0]["params"]["ping_type"] for b in post.bodies] == ["backfill", "backfill", "live"]
    assert "timestamp_micros" in post.bodies[0]


def test_client_id_is_stable(state):
    post = FakePost()
    ping(state, post)
    ping(state, post, NOW + timedelta(days=1))
    assert post.bodies[0]["client_id"] == post.bodies[1]["client_id"]


def test_last_mode_is_reported(state):
    analytics.set_last_mode("relax", path=state)
    post = FakePost()
    ping(state, post)
    assert post.bodies[0]["events"][0]["params"]["mode"] == "relax"
