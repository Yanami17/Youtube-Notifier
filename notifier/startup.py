"""'Start with Windows' via the per-user Run registry key."""

import logging
import os
import sys
import winreg

from .config import DATA_DIR, RUN_VALUE_NAME

log = logging.getLogger(__name__)
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command() -> str:
    if getattr(sys, "frozen", False):
        launcher = os.path.join(DATA_DIR, "YouTubeNotifier.exe")   # the self-rebuilding launcher
        return f'"{launcher if os.path.exists(launcher) else sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.join(DATA_DIR, "main.py")
    return f'"{pythonw if os.path.exists(pythonw) else sys.executable}" "{main}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        log.info("Start with Windows %s", "enabled" if enabled else "disabled")
    except OSError as e:
        log.error("Could not update startup entry: %s", e)
