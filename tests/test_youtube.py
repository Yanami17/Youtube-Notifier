import pytest

from notifier.youtube import CheckError, _between, parse_channel_page

PAGE = "<html>var ytInitialData = {{}};{body}</html>"

LIVE = PAGE.format(body=(
    '"isLiveNow":true,"startTimestamp":"2026-10-10T20:00:00+00:00",'
    '"title":{"runs":[{"text":"Late night stream"}]},'
    '"viewCount":{"videoViewCountRenderer":{"viewCount":{"runs":[{"text":"12,345"}]}}},'
    '"canonicalUrl":"https://www.youtube.com/watch?v=abcDEF12345"'
))

UPCOMING = PAGE.format(body=(
    '"isUpcoming":true,"liveStreamOfflineSlateRenderer":{"scheduledStartTime":"1791680400"},'
    '"title":{"runs":[{"text":"Scheduled stream"}]},'
    '"canonicalUrl":"https://www.youtube.com/watch?v=zzzUPCOM123"'
))

OFFLINE = PAGE.format(body='"canonicalUrl":"https://www.youtube.com/@someone"')


def test_between_finds_text():
    assert _between('a"hello"b', 'a"') == "hello"


def test_between_missing_marker_or_too_long():
    assert _between("nothing here", "marker") is None
    assert _between('m"' + "x" * 50 + '"', 'm"', max_len=10) is None


def test_live_page():
    status = parse_channel_page(LIVE, "@x")
    assert status.upcoming is None
    live = status.live
    assert live["video_id"] == "abcDEF12345"
    assert live["title"] == "Late night stream"
    assert live["viewers"] == "12345"
    assert live["start_time"] == "2026-10-10T20:00:00+00:00"
    assert live["url"].endswith("abcDEF12345")


def test_upcoming_page():
    status = parse_channel_page(UPCOMING, "@x")
    assert status.live is None
    assert status.upcoming["video_id"] == "zzzUPCOM123"
    assert status.upcoming["start_epoch"] == 1791680400


def test_offline_page_is_neither():
    status = parse_channel_page(OFFLINE, "@x")
    assert status.live is None and status.upcoming is None


def test_live_flag_without_video_id_is_not_live():
    html = PAGE.format(body='"isLiveNow":true')
    assert parse_channel_page(html, "@x").live is None


def test_unrecognised_page_raises():
    with pytest.raises(CheckError):
        parse_channel_page("<html>please accept cookies</html>", "@x")
