"""User-editable settings, persisted to settings.json."""

import json
import logging
import threading

from .config import SETTINGS_FILE

log = logging.getLogger(__name__)

DEFAULTS = {
    "check_interval": 60,        # seconds between live checks
    "toast_seconds": 8,          # 0 = stay until dismissed
    "sound": True,
    "notify_upcoming": True,     # "starting soon" notifications
    "upcoming_lead_min": 15,     # how early to warn
}

_lock = threading.Lock()
_values = dict(DEFAULTS)


def load():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.warning("Could not read settings: %s", e)
        return
    with _lock:
        _values.update({k: v for k, v in saved.items() if k in DEFAULTS})


def get(key: str):
    with _lock:
        return _values[key]


def all_values() -> dict:
    with _lock:
        return dict(_values)


def update(**changes):
    with _lock:
        _values.update({k: v for k, v in changes.items() if k in DEFAULTS})
        snapshot = dict(_values)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=4)
    except OSError as e:
        log.error("Could not save settings: %s", e)
