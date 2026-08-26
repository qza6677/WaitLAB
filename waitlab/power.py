from __future__ import annotations

from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal


WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMECRITICAL = 0x0006
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
RESUME_EVENTS = {
    PBT_APMRESUMECRITICAL,
    PBT_APMRESUMESUSPEND,
    PBT_APMRESUMEAUTOMATIC,
}


class _PowerEventFilter(QAbstractNativeEventFilter):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API name
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_POWERBROADCAST:
                self.callback(int(msg.wParam))
        return False, 0


class SystemPowerMonitor(QObject):
    suspended = Signal()
    resumed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._is_suspended = False
        self._filter = _PowerEventFilter(self._handle_power_event)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def _handle_power_event(self, event_code: int) -> None:
        if event_code == PBT_APMSUSPEND:
            if not self._is_suspended:
                self._is_suspended = True
                self.suspended.emit()
            return
        if event_code in RESUME_EVENTS and self._is_suspended:
            self._is_suspended = False
            self.resumed.emit()

    def close(self) -> None:
        app = QCoreApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._filter)

