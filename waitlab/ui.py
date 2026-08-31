from __future__ import annotations

import os
import random
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
    Qt,
    QTimer,
    QSize,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .connection import HookConnectionInfo, HookConnectionMonitor, HookConnectionState
from .cookie import (
    CookieContext,
    CookieStateMachine,
)
from .desktop_activity import DesktopActivityEvent, DesktopEventKind, DesktopTurnSnapshot
from .models import (
    DEFAULT_TAG,
    CompletedTaskSummary,
    CompletedFocusRecord,
    DefaultTaskEntry,
    ServiceUpdate,
    Task,
)
from .preferences import PopupMode, Preferences
from .service import AI_INITIAL_PROMPT_GRACE_SECONDS, WaitLabService
from .storage_defaults import DEFAULT_TASKS
from . import __version__
from .ui_charts import DailyTagStackedChart, TagDonutChart  # noqa: F401 - compatibility exports
from .ui_dialogs import (
    FocusEndTimeDialog,
    SettingsDialog,
    StatisticsDialog,
    TagManagerDialog,  # noqa: F401 - compatibility export
    TaskManagerDialog,
)
from .ui_primitives import (
    PresentationMode,
    app_icon,
    choose_presentation_mode,
    format_duration,
    tag_tone,
    tag_tone_colors,
)
from .ui_styles import window_stylesheet as _window_stylesheet
from .ui_widgets import FlowLayout, PetFace, TagChipBar  # noqa: F401 - compatibility exports
from .updates import (
    ReleaseInfo,
    cleanup_download_directory,
    describe_update_error,
    launch_installer,
)
from .update_manager import UpdateManager
from .windowing import apply_native_topmost


DESKTOP_SOURCE_GRACE_SECONDS = 12.0


