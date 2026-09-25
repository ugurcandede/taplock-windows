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


# ---- self-update ------------------------------------------------------------


def test_swap_puts_the_new_exe_in_place(tmp_path):
    exe, new = tmp_path / "TapLock.exe", tmp_path / "TapLock.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    updates.swap_executable(exe, new)
    assert exe.read_bytes() == b"new"
    assert (tmp_path / "TapLock.exe.old").read_bytes() == b"old"
    assert not new.exists()


def test_swap_rolls_back_when_the_new_exe_is_missing(tmp_path):
    exe = tmp_path / "TapLock.exe"
    exe.write_bytes(b"old")
    try:
        updates.swap_executable(exe, tmp_path / "missing.exe")
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError")
    assert exe.read_bytes() == b"old"
    assert not (tmp_path / "TapLock.exe.old").exists()


def test_swap_replaces_a_stale_old_exe(tmp_path):
    exe, new = tmp_path / "TapLock.exe", tmp_path / "TapLock.exe.new"
    exe.write_bytes(b"current")
    new.write_bytes(b"next")
    (tmp_path / "TapLock.exe.old").write_bytes(b"ancient")
    updates.swap_executable(exe, new)
    assert (tmp_path / "TapLock.exe.old").read_bytes() == b"current"


def test_cleanup_removes_the_previous_exe(tmp_path):
    exe = tmp_path / "TapLock.exe"
    (tmp_path / "TapLock.exe.old").write_bytes(b"old")
    updates.cleanup_previous(exe)
    assert not (tmp_path / "TapLock.exe.old").exists()
    updates.cleanup_previous(exe)  # nothing to remove is fine


class _Response:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_rejects_anything_but_an_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: _Response(b"<html>not found</html>"))
    dest = tmp_path / "TapLock.exe.new"
    try:
        updates._download("https://example.com", dest)
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError")
    assert not dest.exists()


def test_download_writes_an_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: _Response(b"MZ\x90\x00rest"))
    dest = tmp_path / "TapLock.exe.new"
    updates._download("https://example.com", dest)
    assert dest.read_bytes().startswith(b"MZ")
