"""Background polling loop, live-status bookkeeping, pause and notification rules."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import settings, storage
from .config import MAX_WORKERS
from .ui import show_live_toast
from .youtube import CheckError, check_channel, minutes_until

log = logging.getLogger(__name__)

stop_event = threading.Event()
wake_event = threading.Event()   # set to cut the wait short (refresh, settings change)

_live_now: dict[str, str] = {}   # handle -> channel name
_errors: dict[str, str] = {}     # handle -> last check error
_live_lock = threading.Lock()
_check_lock = threading.Lock()   # serialises the loop and manual refreshes
_paused_until = 0.0              # epoch seconds; 0 = not paused
last_checked = "Never"


# ── Pause / snooze ──────────────────────────

def pause(seconds: float):
    global _paused_until
    _paused_until = time.time() + seconds
    log.info("Notifications paused until %s", datetime.fromtimestamp(_paused_until).strftime("%H:%M"))


def resume():
    global _paused_until
    _paused_until = 0.0
    log.info("Notifications resumed")


def paused_until() -> float | None:
    """Epoch when the pause ends, or None if not paused."""
    return _paused_until if _paused_until > time.time() else None


# ── Snapshots ───────────────────────────────

def live_snapshot() -> dict[str, str]:
    with _live_lock:
        return dict(_live_now)


def error_snapshot() -> dict[str, str]:
    with _live_lock:
        return dict(_errors)


# ── Checking ────────────────────────────────

def _check(handle: str):
    try:
        return check_channel(handle), None
    except CheckError as e:
        return None, str(e)
    except Exception as e:
        log.exception("Unexpected error checking %s", handle)
        return None, f"unexpected error: {e}"


def _maybe_notify_live(handle, name, stream, opts, force):
    last = storage.last_notified(handle)
    if not force and last == stream["video_id"]:
        return
    if not opts["notify"]:
        storage.set_notified(handle, stream["video_id"])   # muted: don't re-fire when unmuted mid-stream
        return
    if paused_until() and not force:
        return                                             # stays un-notified, so it fires after the pause
    log.info("%s went live: %s", name, stream["title"])
    show_live_toast(
        channel_name=name, stream_title=stream["title"], stream_url=stream["url"],
        thumbnail_url=stream["thumbnail"], viewers=stream["viewers"],
        start_time=stream["start_time"], sound=opts["sound"],
    )
    storage.set_notified(handle, stream["video_id"])


def _maybe_notify_upcoming(handle, name, stream, opts):
    key = f"{handle}#soon"
    if storage.last_notified(key) == stream["video_id"]:
        return
    mins = minutes_until(stream["start_epoch"])
    if mins > settings.get("upcoming_lead_min") or mins < -30:
        return
    if not (opts["notify"] and opts["upcoming"]) or paused_until():
        return
    log.info("%s starts in %d min: %s", name, mins, stream["title"])
    show_live_toast(
        channel_name=name, stream_title=stream["title"], stream_url=stream["url"],
        thumbnail_url=stream["thumbnail"], badge="SOON",
        meta=f"Starts in {max(0, round(mins))} min" if mins > 0 else "Starting now",
        sound=opts["sound"],
    )
    storage.set_notified(key, stream["video_id"])


def run_single_check(force_notify: bool = False):
    global last_checked

    with _check_lock:
        current = storage.channels_snapshot()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(_check, current))

        for (handle, name), (status, error) in zip(current.items(), results):
            with _live_lock:
                if error:
                    if _errors.get(handle) != error:
                        log.warning("Check failed for %s: %s", handle, error)
                    _errors[handle] = error
                    continue                    # keep previous live/notified state on errors
                if handle in _errors:
                    log.info("Check recovered for %s", handle)
                    del _errors[handle]

            opts = storage.get_options(handle)
            if status.live:
                with _live_lock:
                    _live_now[handle] = name
                _maybe_notify_live(handle, name, status.live, opts, force_notify)
                continue

            with _live_lock:
                _live_now.pop(handle, None)
            if storage.last_notified(handle) is not None:
                storage.set_notified(handle, None)
            if status.upcoming and settings.get("notify_upcoming"):
                _maybe_notify_upcoming(handle, name, status.upcoming, opts)
            elif storage.last_notified(f"{handle}#soon") is not None:
                storage.set_notified(f"{handle}#soon", None)

        # Forget entries for channels removed mid-check
        remaining = storage.channels_snapshot()
        with _live_lock:
            for table in (_live_now, _errors):
                for h in [h for h in table if h not in remaining]:
                    del table[h]

        last_checked = datetime.now().strftime("%H:%M:%S")


def checker_loop(on_update=None):
    log.info("Checker started.")
    while not stop_event.is_set():
        try:
            run_single_check()
            if on_update:
                on_update()
        except Exception:
            log.exception("Check cycle failed")
        wake_event.wait(settings.get("check_interval"))
        wake_event.clear()
