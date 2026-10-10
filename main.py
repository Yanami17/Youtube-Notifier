import ctypes
import logging
import sys

from notifier import instance, log, settings, storage


def main():
    log.setup()
    if not instance.acquire():
        logging.getLogger("main").info("Another instance is already running; exiting.")
        ctypes.windll.user32.MessageBoxW(0, "YouTube Live Notifier is already running\n(look in the system tray).",
                                         "YouTube Live Notifier", 0x40)
        return 0

    settings.load()
    storage.load_channels()
    storage.load_state()

    from notifier.tray import run_tray   # imported late: pulls in Tk/pystray
    run_tray()
    return 0


if __name__ == "__main__":
    sys.exit(main())
