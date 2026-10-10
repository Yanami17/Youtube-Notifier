"""System-tray icon, animation and menu."""

import logging
import os
import threading
import webbrowser
from datetime import datetime

import pystray
from pystray import MenuItem as item

from . import monitor, settings, startup, storage
from .config import APP_NAME, CHANNELS as DEFAULT_CHANNELS, LOG_FILE
from .icons import idle_icon, live_frames
from .ui import (
    open_add_channel_dialog,
    open_manage_channels_dialog,
    open_remove_channel_dialog,
    open_settings_dialog,
)
from .youtube import resolve_display_name

log = logging.getLogger(__name__)

_ICON_IDLE = idle_icon()
_LIVE_FRAMES = live_frames()

PAUSE_CHOICES = [("30 minutes", 30 * 60), ("1 hour", 3600), ("2 hours", 2 * 3600), ("4 hours", 4 * 3600)]

_icon: pystray.Icon | None = None


def _refresh_title():
    """Tooltip: live channels > paused > failing checks > last-checked time. (Windows caps it at 127 chars.)"""
    if not _icon:
        return
    live = monitor.live_snapshot()
    until = monitor.paused_until()
    errors = monitor.error_snapshot()
    if live:
        title = f"🔴 LIVE: {', '.join(live.values())}"
    elif until:
        title = f"{APP_NAME}  •  paused until {datetime.fromtimestamp(until):%H:%M}"
    elif errors:
        title = f"{APP_NAME}  •  ⚠ {len(errors)} channel(s) failing — see log"
    else:
        title = f"{APP_NAME}  •  {monitor.last_checked}"
    _icon.title = title[:127]


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


# ── Channel management ──────────────────────

@_in_thread
def _add_channel():
    handle = open_add_channel_dialog()
    if handle:
        ok, msg = storage.add_channel(handle, resolve_display_name(handle))
        log.info(msg)
        if ok:
            monitor.wake_event.set()     # check the new channel right away


@_in_thread
def _remove_channel():
    handle = open_remove_channel_dialog(storage.channels_snapshot(), DEFAULT_CHANNELS)
    if handle:
        log.info(storage.remove_channel(handle)[1])


@_in_thread
def _manage_channels():
    open_manage_channels_dialog(
        storage.channels_snapshot(), DEFAULT_CHANNELS, storage.get_options, storage.set_options)


# ── Settings ────────────────────────────────

@_in_thread
def _open_settings():
    result = open_settings_dialog(settings.all_values(), startup.is_enabled())
    if result is None:
        return
    boot = result.pop("startup")
    settings.update(**result)
    if boot != startup.is_enabled():
        startup.set_enabled(boot)
    monitor.wake_event.set()             # pick up a new check interval immediately


def _toggle_startup(_icon, _item):
    startup.set_enabled(not startup.is_enabled())


def _open_log(_icon=None, _item=None):
    try:
        os.startfile(LOG_FILE)
    except OSError as e:
        log.warning("Could not open log: %s", e)


# ── Pause ───────────────────────────────────

def _pause_for(seconds: int):
    def action(_icon, _item):
        monitor.pause(seconds)
        _refresh_title()
    return action


def _resume(_icon, _item):
    monitor.resume()
    _refresh_title()
    monitor.wake_event.set()             # fire anything that went live while paused


def _pause_menu_items():
    until = monitor.paused_until()
    if until:
        yield item(f"Paused until {datetime.fromtimestamp(until):%H:%M}", None, enabled=False)
        yield item("Resume now", _resume)
        yield pystray.Menu.SEPARATOR
    for label, secs in PAUSE_CHOICES:
        yield item(label, _pause_for(secs))


# ── Misc actions ────────────────────────────

@_in_thread
def _refresh():
    monitor.run_single_check(force_notify=True)
    _refresh_title()


def _open_live(handle: str):
    return lambda _icon, _item: webbrowser.open(f"https://www.youtube.com/{handle}/live")


def _quit(icon, _item):
    log.info("Shutting down.")
    monitor.stop_event.set()
    monitor.wake_event.set()
    storage.clear_state()
    icon.stop()


def _menu_items():
    """Rebuilt by pystray every time the menu opens."""
    live = monitor.live_snapshot()
    for handle, name in live.items():
        yield item(f"● {name}  –  LIVE", _open_live(handle))
    if live:
        yield pystray.Menu.SEPARATOR

    errors = monitor.error_snapshot()
    paused = monitor.paused_until()
    status = f"Last checked: {monitor.last_checked}"
    if errors:
        status += f"   ⚠ {len(errors)} failing"
    yield item(status, None, enabled=False)
    yield pystray.Menu.SEPARATOR

    yield item("Refresh now", _refresh)
    yield item("Pause notifications" + (" ⏸" if paused else ""), pystray.Menu(_pause_menu_items))
    yield pystray.Menu.SEPARATOR

    yield item("Add channel", _add_channel)
    yield item("Manage channels", _manage_channels)
    yield item("Remove channel", _remove_channel)
    yield pystray.Menu.SEPARATOR

    yield item("Settings…", _open_settings)
    yield item("Start with Windows", _toggle_startup, checked=lambda _i: startup.is_enabled())
    yield item("Open log", _open_log)
    yield pystray.Menu.SEPARATOR
    yield item("Exit", _quit)


def run_tray():
    global _icon
    _icon = pystray.Icon("YouTubeNotifier", _ICON_IDLE, APP_NAME, menu=pystray.Menu(_menu_items))
    threading.Thread(
        target=monitor.checker_loop, args=(_refresh_title,), daemon=True, name="Checker"
    ).start()
    threading.Thread(target=_animate_loop, daemon=True, name="TrayAnim").start()
    _icon.run()
