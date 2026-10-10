"""Scraping helpers for YouTube channel pages (no API key required)."""

import requests

from .config import REQUEST_TIMEOUT, USER_AGENT

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT

_OG_TITLE = '<meta property="og:title" content="'


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
        print("Name resolution error:", e)
    return handle


def check_live_status(handle: str) -> dict | None:
    """Return stream info if `handle` is live right now, else None."""
    try:
        r = _session.get(f"https://www.youtube.com/{handle}/live", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"Live check error ({handle}):", e)
        return None
    if r.status_code != 200:
        return None

    html = r.text
    if '"isLiveNow":true' not in html:
        return None

    video_id = _between(html, '"canonicalUrl":"https://www.youtube.com/watch?v=', max_len=11)
    if not video_id:
        return None

    title = (
        _between(html, '"title":{"runs":[{"text":"')
        or _between(html, _OG_TITLE)
        or f"{handle} is Live!"
    )
    raw_viewers = _between(
        html, '"viewCount":{"videoViewCountRenderer":{"viewCount":{"runs":[{"text":"'
    )

    return {
        "video_id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "viewers": raw_viewers.replace(",", "") if raw_viewers else None,
        "start_time": _between(html, '"startTimestamp":"', max_len=30),
    }
