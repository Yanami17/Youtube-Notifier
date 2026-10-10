"""Background polling loop and live-status bookkeeping."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import storage
from .config import CHECK_INTERVAL, MAX_WORKERS
from .ui import show_live_toast
from .youtube import check_live_status

stop_event = threading.Event()

_live_now: dict[str, str] = {}   # handle -> channel name
_live_lock = threading.Lock()
_check_lock = threading.Lock()   # serialises the loop and manual refreshes
last_checked = "Never"


def live_snapshot() -> dict[str, str]:
    with _live_lock:
        return dict(_live_now)


def run_single_check(force_notify: bool = False):
    global last_checked

    with _check_lock:
        current = storage.channels_snapshot()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            streams = list(pool.map(check_live_status, current))

        for (handle, name), stream in zip(current.items(), streams):
            if stream is None:
                with _live_lock:
                    _live_now.pop(handle, None)
                if storage.last_notified(handle) is not None:
                    storage.set_notified(handle, None)
                continue

            with _live_lock:
                _live_now[handle] = name
            if force_notify or storage.last_notified(handle) != stream["video_id"]:
                print(f"{name} went live!")
                show_live_toast(
                    channel_name=name,
                    stream_title=stream["title"],
                    stream_url=stream["url"],
                    thumbnail_url=stream["thumbnail"],
                    viewers=stream["viewers"],
                    start_time=stream["start_time"],
                )
                storage.set_notified(handle, stream["video_id"])

        # Drop live entries for channels removed mid-check
        remaining = storage.channels_snapshot()
        with _live_lock:
            for h in [h for h in _live_now if h not in remaining]:
                del _live_now[h]

        last_checked = datetime.now().strftime("%H:%M:%S")


def checker_loop(on_update=None):
    print("YouTube Live Notifier started.")
    while not stop_event.is_set():
        try:
            run_single_check()
            if on_update:
                on_update()
        except Exception as e:
            print("Error:", e)
        stop_event.wait(CHECK_INTERVAL)
