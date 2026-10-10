"""Persistence for the monitored-channel list, per-channel options and live state."""

import json
import logging
import threading

from .config import CHANNELS as DEFAULT_CHANNELS, CHANNELS_FILE, STATE_FILE

log = logging.getLogger(__name__)

_lock = threading.RLock()
channels: dict[str, str] = {}                 # handle -> display name
options: dict[str, dict] = {}                 # handle -> non-default overrides
state: dict[str, str | None] = {}             # handle -> last notified live video_id;
                                              # "<handle>#soon" -> last notified upcoming video_id

OPTION_DEFAULTS = {
    "notify": True,        # live notifications for this channel
    "upcoming": True,      # "starting soon" notifications for this channel
    "sound": "default",    # "default" | "none" | path to a .wav file
}


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("Failed to read %s: %s", path, e)
        return {}


def _write_json(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        log.error("Failed to write %s: %s", path, e)


# ── Channels ────────────────────────────────

def parse_channels_file(raw: dict) -> tuple[dict[str, str], dict[str, dict]]:
    """channels.json maps handle -> "Name" (legacy) or {"name": ..., <option overrides>}."""
    names, opts = {}, {}
    for handle, value in raw.items():
        if isinstance(value, str):
            names[handle] = value
        elif isinstance(value, dict):
            if value.get("name"):
                names[handle] = value["name"]
            override = {k: v for k, v in value.items() if k in OPTION_DEFAULTS and v != OPTION_DEFAULTS[k]}
            if override:
                opts[handle] = override
    return names, opts


def load_channels():
    names, opts = parse_channels_file(_read_json(CHANNELS_FILE))
    with _lock:
        channels.clear()
        channels.update({**DEFAULT_CHANNELS, **names})
        options.clear()
        options.update({h: o for h, o in opts.items() if h in channels})
        log.info("Loaded %d channels.", len(channels))


def channels_snapshot() -> dict[str, str]:
    with _lock:
        return dict(channels)


def _save_channels():
    with _lock:
        data = {}
        for handle, name in channels.items():
            override = options.get(handle, {})
            if handle in DEFAULT_CHANNELS and not override:
                continue    # built-ins only need an entry when customised
            data[handle] = {"name": name, **override} if override else name
        _write_json(CHANNELS_FILE, data)


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
        options.pop(handle, None)
        _save_channels()
        state.pop(handle, None)
        state.pop(f"{handle}#soon", None)
    save_state()
    return True, f"Stopped monitoring {handle}."


# ── Per-channel options ─────────────────────

def get_options(handle: str) -> dict:
    with _lock:
        return {**OPTION_DEFAULTS, **options.get(handle, {})}


def set_options(handle: str, new: dict):
    override = {k: v for k, v in new.items() if k in OPTION_DEFAULTS and v != OPTION_DEFAULTS[k]}
    with _lock:
        if override:
            options[handle] = override
        else:
            options.pop(handle, None)
        _save_channels()


# ── State ───────────────────────────────────

def load_state():
    data = _read_json(STATE_FILE)
    with _lock:
        state.clear()
        state.update(data)


def save_state():
    with _lock:
        _write_json(STATE_FILE, state)


def last_notified(key: str) -> str | None:
    with _lock:
        return state.get(key)


def set_notified(key: str, video_id: str | None):
    with _lock:
        if video_id is None:
            state.pop(key, None)
        else:
            state[key] = video_id
    save_state()


def clear_state():
    with _lock:
        state.clear()
    save_state()