class PetWindow(QWidget):
    quit_requested = Signal()
    update_check_finished = Signal(str)
    update_available = Signal(object, bool)
    update_downloaded = Signal(object)

    def __init__(self, service: WaitLabService) -> None:
        super().__init__()
        self.service = service
        self.update_manager = UpdateManager(__version__)
        self.tray: QSystemTrayIcon | None = None
        self.task_dialog: TaskManagerDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.task_picker_open = False
        self.page_hidden = False
        self.pet_hidden = False
        self._native_topmost_enabled = True
        self._next_native_topmost_sync = 0.0
        self._suggestion_signature: tuple[tuple[int | None, str, str, str], ...] | None = None
        self._suggestion_mode: str | None = None
        self._fixed_cycle_candidates: list[Task] | None = None
        self._paused_signature: tuple[tuple[int, str, str, int], ...] | None = None
        self._completed_signature: tuple[tuple[int | None, str, float, int, str], ...] | None = None
        self.completion_banner_until = 0.0
        self._completion_queue: list[ServiceUpdate] = []
        self._completion_notified_turns: OrderedDict[str, None] = OrderedDict()
        self._active_completion_turn_id: str | None = None
        self.task_completion_banner_until = 0.0
        self._notice_until = 0.0
        self._notice_title = ""
        self._notice_body = ""
        self._notice_level = "info"
        self._deleted_history_record: CompletedFocusRecord | None = None
        self._deleted_history_until = 0.0
        self.last_message = "等待下一次 Codex 指令"
        self._drag_origin: QPoint | None = None
        self.hook_monitor = HookConnectionMonitor(service)
        self._hook_info: HookConnectionInfo | None = None
        self._next_connection_check = 0.0
        self._desktop_source_available: bool | None = None
        self._desktop_source_error: str | None = None
        self._desktop_source_path: Path | None = None
        self._desktop_unavailable_since: float | None = None
        self._desktop_turn_ids: set[str] = set()
        self._desktop_unknown_notice_shown = False
        self._desktop_snapshots: tuple[DesktopTurnSnapshot, ...] = ()
        self._ai_attention = False
        self.presentation_mode: PresentationMode | None = None
        self.cookie_state_machine = CookieStateMachine()
        self._focus_full_title = ""
        self._state_full_title = ""
        self._fit_to_content_pending = False
        self._last_calendar_day = time.localtime()[:3]

        self.setWindowTitle("WaitLAB")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(82)
        self._build_ui()
        self.update_check_finished.connect(self._show_update_result)
        self.update_available.connect(self._offer_update)
        self.update_downloaded.connect(self._install_downloaded_update)
        self._apply_window_preferences()
        self._restore_position()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        # The worker thread handles Codex polling independently.  The regular
        # timer only advances visible clocks and expires transient notices;
        # data/layout refreshes are driven by actual state changes.
        self.timer.start(1000)
        self.refresh()
        # Qt widget tests run in an isolated/offline process. Do not start a
        # background HTTPS updater from that process: the network worker can
        # outlive the test widget and has caused intermittent Windows
        # Python/SSL access violations while the Qt event loop is shutting
        # down. Normal desktop runs keep the user's automatic update setting.
        auto_check_updates = self.service.load_preferences().auto_check_updates
        running_under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if auto_check_updates and not running_under_pytest:
            QTimer.singleShot(3500, lambda: self.check_for_updates(silent=True))

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.card = QFrame()
        self.card.setObjectName("mainCard")
        self.card.setProperty("presentation", PresentationMode.ICON.value)
        self.card.setStyleSheet(_window_stylesheet())
        self._outer_shadow = QGraphicsDropShadowEffect(self)
        self._outer_shadow.setBlurRadius(28)
        self._outer_shadow.setOffset(0, 7)
        self._outer_shadow.setColor(QColor(40, 55, 50, 55))
        self.card.setGraphicsEffect(self._outer_shadow)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.pet = PetFace(88)
        self.pet.clicked.connect(self.toggle_picker)
        self.pet.context_requested.connect(self.show_pet_menu)
        self.pet.drag_started.connect(self._begin_pet_drag)
        self.pet.drag_moved.connect(self._move_pet_drag)
        self.pet.drag_finished.connect(self._finish_pet_drag)
        header.addWidget(self.pet)

        self.header_details = QWidget()
        titles = QVBoxLayout(self.header_details)
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        self.state_label = QLabel("空闲")
        self.state_label.setObjectName("stateTitle")
        self.state_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.state_label.setMinimumWidth(0)
        self.state_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title_row.addWidget(self.state_label, 1)
        tasks_button = QPushButton("任务")
        tasks_button.setObjectName("ghostButton")
        tasks_button.setToolTip("管理手动任务和固定循环任务")
        tasks_button.clicked.connect(self.open_task_manager)
        settings_button = QPushButton("设置")
        settings_button.setObjectName("ghostButton")
        settings_button.setToolTip("调整提醒、置顶、静默时段和日用任务")
        settings_button.clicked.connect(self.open_settings)
        stats_button = QPushButton("统计")
        stats_button.setObjectName("ghostButton")
        stats_button.setToolTip("查看今天、本周和本月的 Waiting Task 时长")
        stats_button.clicked.connect(self.open_statistics)
        hide_button = QPushButton("−")
        hide_button.setObjectName("iconButton")
        hide_button.setFixedSize(28, 28)
        hide_button.setToolTip("隐藏页面，仅保留 Cookie")
        hide_button.clicked.connect(self.hide_page)
        title_row.addWidget(hide_button)
        pet_hide_button = QPushButton("×")
        pet_hide_button.setObjectName("iconButton")
        pet_hide_button.setFixedSize(28, 28)
        pet_hide_button.setToolTip("隐藏桌宠到系统托盘")
        pet_hide_button.clicked.connect(self.hide_pet)
        title_row.addWidget(pet_hide_button)
        titles.addLayout(title_row)
        self.message_label = QLabel(self.last_message)
        self.message_label.setObjectName("muted")
        self.message_label.setWordWrap(True)
        titles.addWidget(self.message_label)
        self.connection_status_button = QPushButton("Codex · 检查中")
        self.connection_status_button.setObjectName("connectionStatusButton")
        self.connection_status_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connection_status_button.clicked.connect(self.show_codex_connection_info)
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(3)
        meta_row.addWidget(self.connection_status_button, 0, Qt.AlignmentFlag.AlignLeft)
        meta_row.addStretch()
        meta_row.addWidget(tasks_button)
        meta_row.addWidget(stats_button)
        meta_row.addWidget(settings_button)
        titles.addLayout(meta_row)
        header.addWidget(self.header_details, 1)

        self.focus_card = QFrame()
        self.focus_card.setObjectName("focusCard")
        self.focus_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.focus_card.setMinimumWidth(350)
        focus_shadow = QGraphicsDropShadowEffect(self.focus_card)
        focus_shadow.setBlurRadius(18)
        focus_shadow.setOffset(0, 4)
        focus_shadow.setColor(QColor(40, 55, 50, 35))
        self.focus_card.setGraphicsEffect(focus_shadow)
        focus_layout = QVBoxLayout(self.focus_card)
        focus_layout.setContentsMargins(14, 12, 14, 12)
        focus_layout.setSpacing(7)
        focus_info = QHBoxLayout()
        focus_info.setContentsMargins(0, 0, 0, 0)
        focus_info.setSpacing(6)
        self.focus_title = QLabel()
        self.focus_title.setObjectName("focusTitle")
        self.focus_title.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.focus_title.setMinimumWidth(0)
        self.focus_title.setMinimumHeight(22)
        self.focus_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.focus_time = QLabel("00:00")
        self.focus_time.setObjectName("timerCompact")
        self.focus_time.setMinimumWidth(72)
        self.focus_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        focus_info.addWidget(self.focus_title, 1)
        focus_info.addWidget(self.focus_time, 0)
        self.focus_hide_button = QPushButton("−")
        self.focus_hide_button.setObjectName("iconButton")
        self.focus_hide_button.setFixedSize(24, 24)
        self.focus_hide_button.setToolTip("隐藏页面，仅保留 Cookie")
        self.focus_hide_button.clicked.connect(self.hide_page)
        focus_info.addWidget(self.focus_hide_button, 0)
        focus_layout.addLayout(focus_info)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.pause_button = QPushButton("Ⅱ\n暂停")
        self.pause_button.setObjectName("playerButton")
        self.pause_button.setToolTip("暂停或继续微任务")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.switch_button = QPushButton("↔\n切换")
        self.switch_button.setObjectName("playerSwitchButton")
        self.switch_button.setToolTip("暂停当前任务后切换到另一个任务")
        self.switch_button.clicked.connect(self.open_task_switcher)
        complete_button = QPushButton("✓\n完成")
        complete_button.setObjectName("playerPrimaryButton")
        complete_button.setToolTip("完成当前微任务")
        complete_button.clicked.connect(self.complete_focus)
        abandon_button = QPushButton("×\n取消")
        abandon_button.setObjectName("playerCloseButton")
        abandon_button.setToolTip("取消本次计时并放回任务池")
        abandon_button.clicked.connect(self.abandon_focus)
        for button in (self.pause_button, complete_button, abandon_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumSize(88, 44)
        self.switch_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.switch_button.setFixedWidth(72)
        controls.addWidget(self.pause_button, 1)
        controls.addWidget(self.switch_button, 0)
        controls.addWidget(complete_button, 1)
        controls.addWidget(abandon_button, 1)
        self.focus_controls = QWidget(self.focus_card)
        self.focus_controls.setLayout(controls)
        focus_layout.addWidget(self.focus_controls)
        header.addWidget(self.focus_card, 1)
        layout.addLayout(header)

        self.ai_card = QFrame()
        self.ai_card.setObjectName("aiCard")
        self.ai_card.setProperty("attention", False)
        self.ai_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        ai_layout = QHBoxLayout(self.ai_card)
        ai_layout.setContentsMargins(13, 8, 13, 8)
        self.ai_status_label = QLabel("Codex 对话进行中")
        self.ai_status_label.setObjectName("cardLabel")
        ai_layout.addWidget(self.ai_status_label)
        ai_layout.addStretch()
        layout.addWidget(self.ai_card)

        self.picker = QFrame()
        self.picker.setObjectName("picker")
        self.picker.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        picker_layout = QVBoxLayout(self.picker)
        picker_layout.setContentsMargins(2, 1, 2, 0)
        picker_layout.setSpacing(2)
        picker_header = QHBoxLayout()
        picker_header.setSpacing(3)
        self.picker_title = QLabel("选一个等待任务")
        self.picker_title.setObjectName("sectionTitle")
        self.picker_source = QLabel()
        self.picker_source.setObjectName("sourcePill")
        picker_header.addWidget(self.picker_title)
        picker_header.addStretch()
        picker_header.addWidget(self.picker_source)
        picker_layout.addLayout(picker_header)
        self.home_stats_label = QLabel("今天 · Waiting Task 00:00")
        self.home_stats_label.setObjectName("muted")
        picker_layout.addWidget(self.home_stats_label)
        task_input_row = QHBoxLayout()
        task_input_row.setSpacing(4)
        self.quick_task_input = QLineEdit()
        self.quick_task_input.setPlaceholderText("新增一个具体任务…")
        self.quick_task_input.returnPressed.connect(self._add_quick_task)
        task_input_row.addWidget(self.quick_task_input)
        picker_layout.addLayout(task_input_row)

        add_row = QHBoxLayout()
        add_row.setSpacing(4)
        quick_tag_label = QLabel("标签")
        quick_tag_label.setObjectName("muted")
        self.quick_task_tag = TagChipBar(
            self.service.available_tags(),
            single_line=True,
        )
        self.quick_task_tag.setObjectName("quickTaskTag")
        self.quick_task_tag.set_compact(True)
        self.quick_task_tag.setToolTip("为新任务选择标签")
        self.quick_task_tag.geometry_changed.connect(self._schedule_fit_to_content)
        self.quick_task_tag_scroll = QScrollArea()
        self.quick_task_tag_scroll.setObjectName("quickTaskTagScroll")
        self.quick_task_tag_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.quick_task_tag_scroll.setWidgetResizable(False)
        self.quick_task_tag_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.quick_task_tag_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.quick_task_tag_scroll.setWidget(self.quick_task_tag)
        add_quick = QPushButton("新增")
        add_quick.clicked.connect(self._add_quick_task)
        add_row.addWidget(quick_tag_label)
        add_row.addWidget(self.quick_task_tag_scroll, 1)
        add_row.addStretch()
        add_row.addWidget(add_quick)
        picker_layout.addLayout(add_row)
        self.random_task_button = QPushButton("随机开始一个固定任务")
        self.random_task_button.setObjectName("secondaryButton")
        self.random_task_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.random_task_button.clicked.connect(self._start_random_task)
        picker_layout.addWidget(self.random_task_button)
        self.enable_fixed_tasks_button = QPushButton("启用固定任务")
        self.enable_fixed_tasks_button.setObjectName("secondaryButton")
        self.enable_fixed_tasks_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.enable_fixed_tasks_button.setToolTip("启用当前任务池中的固定循环任务")
        self.enable_fixed_tasks_button.clicked.connect(self._enable_fixed_tasks)
        self.enable_fixed_tasks_button.hide()
        picker_layout.addWidget(self.enable_fixed_tasks_button)
        self.suggestion_container = QWidget()
        self.suggestion_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self.suggestion_layout = QVBoxLayout(self.suggestion_container)
        self.suggestion_layout.setContentsMargins(0, 0, 0, 0)
        self.suggestion_layout.setSpacing(1)
        picker_layout.addWidget(self.suggestion_container)
        completed_header = QLabel("今日已完成")
        completed_header.setObjectName("muted")
        picker_layout.addWidget(completed_header)
        self.today_completed_list = QListWidget()
        self.today_completed_list.setObjectName("todayCompletedList")
        self.today_completed_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.today_completed_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.today_completed_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.today_completed_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.today_completed_list.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        self.today_completed_list.setMinimumHeight(0)
        picker_layout.addWidget(self.today_completed_list)
        self.today_completed_empty = QLabel("今日还没有完成微任务")
        self.today_completed_empty.setObjectName("muted")
        self.today_completed_empty.setWordWrap(True)
        picker_layout.addWidget(self.today_completed_empty)
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

        # Keep Cookie visually separate from the operation surface.  The
        # transparent outer card hosts the pet and one rounded bubble; all
        # status, task, and control widgets remain inside that bubble.
        self.bubble_card = QFrame()
        self.bubble_card.setObjectName("bubbleCard")
        self.bubble_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        bubble_shadow = QGraphicsDropShadowEffect(self.bubble_card)
        bubble_shadow.setBlurRadius(22)
        bubble_shadow.setOffset(0, 6)
        bubble_shadow.setColor(QColor(40, 55, 50, 48))
        self.bubble_card.setGraphicsEffect(bubble_shadow)
        bubble_layout = QVBoxLayout(self.bubble_card)
        bubble_layout.setContentsMargins(13, 6, 13, 6)
        bubble_layout.setSpacing(2)
        self.notice_card = QFrame()
        self.notice_card.setObjectName("noticeCard")
        self.notice_card.setProperty("level", "info")
        notice_layout = QHBoxLayout(self.notice_card)
        notice_layout.setContentsMargins(10, 8, 8, 8)
        notice_layout.setSpacing(8)
        notice_text = QVBoxLayout()
        notice_text.setContentsMargins(0, 0, 0, 0)
        notice_text.setSpacing(1)
        self.notice_title_label = QLabel()
        self.notice_title_label.setObjectName("noticeTitle")
        self.notice_body_label = QLabel()
        self.notice_body_label.setObjectName("noticeBody")
        self.notice_body_label.setWordWrap(True)
        notice_text.addWidget(self.notice_title_label)
        notice_text.addWidget(self.notice_body_label)
        self.notice_action_row = QHBoxLayout()
        self.notice_action_row.setContentsMargins(0, 4, 0, 0)
        self.notice_action_row.setSpacing(6)
        self.notice_continue_button = QPushButton("继续微任务")
        self.notice_continue_button.setObjectName("noticeActionButton")
        self.notice_continue_button.clicked.connect(self._continue_after_ai)
        self.notice_pause_button = QPushButton("暂停")
        self.notice_pause_button.setObjectName("noticeActionButton")
        self.notice_pause_button.clicked.connect(self._pause_after_ai)
        self.notice_complete_button = QPushButton("完成")
        self.notice_complete_button.setObjectName("noticePrimaryActionButton")
        self.notice_complete_button.clicked.connect(self._complete_after_ai)
        self.notice_undo_button = QPushButton("撤销删除")
        self.notice_undo_button.setObjectName("noticeActionButton")
        self.notice_undo_button.clicked.connect(self._undo_deleted_history)
        self.notice_action_row.addWidget(self.notice_continue_button)
        self.notice_action_row.addWidget(self.notice_pause_button)
        self.notice_action_row.addWidget(self.notice_complete_button)
        self.notice_action_row.addWidget(self.notice_undo_button)
        self.notice_action_row.addStretch()
        notice_text.addLayout(self.notice_action_row)
        self.notice_action_row.setEnabled(False)
        notice_layout.addLayout(notice_text, 1)
        dismiss_notice = QPushButton("×")
        dismiss_notice.setObjectName("iconButton")
        dismiss_notice.setFixedSize(24, 24)
        dismiss_notice.setToolTip("关闭这条提示")
        dismiss_notice.clicked.connect(self.dismiss_notice)
        notice_layout.addWidget(dismiss_notice, 0, Qt.AlignmentFlag.AlignTop)
        self.notice_card.hide()
        self.notice_action_row.setEnabled(False)
        self.notice_continue_button.hide()
        self.notice_pause_button.hide()
        self.notice_complete_button.hide()
        self.notice_undo_button.hide()
        bubble_layout.addWidget(self.notice_card)
        self.compact_timer_label = QLabel("00:00")
        self.compact_timer_label.setObjectName("compactTimer")
        self.compact_timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.compact_timer_label.setToolTip("当前 Waiting Task 计时")
        self.compact_timer_label.hide()
        bubble_layout.addWidget(self.compact_timer_label, 0, Qt.AlignmentFlag.AlignCenter)
        header.removeWidget(self.header_details)
        header.removeWidget(self.focus_card)
        layout.removeWidget(self.ai_card)
        layout.removeWidget(self.picker)
        layout.removeWidget(self.footer_widget)
        for widget in (
            self.header_details,
            self.focus_card,
            self.ai_card,
            self.picker,
            self.footer_widget,
        ):
            widget.setParent(self.bubble_card)
            bubble_layout.addWidget(widget)
        header.addWidget(self.bubble_card, 1)

    def set_tray(self, tray: QSystemTrayIcon) -> None:
        self.tray = tray

    def _notice_is_visible(self) -> bool:
        return bool(self._notice_body) and time.monotonic() < self._notice_until

    def show_notice(
        self,
        title: str,
        body: str,
        *,
        level: str = "info",
        duration: float = 5.0,
        sound: bool = False,
    ) -> None:
        """Show a short-lived notification inside Cookie's operation bubble."""

        self._notice_title = str(title).strip()
        self._notice_body = str(body).strip()
        self._notice_level = level if level in {"info", "success", "warning", "error"} else "info"
        self.notice_title_label.setText(self._notice_title)
        self.notice_body_label.setText(self._notice_body)
        self._hide_notice_actions()
        self.notice_card.setProperty("level", self._notice_level)
        self.notice_card.style().unpolish(self.notice_card)
        self.notice_card.style().polish(self.notice_card)
        self._notice_until = time.monotonic() + max(0.0, float(duration))
        self.notice_card.setVisible(bool(self._notice_body))
        if sound:
            self._play_notification_sound(self.service.load_preferences())
        self.refresh()

    def _enqueue_completion_reminder(self, update: ServiceUpdate) -> None:
        """Queue one completion notice per Codex turn.

        Desktop polling can deliver several terminal transitions in a single
        tick. Keeping a small FIFO prevents the latest turn from overwriting
        an earlier reminder and makes each output actionable.
        """

        turn_id = update.ai_turn_id
        if turn_id is not None:
            if turn_id in self._completion_notified_turns:
                return
            self._completion_notified_turns[turn_id] = None
            if len(self._completion_notified_turns) > 4096:
                self._completion_notified_turns.popitem(last=False)

        preferences = self.service.load_preferences()

        if not preferences.in_app_notifications:
            return
        self._completion_queue.append(update)
        self._show_next_completion_reminder()

    def _show_next_completion_reminder(self) -> None:
        if (
            self._active_completion_turn_id is not None
            or not self._completion_queue
            or self._notice_is_visible()
        ):
            return
        update = self._completion_queue.pop(0)
        self._active_completion_turn_id = update.ai_turn_id or f"anonymous-{time.time_ns()}"
        self.completion_banner_until = time.monotonic() + 12.0
        completed = update.ai_completed
        title = "Codex 已完成" if completed else "Codex 已中断"
        has_focus = self.service.focus is not None
        body = (
            "Codex 已输出。你可以继续当前微任务，也可以在这里暂停或完成它。"
            if completed and has_focus
            else "可以回到 Codex 查看结果。"
            if completed
            else "Codex 已中断。你可以继续当前微任务，也可以在这里暂停或完成它。"
            if has_focus
            else "请回到 Codex 查看中断或失败原因。"
        )
        self.show_notice(
            title,
            body,
            level="success" if completed else "warning",
            duration=12.0,
        )
        if has_focus:
            self._show_completion_actions()
            self.refresh()

    def _hide_notice_actions(self) -> None:
        self.notice_action_row.setEnabled(False)
        self.notice_continue_button.hide()
        self.notice_pause_button.hide()
        self.notice_complete_button.hide()
        self.notice_undo_button.hide()

    def _show_history_undo(self) -> None:
        if self._deleted_history_record is None:
            return
        self.notice_action_row.setEnabled(True)
        self.notice_undo_button.show()

    def _show_completion_actions(self) -> None:
        if self.service.focus is None:
            return
        self.notice_action_row.setEnabled(True)
        self.notice_continue_button.show()
        self.notice_pause_button.show()
        self.notice_complete_button.show()

    def _continue_after_ai(self) -> None:
        # The default is to keep the Waiting Task running.  Explicitly
        # dismissing the reminder makes the choice visible without changing
        # the focus clock.
        self.dismiss_notice()

    def _pause_after_ai(self) -> None:
        self.apply_update(
            self.service.pause_focus(message="Codex 已输出，微任务已暂停")
        )
        self.dismiss_notice()

    def _complete_after_ai(self) -> None:
        self._active_completion_turn_id = None
        self.complete_focus()

    def dismiss_notice(self) -> None:
        self._active_completion_turn_id = None
        self.completion_banner_until = 0.0
        self._notice_until = 0.0
        self._notice_title = ""
        self._notice_body = ""
        self._deleted_history_record = None
        self._deleted_history_until = 0.0
        self._hide_notice_actions()
        self.notice_card.hide()
        self._show_next_completion_reminder()
        self.refresh()

    def set_hook_listener_error(self, error: str) -> None:
        self.hook_monitor.set_listener_error(error)
        self._update_connection_status(force=True)

    def set_desktop_source_status(
        self,
        available: bool,
        error: str | None,
        database: Path,
    ) -> bool:
        changed = (
            self._desktop_source_available != available
            or self._desktop_source_error != error
            or self._desktop_source_path != database
        )
        self._desktop_source_available = available
        self._desktop_source_error = error
        self._desktop_source_path = database
        if available:
            self._desktop_unavailable_since = None
            self._desktop_unknown_notice_shown = False
        elif self._desktop_unavailable_since is None:
            self._desktop_unavailable_since = time.monotonic()
        if changed:
            self._update_connection_status(force=True)
        return changed

    def _desktop_source_is_unknown(self) -> bool:
        """Return whether a lost desktop source invalidates active rows."""

        return (
            self._desktop_unavailable_since is not None
            and time.monotonic() - self._desktop_unavailable_since
            >= DESKTOP_SOURCE_GRACE_SECONDS
            and bool(self._desktop_turn_ids)
        )

    def set_desktop_snapshots(
        self,
        snapshots: tuple[DesktopTurnSnapshot, ...],
    ) -> None:
        self._desktop_snapshots = snapshots
        # The reader returns a bounded snapshot. Mirror it here instead of
        # retaining every turn observed since process start.
        self._desktop_turn_ids = {snapshot.turn_id for snapshot in snapshots}

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
        self._desktop_turn_ids.add(event.turn_id)
        if event.kind is DesktopEventKind.STARTED:
            initial_is_recent = (
                event.occurred_at - event.started_at
            ).total_seconds() <= AI_INITIAL_PROMPT_GRACE_SECONDS
            update = self.service.on_ai_started(
                event.thread_id,
                event.turn_id,
                when=event.started_at,
                # A first-poll row may predate this WaitLAB process. Keep it
                # tracked so a legitimate long-running turn is not lost, but
                # only a recent row should replay the task picker.
                show_task_picker=not event.initial or initial_is_recent,
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
                when=event.occurred_at,
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
            preferences = self.service.load_preferences()
            # ``tray_only`` was the old external-toast mode. Keep reading it
            # for compatibility, but present the picker in Cookie now that all
            # Codex prompts are in-app.
            popup_mode = (
                PopupMode.RAISE
                if preferences.popup_mode is PopupMode.TRAY_ONLY
                else preferences.popup_mode
            )
            self.page_hidden = False
            self.task_picker_open = True
            self._invalidate_fixed_cycle_candidates()
            if self.task_picker_open:
                self._set_presentation_mode(PresentationMode.PICKER)
            if popup_mode is PopupMode.RAISE:
                self.pet_hidden = False
                self.show()
                self.raise_()
                self.activateWindow()
            elif popup_mode is PopupMode.QUIET:
                self.pet_hidden = False
                self.show()
        if update.ai_completed or update.ai_blocked:
            if self.service.focus is None:
                self.task_picker_open = False
            self._enqueue_completion_reminder(update)
        if update.ai_needs_attention:
            preferences = self.service.load_preferences()
            if (
                preferences.in_app_notifications
                and self._active_completion_turn_id is None
            ):
                self.show_notice(
                    "Codex 等待批准",
                    "请回到 Codex 处理权限请求；当前微任务仍在继续计时。",
                    level="warning",
                    duration=7.0,
                )
            if not preferences.is_quiet_now():
                self._play_notification_sound(preferences)
        self.refresh()

    @staticmethod
    def _play_notification_sound(preferences: Preferences) -> None:
        if preferences.notification_sound and not preferences.is_quiet_now():
            QApplication.beep()

    def refresh(self) -> None:
        now = time.monotonic()
        if (
            self._active_completion_turn_id is not None
            and now >= self._notice_until
        ):
            self._active_completion_turn_id = None
            self._hide_notice_actions()
        if (
            self._deleted_history_record is not None
            and now >= self._deleted_history_until
        ):
            self._deleted_history_record = None
            self._deleted_history_until = 0.0
            self._hide_notice_actions()
        if (
            self._active_completion_turn_id is None
            and self._completion_queue
            and not self._notice_is_visible()
        ):
            self._show_next_completion_reminder()
            return
        focus = self.service.focus
        running_ai = self.service.running_ai_sessions()
        attention_ai = self.service.attention_ai_sessions()
        desktop_source_unknown = self._desktop_source_is_unknown()
        unknown_running = (
            desktop_source_unknown
            and any(session.turn_id in self._desktop_turn_ids for session in running_ai)
        )
        unknown_attention = (
            desktop_source_unknown
            and any(session.turn_id in self._desktop_turn_ids for session in attention_ai)
        )
        source_state_unknown = unknown_running or unknown_attention
        if source_state_unknown and not self._desktop_unknown_notice_shown:
            self._desktop_unknown_notice_shown = True
            self.show_notice(
                "Codex 状态待确认",
                "暂时无法读取 Codex 状态，Cookie 不会继续判断它正在工作；连接恢复后会自动同步。",
                level="warning",
                duration=60.0,
            )
            return
        if desktop_source_unknown:
            running_ai = [
                session
                for session in running_ai
                if session.turn_id not in self._desktop_turn_ids
            ]
            attention_ai = [
                session
                for session in attention_ai
                if session.turn_id not in self._desktop_turn_ids
            ]
        primary_ai = (
            running_ai[0] if running_ai else attention_ai[0] if attention_ai else None
        )
        completed_visible = now < self.completion_banner_until
        task_completed_visible = time.monotonic() < self.task_completion_banner_until
        completion_notice_visible = (
            completed_visible
            and self._active_completion_turn_id is not None
            and self._notice_is_visible()
        )
        terminal_blocked = (
            completed_visible
            and self.service.last_ai_terminal_status not in {None, "completed"}
        )
        has_running_ai = bool(running_ai)
        needs_attention = bool(attention_ai)

        cookie_state = self.cookie_state_machine.transition(
            CookieContext(
                focus_active=focus is not None,
                focus_paused=focus is not None and focus.is_paused,
                ai_active=has_running_ai,
                ai_needs_attention=needs_attention,
                completion_visible=completed_visible,
                task_completion_visible=task_completed_visible,
                terminal_error=terminal_blocked,
            )
        )

        if source_state_unknown:
            state = "Codex 状态待确认"
        elif has_running_ai and needs_attention:
            state = "Codex 正在工作 · 另有任务等待批准"
        elif has_running_ai:
            state = "Codex 对话进行中"
        elif needs_attention:
            state = "Codex 等待批准"
        elif focus is not None:
            state = "微任务已暂停" if focus.is_paused else "正在回收等待时间"
        elif task_completed_visible:
            state = "微任务已完成"
        elif completion_notice_visible:
            # The in-bubble completion notice is the single source of truth;
            # keep the header neutral instead of repeating its title.
            state = "等待下一轮"
        elif completed_visible:
            state = "Codex 已中断" if terminal_blocked else "Codex 已完成"
        else:
            state = "等待下一轮"

        self.pet.set_state(cookie_state)
        self._state_full_title = state
        self.state_label.setText(state)
        self.state_label.setToolTip(state)
        self._elide_state_title()
        self.message_label.setVisible(not (completion_notice_visible and focus is None))
        self.message_label.setText(self.last_message)

        self.ai_card.setVisible(False)
        if source_state_unknown:
            self.ai_status_label.setText("Codex 连接中断，状态待确认")
        elif has_running_ai:
            assert primary_ai is not None
            status_text = "Codex 对话进行中"
            if len(running_ai) > 1:
                status_text += f" · {len(running_ai)} 个任务"
            if needs_attention:
                status_text += " · 另有任务等待批准"
            self.ai_status_label.setText(status_text)
        elif needs_attention and primary_ai is not None:
            self.ai_status_label.setText("Codex 等待你的操作")
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
        highlighted = needs_attention or terminal_blocked
        if highlighted != self._ai_attention:
            self._ai_attention = highlighted
            self.ai_card.setProperty("attention", highlighted)
            self.ai_card.style().unpolish(self.ai_card)
            self.ai_card.style().polish(self.ai_card)

        if focus is not None:
            self._focus_full_title = focus.task.title
            self.focus_title.setText(self._focus_full_title)
            self.focus_title.setToolTip(focus.task.title)
            elapsed_text = format_duration(focus.elapsed_seconds())
            self.focus_time.setText(elapsed_text)
            self.compact_timer_label.setText(elapsed_text)
            self.compact_timer_label.setToolTip(f"{focus.task.title} · {elapsed_text}")
            self.pause_button.setText("▶\n继续" if focus.is_paused else "Ⅱ\n暂停")
            self.switch_button.setEnabled(True)
            self.switch_button.setToolTip(
                "选择另一个 Waiting Task"
                if focus.is_paused
                else "自动暂停当前任务，并选择另一个 Waiting Task"
            )

        else:
            self._focus_full_title = ""
            self.focus_title.clear()
            self.focus_title.setToolTip("")
            self.compact_timer_label.setText("00:00")
            self.compact_timer_label.setToolTip("当前 Waiting Task 计时")
            self.switch_button.setEnabled(False)

        picker_visible = (
            self.task_picker_open
            and not self.page_hidden
            and (focus is None or focus.is_paused)
        )
        if picker_visible:
            self.picker_title.setText(
                "切换 Waiting Task" if focus is not None else "选一个等待任务"
            )
            self._refresh_suggestions()
        notice_visible = self._notice_is_visible() and not self.page_hidden

        self._set_presentation_mode(
            choose_presentation_mode(
                focus is not None,
                picker_visible,
                page_hidden=self.page_hidden,
                notice_open=notice_visible,
                focus_paused=focus is not None and focus.is_paused,
            )
        )
        self.notice_card.setVisible(
            notice_visible and self.presentation_mode is not PresentationMode.ICON
        )
        # Keep the AI lifecycle visible alongside an active micro-task.  The
        # task timer is independent, so completion/attention feedback never
        # replaces or stops the focus bubble.
        self.ai_card.setVisible(
            self.presentation_mode is not PresentationMode.ICON
            and not self.page_hidden
            and (has_running_ai or needs_attention or unknown_running or unknown_attention)
        )
        if focus is not None:
            self._elide_focus_title()
            QTimer.singleShot(0, self._elide_focus_title)
        if self._state_full_title:
            QTimer.singleShot(0, self._elide_state_title)

        self._update_today_stats()
        self._update_connection_status()
        self.card.layout().invalidate()
        self.card.layout().activate()
        self._schedule_fit_to_content()
        if (
            self.isVisible()
            and self._native_topmost_enabled
            and time.monotonic() >= self._next_native_topmost_sync
        ):
            self._next_native_topmost_sync = time.monotonic() + 1.5
            self._apply_native_topmost()

    def _update_today_stats(self) -> None:
        """Refresh the two compact daily totals shown on the home page."""

        day_stats = self.service.stats_cache.get("day")
        minutes = int(day_stats.waiting_seconds // 60)
        self.today_label.setText(f"\u4eca\u65e5\u56de\u6536 {minutes} \u5206\u949f")
        self.home_stats_label.setText(
            "\u4eca\u5929 \u00b7 Waiting Task "
            f"{format_duration(day_stats.waiting_seconds)}"
        )

    def _tick(self) -> None:
        """Update elapsed labels without re-querying storage or rebuilding UI."""

        now = time.monotonic()
        calendar_day = time.localtime()[:3]
        calendar_day_changed = calendar_day != self._last_calendar_day
        if calendar_day_changed:
            self._last_calendar_day = calendar_day
        transient_expired = (
            bool(self._notice_body)
            and now >= self._notice_until
        ) or (
            self._deleted_history_record is not None
            and now >= self._deleted_history_until
        )
        focus = self.service.focus
        if focus is not None:
            elapsed_text = format_duration(focus.elapsed_seconds())
            self.focus_time.setText(elapsed_text)
            self.compact_timer_label.setText(elapsed_text)
            if not calendar_day_changed:
                self._update_today_stats()
        desktop_unknown_due = (
            self._desktop_unavailable_since is not None
            and bool(self._desktop_turn_ids)
            and now - self._desktop_unavailable_since >= DESKTOP_SOURCE_GRACE_SECONDS
            and not self._desktop_unknown_notice_shown
        )
        if calendar_day_changed or transient_expired or desktop_unknown_due or (
            self._active_completion_turn_id is None
            and self._completion_queue
            and not self._notice_is_visible()
        ):
            self.refresh()

    def _schedule_fit_to_content(self) -> None:
        """Coalesce layout fitting requests from refresh and chip geometry."""

        if self._fit_to_content_pending:
            return
        self._fit_to_content_pending = True
        QTimer.singleShot(0, self._run_scheduled_fit_to_content)

    def _run_scheduled_fit_to_content(self) -> None:
        self._fit_to_content_pending = False
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        # Presentation-mode stylesheet changes can restore Qt's default
        # button metrics; reapply the compact home selector after polishing.
        self.quick_task_tag.set_compact(True)
        self.quick_task_tag.sync_height()
        self.card.layout().invalidate()
        self.card.layout().activate()
        target_height = self.card.sizeHint().height() + 16
        if self.height() != target_height:
            self.setFixedHeight(target_height)

    def _elide_focus_title(self) -> None:
        if not self._focus_full_title:
            return
        width = self.focus_title.contentsRect().width()
        if width <= 0:
            return
        self.focus_title.setText(
            self.focus_title.fontMetrics().elidedText(
                self._focus_full_title,
                Qt.TextElideMode.ElideRight,
                width,
            )
        )

    def _elide_state_title(self) -> None:
        if not self._state_full_title:
            return
        width = self.state_label.contentsRect().width()
        if width <= 0:
            return
        self.state_label.setText(
            self.state_label.fontMetrics().elidedText(
                self._state_full_title,
                Qt.TextElideMode.ElideRight,
                width,
            )
        )

    def _set_presentation_mode(self, mode: PresentationMode) -> None:
        if self.presentation_mode is mode:
            return
        self.presentation_mode = mode
        is_picker = mode is PresentationMode.PICKER
        is_player = mode is PresentationMode.PLAYER
        is_compact_player = mode is PresentationMode.COMPACT_PLAYER
        is_notice = mode is PresentationMode.NOTICE
        self.bubble_card.setVisible(mode is not PresentationMode.ICON)
        self.header_details.setVisible(is_picker or is_notice)
        self.focus_card.setVisible(is_player)
        self.focus_controls.setVisible(is_player)
        self.focus_hide_button.setVisible(is_player)
        self.compact_timer_label.setVisible(is_compact_player)
        self.picker.setVisible(is_picker)
        self.notice_card.setVisible(is_notice or is_picker or is_player)
        self.ai_card.setVisible(False)
        self.footer_widget.setVisible(False)

        if mode is PresentationMode.ICON:
            width = self.pet.width() + 16
            margins = (4, 4, 4, 4)
            opacity = 0.88
        elif mode is PresentationMode.PLAYER:
            # The player sits beside the pet inside the same header row.  Its
            # window width therefore needs to reserve a full title row and
            # four equal-width controls; the old 340px minimum squeezed
            # long task names into the action buttons.
            # Include the pet, bubble margins, and the focus card's minimum
            # width in the top-level geometry; otherwise the rightmost
            # cancel button can be clipped at the window edge.
            width = max(570, self.pet.width() + 482)
            margins = (7, 6, 7, 6)
            opacity = 0.96
        elif mode is PresentationMode.COMPACT_PLAYER:
            # Keep only one small timer line in the existing bubble while the
            # expanded page is hidden.  The full player returns on click.
            self.focus_card.setMinimumWidth(0)
            width = max(220, self.pet.width() + 120)
            margins = (6, 5, 6, 5)
            opacity = 0.96
        elif mode is PresentationMode.NOTICE:
            width = max(360, self.pet.width() + 290)
            margins = (7, 6, 7, 6)
            opacity = 0.96
        else:
            width = max(430, self.pet.width() + 342)
            # The picker is the dense home surface.  Keep the outer card
            # margins smaller than the player margins so the title, status,
            # and task controls stay together without reducing click targets.
            margins = (10, 8, 10, 8)
            opacity = 1.0

        self.card.layout().setContentsMargins(*margins)
        self.card.setProperty("presentation", mode.value)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        # Re-apply the home picker row height after polishing.  Qt's
        # stylesheet pass can restore the platform button metric (42px on
        # Windows), which otherwise makes the compact spacing changes nearly
        # invisible and pushes the lower task sections down again.
        if mode in {PresentationMode.PICKER, PresentationMode.NOTICE}:
            if mode is PresentationMode.PICKER:
                for button in self.suggestion_container.findChildren(QPushButton):
                    if button.objectName() in {"taskButton", "pausedTaskButton"}:
                        button.setFixedHeight(32)
            for button in self.header_details.findChildren(QPushButton):
                if button.objectName() == "ghostButton":
                    button.setFixedHeight(28)
        self.setWindowOpacity(opacity)
        if mode is PresentationMode.PLAYER:
            self.focus_card.setMinimumWidth(350)
        self.setFixedWidth(width)
        self._outer_shadow.setColor(
            QColor(40, 55, 50, 55 if mode is PresentationMode.ICON else 0)
        )
        QTimer.singleShot(0, self._apply_native_topmost)
        self.card.layout().invalidate()
        self.card.layout().activate()
        self._schedule_fit_to_content()
        QTimer.singleShot(0, self._keep_on_screen)

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
        recalibrate = dialog.addButton("重新校准 Codex 状态", QMessageBox.ButtonRole.ActionRole)
        dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() is recalibrate:
            update = self.service.reconcile_desktop_sessions(
                self._desktop_snapshots,
            )
            self.apply_update(update)
            if not update.ai_completed and not update.ai_blocked:
                self.last_message = "Codex 状态已校准，未发现可关闭的陈旧会话"
                self.refresh()

    def _refresh_today_completed(self) -> None:
        summaries = self.service.today_completed_tasks()
        signature = tuple(
            (
                summary.task_id,
                summary.title,
                round(summary.total_seconds, 3),
                summary.completed_count,
                summary.tag,
            )
            for summary in summaries
        )
        if signature != self._completed_signature:
            self._completed_signature = signature
            self.today_completed_list.clear()
            for summary in summaries:
                item = QListWidgetItem(self.today_completed_list)
                row = QFrame()
                row.setObjectName("completedRow")
                row_layout_parent = QVBoxLayout(row)
                row_layout_parent.setContentsMargins(0, 0, 0, 0)
                row_layout_parent.setSpacing(3)
                top_row = QFrame()
                row_layout = QHBoxLayout(top_row)
                row_layout.setContentsMargins(10, 6, 10, 1)
                row_layout.setSpacing(4)

                task_text = QVBoxLayout()
                task_text.setContentsMargins(0, 0, 0, 0)
                task_text.setSpacing(3)
                title = QLabel(summary.title)
                title.setObjectName("completedTitle")
                title.setToolTip(summary.title)
                title.setWordWrap(True)
                title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                completed_at = summary.last_completed_at.astimezone().strftime("%H:%M")
                meta_text = f"{summary.tag}  ·  完成 {summary.completed_count} 次 · 最近 {completed_at}"
                meta = QLabel(meta_text)
                meta.setObjectName("completedMeta")
                meta.setStyleSheet(
                    f"color: {tag_tone_colors(tag_tone(summary.tag))[0]};"
                )
                meta.setWordWrap(True)
                task_text.addWidget(title)
                task_text.addWidget(meta)
                row_layout.addLayout(task_text, 1)
                row_layout_parent.addWidget(top_row)

                bottom_row = QHBoxLayout()
                bottom_row.setContentsMargins(10, 0, 10, 6)
                bottom_row.setSpacing(5)
                duration = QLabel(f"总计 {format_duration(summary.total_seconds)}")
                duration.setObjectName("completedDuration")
                duration.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                bottom_row.addWidget(duration, 0)
                bottom_row.addStretch(1)
                continue_button = QPushButton("继续")
                continue_button.setObjectName("completedContinueButton")
                continue_button.setFixedWidth(54)
                continue_button.setToolTip("继续进行这个微任务")
                continue_button.clicked.connect(
                    lambda _checked=False, selected=summary: self._continue_completed_task(selected)
                )
                bottom_row.addWidget(continue_button, 0)
                details_button = QPushButton("展开")
                details_button.setObjectName("completedDetailsButton")
                details_button.setFixedWidth(54)
                bottom_row.addWidget(details_button, 0)
                row_layout_parent.addLayout(bottom_row)
                details = QListWidget()
                details.setObjectName("completedDetailsList")
                details.setVisible(False)
                for record in self.service.completed_focus_records(
                    summary.task_id, summary.title, summary.kind, summary.tag
                ):
                    detail_item = QListWidgetItem(details)
                    detail_item.setSizeHint(QSize(0, 30))
                    detail_row = QFrame()
                    detail_layout = QHBoxLayout(detail_row)
                    detail_layout.setContentsMargins(6, 2, 4, 2)
                    started = record.started_at.astimezone().strftime("%m-%d %H:%M")
                    ended = record.ended_at.astimezone().strftime("%m-%d %H:%M")
                    detail_layout.addWidget(QLabel(f"{started}–{ended}  {format_duration(record.duration_seconds)}"), 1)
                    delete_record = QPushButton("删除")
                    edit_end = QPushButton("改结束")
                    edit_end.setObjectName("completedEditButton")
                    edit_end.setFixedWidth(64)
                    edit_end.setToolTip("修改这条微任务记录的结束时间")
                    edit_end.clicked.connect(
                        lambda _checked=False, record_id=record.id: self._edit_completed_end_time(record_id)
                    )
                    detail_layout.addWidget(edit_end)
                    delete_record.setObjectName("completedDeleteButton")
                    delete_record.setFixedWidth(48)
                    delete_record.clicked.connect(
                        lambda _checked=False, record_id=record.id: self._delete_completed_record(record_id)
                    )
                    detail_layout.addWidget(delete_record)
                    details.setItemWidget(detail_item, detail_row)
                details_height = max(0, details.count() * 30 + 8)
                details.setMinimumHeight(0)
                details.setMaximumHeight(max(1, details_height))
                details.setFixedHeight(max(1, details_height))
                row_layout_parent.addWidget(details)
                details_button.clicked.connect(
                    lambda _checked=False, widget=details, button=details_button, list_item=item: self._toggle_completed_details(widget, button, list_item)
                )
                self.today_completed_list.setItemWidget(item, row)
                item.setSizeHint(QSize(0, max(56, row.sizeHint().height())))

        has_completed = bool(summaries)
        self.today_completed_list.setVisible(has_completed)
        self.today_completed_empty.setVisible(not has_completed)
        self._update_completed_list_height()
        QTimer.singleShot(0, self._update_completed_list_height)

    def _update_completed_list_height(self) -> None:
        if not self.today_completed_list.isVisible() or self.today_completed_list.count() == 0:
            self.today_completed_list.setMinimumHeight(0)
            self.today_completed_list.setMaximumHeight(0)
            return
        total_height = sum(
            max(56, self.today_completed_list.item(index).sizeHint().height())
            for index in range(self.today_completed_list.count())
        )
        # Grow to the actual number of rows, while retaining a bounded scroll
        # area for unusually long histories so the desktop pet stays usable.
        target = min(430, max(56, total_height + 2))
        self.today_completed_list.setMinimumHeight(target)
        self.today_completed_list.setMaximumHeight(target)

    def _continue_completed_task(self, summary: CompletedTaskSummary) -> None:
        if self.service.has_active_focus():
            self.apply_update(
                self.service.pause_focus(message="褰撳墠浠诲姟宸叉殏鍋滐紝姝ｅ湪鍒囨崲浠诲姟")
            )
            self.start_focus(Task(summary.task_id, summary.title, summary.kind, 0, summary.tag))
            return
        if self.service.focus is not None and self.service.has_active_focus():
            self.apply_update(
                self.service.pause_focus(message="褰撳墠浠诲姟宸叉殏鍋滐紝姝ｅ湪鍒囨崲浠诲姟")
            )
            self.last_message = "请先完成或暂停当前微任务"
            self.refresh()
            return
        self.start_focus(Task(summary.task_id, summary.title, summary.kind, 0, summary.tag))

    def _toggle_completed_details(
        self,
        details: QWidget,
        button: QPushButton,
        item: QListWidgetItem,
    ) -> None:
        visible = not details.isVisible()
        details.setVisible(visible)
        button.setText("收起" if visible else "展开")
        row = self.today_completed_list.itemWidget(item)
        if row is not None:
            item.setSizeHint(QSize(0, row.sizeHint().height()))
        self._update_completed_list_height()
        self.today_completed_list.updateGeometry()
        self._schedule_fit_to_content()

    def _delete_completed_record(self, record_id: int) -> None:
        record = self.service.get_completed_focus_record(record_id)
        if record is None:
            return
        answer = QMessageBox.question(
            self,
            "删除计时记录？",
            f"删除“{record.title}”在 {record.started_at.astimezone().strftime('%H:%M')}–"
            f"{record.ended_at.astimezone().strftime('%H:%M')} 的计时记录？\n"
            "删除后可在 8 秒内撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.service.archive_focus_session(record_id):
            return
        self._deleted_history_record = record
        self._deleted_history_until = time.monotonic() + 8.0
        self.service.stats_cache.invalidate()
        self._completed_signature = None
        self._refresh_today_completed()
        self.show_notice(
            "计时记录已删除",
            f"{record.title} · {format_duration(record.duration_seconds)}",
            level="warning",
            duration=8.0,
        )
        self._show_history_undo()

    def _edit_completed_end_time(self, record_id: int) -> None:
        record = self.service.get_completed_focus_record(record_id)
        if record is None:
            return
        dialog = FocusEndTimeDialog(record, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_end = dialog.ended_at()
        try:
            updated = self.service.update_completed_focus_end_time(
                record_id,
                selected_end,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "结束时间无效", str(exc))
            return
        if not updated:
            self.last_message = "这条计时记录已不存在或无法修改"
            self.refresh()
            return
        self._completed_signature = None
        self._refresh_today_completed()
        self.show_notice(
            "结束时间已修改",
            f"{record.title} · {selected_end.astimezone().strftime('%m-%d %H:%M:%S')}",
            level="success",
            duration=4.0,
        )

    def _undo_deleted_history(self) -> None:
        record = self._deleted_history_record
        if record is None or time.monotonic() >= self._deleted_history_until:
            self._deleted_history_record = None
            self._deleted_history_until = 0.0
            self._hide_notice_actions()
            return
        if not self.service.restore_archived_focus_session(record.id):
            self.last_message = "记录恢复失败，请检查本地数据库"
            self._deleted_history_record = None
            self._deleted_history_until = 0.0
            self._hide_notice_actions()
            self.refresh()
            return
        self._deleted_history_record = None
        self._deleted_history_until = 0.0
        self.service.stats_cache.invalidate()
        self._completed_signature = None
        self._refresh_today_completed()
        self.show_notice(
            "计时记录已恢复",
            f"已恢复：{record.title}",
            level="success",
            duration=4.0,
        )

    def _refresh_suggestions(self) -> None:
        manual = self.service.list_manual_tasks()
        switching = self.service.focus is not None and self.service.focus.is_paused
        fixed_cycle = self._fixed_cycle_candidates_for_picker()
        paused = self.service.paused_focuses()
        paused_keys = {
            (session.task.id, session.task.kind, session.task.title)
            for session in paused
        }

        def available(candidates: list[Task]) -> list[Task]:
            return [
                task
                for task in candidates
                if (task.id, task.kind, task.title) not in paused_keys
            ]

        # Both queues are available on the home page.  The switcher uses the
        # same candidates, so an automatic pause never removes fixed tasks or
        # reinstates the old "finish before switching" restriction.
        groups = [
            ("我的具体任务", available(manual)),
            ("固定循环任务", available(fixed_cycle)),
        ]
        source_mode = "switcher" if switching else "picker"
        tasks = [task for _title, group in groups for task in group]
        signature = tuple((task.id, task.title, task.kind.value, task.tag) for task in tasks)
        paused_signature = tuple(
            (
                session.id,
                session.task.title,
                session.task.tag,
                int(session.elapsed_seconds()),
            )
            for session in paused
        )
        self._refresh_today_completed()
        has_fixed_cycle = bool(fixed_cycle)
        self.random_task_button.setVisible(has_fixed_cycle)
        self.enable_fixed_tasks_button.setVisible(not has_fixed_cycle)
        self.picker_source.setText(
            "切换任务"
            if switching
            else "我的任务 + 固定"
            if manual and has_fixed_cycle
            else "我的具体任务"
            if manual
            else "固定循环任务"
            if has_fixed_cycle
            else "固定任务未启用"
        )
        if (
            signature == self._suggestion_signature
            and paused_signature == self._paused_signature
            and source_mode == self._suggestion_mode
        ):
            return
        self._suggestion_signature = signature
        self._suggestion_mode = source_mode
        self._paused_signature = paused_signature
        while self.suggestion_layout.count():
            item = self.suggestion_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if paused:
            paused_title = QLabel("已暂停任务（点击继续）")
            paused_title.setObjectName("muted")
            self.suggestion_layout.addWidget(paused_title)
            for session in paused:
                button = QPushButton(
                    f"↻  {session.task.title}  ·  {format_duration(session.elapsed_seconds())}"
                )
                button.setObjectName("pausedTaskButton")
                button.setFixedHeight(32)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip("继续这个已暂停的 Waiting Task")
                button.clicked.connect(
                    lambda _checked=False, selected=session.task: self.start_focus(selected)
                )
                self.suggestion_layout.addWidget(button)
        if not tasks and not paused:
            empty = QLabel("暂无可用任务，请在 Waiting Task 中添加手动任务或启用固定任务。")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.suggestion_layout.addWidget(empty)
            configure = QPushButton("打开 Waiting Task")
            configure.clicked.connect(self.open_task_manager)
            self.suggestion_layout.addWidget(configure)
            return
        index = 1
        for section_title, section_tasks in groups:
            if not section_tasks:
                continue
            section = QLabel(section_title)
            section.setObjectName("muted")
            self.suggestion_layout.addWidget(section)
            for task in section_tasks:
                button = QPushButton(f"{index} · {task.title}  ·  {task.tag}")
                button.setObjectName("taskButton")
                button.setFixedHeight(32)
                button.setToolTip(task.title)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(
                    lambda _checked=False, selected=task: self.start_focus(selected)
                )
                self.suggestion_layout.addWidget(button)
                index += 1
        self.suggestion_layout.invalidate()
        self.suggestion_container.updateGeometry()
        self.picker.layout().invalidate()
        self.picker.updateGeometry()
        self.card.layout().invalidate()

    def _fixed_cycle_candidates_for_picker(self) -> list[Task]:
        """Return one stable random sample for the currently open picker."""

        if self._fixed_cycle_candidates is None:
            self._fixed_cycle_candidates = self.service.fixed_cycle_tasks(
                randomize=True,
            )
        return list(self._fixed_cycle_candidates)

    def _invalidate_fixed_cycle_candidates(self) -> None:
        self._fixed_cycle_candidates = None
        self._suggestion_signature = None
        self._suggestion_mode = None

    def _start_random_task(self) -> None:
        tasks = self._fixed_cycle_candidates_for_picker()
        if not tasks:
            self.last_message = "暂无启用的固定循环任务"
            self.refresh()
            return
        self.start_focus(random.choice(tasks))

    def _enable_fixed_tasks(self) -> None:
        """Enable the existing fixed-task entries without replacing them."""

        entries = self.service.default_task_entries()
        if not entries:
            entries = [DefaultTaskEntry(title, True, DEFAULT_TAG) for title in DEFAULT_TASKS]
        else:
            entries = [
                DefaultTaskEntry(entry.title, True, entry.tag)
                for entry in entries
            ]
        self.service.set_default_task_entries(entries)
        self.last_message = "固定循环任务已启用"
        self._invalidate_fixed_cycle_candidates()
        self.refresh()

    def _add_quick_task(self) -> None:
        try:
            task = self.service.add_manual_task(
                self.quick_task_input.text(),
                self.quick_task_tag.currentText(),
            )
        except ValueError:
            self.quick_task_input.setFocus()
            return
        self.quick_task_input.clear()
        self._suggestion_signature = None
        self.start_focus(task)

    def start_focus(self, task: Task) -> None:
        if self.service.has_active_focus():
            self.apply_update(
                self.service.pause_focus(message="褰撳墠浠诲姟宸叉殏鍋滐紝姝ｅ湪鍒囨崲浠诲姟")
            )
        if self.service.has_active_focus():
            self.last_message = "请先暂停当前微任务，再切换任务"
            self.refresh()
            return
        self.task_picker_open = False
        self._invalidate_fixed_cycle_candidates()
        self._paused_signature = None
        self.apply_update(self.service.start_focus(task))
        if self.task_dialog is not None:
            self.task_dialog.hide()

    def open_task_switcher(self) -> None:
        focus = self.service.focus
        if focus is None:
            self.task_picker_open = True
            self._invalidate_fixed_cycle_candidates()
            self._paused_signature = None
            self.refresh()
            return
        if not focus.is_paused:
            self.apply_update(
                self.service.pause_focus(message="当前任务已暂停，选择另一个任务继续")
            )
        self.task_picker_open = True
        self._invalidate_fixed_cycle_candidates()
        self._paused_signature = None
        self.last_message = "当前任务已暂停，选择另一个任务继续"
        self.refresh()

    def toggle_pause(self) -> None:
        self.apply_update(self.service.toggle_focus_pause())

    def complete_focus(self) -> None:
        completed_title = self.service.focus.task.title if self.service.focus is not None else "微任务"
        self._active_completion_turn_id = None
        self._invalidate_fixed_cycle_candidates()
        self.apply_update(self.service.complete_focus())
        # Keep the completion expression in sync with the in-bubble success
        # notice so the user has enough time to see the feedback.
        self.task_completion_banner_until = time.monotonic() + 4.0
        self.task_picker_open = True
        self.show_notice(
            "微任务完成",
            f"已完成：{completed_title}",
            level="success",
            duration=4.0,
            sound=True,
        )
        if self.task_dialog is not None:
            self.task_dialog.refresh()

    def abandon_focus(self) -> None:
        self._invalidate_fixed_cycle_candidates()
        self.apply_update(self.service.abandon_focus())

    def manual_ai_start(self) -> None:
        self.apply_update(self.service.manual_ai_started())

    def manual_ai_finish(self) -> None:
        self.apply_update(self.service.manual_ai_finished())

    def toggle_picker(self) -> None:
        if self.page_hidden:
            # Cookie is also the restore control: bring back the task picker
            # when idle, or the full player controls when a task is running.
            self.page_hidden = False
            if self.service.focus is None:
                self.task_picker_open = True
                self._invalidate_fixed_cycle_candidates()
            self.refresh()
            self.raise_()
            self.activateWindow()
            self._apply_native_topmost()
            return
        if self.service.focus is None or self.service.focus.is_paused:
            self.task_picker_open = not self.task_picker_open
            if self.task_picker_open:
                self._invalidate_fixed_cycle_candidates()
            self._paused_signature = None
            self.refresh()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.service.focus is not None:
            if event.key() == Qt.Key.Key_Space:
                self.toggle_pause()
                event.accept()
                return
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self.complete_focus()
                event.accept()
                return
        elif self.task_picker_open and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_3:
            tasks = [
                *self.service.list_manual_tasks(),
                *self._fixed_cycle_candidates_for_picker(),
            ]
            index = event.key() - Qt.Key.Key_1
            if index < len(tasks):
                self.start_focus(tasks[index])
                event.accept()
                return
        if event.key() == Qt.Key.Key_Escape:
            self.close_picker()
            event.accept()
            return
        super().keyPressEvent(event)

    def show_pet_menu(self, global_position: QPoint) -> None:
        menu = QMenu(self)
        if self.service.focus is not None:
            pause_label = "继续微任务" if self.service.focus.is_paused else "暂停微任务"
            pause_action = menu.addAction(pause_label)
            pause_action.triggered.connect(self.toggle_pause)
            if self.service.focus.is_paused:
                switch_action = menu.addAction("切换微任务")
                switch_action.triggered.connect(self.open_task_switcher)
            complete_action = menu.addAction("完成微任务")
            complete_action.triggered.connect(self.complete_focus)
            cancel_action = menu.addAction("取消并放回")
            cancel_action.triggered.connect(self.abandon_focus)
            menu.addSeparator()
        else:
            picker_action = menu.addAction("选择微任务")
            picker_action.triggered.connect(self.toggle_picker)
        tasks_action = menu.addAction("Waiting Task")
        tasks_action.triggered.connect(self.open_task_manager)
        stats_action = menu.addAction("统计")
        stats_action.triggered.connect(self.open_statistics)
        settings_action = menu.addAction("设置")
        settings_action.triggered.connect(self.open_settings)
        update_action = menu.addAction("检查更新")
        update_action.triggered.connect(self.check_for_updates)
        menu.addSeparator()
        hide_action = menu.addAction("隐藏到托盘")
        hide_action.triggered.connect(self.hide_pet)
        topmost_action = menu.addAction(
            "取消始终置顶" if self._native_topmost_enabled else "始终置顶"
        )
        topmost_action.triggered.connect(self.toggle_always_on_top)
        page_action = menu.addAction(
            "显示任务页面" if self.page_hidden else "隐藏页面（仅保留 Cookie）"
        )
        page_action.triggered.connect(self.show_page if self.page_hidden else self.hide_page)
        quit_action = menu.addAction("退出 WaitLAB")
        quit_action.triggered.connect(self.quit_requested)
        menu.exec(global_position)

    def close_picker(self) -> None:
        self.task_picker_open = False
        self._invalidate_fixed_cycle_candidates()
        self.refresh()

    def skip_current_round(self) -> None:
        self.task_picker_open = False
        self._invalidate_fixed_cycle_candidates()
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
            self.settings_dialog.history_cleared.connect(self._history_cleared)
        self.settings_dialog.refresh()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def open_statistics(self) -> None:
        dialog = StatisticsDialog(self.service, self)
        dialog.exec()

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
        self._invalidate_fixed_cycle_candidates()
        self._refresh_quick_task_tags()
        self.refresh()

    def _refresh_quick_task_tags(self) -> None:
        """Keep the home-page quick-add selector in sync with tag management."""

        tags = self.service.available_tags()
        selected = self.quick_task_tag.currentText()
        self.quick_task_tag.set_tags(tags, selected)

    def _settings_changed(self) -> None:
        self._invalidate_fixed_cycle_candidates()
        if self.task_dialog is not None:
            self.task_dialog.refresh()
        self.last_message = "设置已保存"
        self._apply_window_preferences()
        self.refresh()

    def _history_cleared(self) -> None:
        self._completed_signature = None
        self.last_message = "历史记录已清空"
        self.refresh()

    def _apply_window_preferences(self) -> None:
        preferences = self.service.load_preferences()
        old_cookie_size = self.pet.width()
        self.pet.set_size(preferences.cookie_size)
        if self.pet.width() != old_cookie_size:
            # Force geometry recalculation even when the presentation mode
            # itself did not change (for example while the player is open).
            self.presentation_mode = None
        self._native_topmost_enabled = preferences.always_on_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, preferences.always_on_top)
        self.show()
        apply_native_topmost(self, preferences.always_on_top)
        QTimer.singleShot(0, lambda: apply_native_topmost(self, preferences.always_on_top))

    def toggle_always_on_top(self) -> None:
        enabled = not self.service.load_preferences().always_on_top
        self.service.set_setting("always_on_top", "1" if enabled else "0")
        self._apply_window_preferences()
        self.refresh()

    def hide_page(self) -> None:
        """Collapse the page to the Cookie icon without stopping the app."""

        self.page_hidden = True
        self.task_picker_open = False
        self.refresh()
        self.show()
        self._apply_native_topmost()

    def show_page(self) -> None:
        """Restore the expanded page while keeping any active focus session."""

        self.page_hidden = False
        self.pet_hidden = False
        if self.service.focus is None:
            self.task_picker_open = True
        self.show()
        self.refresh()
        self.raise_()
        self.activateWindow()
        self._apply_native_topmost()

    def hide_pet(self) -> None:
        """Hide the whole desktop pet to the tray; timers keep running."""

        self.pet_hidden = True
        self.hide()

    def restore_from_tray(self, show_page: bool = False) -> None:
        self.pet_hidden = False
        if show_page:
            self.page_hidden = False
            if self.service.focus is None:
                self.task_picker_open = True
        self.show()
        self.refresh()
        self.raise_()
        self.activateWindow()
        self._apply_native_topmost()

    def _apply_native_topmost(self) -> None:
        apply_native_topmost(self, self._native_topmost_enabled)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Windows can reorder a tool window when another app is activated.
        # Reassert the native z-order after Qt has completed the show event.
        QTimer.singleShot(0, self._apply_native_topmost)

    def check_for_updates(self, silent: bool = False) -> None:
        def on_result(result: object) -> None:
            self._handle_update_check_result(result, silent)

        started = self.update_manager.check(on_result)
        if not started and not silent:
            self._show_update_result("更新检查已在进行中，请稍候")

    def _handle_update_check_result(self, result: object, silent: bool) -> None:
        if isinstance(result, BaseException):
            if not silent:
                self.update_check_finished.emit(describe_update_error(result))
        elif isinstance(result, ReleaseInfo):
            self.update_available.emit(result, silent)
        elif not silent:
            self.update_check_finished.emit("当前已是最新版本")

    def _offer_update(self, release: ReleaseInfo, silent: bool) -> None:
        if self.service.focus is not None:
            self._show_update_result(f"发现 {release.version}；当前任务结束后可一键更新")
            return
        if silent:
            self._show_update_result(f"发现 WaitLAB {release.version}，右键桌宠可开始更新")
            return
        answer = QMessageBox.question(self, "更新 WaitLAB", f"发现版本 {release.version}。现在下载、校验并安装吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._show_update_result("正在下载并校验更新…")
        started = self.update_manager.download(
            release,
            self._handle_update_download_result,
        )
        if not started:
            self._show_update_result("更新下载已在进行中，请稍候")

    def _handle_update_download_result(self, result: object) -> None:
        if isinstance(result, Path):
            self.update_downloaded.emit(result)
        elif isinstance(result, BaseException):
            self.update_check_finished.emit(describe_update_error(result))

    def _install_downloaded_update(self, installer: Path) -> None:
        if self.service.focus is not None:
            self._show_update_result("任务仍在进行，已取消本次安装")
            cleanup_download_directory(installer, delay_seconds=0.0)
            return
        try:
            launch_installer(installer)
        except OSError as exc:
            cleanup_download_directory(installer, delay_seconds=0.0)
            self._show_update_result(describe_update_error(exc))
            return
        cleanup_download_directory(installer)
        self.quit_requested.emit()

    def _show_update_result(self, message: str) -> None:
        self.last_message = message
        preferences = self.service.load_preferences()
        if not preferences.is_quiet_now():
            self.show_notice("WaitLAB 更新", message, duration=4.5)
        self.refresh()

    def save_position(self) -> None:
        self.service.set_setting("window_x", str(self.x()))
        self.service.set_setting("window_y", str(self.y()))

    def _restore_position(self) -> None:
        x_value = self.service.get_setting("window_x", "")
        y_value = self.service.get_setting("window_y", "")
        saved_point = None
        try:
            if x_value and y_value:
                saved_point = QPoint(int(x_value), int(y_value))
        except (TypeError, ValueError):
            self.service.set_setting("window_x", "")
            self.service.set_setting("window_y", "")
        screen = QApplication.screenAt(saved_point) if saved_point is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            preferred_y = saved_point.y() if saved_point is not None else geometry.top() + 180
            maximum_y = max(geometry.top() + 10, geometry.bottom() - self.height() - 10)
            preferred_x = saved_point.x() if saved_point is not None else geometry.left() + 10
            maximum_x = max(geometry.left() + 10, geometry.right() - self.width() - 10)
            self.move(min(max(preferred_x, geometry.left() + 10), maximum_x), min(max(preferred_y, geometry.top() + 10), maximum_y))

    def _begin_pet_drag(self, global_position: QPoint) -> None:
        self._drag_origin = global_position - self.frameGeometry().topLeft()

    def _move_pet_drag(self, global_position: QPoint) -> None:
        if self._drag_origin is not None:
            self.move(global_position - self._drag_origin)

    def _finish_pet_drag(self) -> None:
        self._drag_origin = None
        self._snap_to_near_edge()
        self.save_position()

    def _snap_to_near_edge(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self._keep_on_screen()
        threshold = 28
        x = self.x()
        if abs(x - geometry.left()) <= threshold:
            self.move(geometry.left() + 10, self.y())
        elif abs((x + self.width()) - geometry.right()) <= threshold:
            self.move(geometry.right() - self.width() - 10, self.y())

    def _keep_on_screen(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = min(max(self.x(), geometry.left() + 10), max(geometry.left() + 10, geometry.right() - self.width() - 10))
        y = min(max(self.y(), geometry.top() + 10), max(geometry.top() + 10, geometry.bottom() - self.height() - 10))
        self.move(x, y)

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
            self._snap_to_near_edge()
            self.save_position()
        super().mouseReleaseEvent(event)


def create_tray(window: PetWindow) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(app_icon(), window)
    tray.setToolTip("WaitLAB · 把等待变成可用进度")
    menu = QMenu()
    show_action = menu.addAction("显示 WaitLAB")
    show_action.triggered.connect(window.restore_from_tray)
    page_action = menu.addAction("显示任务页面")
    page_action.triggered.connect(lambda: window.restore_from_tray(show_page=True))
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
        lambda reason: window.restore_from_tray()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()
    return tray
