import requests
import time
import json
import os
import math
import threading

from config import CHANNELS as DEFAULT_CHANNELS, CHECK_INTERVAL, APP_ID, STATE_FILE, CHANNELS_FILE

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from ui import (
    open_add_channel_dialog,
    open_remove_channel_dialog,
    open_view_channels_dialog,
    show_live_toast,
)


running = True
state = {}

# Runtime channels dict — merged from config.py defaults + channels.json
channels = {}
channels_lock = threading.Lock()


# =========================
# Channel Persistence
# =========================

def load_channels():
    global channels
    user_channels = {}

    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                user_channels = json.load(f)
        except Exception as e:
            print("Failed to load channels.json:", e)

    with channels_lock:
        channels = {**DEFAULT_CHANNELS, **user_channels}

    print(f"Loaded {len(channels)} channels.")


def save_user_channels():
    with channels_lock:
        user_channels = {
            handle: name
            for handle, name in channels.items()
            if handle not in DEFAULT_CHANNELS
        }

    try:
        with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
            json.dump(user_channels, f, indent=4)
    except Exception as e:
        print("Failed to save channels.json:", e)


def add_channel(handle: str, display_name: str):
    handle = handle.strip()
    if not handle.startswith("@"):
        handle = "@" + handle

    with channels_lock:
        if handle in channels:
            return False, f"{handle} is already being monitored."
        channels[handle] = display_name

    save_user_channels()
    print(f"Added channel: {handle} -> {display_name}")
    return True, f"Now monitoring {display_name} ({handle})."


def remove_channel(handle: str):
    with channels_lock:
        if handle not in channels:
            return False, f"{handle} not found."
        if handle in DEFAULT_CHANNELS:
            return False, f"{handle} is a built-in channel. Edit config.py to remove it."
        del channels[handle]

    state.pop(handle, None)
    save_state(state)
    save_user_channels()
    print(f"Removed channel: {handle}")
    return True, f"Stopped monitoring {handle}."


# =========================
# Helpers
# =========================

def resolve_display_name(handle: str) -> str:
    """Scrape og:title from the channel page for a friendly display name."""
    try:
        url = f"https://www.youtube.com/{handle}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            html = response.text
            marker = '<meta property="og:title" content="'
            if marker in html:
                start = html.index(marker) + len(marker)
                end = html.index('"', start)
                return html[start:end]
    except Exception as e:
        print("Name resolution error:", e)
    return handle


# =========================
# State Handling
# =========================

