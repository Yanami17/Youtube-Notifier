"""System-tray icon, animation and menu."""

import threading
import webbrowser

import pystray
from pystray import MenuItem as item

from . import monitor, storage
from .config import CHANNELS as DEFAULT_CHANNELS
from .icons import idle_icon, live_frames
from .ui import (
    open_add_channel_dialog,
    open_remove_channel_dialog,
    open_view_channels_dialog,
)
from .youtube import resolve_display_name

IDLE_TITLE = "YouTube Live Notifier"


_ICON_IDLE = idle_icon()
_LIVE_FRAMES = live_frames()

_icon: pystray.Icon | None = None


def _refresh_title():
    if not _icon:
        return
    live = monitor.live_snapshot()
    _icon.title = (
        f"🔴 LIVE: {', '.join(live.values())}" if live
        else f"{IDLE_TITLE}  •  {monitor.last_checked}"
    )


def _animate_loop():
    """Pulse the icon while anything is live; only touch it when the image changes."""
    frame, current = 0, None
    while not monitor.stop_event.wait(0.12):
        if not _icon:
            continue
        if monitor.live_snapshot():
            target = _LIVE_FRAMES[frame % len(_LIVE_FRAMES)]
            frame += 1
        else:
            target, frame = _ICON_IDLE, 0
        if target is not current:
            _icon.icon = current = target


def _in_thread(fn):
    """Menu actions run on a worker thread so the tray stays responsive."""
    def action(_icon=None, _item=None):
        threading.Thread(target=fn, daemon=True).start()
    return action


@_in_thread
def _add_channel():
    handle = open_add_channel_dialog()
    if handle:
        print(storage.add_channel(handle, resolve_display_name(handle))[1])


@_in_thread
def _remove_channel():
    handle = open_remove_channel_dialog(storage.channels_snapshot(), DEFAULT_CHANNELS)
    if handle:
        print(storage.remove_channel(handle)[1])


@_in_thread
def _view_channels():
    open_view_channels_dialog(storage.channels_snapshot(), DEFAULT_CHANNELS)


@_in_thread
def _refresh():
    monitor.run_single_check(force_notify=True)
    _refresh_title()


def _open_live(handle: str):
    return lambda _icon, _item: webbrowser.open(f"https://www.youtube.com/{handle}/live")


def _quit(icon, _item):
    print("Shutting down notifier...")
    monitor.stop_event.set()
    storage.clear_state()
    icon.stop()


def _menu_items():
    """Rebuilt by pystray every time the menu opens."""
    live = monitor.live_snapshot()
    for handle, name in live.items():
        yield item(f"● {name}  –  LIVE", _open_live(handle))
    if live:
        yield pystray.Menu.SEPARATOR
    yield item(f"Last checked: {monitor.last_checked}", None, enabled=False)
    yield pystray.Menu.SEPARATOR
    yield item("Refresh Now", _refresh)
    yield pystray.Menu.SEPARATOR
    yield item("Add Channel", _add_channel)
    yield item("Remove Channel", _remove_channel)
    yield item("View Channels", _view_channels)
    yield pystray.Menu.SEPARATOR
    yield item("Exit", _quit)


def run_tray():
    global _icon
    _icon = pystray.Icon("YouTubeNotifier", _ICON_IDLE, IDLE_TITLE, menu=pystray.Menu(_menu_items))
    threading.Thread(
        target=monitor.checker_loop, args=(_refresh_title,), daemon=True, name="Checker"
    ).start()
    threading.Thread(target=_animate_loop, daemon=True, name="TrayAnim").start()
    _icon.run()
