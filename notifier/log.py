"""Logging setup. The windowed exe has no console, so everything goes to notifier.log."""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from .config import LOG_FILE


def setup():
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if sys.stderr is not None:   # running from source
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("crash").critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
