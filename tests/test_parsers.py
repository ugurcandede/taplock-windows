import pytest

from parsers import (
    best_unit,
    format_countdown,
    format_duration,
    format_mmss,
    luminance,
    parse_color,
    parse_duration,
    rgb255,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30", 30),
        ("30s", 30),
        ("90s", 90),
        ("2m", 120),
        ("1m30s", 90),
        ("1h", 3600),
        ("1h30m", 5400),
        ("1h2m5s", 3725),
        ("  45  ", 45),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", "m", "0s", "0m0s"])
def test_parse_duration_rejects(text):
    assert parse_duration(text) is None


@pytest.mark.parametrize(
    "seconds,expected",
    [(45, "45s"), (60, "1m"), (90, "1m30s"), (3600, "1h"), (3660, "1h1m"), (3725, "1h2m5s")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [(0, "0"), (45, "45"), (59, "59"), (60, "1:00"), (65, "1:05")])
def test_format_countdown_drops_minutes_below_one(seconds, expected):
    assert format_countdown(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [(0, "0:00"), (45, "0:45"), (65, "1:05"), (1500, "25:00")])
def test_format_mmss_always_shows_minutes(seconds, expected):
    assert format_mmss(seconds) == expected


def test_countdown_clamps_negative():
    assert format_countdown(-5) == "0"
    assert format_mmss(-5) == "0:00"


@pytest.mark.parametrize(
    "seconds,expected", [(3600, (1, "h")), (7200, (2, "h")), (1500, (25, "m")), (90, (90, "s")), (45, (45, "s"))]
)
def test_best_unit(seconds, expected):
    assert best_unit(seconds) == expected


def test_best_unit_prefers_exact_division():
    # 3660s is a whole number of minutes but not of hours.
    assert best_unit(3660) == (61, "m")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("green", (0.0, 0.8, 0.0)),
        ("GREEN", (0.0, 0.8, 0.0)),
        ("grey", (0.5, 0.5, 0.5)),
        ("#FF0000", (1.0, 0.0, 0.0)),
        ("fff", (1.0, 1.0, 1.0)),
        ("#000", (0.0, 0.0, 0.0)),
    ],
)
def test_parse_color(text, expected):
    assert parse_color(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "nope", "#12345", "#gggggg", "1234567"])
def test_parse_color_rejects(text):
    assert parse_color(text) is None


def test_luminance_splits_light_from_dark():
    assert luminance((1.0, 1.0, 1.0)) > 0.5
    assert luminance((0.0, 0.0, 0.0)) < 0.5
    # The overlay picks black text above 0.5; yellow must land on the light side.
    assert luminance(parse_color("yellow")) > 0.5
    assert luminance(parse_color("blue")) < 0.5


def test_rgb255():
    assert rgb255((0.0, 0.8, 0.0)) == (0, 204, 0)
    assert rgb255((1.0, 1.0, 1.0)) == (255, 255, 255)
