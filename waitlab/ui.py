from __future__ import annotations

import math
import time
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .autostart import is_autostart_enabled, set_autostart
from .connection import HookConnectionInfo, HookConnectionMonitor, HookConnectionState
from .desktop_activity import DesktopActivityEvent, DesktopEventKind
from .models import DefaultTaskEntry, ServiceUpdate, Task, TaskKind, utc_now
from .preferences import PopupMode, Preferences
from .service import WaitLabService
from .storage import DEFAULT_TASKS


COLORS = {
    "ink": "#203133",
    "muted": "#748183",
    "cream": "#FFF9EF",
    "mint": "#63B89C",
    "mint_dark": "#367D69",
    "peach": "#F3A77C",
    "yellow": "#F6C85F",
    "line": "#E9E2D7",
    "white": "#FFFFFF",
}


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def app_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS["mint"]))
    painter.drawRoundedRect(QRectF(4, 4, size - 8, size - 8), 18, 18)
    painter.setBrush(QColor(COLORS["cream"]))
    painter.drawEllipse(QRectF(14, 17, size - 28, size - 25))
    painter.setBrush(QColor(COLORS["ink"]))
    painter.drawEllipse(QRectF(24, 30, 5, 7))
    painter.drawEllipse(QRectF(size - 29, 30, 5, 7))
    painter.end()
    return QIcon(pixmap)


class PetFace(QWidget):
    clicked = Signal()
    context_requested = Signal(QPoint)
    drag_started = Signal(QPoint)
    drag_moved = Signal(QPoint)
    drag_finished = Signal()

    def __init__(self, size: int = 58, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.mode = "idle"
        self.phase = 0.0
        self._press_position: QPoint | None = None
        self._dragging = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(90)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def _animate(self) -> None:
        self.phase += 0.16
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.globalPosition().toPoint()
            self._dragging = False
            self.drag_started.emit(self._press_position)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if (current - self._press_position).manhattanLength() >= 4:
                self._dragging = True
            if self._dragging:
                self.drag_moved.emit(current)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press_position is not None:
            self.drag_finished.emit()
            if not self._dragging:
                self.clicked.emit()
            self._press_position = None
            self._dragging = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width(), self.height()) / 74.0
        painter.scale(scale, scale)
        bob = math.sin(self.phase) * 2 if self.mode in {"waiting", "attention", "blocked"} else 0
        painter.translate(0, bob)

        accent = {
            "idle": QColor(COLORS["mint"]),
            "waiting": QColor(COLORS["yellow"]),
            "focus": QColor(COLORS["mint"]),
            "done": QColor(COLORS["peach"]),
            "paused": QColor("#B7A6D9"),
            "attention": QColor(COLORS["peach"]),
            "blocked": QColor("#D97862"),
        }.get(self.mode, QColor(COLORS["mint"]))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent.lighter(145))
        painter.drawEllipse(QRectF(4, 7, 66, 61))
        painter.setBrush(accent)
        left_ear = QPainterPath()
        left_ear.moveTo(14, 23)
        left_ear.lineTo(18, 5)
        left_ear.lineTo(31, 20)
        left_ear.closeSubpath()
        right_ear = QPainterPath()
        right_ear.moveTo(43, 20)
        right_ear.lineTo(56, 5)
        right_ear.lineTo(60, 24)
        right_ear.closeSubpath()
        painter.drawPath(left_ear)
        painter.drawPath(right_ear)

        painter.setBrush(QColor(COLORS["cream"]))
        painter.drawEllipse(QRectF(13, 19, 48, 43))
        painter.setBrush(QColor(COLORS["ink"]))
        eye_height = 2 if self.mode == "done" else 6
        painter.drawRoundedRect(QRectF(27, 35, 4, eye_height), 2, 2)
        painter.drawRoundedRect(QRectF(44, 35, 4, eye_height), 2, 2)
        painter.setPen(QPen(QColor(COLORS["ink"]), 2))
        if self.mode == "done":
            painter.drawArc(QRectF(32, 38, 12, 10), 200 * 16, 140 * 16)
        else:
            painter.drawArc(QRectF(34, 42, 8, 5), 200 * 16, 140 * 16)


class PresentationMode(StrEnum):
    ICON = "icon"
    PICKER = "picker"
    PLAYER = "player"


def choose_presentation_mode(has_focus: bool, picker_open: bool) -> PresentationMode:
    if has_focus:
        return PresentationMode.PLAYER
    if picker_open:
        return PresentationMode.PICKER
    return PresentationMode.ICON


class TaskManagerDialog(QDialog):
    tasks_changed = Signal()
    task_started = Signal(object)

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("WaitLAB · 任务池")
        self.setMinimumSize(500, 520)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(_dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("科研微任务")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("只要有未完成的手动任务，固定任务就不会出现。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("例如：核对图 3 的统计标注")
        self.input.returnPressed.connect(self._add_task)
        add_button = QPushButton("添加")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_task)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(add_button)
        layout.addLayout(input_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._start_selected())
        layout.addWidget(self.list_widget, 1)

        action_row = QHBoxLayout()
        start_button = QPushButton("开始所选")
        start_button.setObjectName("primaryButton")
        start_button.clicked.connect(self._start_selected)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self._delete_selected)
        action_row.addWidget(start_button)
        action_row.addWidget(delete_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        fallback_title = QLabel("没有手动任务时，将依次循环：")
        fallback_title.setObjectName("muted")
        self.fallback = QLabel()
        self.fallback.setWordWrap(True)
        self.fallback.setObjectName("fallback")
        layout.addWidget(fallback_title)
        layout.addWidget(self.fallback)
        self.refresh()

    def refresh(self) -> None:
        entries = self.service.storage.default_task_entries()
        enabled = [entry.title for entry in entries if entry.enabled]
        disabled_count = sum(not entry.enabled for entry in entries)
        if enabled:
            suffix = f"（另有 {disabled_count} 项已停用）" if disabled_count else ""
            self.fallback.setText("  ·  ".join(enabled) + suffix)
        else:
            self.fallback.setText("固定任务已全部停用，可在设置中重新启用。")
        self.list_widget.clear()
        tasks = self.service.storage.list_manual_tasks()
        if not tasks:
            placeholder = QListWidgetItem("还没有手动任务，将使用固定滚动任务")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(placeholder)
            return
        for task in tasks:
            item = QListWidgetItem(task.title)
            item.setData(Qt.ItemDataRole.UserRole, task)
            self.list_widget.addItem(item)

    def _add_task(self) -> None:
        try:
            self.service.storage.add_manual_task(self.input.text())
        except ValueError:
            self.input.setFocus()
            return
        self.input.clear()
        self.refresh()
        self.tasks_changed.emit()


class SettingsDialog(QDialog):
    settings_changed = Signal()

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("WaitLAB · 设置")
        self.setMinimumSize(560, 650)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(_dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)

        title = QLabel("日用设置")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("控制桌宠的提醒方式，并维护没有手动任务时使用的固定循环。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        behavior_title = QLabel("提醒与启动")
        behavior_title.setObjectName("sectionTitle")
        layout.addWidget(behavior_title)

        self.popup_mode = QComboBox()
        self.popup_mode.addItem("弹出并置顶", PopupMode.RAISE.value)
        self.popup_mode.addItem("静默显示，不主动置顶", PopupMode.QUIET.value)
        self.popup_mode.addItem("仅托盘提醒", PopupMode.TRAY_ONLY.value)
        self.completion_notifications = QCheckBox("Codex 完成或中断时显示系统通知")
        self.notification_sound = QCheckBox("提醒时播放提示音")
        self.autostart = QCheckBox("登录 Windows 后自动启动 WaitLAB")
        layout.addWidget(QLabel("收到新的 Codex 指令时："))
        layout.addWidget(self.popup_mode)
        layout.addWidget(self.completion_notifications)
        layout.addWidget(self.notification_sound)
        layout.addWidget(self.autostart)

        fixed_header = QHBoxLayout()
        fixed_title = QLabel("固定循环任务")
        fixed_title.setObjectName("sectionTitle")
        fixed_help = QLabel("勾选启用；列表顺序就是轮播顺序")
        fixed_help.setObjectName("muted")
        fixed_header.addWidget(fixed_title)
        fixed_header.addStretch()
        fixed_header.addWidget(fixed_help)
        layout.addLayout(fixed_header)

        self.fixed_list = QListWidget()
        self.fixed_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fixed_list.itemDoubleClicked.connect(lambda _item: self._rename_selected())
        layout.addWidget(self.fixed_list, 1)

        fixed_controls = QHBoxLayout()
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add_fixed_task)
        rename_button = QPushButton("重命名")
        rename_button.clicked.connect(self._rename_selected)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self._delete_selected)
        up_button = QPushButton("上移")
        up_button.clicked.connect(lambda: self._move_selected(-1))
        down_button = QPushButton("下移")
        down_button.clicked.connect(lambda: self._move_selected(1))
        reset_button = QPushButton("恢复默认")
        reset_button.clicked.connect(self._reset_defaults)
        for button in (add_button, rename_button, delete_button, up_button, down_button):
            fixed_controls.addWidget(button)
        fixed_controls.addStretch()
        fixed_controls.addWidget(reset_button)
        layout.addLayout(fixed_controls)

        actions = QHBoxLayout()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("保存设置")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        actions.addStretch()
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        preferences = Preferences.load(self.service.storage)
        mode_index = self.popup_mode.findData(preferences.popup_mode.value)
        self.popup_mode.setCurrentIndex(max(0, mode_index))
        self.completion_notifications.setChecked(preferences.completion_notifications)
        self.notification_sound.setChecked(preferences.notification_sound)
        self.autostart.setChecked(is_autostart_enabled())
        self._fill_fixed_tasks(self.service.storage.default_task_entries())

    def _fill_fixed_tasks(self, entries: list[DefaultTaskEntry]) -> None:
        self.fixed_list.clear()
        for entry in entries:
            item = QListWidgetItem(entry.title)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked
            )
            self.fixed_list.addItem(item)

    def _add_fixed_task(self) -> None:
        title, accepted = QInputDialog.getText(self, "添加固定任务", "任务名称：")
        clean_title = " ".join(title.strip().split())
        if not accepted or not clean_title:
            return
        if self._has_title(clean_title):
            QMessageBox.information(self, "任务已存在", "固定循环中已经有同名任务。")
            return
        item = QListWidgetItem(clean_title)
        item.setFlags(
            item.flags() | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(Qt.CheckState.Checked)
        self.fixed_list.addItem(item)
        self.fixed_list.setCurrentItem(item)

    def _rename_selected(self) -> None:
        item = self.fixed_list.currentItem()
        if item is None:
            return
        title, accepted = QInputDialog.getText(
            self,
            "重命名固定任务",
            "任务名称：",
            text=item.text(),
        )
        clean_title = " ".join(title.strip().split())
        if not accepted or not clean_title:
            return
        if self._has_title(clean_title, except_item=item):
            QMessageBox.information(self, "任务已存在", "固定循环中已经有同名任务。")
            return
        item.setText(clean_title)

    def _delete_selected(self) -> None:
        row = self.fixed_list.currentRow()
        if row >= 0:
            self.fixed_list.takeItem(row)

    def _move_selected(self, offset: int) -> None:
        row = self.fixed_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.fixed_list.count():
            return
        item = self.fixed_list.takeItem(row)
        self.fixed_list.insertItem(target, item)
        self.fixed_list.setCurrentRow(target)

    def _reset_defaults(self) -> None:
        self._fill_fixed_tasks([DefaultTaskEntry(title) for title in DEFAULT_TASKS])

    def _has_title(
        self,
        title: str,
        except_item: QListWidgetItem | None = None,
    ) -> bool:
        return any(
            self.fixed_list.item(index) is not except_item
            and self.fixed_list.item(index).text().strip() == title
            for index in range(self.fixed_list.count())
        )

    def _save(self) -> None:
        entries: list[DefaultTaskEntry] = []
        seen: set[str] = set()
        for index in range(self.fixed_list.count()):
            item = self.fixed_list.item(index)
            title = " ".join(item.text().strip().split())
            if not title:
                QMessageBox.warning(self, "无法保存", "固定任务名称不能为空。")
                return
            if title in seen:
                QMessageBox.warning(self, "无法保存", f"固定任务重复：{title}")
                return
            seen.add(title)
            entries.append(
                DefaultTaskEntry(title, item.checkState() == Qt.CheckState.Checked)
            )

        preferences = Preferences(
            popup_mode=PopupMode(str(self.popup_mode.currentData())),
            completion_notifications=self.completion_notifications.isChecked(),
            notification_sound=self.notification_sound.isChecked(),
        )
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as exc:
            QMessageBox.critical(self, "开机启动设置失败", str(exc))
            return
        self.service.storage.set_default_task_entries(entries)
        preferences.save(self.service.storage)
        self.settings_changed.emit()
        self.accept()

    def _selected_task(self) -> Task | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _start_selected(self) -> None:
        task = self._selected_task()
        if task is not None:
            self.task_started.emit(task)

    def _delete_selected(self) -> None:
        task = self._selected_task()
        if task is None or task.id is None:
            return
        self.service.storage.delete_manual_task(task.id)
        self.refresh()
        self.tasks_changed.emit()


class PetWindow(QWidget):
    quit_requested = Signal()

    def __init__(self, service: WaitLabService) -> None:
        super().__init__()
        self.service = service
        self.tray: QSystemTrayIcon | None = None
        self.task_dialog: TaskManagerDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.task_picker_open = False
        self._suggestion_signature: tuple[tuple[int | None, str, str], ...] | None = None
        self.completion_banner_until = 0.0
        self.last_message = "等待下一次 Codex 指令"
        self._drag_origin: QPoint | None = None
        self.hook_monitor = HookConnectionMonitor(service.storage)
        self._hook_info: HookConnectionInfo | None = None
        self._next_connection_check = 0.0
        self._desktop_source_available: bool | None = None
        self._desktop_source_error: str | None = None
        self._desktop_source_path: Path | None = None
        self._ai_attention = False
        self.presentation_mode: PresentationMode | None = None

        self.setWindowTitle("WaitLAB")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(82)
        self._build_ui()
        self._restore_position()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)
        self.refresh()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.card = QFrame()
        self.card.setObjectName("mainCard")
        self.card.setProperty("presentation", PresentationMode.ICON.value)
        self.card.setStyleSheet(_window_stylesheet())
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(40, 55, 50, 55))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.pet = PetFace(58)
        self.pet.clicked.connect(self.toggle_picker)
        self.pet.context_requested.connect(self.show_pet_menu)
        self.pet.drag_started.connect(self._begin_pet_drag)
        self.pet.drag_moved.connect(self._move_pet_drag)
        self.pet.drag_finished.connect(self._finish_pet_drag)
        header.addWidget(self.pet)

        self.header_details = QWidget()
        titles = QVBoxLayout(self.header_details)
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self.state_label = QLabel("空闲")
        self.state_label.setObjectName("stateTitle")
        title_row.addWidget(self.state_label, 1)
        tasks_button = QPushButton("任务")
        tasks_button.setObjectName("ghostButton")
        tasks_button.clicked.connect(self.open_task_manager)
        settings_button = QPushButton("设置")
        settings_button.setObjectName("ghostButton")
        settings_button.clicked.connect(self.open_settings)
        hide_button = QPushButton("—")
        hide_button.setObjectName("iconButton")
        hide_button.setFixedWidth(28)
        hide_button.clicked.connect(self.hide)
        title_row.addWidget(tasks_button)
        title_row.addWidget(settings_button)
        title_row.addWidget(hide_button)
        titles.addLayout(title_row)
        self.message_label = QLabel(self.last_message)
        self.message_label.setObjectName("muted")
        self.message_label.setWordWrap(True)
        titles.addWidget(self.message_label)
        self.connection_status_button = QPushButton("Codex · 检查中")
        self.connection_status_button.setObjectName("connectionStatusButton")
        self.connection_status_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connection_status_button.clicked.connect(self.show_codex_connection_info)
        titles.addWidget(self.connection_status_button, 0, Qt.AlignmentFlag.AlignLeft)
        header.addWidget(self.header_details, 1)

        self.focus_card = QFrame()
        self.focus_card.setObjectName("focusCard")
        self.focus_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        focus_layout = QHBoxLayout(self.focus_card)
        focus_layout.setContentsMargins(10, 7, 7, 7)
        focus_layout.setSpacing(7)
        focus_info = QVBoxLayout()
        focus_info.setSpacing(1)
        self.focus_title = QLabel()
        self.focus_title.setObjectName("focusTitle")
        self.focus_title.setFixedWidth(165)
        self.focus_time = QLabel("00:00")
        self.focus_time.setObjectName("timerCompact")
        focus_info.addWidget(self.focus_title)
        focus_info.addWidget(self.focus_time)
        focus_layout.addLayout(focus_info, 1)
        self.pause_button = QPushButton("暂停")
        self.pause_button.setObjectName("playerButton")
        self.pause_button.setToolTip("暂停或继续微任务")
        self.pause_button.clicked.connect(self.toggle_pause)
        complete_button = QPushButton("完成")
        complete_button.setObjectName("playerPrimaryButton")
        complete_button.setToolTip("完成当前微任务")
        complete_button.clicked.connect(self.complete_focus)
        abandon_button = QPushButton("×")
        abandon_button.setObjectName("playerCloseButton")
        abandon_button.setToolTip("取消本次计时并放回任务池")
        abandon_button.setFixedWidth(30)
        abandon_button.clicked.connect(self.abandon_focus)
        focus_layout.addWidget(self.pause_button)
        focus_layout.addWidget(complete_button)
        focus_layout.addWidget(abandon_button)
        header.addWidget(self.focus_card, 1)
        layout.addLayout(header)

        self.ai_card = QFrame()
        self.ai_card.setObjectName("aiCard")
        self.ai_card.setProperty("attention", False)
        self.ai_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        ai_layout = QHBoxLayout(self.ai_card)
        ai_layout.setContentsMargins(13, 10, 13, 10)
        self.ai_status_label = QLabel("Codex 正在工作")
        self.ai_status_label.setObjectName("cardLabel")
        self.ai_time_label = QLabel("00:00")
        self.ai_time_label.setObjectName("timerSmall")
        ai_layout.addWidget(self.ai_status_label)
        ai_layout.addStretch()
        ai_layout.addWidget(self.ai_time_label)
        layout.addWidget(self.ai_card)

        self.picker = QFrame()
        self.picker.setObjectName("picker")
        self.picker.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        picker_layout = QVBoxLayout(self.picker)
        picker_layout.setContentsMargins(2, 4, 2, 2)
        picker_layout.setSpacing(8)
        picker_header = QHBoxLayout()
        self.picker_title = QLabel("选一个等待任务")
        self.picker_title.setObjectName("sectionTitle")
        self.picker_source = QLabel()
        self.picker_source.setObjectName("sourcePill")
        picker_header.addWidget(self.picker_title)
        picker_header.addStretch()
        picker_header.addWidget(self.picker_source)
        picker_layout.addLayout(picker_header)
        self.suggestion_container = QWidget()
        self.suggestion_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self.suggestion_layout = QVBoxLayout(self.suggestion_container)
        self.suggestion_layout.setContentsMargins(0, 0, 0, 0)
        self.suggestion_layout.setSpacing(7)
        picker_layout.addWidget(self.suggestion_container)
        later_button = QPushButton("本轮跳过")
        later_button.setObjectName("linkButton")
        later_button.clicked.connect(self.skip_current_round)
        picker_layout.addWidget(later_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.picker)

        self.footer_widget = QWidget()
        footer = QHBoxLayout(self.footer_widget)
        footer.setContentsMargins(0, 0, 0, 0)
        start_ai = QPushButton("手动开始等待")
        start_ai.setObjectName("ghostButton")
        start_ai.clicked.connect(self.manual_ai_start)
        finish_ai = QPushButton("AI 已完成")
        finish_ai.setObjectName("ghostButton")
        finish_ai.clicked.connect(self.manual_ai_finish)
        self.today_label = QLabel("今日回收 0 分钟")
        self.today_label.setObjectName("muted")
        footer.addWidget(start_ai)
        footer.addWidget(finish_ai)
        footer.addStretch()
        footer.addWidget(self.today_label)
        layout.addWidget(self.footer_widget)

    def set_tray(self, tray: QSystemTrayIcon) -> None:
        self.tray = tray

    def set_hook_listener_error(self, error: str) -> None:
        self.hook_monitor.set_listener_error(error)
        self._update_connection_status(force=True)

    def set_desktop_source_status(
        self,
        available: bool,
        error: str | None,
        database: Path,
    ) -> None:
        changed = (
            self._desktop_source_available != available
            or self._desktop_source_error != error
            or self._desktop_source_path != database
        )
        self._desktop_source_available = available
        self._desktop_source_error = error
        self._desktop_source_path = database
        if changed:
            self._update_connection_status(force=True)

    def handle_hook_event(self, payload: dict) -> None:
        event_name = payload.get("event")
        if not isinstance(event_name, str):
            return
        self.hook_monitor.record_event(event_name)
        self._update_connection_status(force=True)
        session_id = str(payload.get("session_id") or "codex")
        turn_id = str(payload.get("turn_id") or f"unknown-{time.time_ns()}")
        if event_name == "UserPromptSubmit":
            update = self.service.on_ai_started(session_id, turn_id)
        elif event_name == "PermissionRequest":
            update = self.service.on_ai_needs_attention(session_id, turn_id)
        elif event_name == "PostToolUse":
            update = self.service.on_ai_resumed(turn_id)
        elif event_name == "Stop":
            update = self.service.on_ai_finished(turn_id)
        else:
            return
        self.apply_update(update)

    def handle_desktop_event(self, event: DesktopActivityEvent) -> None:
        if event.kind is DesktopEventKind.STARTED:
            update = self.service.on_ai_started(
                event.thread_id,
                event.turn_id,
                when=event.started_at,
            )
        elif event.kind is DesktopEventKind.NEEDS_ATTENTION:
            update = self.service.on_ai_needs_attention(
                event.thread_id,
                event.turn_id,
                when=event.occurred_at,
                fallback_latest=False,
            )
        elif event.kind is DesktopEventKind.RESUMED:
            update = self.service.on_ai_resumed(
                event.turn_id,
                fallback_latest=False,
            )
        elif event.kind in {DesktopEventKind.COMPLETED, DesktopEventKind.BLOCKED}:
            update = self.service.on_ai_finished(
                event.turn_id,
                when=event.occurred_at,
                status=(
                    "completed"
                    if event.kind is DesktopEventKind.COMPLETED
                    else event.status
                ),
                fallback_latest=False,
                session_id=event.thread_id,
                started_at=event.started_at,
                create_if_missing=True,
            )
        else:
            return
        self.apply_update(update)

    def apply_update(self, update: ServiceUpdate) -> None:
        if update.message:
            self.last_message = update.message
        if update.show_task_picker:
            preferences = Preferences.load(self.service.storage)
            self.task_picker_open = preferences.popup_mode is not PopupMode.TRAY_ONLY
            if self.task_picker_open:
                self._set_presentation_mode(PresentationMode.PICKER)
            if preferences.popup_mode is PopupMode.RAISE:
                self.show()
                self.raise_()
                self.activateWindow()
            elif preferences.popup_mode is PopupMode.QUIET:
                self.show()
            elif self.tray is not None:
                self.tray.showMessage(
                    "Codex 正在工作",
                    "点击 WaitLAB 选择一个等待微任务，或忽略本轮。",
                    QSystemTrayIcon.MessageIcon.Information,
                    4500,
                )
                self._play_notification_sound(preferences)
        if update.ai_completed or update.ai_blocked:
            self.completion_banner_until = time.monotonic() + 12
            if self.service.focus is None:
                self.task_picker_open = False
            preferences = Preferences.load(self.service.storage)
            if preferences.completion_notifications:
                if self.tray is not None:
                    self.tray.showMessage(
                        "Codex 已完成" if update.ai_completed else "Codex 已中断",
                        (
                            "微任务仍在继续计时，做到自然断点再回来。"
                            if update.ai_completed and self.service.focus is not None
                            else "可以回到 Codex 查看结果。"
                            if update.ai_completed
                            else "微任务仍在继续计时；可回到 Codex 查看原因。"
                            if self.service.focus is not None
                            else "请回到 Codex 查看中断或失败原因。"
                        ),
                        QSystemTrayIcon.MessageIcon.Information
                        if update.ai_completed
                        else QSystemTrayIcon.MessageIcon.Warning,
                        5000,
                    )
                self._play_notification_sound(preferences)
        if update.ai_needs_attention:
            preferences = Preferences.load(self.service.storage)
            if self.tray is not None:
                self.tray.showMessage(
                    "Codex 等待批准",
                    "请回到 Codex 处理权限请求；当前微任务仍在继续计时。",
                    QSystemTrayIcon.MessageIcon.Warning,
                    7000,
                )
                self._play_notification_sound(preferences)
        self.refresh()

    @staticmethod
    def _play_notification_sound(preferences: Preferences) -> None:
        if preferences.notification_sound:
            QApplication.beep()

    def refresh(self) -> None:
        focus = self.service.focus
        open_ai = self.service.storage.get_open_ai()
        completed_visible = time.monotonic() < self.completion_banner_until
        terminal_blocked = (
            completed_visible
            and self.service.last_ai_terminal_status not in {None, "completed"}
        )
        needs_attention = open_ai is not None and open_ai.status == "needs_attention"

        if needs_attention:
            mode = "attention"
            state = "Codex 等待批准"
        elif focus is not None:
            mode = (
                "blocked"
                if terminal_blocked
                else "done"
                if completed_visible
                else "waiting"
                if open_ai is not None
                else "paused"
                if focus.is_paused
                else "focus"
            )
            state = "微任务已暂停" if focus.is_paused else "正在回收等待时间"
        elif open_ai is not None:
            mode = "waiting"
            state = "Codex 正在工作"
        elif completed_visible:
            mode = "blocked" if terminal_blocked else "done"
            state = "Codex 已中断" if terminal_blocked else "Codex 已完成"
        else:
            mode = "idle"
            state = "等待下一轮"

        self.pet.set_mode(mode)
        self.state_label.setText(state)
        self.message_label.setText(self.last_message)

        self.ai_card.setVisible(False)
        if open_ai is not None:
            self.ai_status_label.setText(
                "需要你的操作 · 微任务继续" if needs_attention else "Codex 正在工作"
            )
            self.ai_time_label.setText(format_duration(open_ai.elapsed_seconds()))
        elif completed_visible:
            self.ai_status_label.setText(
                "Codex 已中断 · 微任务继续"
                if terminal_blocked and focus is not None
                else "Codex 已中断或运行失败"
                if terminal_blocked
                else "Codex 已完成 · 微任务继续"
                if focus is not None
                else "Codex 已完成"
            )
            self.ai_time_label.setText(
                format_duration(self.service.last_ai_completion_seconds or 0)
            )
        highlighted = needs_attention or terminal_blocked
        if highlighted != self._ai_attention:
            self._ai_attention = highlighted
            self.ai_card.setProperty("attention", highlighted)
            self.ai_card.style().unpolish(self.ai_card)
            self.ai_card.style().polish(self.ai_card)

        if focus is not None:
            elided_title = self.focus_title.fontMetrics().elidedText(
                focus.task.title,
                Qt.TextElideMode.ElideRight,
                self.focus_title.width(),
            )
            self.focus_title.setText(elided_title)
            self.focus_title.setToolTip(focus.task.title)
            self.focus_time.setText(format_duration(focus.elapsed_seconds()))
            self.pause_button.setText("继续" if focus.is_paused else "暂停")

        picker_visible = self.task_picker_open and focus is None
        if picker_visible:
            self._refresh_suggestions()

        self._set_presentation_mode(
            choose_presentation_mode(focus is not None, picker_visible)
        )

        minutes = int(self.service.storage.today_focus_seconds() // 60)
        self.today_label.setText(f"今日回收 {minutes} 分钟")
        self._update_connection_status()
        self.card.layout().invalidate()
        self.card.layout().activate()
        QTimer.singleShot(0, self._fit_to_content)

    def _fit_to_content(self) -> None:
        self.card.layout().invalidate()
        self.card.layout().activate()
        self.setFixedHeight(self.card.sizeHint().height() + 16)

    def _set_presentation_mode(self, mode: PresentationMode) -> None:
        if self.presentation_mode is mode:
            return
        self.presentation_mode = mode
        is_picker = mode is PresentationMode.PICKER
        is_player = mode is PresentationMode.PLAYER
        self.header_details.setVisible(is_picker)
        self.focus_card.setVisible(is_player)
        self.picker.setVisible(is_picker)
        self.ai_card.setVisible(False)
        self.footer_widget.setVisible(False)

        if mode is PresentationMode.ICON:
            width = 82
            margins = (4, 4, 4, 4)
            opacity = 0.88
        elif mode is PresentationMode.PLAYER:
            width = 410
            margins = (7, 6, 7, 6)
            opacity = 0.96
        else:
            width = 430
            margins = (16, 14, 16, 14)
            opacity = 1.0

        self.card.layout().setContentsMargins(*margins)
        self.card.setProperty("presentation", mode.value)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        self.setWindowOpacity(opacity)
        self.setFixedWidth(width)
        self.card.layout().invalidate()
        self.card.layout().activate()
        QTimer.singleShot(0, self._fit_to_content)
        QTimer.singleShot(0, self._snap_to_left_edge)

    def _update_connection_status(self, force: bool = False) -> None:
        current = time.monotonic()
        if not force and current < self._next_connection_check:
            return
        self._next_connection_check = current + 2.0
        info = self.hook_monitor.inspect()
        self._hook_info = info
        if self._desktop_source_available is True:
            label = "Codex · 已连接"
            detail = "桌面端本机状态源正常；不读取对话正文。"
            state = "connected"
        elif info.state is HookConnectionState.CONNECTED:
            label = "Codex · Hook 兜底"
            detail = "桌面状态源不可用，当前由可选 Hook 提供事件。"
            state = "fallback"
        elif self._desktop_source_available is None:
            label = "Codex · 检查中"
            detail = "正在检查桌面端本机状态源。"
            state = "checking"
        else:
            label = "Codex · 降级模式"
            detail = "自动状态源暂不可用；手动按钮和快捷键仍可使用。"
            state = "degraded"
        self.connection_status_button.setText(label)
        self.connection_status_button.setToolTip(detail)
        self.connection_status_button.setProperty("state", state)
        self.connection_status_button.style().unpolish(self.connection_status_button)
        self.connection_status_button.style().polish(self.connection_status_button)

    def show_codex_connection_info(self) -> None:
        self._update_connection_status(force=True)
        info = self._hook_info
        if info is None:
            return
        configured = "、".join(info.configured_events) or "无"
        last_event = "尚未收到"
        if info.last_event_at is not None:
            local_time = info.last_event_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            last_event = f"{info.last_event_name or '未知事件'} · {local_time}"
        desktop_state = "正常（主连接）" if self._desktop_source_available else "不可用"
        desktop_error = self._desktop_source_error or "无"
        source_path = self._desktop_source_path or "尚未检查"
        dialog = QMessageBox(self)
        dialog.setWindowTitle("WaitLAB · Codex 连接")
        dialog.setIcon(
            QMessageBox.Icon.Warning
            if self._desktop_source_available is False
            and info.state is not HookConnectionState.CONNECTED
            else QMessageBox.Icon.Information
        )
        dialog.setText(self.connection_status_button.text())
        dialog.setInformativeText(
            f"桌面状态源：{desktop_state}\n"
            f"状态库：{source_path}\n"
            f"读取范围：thread_id、turn_id、status、开始/结束时间\n"
            f"错误：{desktop_error}\n\n"
            f"可选 Hook：{info.label}\n"
            f"配置文件：{info.hooks_path}\n"
            f"已配置事件：{configured}\n"
            f"最近事件：{last_event}\n\n"
            "隐私说明：主连接不会查询消息、标题、工作目录、错误详情或 item JSON。\n"
            "Hook 只用于兼容和权限等待增强；Windows 桌面端不需要 /hooks。"
        )
        dialog.exec()

    def _refresh_suggestions(self) -> None:
        tasks = self.service.suggested_tasks()
        signature = tuple((task.id, task.title, task.kind.value) for task in tasks)
        if signature == self._suggestion_signature:
            return
        self._suggestion_signature = signature
        while self.suggestion_layout.count():
            item = self.suggestion_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        source = "手动任务" if tasks and tasks[0].kind is TaskKind.MANUAL else "固定循环"
        self.picker_source.setText(source)
        if not tasks:
            empty = QLabel("暂无可用任务。请添加手动任务，或在设置中启用固定任务。")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.suggestion_layout.addWidget(empty)
            configure = QPushButton("打开设置")
            configure.clicked.connect(self.open_settings)
            self.suggestion_layout.addWidget(configure)
            return
        for task in tasks:
            button = QPushButton(task.title)
            button.setObjectName("taskButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, selected=task: self.start_focus(selected))
            self.suggestion_layout.addWidget(button)
        self.suggestion_layout.invalidate()
        self.suggestion_container.updateGeometry()
        self.picker.layout().invalidate()
        self.picker.updateGeometry()
        self.card.layout().invalidate()

    def start_focus(self, task: Task) -> None:
        if self.service.focus is not None:
            self.last_message = "先完成或放回当前微任务"
            self.refresh()
            return
        self.task_picker_open = False
        self.apply_update(self.service.start_focus(task))
        if self.task_dialog is not None:
            self.task_dialog.hide()

    def toggle_pause(self) -> None:
        self.apply_update(self.service.toggle_focus_pause())

    def complete_focus(self) -> None:
        self.apply_update(self.service.complete_focus())
        self._suggestion_signature = None
        if self.task_dialog is not None:
            self.task_dialog.refresh()

    def abandon_focus(self) -> None:
        self.apply_update(self.service.abandon_focus())
        self._suggestion_signature = None

    def manual_ai_start(self) -> None:
        self.apply_update(self.service.manual_ai_started())

    def manual_ai_finish(self) -> None:
        self.apply_update(self.service.manual_ai_finished())

    def toggle_picker(self) -> None:
        if self.service.focus is None:
            self.task_picker_open = not self.task_picker_open
            self.refresh()

    def show_pet_menu(self, global_position: QPoint) -> None:
        menu = QMenu(self)
        if self.service.focus is not None:
            pause_label = "继续微任务" if self.service.focus.is_paused else "暂停微任务"
            pause_action = menu.addAction(pause_label)
            pause_action.triggered.connect(self.toggle_pause)
            complete_action = menu.addAction("完成微任务")
            complete_action.triggered.connect(self.complete_focus)
            cancel_action = menu.addAction("取消并放回")
            cancel_action.triggered.connect(self.abandon_focus)
            menu.addSeparator()
        else:
            picker_action = menu.addAction("选择微任务")
            picker_action.triggered.connect(self.toggle_picker)
        tasks_action = menu.addAction("任务池")
        tasks_action.triggered.connect(self.open_task_manager)
        settings_action = menu.addAction("设置")
        settings_action.triggered.connect(self.open_settings)
        menu.addSeparator()
        hide_action = menu.addAction("隐藏到托盘")
        hide_action.triggered.connect(self.hide)
        quit_action = menu.addAction("退出 WaitLAB")
        quit_action.triggered.connect(self.quit_requested)
        menu.exec(global_position)

    def close_picker(self) -> None:
        self.task_picker_open = False
        self.refresh()

    def skip_current_round(self) -> None:
        self.task_picker_open = False
        self.apply_update(self.service.skip_current_ai_round())

    def open_task_manager(self) -> None:
        if self.task_dialog is None:
            self.task_dialog = TaskManagerDialog(self.service, self)
            self.task_dialog.tasks_changed.connect(self._tasks_changed)
            self.task_dialog.task_started.connect(self.start_focus)
        self.task_dialog.refresh()
        self.task_dialog.show()
        self.task_dialog.raise_()
        self.task_dialog.activateWindow()

    def open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.service, self)
            self.settings_dialog.settings_changed.connect(self._settings_changed)
        self.settings_dialog.refresh()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def show_recovery_prompt(self) -> None:
        focus = self.service.focus
        if focus is None or not self.service.has_recovered_focus:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("继续上次的微任务？")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(f"上次的微任务尚未结束：\n\n{focus.task.title}")
        dialog.setInformativeText(
            f"已记录 {format_duration(focus.elapsed_seconds())}。离线时间没有计入。"
        )
        continue_button = dialog.addButton("继续任务", QMessageBox.ButtonRole.AcceptRole)
        end_button = dialog.addButton("结束并放回", QMessageBox.ButtonRole.DestructiveRole)
        pause_button = dialog.addButton("保持暂停", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(continue_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is continue_button:
            self.apply_update(self.service.resume_focus(message="已继续上次的微任务"))
        elif clicked is end_button:
            self.apply_update(self.service.abandon_focus())
        elif clicked is pause_button:
            self.apply_update(ServiceUpdate(message="上次的微任务保持暂停"))

    def _tasks_changed(self) -> None:
        self._suggestion_signature = None
        self.refresh()

    def _settings_changed(self) -> None:
        self._suggestion_signature = None
        if self.task_dialog is not None:
            self.task_dialog.refresh()
        self.last_message = "设置已保存"
        self.refresh()

    def save_position(self) -> None:
        self.service.storage.set_setting("window_x", str(self.x()))
        self.service.storage.set_setting("window_y", str(self.y()))

    def _restore_position(self) -> None:
        x_value = self.service.storage.get_setting("window_x", "")
        y_value = self.service.storage.get_setting("window_y", "")
        saved_point = QPoint(int(x_value), int(y_value)) if x_value and y_value else None
        screen = QApplication.screenAt(saved_point) if saved_point is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            preferred_y = saved_point.y() if saved_point is not None else geometry.top() + 180
            maximum_y = max(geometry.top() + 10, geometry.bottom() - self.height() - 10)
            y = min(max(preferred_y, geometry.top() + 10), maximum_y)
            self.move(geometry.left() + 10, y)

    def _begin_pet_drag(self, global_position: QPoint) -> None:
        self._drag_origin = global_position - self.frameGeometry().topLeft()

    def _move_pet_drag(self, global_position: QPoint) -> None:
        if self._drag_origin is not None:
            self.move(global_position - self._drag_origin)

    def _finish_pet_drag(self) -> None:
        self._drag_origin = None
        self._snap_to_left_edge()
        self.save_position()

    def _snap_to_left_edge(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        maximum_y = max(geometry.top() + 10, geometry.bottom() - self.height() - 10)
        y = min(max(self.y(), geometry.top() + 10), maximum_y)
        self.move(geometry.left() + 10, y)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.presentation_mode is PresentationMode.ICON:
            self.setWindowOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.presentation_mode is PresentationMode.ICON:
            self.setWindowOpacity(0.88)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 105:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None:
            self._drag_origin = None
            self._snap_to_left_edge()
            self.save_position()
        super().mouseReleaseEvent(event)


def create_tray(window: PetWindow) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(app_icon(), window)
    tray.setToolTip("WaitLAB · 把等待变成科研进度")
    menu = QMenu()
    show_action = menu.addAction("显示 WaitLAB")
    show_action.triggered.connect(window.show)
    settings_action = menu.addAction("设置")
    settings_action.triggered.connect(window.open_settings)
    menu.addSeparator()
    start_action = menu.addAction("手动开始等待")
    start_action.triggered.connect(window.manual_ai_start)
    finish_action = menu.addAction("AI 已完成")
    finish_action.triggered.connect(window.manual_ai_finish)
    pause_action = menu.addAction("暂停 / 继续微任务")
    pause_action.triggered.connect(window.toggle_pause)
    menu.addSeparator()
    quit_action = menu.addAction("退出")
    quit_action.triggered.connect(window.quit_requested)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.show()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()
    return tray


def _window_stylesheet() -> str:
    return f"""
    QFrame#mainCard {{
        background: {COLORS['cream']};
        border: 1px solid {COLORS['line']};
        border-radius: 22px;
    }}
    QFrame#mainCard[presentation="icon"] {{
        border-radius: 30px;
    }}
    QFrame#mainCard[presentation="player"] {{
        border-radius: 18px;
    }}
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI'; }}
    QLabel#stateTitle {{ font-size: 17px; font-weight: 700; }}
    QLabel#muted {{ color: {COLORS['muted']}; font-size: 11px; }}
    QLabel#sectionTitle {{ font-size: 14px; font-weight: 700; }}
    QLabel#eyebrow {{ color: {COLORS['mint_dark']}; font-size: 10px; font-weight: 700; }}
    QLabel#focusTitle {{ font-size: 14px; font-weight: 650; }}
    QLabel#timerLarge {{ font-family: 'Cascadia Mono'; font-size: 29px; font-weight: 700; }}
    QLabel#timerSmall {{ font-family: 'Cascadia Mono'; font-size: 16px; font-weight: 700; }}
    QLabel#timerCompact {{ color: {COLORS['muted']}; font-family: 'Cascadia Mono'; font-size: 13px; font-weight: 700; }}
    QLabel#cardLabel {{ font-size: 12px; font-weight: 650; }}
    QLabel#sourcePill {{
        color: {COLORS['mint_dark']}; background: #DDF1E9; border-radius: 8px;
        padding: 3px 8px; font-size: 10px; font-weight: 650;
    }}
    QFrame#aiCard {{ background: #FFF1D1; border-radius: 13px; }}
    QFrame#aiCard[attention="true"] {{ background: #FFE2D2; border: 1px solid {COLORS['peach']}; }}
    QFrame#focusCard {{ background: {COLORS['white']}; border: 1px solid {COLORS['line']}; border-radius: 15px; }}
    QPushButton {{
        color: {COLORS['ink']}; background: {COLORS['white']};
        border: 1px solid {COLORS['line']}; border-radius: 9px;
        padding: 7px 11px; font-family: 'Microsoft YaHei UI'; font-size: 11px;
    }}
    QPushButton:hover {{ border-color: {COLORS['mint']}; background: #F5FCF9; }}
    QPushButton#primaryButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; font-weight: 650;
    }}
    QPushButton#primaryButton:hover {{ background: #2F6E5D; }}
    QPushButton#playerButton, QPushButton#playerPrimaryButton {{
        min-width: 38px; padding: 6px 7px; border-radius: 9px; font-size: 10px;
    }}
    QPushButton#playerPrimaryButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; font-weight: 650;
    }}
    QPushButton#playerCloseButton {{
        background: transparent; border: none; color: {COLORS['muted']}; padding: 5px; font-size: 16px;
    }}
    QPushButton#playerCloseButton:hover {{ color: #A5533D; background: #FFE9DE; }}
    QPushButton#taskButton {{
        text-align: left; padding: 11px 13px; background: {COLORS['white']}; font-size: 12px;
    }}
    QPushButton#ghostButton {{ background: transparent; padding: 6px 8px; }}
    QPushButton#connectionStatusButton {{
        background: #F0ECE5; border: none; border-radius: 8px;
        color: {COLORS['muted']}; padding: 3px 7px; font-size: 9px;
    }}
    QPushButton#connectionStatusButton[state="connected"] {{ background: #DDF1E9; color: {COLORS['mint_dark']}; }}
    QPushButton#connectionStatusButton[state="fallback"] {{ background: #FFF1D1; color: #8A6217; }}
    QPushButton#connectionStatusButton[state="degraded"] {{ background: #FFE2D2; color: #9B4F30; }}
    QPushButton#iconButton {{ background: transparent; border: none; font-size: 15px; }}
    QPushButton#linkButton {{ background: transparent; border: none; color: {COLORS['muted']}; padding-left: 2px; }}
    """


def _dialog_stylesheet() -> str:
    return f"""
    QDialog {{ background: {COLORS['cream']}; }}
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI'; }}
    QLabel#dialogTitle {{ font-size: 23px; font-weight: 750; }}
    QLabel#muted {{ color: {COLORS['muted']}; font-size: 12px; }}
    QLabel#fallback {{ color: {COLORS['ink']}; background: #FFF1D1; border-radius: 10px; padding: 10px; }}
    QLineEdit, QListWidget, QComboBox {{
        color: {COLORS['ink']}; background: white; border: 1px solid {COLORS['line']};
        border-radius: 10px; padding: 9px; font-family: 'Microsoft YaHei UI';
    }}
    QCheckBox {{ color: {COLORS['ink']}; spacing: 8px; padding: 3px; }}
    QListWidget::item {{ padding: 9px; border-bottom: 1px solid #F2EEE8; }}
    QListWidget::item:selected {{ color: {COLORS['ink']}; background: #DDF1E9; border-radius: 7px; }}
    QPushButton {{
        color: {COLORS['ink']}; background: white; border: 1px solid {COLORS['line']};
        border-radius: 9px; padding: 8px 13px; font-family: 'Microsoft YaHei UI';
    }}
    QPushButton#primaryButton {{ color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; }}
    """
