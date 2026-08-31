from __future__ import annotations

import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QThread, QTimer, Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .app_composition import ApplicationContext
from .desktop_activity import DesktopActivityReader, DesktopActivityWorker
from .hotkeys import GlobalHotkeys
from .ipc import HookEventServer
from .models import ServiceUpdate
from .logging_utils import configure_logging
from .paths import data_directory, database_path
from .power import SystemPowerMonitor
from .service import WaitLabService
from .ui import PetWindow, app_icon, create_tray


class DesktopActivityReceiver(QObject):
    """Deliver desktop-reader results on the GUI thread.

    ``DesktopActivityWorker`` lives in a QThread, while ``Storage`` and the
    widgets belong to the main thread.  A decorated QObject slot is important
    here: connecting a plain Python function can execute it in the emitter
    thread, which makes SQLite silently stop updating the UI after the first
    Codex prompt.
    """

    def __init__(self, window: PetWindow, service: WaitLabService) -> None:
        super().__init__(window)
        self.window = window
        self.service = service

    @Slot(object, object, object, object, object)
    def handle_poll(
        self,
        events: object,
        available: object,
        error: object,
        database: object,
        snapshots: object,
    ) -> None:
        source_path = database if isinstance(database, Path) else database_path()
        source_changed = bool(
            self.window.set_desktop_source_status(
                bool(available),
                str(error) if error else None,
                source_path,
            )
        )
        if isinstance(snapshots, tuple):
            self.window.set_desktop_snapshots(snapshots)
        for event in events if isinstance(events, list) else []:
            self.window.handle_desktop_event(event)
        if bool(available):
            # Reconcile terminal/stale rows even when WaitLAB missed the
            # original transition while it was closed or restarting.
            reconciliation = self.service.reconcile_desktop_sessions(
                snapshots if isinstance(snapshots, tuple) else ()
            )
            has_effect = any((
                reconciliation.show_task_picker,
                reconciliation.ai_completed,
                reconciliation.ai_blocked,
                reconciliation.ai_needs_attention,
                reconciliation.ai_resumed,
                reconciliation.focus_changed,
                reconciliation.message is not None,
            ))
            if has_effect:
                self.window.apply_update(reconciliation)
            elif source_changed:
                self.window.refresh()
        elif source_changed:
            self.window.refresh()


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
    logger = configure_logging(app_data)
    logger.info("WaitLAB starting")
    instance_lock = QLockFile(str(app_data / "waitlab.lock"))
    if not instance_lock.tryLock(100):
        QMessageBox.information(None, "WaitLAB", "WaitLAB 已经在运行，可从系统托盘打开。")
        return 0

    database = database_path()
    try:
        # Codex provides lifecycle events only; Waiting Task owns all user
        # facing timing. Keep legacy AI duration columns readable without
        # writing new Codex duration segments in the desktop application.
        context = ApplicationContext.open(database, track_ai_time=False)
        storage = context.storage
        try:
            purged_archives = storage.purge_archived_focus_sessions()
            if purged_archives:
                logger.info("Purged %d expired deleted focus records", purged_archives)
        except Exception:
            # Retention maintenance is best-effort and must not prevent the
            # main app from opening a usable database.
            logger.exception("Unable to purge deleted focus archives")
        try:
            purged_ai = storage.purge_ai_sessions()
            if purged_ai:
                logger.info("Purged %d expired Codex lifecycle rows", purged_ai)
        except Exception:
            logger.exception("Unable to purge Codex lifecycle rows")
    except Exception as exc:
        logger.exception("Unable to open local database: %s", database)
        backup = None
        if database.is_file():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup = database.with_name(f"waitlab-corrupt-{timestamp}.db")
            try:
                shutil.copy2(database, backup)
            except OSError:
                backup = None
        detail = f"\n已保存备份：{backup}" if backup else ""
        QMessageBox.critical(
            None,
            "WaitLAB 无法启动",
            f"本地数据文件无法打开：\n{exc}{detail}\n\n请查看日志目录：{app_data / 'logs'}",
        )
        instance_lock.unlock()
        return 1
    service = context.service
    window = PetWindow(service)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.warning(window, "WaitLAB", "系统托盘不可用，关闭窗口后程序将无法从托盘恢复。")
    tray = create_tray(window)
    window.set_tray(tray)

    event_server = HookEventServer(parent=window)
    event_server.event_received.connect(window.handle_hook_event)
    if not event_server.is_bound:
        window.set_hook_listener_error(event_server.error_string)
        window.show_notice(
            "WaitLAB 监听失败",
            event_server.error_string,
            level="error",
            duration=9.0,
        )

    desktop_reader = DesktopActivityReader()
    desktop_thread = QThread()
    desktop_worker = DesktopActivityWorker(desktop_reader, interval_ms=750)
    desktop_worker.moveToThread(desktop_thread)
    desktop_receiver = DesktopActivityReceiver(window, service)
    desktop_worker.poll_ready.connect(
        desktop_receiver.handle_poll,
        Qt.ConnectionType.QueuedConnection,
    )
    desktop_thread.started.connect(desktop_worker.start)
    desktop_thread.start()

    hotkeys = GlobalHotkeys()
    hotkeys.register(1, "W", window.manual_ai_start)
    hotkeys.register(2, "D", window.manual_ai_finish)
    hotkeys.register(3, "P", window.toggle_pause)

    heartbeat_timer = QTimer(app)
    heartbeat_timer.setInterval(5000)
    heartbeat_timer.timeout.connect(service.heartbeat)
    heartbeat_timer.start()

    maintenance_timer = QTimer(app)
    maintenance_timer.setInterval(60 * 60 * 1000)

    def run_maintenance() -> None:
        try:
            storage.purge_archived_focus_sessions()
            storage.purge_ai_sessions()
        except Exception:
            # Retention is best-effort; a transient database lock must not
            # interrupt the user's active Waiting Task.
            logger.exception("Unable to run periodic retention maintenance")

    maintenance_timer.timeout.connect(run_maintenance)
    maintenance_timer.start()

    power_monitor = SystemPowerMonitor()

    def on_suspend() -> None:
        update = service.pause_focus(message="电脑即将休眠，微任务已自动暂停")
        window.apply_update(update)
        if update.focus_changed:
            window.show_notice(
                "WaitLAB 已暂停",
                "检测到电脑休眠，微任务计时已自动暂停。",
                duration=4.0,
            )

    def on_resume() -> None:
        if service.focus is None:
            return
        window.apply_update(ServiceUpdate(message="电脑已恢复，微任务保持暂停"))
        window.show_notice(
            "WaitLAB 保持暂停",
            "电脑已恢复；确认回到当前任务后再继续计时。",
            duration=5.0,
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
        maintenance_timer.stop()
        desktop_worker.stop_requested.emit()
        desktop_thread.wait(2000)
        power_monitor.close()
        hotkeys.close()
        window.update_manager.shutdown(join_timeout=2.0)
        tray.hide()
        context.close()
        instance_lock.unlock()
        logger.info("WaitLAB stopped")

    window.quit_requested.connect(app.quit)
    app.aboutToQuit.connect(clean_up)
    window.show()
    if service.has_recovered_focus:
        QTimer.singleShot(0, window.show_recovery_prompt)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
