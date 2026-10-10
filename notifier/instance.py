"""Single-instance guard using a named Windows mutex."""

import ctypes

from .config import MUTEX_NAME

_ERROR_ALREADY_EXISTS = 183
_handle = None   # kept alive for the life of the process


def acquire() -> bool:
    """True if we are the only instance; False if another one already runs."""
    global _handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS
