from collections import namedtuple

import startup

_Version = namedtuple("_Version", "major minor build platform service_pack")


def _pretend_build(monkeypatch, build):
    monkeypatch.setattr(
        startup.sys, "getwindowsversion", lambda: _Version(10, 0, build, 2, "")
    )


def test_current_windows_is_supported():
    """No monkeypatching: the machine running the suite must itself qualify."""
    assert startup.unsupported_build() is None


def test_older_build_is_reported(monkeypatch):
    _pretend_build(monkeypatch, 17134)  # Windows 10 1803
    assert startup.unsupported_build() == 17134


def test_the_minimum_build_itself_passes(monkeypatch):
    _pretend_build(monkeypatch, startup.MIN_BUILD)
    assert startup.unsupported_build() is None


def test_windows_11_passes(monkeypatch):
    _pretend_build(monkeypatch, 22000)
    assert startup.unsupported_build() is None


def test_command_points_at_a_real_entry_script():
    """Derived from this module's own location rather than sys.argv, which is
    "-c" under `python -c` and put a path to a file called "-c" in the registry."""
    assert startup._ENTRY.name == "main.py"
    assert startup._ENTRY.is_file()
    assert str(startup._ENTRY) in startup._command()
