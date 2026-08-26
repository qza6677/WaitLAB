from __future__ import annotations

import sys

from PySide6.QtCore import QLockFile, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .desktop_activity import DesktopActivityReader
from .hotkeys import GlobalHotkeys
from .ipc import HookEventServer
from .models import ServiceUpdate
from .paths import data_directory, database_path
from .power import SystemPowerMonitor
from .service import WaitLabService
from .storage import Storage
from .ui import PetWindow, app_icon, create_tray


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("WaitLAB")
    app.setApplicationDisplayName("WaitLAB")
    app.setOrganizationName("WaitLAB")
    app.setWindowIcon(app_icon())
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setQuitOnLastWindowClosed(False)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    app_data = data_directory()
    app_data.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(app_data / "waitlab.lock"))
    if not instance_lock.tryLock(100):
        QMessageBox.information(None, "WaitLAB", "WaitLAB 已经在运行，可从系统托盘打开。")
        return 0

    storage = Storage(database_path())
    service = WaitLabService(storage)
    window = PetWindow(service)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(window, "WaitLAB", "系统托盘不可用，关闭窗口后程序将无法从托盘恢复。")
    tray = create_tray(window)
    window.set_tray(tray)

    event_server = HookEventServer(parent=window)
    event_server.event_received.connect(window.handle_hook_event)
    if not event_server.is_bound:
        window.set_hook_listener_error(event_server.error_string)
        tray.showMessage("WaitLAB 监听失败", event_server.error_string)

    desktop_reader = DesktopActivityReader()
    desktop_timer = QTimer(app)
    desktop_timer.setInterval(750)

    def poll_desktop_activity() -> None:
        events = desktop_reader.poll()
        window.set_desktop_source_status(
            desktop_reader.available,
            desktop_reader.error,
            desktop_reader.database,
        )
        for event in events:
            window.handle_desktop_event(event)

    desktop_timer.timeout.connect(poll_desktop_activity)
    desktop_timer.start()
    poll_desktop_activity()

    hotkeys = GlobalHotkeys()
    hotkeys.register(1, "W", window.manual_ai_start)
    hotkeys.register(2, "D", window.manual_ai_finish)
    hotkeys.register(3, "P", window.toggle_pause)

    heartbeat_timer = QTimer(app)
    heartbeat_timer.setInterval(5000)
    heartbeat_timer.timeout.connect(service.heartbeat)
    heartbeat_timer.start()

    power_monitor = SystemPowerMonitor()

    def on_suspend() -> None:
        update = service.pause_focus(message="电脑即将休眠，微任务已自动暂停")
        window.apply_update(update)
        if update.focus_changed:
            tray.showMessage(
                "WaitLAB 已暂停",
                "检测到电脑休眠，微任务计时已自动暂停。",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def on_resume() -> None:
        if service.focus is None:
            return
        window.apply_update(ServiceUpdate(message="电脑已恢复，微任务保持暂停"))
        tray.showMessage(
            "WaitLAB 保持暂停",
            "电脑已恢复；确认回到科研状态后再继续计时。",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    power_monitor.suspended.connect(on_suspend)
    power_monitor.resumed.connect(on_resume)

    cleaned_up = False

    def clean_up() -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        service.pause_focus(message="退出时已自动暂停")
        window.save_position()
        heartbeat_timer.stop()
        desktop_timer.stop()
        power_monitor.close()
        hotkeys.close()
        tray.hide()
        storage.close()
        instance_lock.unlock()

    window.quit_requested.connect(app.quit)
    app.aboutToQuit.connect(clean_up)
    window.show()
    if service.has_recovered_focus:
        QTimer.singleShot(0, window.show_recovery_prompt)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