def clear_live_state(_state):
    save_state({})
    print("Cleared live state.")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_state(_state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(_state, f, indent=4)


def _scrape_between(html: str, marker: str, end_char: str = '"', max_len: int = 200) -> str | None:
    """Return the text between `marker` and the next `end_char`, or None."""
    if marker not in html:
        return None
    start = html.index(marker) + len(marker)
    end   = html.find(end_char, start)
    if end == -1 or end - start > max_len:
        return None
    return html[start:end]


def check_live_status(handle: str):
    live_url = f"https://www.youtube.com/{handle}/live"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(live_url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None

        html = response.text

        if '"isLiveNow":true' not in html:
            return None

        video_id = _scrape_between(
            html,
            '"canonicalUrl":"https://www.youtube.com/watch?v=',
            end_char='"', max_len=11
        )
        if not video_id:
            return None

        # Stream title — prefer the og:title meta tag
        title = (
            _scrape_between(html, '"title":{"runs":[{"text":"', end_char='"')
            or _scrape_between(html, '<meta property="og:title" content="', end_char='"')
            or f"{handle} is Live!"
        )

        # Viewer / concurrent count  e.g. "watching now"
        viewers = None
        raw_viewers = _scrape_between(html, '"viewCount":{"videoViewCountRenderer":{"viewCount":{"runs":[{"text":"', end_char='"')
        if raw_viewers:
            # strip commas from "12,345"
            viewers = raw_viewers.replace(",", "")

        # Stream start time — ISO 8601 string embedded in the page
        start_time = _scrape_between(html, '"startTimestamp":"', end_char='"', max_len=30)

        return {
            "video_id":   video_id,
            "title":      title,
            "url":        f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail":  f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            "viewers":    viewers,      # str like "12345", or None
            "start_time": start_time,   # ISO str like "2024-03-01T18:00:00+00:00", or None
        }
    except Exception as e:
        print("Live check error:", e)

    return None


# =========================
# Background Loop
# =========================

def run_single_check(force_notify=False):
    global state, _last_checked

    with channels_lock:
        current_channels = dict(channels)

    any_live = False
    for handle, channel_name in current_channels.items():
        stream = check_live_status(handle)

        if stream is None:
            _clear_live(handle)
            if state.get(handle) is not None:
                state[handle] = None
                save_state(state)
            continue

        any_live = True
        _set_live(handle, channel_name)
        last_video_id = state.get(handle)

        if force_notify or last_video_id != stream["video_id"]:
            print(f"{channel_name} went live!")
            show_live_toast(
                channel_name=channel_name,
                stream_title=stream["title"],
                stream_url=stream["url"],
                thumbnail_url=stream.get("thumbnail"),
                viewers=stream.get("viewers"),
                start_time=stream.get("start_time"),
            )
            state[handle] = stream["video_id"]
            save_state(state)

    _last_checked = _fmt_time()

    # Update tooltip to reflect live state
    if _tray_icon_ref:
        live = _live_snapshot()
        if live:
            names = ", ".join(live.values())
            _tray_icon_ref.title = f"🔴 LIVE: {names}"
        else:
            _tray_icon_ref.title = f"YouTube Live Notifier  •  {_last_checked}"


def checker_loop():
    global state
    global running

    print("YouTube Live Notifier started.")
    print("State file path:", os.path.abspath(STATE_FILE))

    state = load_state()

    while running:
        try:
            run_single_check()
        except Exception as e:
            print("Error:", e)
        time.sleep(CHECK_INTERVAL)


# =========================
# Tray Dialogs (run on separate threads to keep tray responsive)
# =========================

def _tray_add_channel(_icon=None, _item=None):
    def _run():
        handle = open_add_channel_dialog()
        if not handle:
            return
        display_name = resolve_display_name(handle)
        ok, msg = add_channel(handle, display_name)
        print(msg)

    threading.Thread(target=_run, daemon=True).start()


def _tray_remove_channel(_icon=None, _item=None):
    def _run():
        with channels_lock:
            snapshot = dict(channels)
        handle = open_remove_channel_dialog(snapshot, DEFAULT_CHANNELS)
        if handle:
            ok, msg = remove_channel(handle)
            print(msg)

    threading.Thread(target=_run, daemon=True).start()


def _tray_view_channels(_icon=None, _item=None):
    def _run():
        with channels_lock:
            snapshot = dict(channels)
        open_view_channels_dialog(snapshot, DEFAULT_CHANNELS)

    threading.Thread(target=_run, daemon=True).start()


# =========================
# Tray Icon — images
# =========================

def _draw_icon(bg: str, dot_color: str | None, dot_alpha: int = 255) -> Image.Image:
    """
    Draw a 64×64 tray icon.

    Layout:
      - Rounded dark square background
      - White play-button triangle (YouTube-style)
      - Optional coloured dot in the bottom-right corner (used for live state)

    dot_alpha 0-255 lets us pulse the dot for the live animation.
    """
    SIZE = 64
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # Rounded background square
    bg_rgb = tuple(int(bg.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    d.rounded_rectangle((2, 2, SIZE - 2, SIZE - 2), radius=12,
                         fill=(*bg_rgb, 255))

    # Play triangle — centred, pointing right
    cx, cy = SIZE // 2 - 2, SIZE // 2
    tri = [(cx - 10, cy - 13), (cx - 10, cy + 13), (cx + 14, cy)]
    d.polygon(tri, fill=(255, 255, 255, 230))

    # Live dot — bottom-right
    if dot_color and dot_alpha > 0:
        dot_rgb = tuple(int(dot_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        # Slightly larger white halo for contrast on any taskbar colour
        d.ellipse((42, 42, 60, 60), fill=(255, 255, 255, dot_alpha))
        d.ellipse((44, 44, 58, 58), fill=(*dot_rgb, dot_alpha))

    return img.convert("RGB")


# Pre-bake the idle icon once
_ICON_IDLE = _draw_icon("#1a1a1a", dot_color=None)

# Live animation: 12 frames where the red dot pulses in opacity
_LIVE_FRAMES: list[Image.Image] = []
for _i in range(12):
    # Sine-ish pulse: alpha 120 → 255 → 120
    _alpha = int(120 + 135 * (0.5 + 0.5 * math.sin(2 * math.pi * _i / 12)))
    _LIVE_FRAMES.append(_draw_icon("#1a1a1a", dot_color="#ff0000", dot_alpha=_alpha))


# =========================
# Tray Icon — state & animation
# =========================

# Tracks which handles are currently live  {handle: channel_name}
_live_now: dict[str, str] = {}
_live_lock = threading.Lock()
_last_checked: str = "Never"

_tray_icon_ref: pystray.Icon | None = None   # set in run_tray()


def _set_live(handle: str, channel_name: str):
    with _live_lock:
        _live_now[handle] = channel_name


def _clear_live(handle: str):
    with _live_lock:
        _live_now.pop(handle, None)


def _any_live() -> bool:
    with _live_lock:
        return bool(_live_now)


def _live_snapshot() -> dict:
    with _live_lock:
        return dict(_live_now)


def _animate_loop():
    """Cycle through live frames while any channel is live.
    Runs on its own daemon thread."""
    frame = 0
    while running:
        if _tray_icon_ref and _any_live():
            _tray_icon_ref.icon = _LIVE_FRAMES[frame % len(_LIVE_FRAMES)]
            frame += 1
        else:
            if _tray_icon_ref:
                _tray_icon_ref.icon = _ICON_IDLE
            frame = 0
        time.sleep(0.12)   # ~8 fps — smooth but not busy


def _fmt_time() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


# =========================
# Tray Icon — menu builder
# =========================

def _open_live_stream(handle: str):
    """Open a live channel URL — used as tray menu action."""
    import webbrowser
    webbrowser.open(f"https://www.youtube.com/{handle}/live")


def _menu_refresh(icon, _item):
    threading.Thread(target=lambda: run_single_check(force_notify=True), daemon=True).start()


def _build_menu_items():
    """
    Generator yielding MenuItem objects for the dynamic tray menu.
    pystray calls this every time the menu is opened.
    """
    live = _live_snapshot()

    # ── Live channels ──────────────────────────
    if live:
        for handle, name in live.items():
            def _make_action(h):
                def _action(icon, _item):
                    _open_live_stream(h)
                return _action
            yield item(f"● {name}  –  LIVE", _make_action(handle))
        yield pystray.Menu.SEPARATOR

    # ── Status ─────────────────────────────────
    yield item(f"Last checked: {_last_checked}", None, enabled=False)
    yield pystray.Menu.SEPARATOR

    # ── Actions ────────────────────────────────
    yield item("Refresh Now", _menu_refresh)
    yield pystray.Menu.SEPARATOR

    # ── Channel management ─────────────────────
    yield item("Add Channel",    _tray_add_channel)
    yield item("Remove Channel", _tray_remove_channel)
    yield item("View Channels",  _tray_view_channels)
    yield pystray.Menu.SEPARATOR

    yield item("Exit", quit_app)


def quit_app(icon, _item):
    global running
    print("Shutting down notifier...")
    running = False
    clear_live_state(state)
    icon.stop()


def run_tray():
    global _tray_icon_ref

    icon = pystray.Icon(
        "YouTubeNotifier",
        _ICON_IDLE,
        "YouTube Live Notifier",
        menu=pystray.Menu(_build_menu_items)
    )
    _tray_icon_ref = icon

    anim_thread = threading.Thread(target=_animate_loop, daemon=True, name="TrayAnim")
    anim_thread.start()

    icon.run()


# =========================
# Entry
# =========================

if __name__ == "__main__":
    load_channels()

    thread = threading.Thread(target=checker_loop, daemon=True)
    thread.start()

    run_tray()