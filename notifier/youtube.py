"""Scraping helpers for YouTube channel pages (no API key required)."""

import logging
import time
from dataclasses import dataclass

import requests

from .config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_session.headers["Accept-Language"] = "en-US,en;q=0.9"
# Pre-accept the EU cookie-consent wall, which otherwise replaces the page.
_session.cookies.set("CONSENT", "YES+1", domain=".youtube.com")
_session.cookies.set("SOCS", "CAI", domain=".youtube.com")

_OG_TITLE = '<meta property="og:title" content="'


class CheckError(Exception):
    """A check could not be completed (network, HTTP error, unrecognised page)."""


@dataclass
class ChannelStatus:
    live: dict | None = None       # stream info if live right now
    upcoming: dict | None = None   # scheduled stream info (includes "start_epoch")


def _between(html: str, marker: str, end_char: str = '"', max_len: int = 200) -> str | None:
    """Text between `marker` and the next `end_char`, or None."""
    i = html.find(marker)
    if i == -1:
        return None
    start = i + len(marker)
    end = html.find(end_char, start)
    if end == -1 or end - start > max_len:
        return None
    return html[start:end]


def resolve_display_name(handle: str) -> str:
    """Friendly channel name from the page's og:title, falling back to the handle."""
    try:
        r = _session.get(f"https://www.youtube.com/{handle}", timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            name = _between(r.text, _OG_TITLE)
            if name:
                return name
    except requests.RequestException as e:
        log.warning("Name resolution failed for %s: %s", handle, e)
    return handle


def _video_info(html: str, handle: str) -> dict | None:
    video_id = _between(html, '"canonicalUrl":"https://www.youtube.com/watch?v=', max_len=11)
    if not video_id:
        return None
    title = (
        _between(html, '"title":{"runs":[{"text":"')
        or _between(html, _OG_TITLE)
        or f"{handle} is Live!"
    )
    return {
        "video_id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
    }


def parse_channel_page(html: str, handle: str) -> ChannelStatus:
    """Extract live / upcoming info from a channel's /live page. Raises CheckError on unknown pages."""
    if "ytInitialData" not in html and '"isLiveNow"' not in html:
        raise CheckError("unrecognised page (consent wall or YouTube markup changed)")

    if '"isLiveNow":true' in html:
        info = _video_info(html, handle)
        if info:
            raw_viewers = _between(
                html, '"viewCount":{"videoViewCountRenderer":{"viewCount":{"runs":[{"text":"')
            info["viewers"] = raw_viewers.replace(",", "") if raw_viewers else None
            info["start_time"] = _between(html, '"startTimestamp":"', max_len=30)
            return ChannelStatus(live=info)

    if '"isUpcoming":true' in html:
        start = _between(html, '"scheduledStartTime":"', max_len=12)
        info = _video_info(html, handle)
        if info and start and start.isdigit():
            info["start_epoch"] = int(start)
            return ChannelStatus(upcoming=info)

    return ChannelStatus()


def check_channel(handle: str) -> ChannelStatus:
    try:
        r = _session.get(f"https://www.youtube.com/{handle}/live", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise CheckError(f"network error: {e}") from e
    if "consent.youtube.com" in r.url:
        raise CheckError("blocked by cookie-consent page")
    if r.status_code == 404:
        raise CheckError("channel not found (HTTP 404) — wrong handle?")
    if r.status_code != 200:
        raise CheckError(f"HTTP {r.status_code}")
    return parse_channel_page(r.text, handle)


def minutes_until(epoch: int) -> float:
    return (epoch - time.time()) / 60
