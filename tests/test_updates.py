import json

import updates


def release(tag):
    return json.dumps({"tag_name": tag, "html_url": f"https://github.com/ugurcandede/taplock-windows/releases/tag/{tag}"})


def test_newer_versions():
    assert updates.is_newer("0.2.0", "0.1.1")
    assert updates.is_newer("1.10.0", "1.9.3")
    assert updates.is_newer("2.0", "1.9.9")


def test_not_newer_versions():
    assert not updates.is_newer("0.1.1", "0.1.1")
    assert not updates.is_newer("0.1.0", "0.1.1")
    assert not updates.is_newer("1.6", "1.6.0")


def test_source_runs_see_any_release():
    assert updates.is_newer("0.1.1", "dev")


def test_parses_newer_release():
    update = updates.parse_release(release("v0.2.0"), "0.1.1")
    assert update == updates.Update("0.2.0", "https://github.com/ugurcandede/taplock-windows/releases/tag/v0.2.0")


def test_same_or_dismissed_release_is_ignored():
    assert updates.parse_release(release("v0.1.1"), "0.1.1") is None
    assert updates.parse_release(release("v0.2.0"), "0.1.1", dismissed="0.2.0") is None


def test_malformed_response_is_ignored():
    assert updates.parse_release('{"message": "rate limited"}', "0.1.1") is None
    assert updates.parse_release("not json", "0.1.1") is None


def test_dismiss_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "_DISMISSED_PATH", tmp_path / "update-dismissed")
    assert updates.dismissed_version() is None
    updates.dismiss("0.2.0")
    assert updates.dismissed_version() == "0.2.0"
