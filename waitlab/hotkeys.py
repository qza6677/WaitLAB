from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject


WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000


class _NativeHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callbacks: dict[int, Callable[[], None]]) -> None:
        super().__init__()
        self.callbacks = callbacks

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API name
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                callback = self.callbacks.get(int(msg.wParam))
                if callback:
                    callback()
                    return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    """Small RegisterHotKey wrapper; silently degrades when a shortcut is occupied."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._registered: set[int] = set()
        self._filter = _NativeHotkeyFilter(self._callbacks)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def register(self, hotkey_id: int, key: str, callback: Callable[[], None]) -> bool:
        self._callbacks[hotkey_id] = callback
        success = bool(
            ctypes.windll.user32.RegisterHotKey(
                None,
                hotkey_id,
                MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                ord(key.upper()),
            )
        )
        if success:
            self._registered.add(hotkey_id)
        return success

    def close(self) -> None:
        for hotkey_id in self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()
        app = QCoreApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._filter)

