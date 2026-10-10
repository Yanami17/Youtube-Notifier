"""Persistence for the monitored-channel list and the per-channel live state."""

import json
import threading

from .config import CHANNELS as DEFAULT_CHANNELS, CHANNELS_FILE, STATE_FILE

_lock = threading.RLock()
channels: dict[str, str] = {}
state: dict[str, str | None] = {}   # handle -> last notified video_id


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"Failed to read {path}: {e}")
        return {}


def _write_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        print(f"Failed to write {path}: {e}")


# ── Channels ────────────────────────────────

def load_channels():
    user = _read_json(CHANNELS_FILE)
    with _lock:
        channels.clear()
        channels.update({**DEFAULT_CHANNELS, **user})
        print(f"Loaded {len(channels)} channels.")


def channels_snapshot() -> dict[str, str]:
    with _lock:
        return dict(channels)


def _save_channels():
    with _lock:
        user = {h: n for h, n in channels.items() if h not in DEFAULT_CHANNELS}
        _write_json(CHANNELS_FILE, user)


def add_channel(handle: str, display_name: str) -> tuple[bool, str]:
    handle = handle.strip()
    if not handle.startswith("@"):
        handle = "@" + handle
    with _lock:
        if handle in channels:
            return False, f"{handle} is already being monitored."
        channels[handle] = display_name
        _save_channels()
    return True, f"Now monitoring {display_name} ({handle})."


def remove_channel(handle: str) -> tuple[bool, str]:
    with _lock:
        if handle not in channels:
            return False, f"{handle} not found."
        if handle in DEFAULT_CHANNELS:
            return False, f"{handle} is a built-in channel. Edit config.py to remove it."
        del channels[handle]
        _save_channels()
        state.pop(handle, None)
    save_state()
    return True, f"Stopped monitoring {handle}."


# ── State ───────────────────────────────────

def load_state():
    data = _read_json(STATE_FILE)
    with _lock:
        state.clear()
        state.update(data)


def save_state():
    with _lock:
        _write_json(STATE_FILE, state)


def last_notified(handle: str) -> str | None:
    with _lock:
        return state.get(handle)


def set_notified(handle: str, video_id: str | None):
    with _lock:
        state[handle] = video_id
    save_state()


def clear_state():
    with _lock:
        state.clear()
    save_state()
