from datetime import datetime, timedelta, timezone

import pytest

from notifier.ui import _ellipsize, _format_elapsed, _format_viewers, _parse_handle, _lighten, hex_to_rgb


@pytest.mark.parametrize("text, expected", [
    ("OuroKronii", "@OuroKronii"),
    ("@OuroKronii", "@OuroKronii"),
    ("  @OuroKronii  ", "@OuroKronii"),
    ("https://www.youtube.com/@OuroKronii", "@OuroKronii"),
    ("https://www.youtube.com/@OuroKronii/streams?x=1", "@OuroKronii"),
    ("ab", None),
    ("has space", None),
    ("", None),
])
def test_parse_handle(text, expected):
    assert _parse_handle(text) == expected


def test_format_viewers():
    assert _format_viewers("12345") == "12,345 watching"
    assert _format_viewers(None) is None
    assert _format_viewers("n/a") is None


@pytest.mark.parametrize("delta, expected", [
    (timedelta(seconds=10), "Just started"),
    (timedelta(minutes=5, seconds=5), "Started 5m ago"),
    (timedelta(hours=2), "Started 2h ago"),
    (timedelta(hours=1, minutes=30, seconds=5), "Started 1h 30m ago"),
])
def test_format_elapsed(delta, expected):
    iso = (datetime.now(timezone.utc) - delta).isoformat()
    assert _format_elapsed(iso) == expected


def test_format_elapsed_bad_input():
    assert _format_elapsed(None) is None
    assert _format_elapsed("not a date") is None


def test_ellipsize():
    assert _ellipsize("short", 10) == "short"
    assert _ellipsize("a very long title indeed", 10) == "a very lo…"


def test_colour_helpers():
    assert hex_to_rgb("#ff8000") == (255, 128, 0)
    assert _lighten("#fffffe") == "#ffffff"
