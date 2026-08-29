import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from waitlab.power import (
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMSUSPEND,
    SystemPowerMonitor,
)


def test_power_monitor_emits_one_suspend_and_keeps_resume_explicit():
    app = QApplication.instance() or QApplication([])
    monitor = SystemPowerMonitor()
    events: list[str] = []
    monitor.suspended.connect(lambda: events.append("suspended"))
    monitor.resumed.connect(lambda: events.append("resumed"))

    monitor._handle_power_event(PBT_APMSUSPEND)
    monitor._handle_power_event(PBT_APMSUSPEND)
    monitor._handle_power_event(PBT_APMRESUMEAUTOMATIC)
    app.processEvents()

    assert events == ["suspended", "resumed"]
    monitor.close()
