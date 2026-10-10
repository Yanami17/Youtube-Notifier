"""
Launcher — built to YouTubeNotifier.exe in the project root.

On every launch it checks whether the app build (bin/YouTubeNotifierApp.exe) is
missing or older than the sources; if so it rebuilds it with PyInstaller, then
starts the app. If the rebuild isn't possible (no Python, build error, app already
running and locking the exe) it falls back to the existing build.

The app can't rebuild itself while running (Windows locks a running exe), hence
this thin launcher.
"""

import ctypes
import glob
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
APP_NAME = "YouTubeNotifierApp"
APP_EXE = os.path.join(ROOT, "bin", f"{APP_NAME}.exe")
SPEC = os.path.join(ROOT, "app.spec")
LOG = os.path.join(ROOT, "build.log")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sources() -> list[str]:
    return [os.path.join(ROOT, "main.py"), SPEC, *glob.glob(os.path.join(ROOT, "notifier", "*.py"))]


def _needs_build() -> bool:
    if not os.path.exists(APP_EXE):
        return True
    built = os.path.getmtime(APP_EXE)
    return any(os.path.getmtime(p) > built for p in _sources() if os.path.exists(p))


def _python() -> list[str] | None:
    for cmd in (["python"], ["py", "-3"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def _build() -> bool:
    py = _python()
    if not py or not os.path.exists(SPEC):
        return False
    cmd = [*py, "-m", "PyInstaller", SPEC, "--noconfirm",
           "--distpath", os.path.join(ROOT, "bin"), "--workpath", os.path.join(ROOT, "build")]
    with open(LOG, "w", encoding="utf-8") as log:
        return subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                              creationflags=NO_WINDOW).returncode == 0


def _message(text: str):
    ctypes.windll.user32.MessageBoxW(0, text, "YouTube Notifier", 0x40)


def main():
    if _needs_build() and not _build() and not os.path.exists(APP_EXE):
        _message(f"Build failed and no existing app found.\nSee {LOG}")
        return
    subprocess.Popen([APP_EXE], cwd=ROOT, env={**os.environ, "YTN_DATA_DIR": ROOT},
                     creationflags=NO_WINDOW)


if __name__ == "__main__":
    main()
