"""Small Windows-specific helpers for the floating desktop pet."""

from __future__ import annotations

import ctypes
import sys
from typing import Any


def apply_native_topmost(widget: Any, enabled: bool) -> bool:
    """Synchronize Windows' native Z-order with Qt's topmost flag.

    Qt's ``WindowStaysOnTopHint`` is normally sufficient, but Windows may
    reorder tool windows after another app is activated or after a frameless
    window changes size.  Reapplying the native Z-order keeps WaitLAB above
    normal browser windows while still allowing the preference to turn it off.
    """

    if sys.platform != "win32":
        return False
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = ctypes.c_int
        hwnd_insert_after = ctypes.c_void_p(-1 if enabled else -2)  # TOPMOST / NOTOPMOST
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOMOVE, NOSIZE, NOACTIVATE, SHOWWINDOW
        result = user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            hwnd_insert_after,
            0,
            0,
            0,
            0,
            flags,
        )
        return bool(result)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return False
