import os
import sys

# Built-in channels (handle -> display name). User channels live in channels.json.
CHANNELS: dict[str, str] = {}

APP_NAME = "YouTube Live Notifier"
MUTEX_NAME = "Local\\YouTubeLiveNotifier.Singleton"   # keep in sync with launcher.py
RUN_VALUE_NAME = "YouTubeLiveNotifier"                 # HKCU\...\Run entry

REQUEST_TIMEOUT = 10
MAX_WORKERS = 8      # parallel channel checks

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Data files: YTN_DATA_DIR (set by the launcher) > next to the exe > project root.
DATA_DIR = os.environ.get("YTN_DATA_DIR") or (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOG_FILE = os.path.join(DATA_DIR, "notifier.log")
