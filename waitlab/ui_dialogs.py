"""Qt dialog windows for task, tag, statistics, and preference management.

Dialogs emit user intent and consume application services. They do not own the
main window lifecycle or database connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from PySide6.QtCore import QDateTime, QTime, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .autostart import is_autostart_enabled, set_autostart
from .models import CompletedFocusRecord, DEFAULT_TAG, DefaultTaskEntry, Task
from .preferences import PopupMode, Preferences
from .service import WaitLabService
from .storage_defaults import DEFAULT_TASKS
from .task_filters import filter_and_sort_tasks
from .ui_charts import DailyTagStackedChart, TagDonutChart
from .ui_primitives import app_icon, format_duration, tag_tone, tag_tone_colors
from .ui_styles import dialog_stylesheet
from .ui_widgets import TagChipBar


class FocusEndTimeDialog(QDialog):
    """Edit the endpoint of one completed Waiting Task segment."""

    def __init__(
        self,
        record: CompletedFocusRecord,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("修改结束时间")
        self.setMinimumWidth(360)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("修改微任务结束时间")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        description = QLabel(
            "只允许把结束时间提前，系统会同步重算本次任务时长和统计。"
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(description)

        layout.addWidget(QLabel("新的结束时间"))
        self.end_time_edit = QDateTimeEdit(self._to_qdatetime(record.ended_at))
        self.end_time_edit.setObjectName("focusEndTimeEdit")
        self.end_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.end_time_edit.setCalendarPopup(True)
        self.end_time_edit.setMinimumDateTime(self._to_qdatetime(record.started_at))
        self.end_time_edit.setMaximumDateTime(self._to_qdatetime(record.ended_at))
        layout.addWidget(self.end_time_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _to_qdatetime(value: datetime) -> QDateTime:
        return QDateTime.fromSecsSinceEpoch(int(value.timestamp()), Qt.TimeSpec.LocalTime)

    def ended_at(self) -> datetime:
        """Return the selected value normalized to UTC for persistence."""

        return datetime.fromtimestamp(
            self.end_time_edit.dateTime().toSecsSinceEpoch(),
            tz=timezone.utc,
        )


class TagManagerDialog(QDialog):
    """Manage the shared labels used by manual and fixed Waiting Tasks."""

    tags_changed = Signal()

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Waiting Task \u00b7 \u6807\u7b7e\u7ba1\u7406")
        self.setMinimumSize(420, 380)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("\u4efb\u52a1\u6807\u7b7e")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        subtitle = QLabel("\u6807\u7b7e\u4f1a\u540c\u6b65\u5e94\u7528\u5230\u624b\u52a8\u4efb\u52a1\u3001\u56fa\u5b9a\u5faa\u73af\u4efb\u52a1\u548c\u5386\u53f2\u8bb0\u5f55\u3002\u6309\u4f4f Ctrl \u53ef\u591a\u9009\u5e76\u6279\u91cf\u5220\u9664\uff1b\u76f8\u5173\u8bb0\u5f55\u4f1a\u5f52\u5165\u201c\u672a\u5206\u7c7b\u201d\u3002")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.tag_list.itemSelectionChanged.connect(self._fill_selected_tag)
        layout.addWidget(self.tag_list, 1)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("\u8f93\u5165\u65b0\u6807\u7b7e\uff0c\u6216\u9009\u62e9\u6807\u7b7e\u540e\u8f93\u5165\u65b0\u540d\u79f0\u2026")
        self.tag_input.returnPressed.connect(self._add_tag)
        layout.addWidget(self.tag_input)

        actions = QHBoxLayout()
        add_button = QPushButton("\u65b0\u589e")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_tag)
        rename_button = QPushButton("\u4fee\u6539\u9009\u4e2d")
        rename_button.clicked.connect(self._rename_tag)
        delete_button = QPushButton("\u5220\u9664\u9009\u4e2d")
        delete_button.clicked.connect(self._delete_tag)
        actions.addWidget(add_button)
        actions.addWidget(rename_button)
        actions.addWidget(delete_button)
        actions.addStretch()
        close_button = QPushButton("\u5b8c\u6210")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        selected = self._selected_tag()
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        usage = self.service.tag_usage_counts()
        for tag in self.service.available_tags():
            tone = tag_tone(tag)
            foreground, background = tag_tone_colors(tone)
            item = QListWidgetItem(f"\u25cf  {tag}  \u00b7  {usage.get(tag, 0)} \u4e2a\u4efb\u52a1")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setForeground(QColor(foreground))
            item.setBackground(QColor(background))
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)
        if selected:
            for index in range(self.tag_list.count()):
                item = self.tag_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == selected:
                    self.tag_list.setCurrentItem(item)
                    break

    def _selected_tag(self) -> str | None:
        item = self.tag_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else item.text().split("  \u00b7  ", 1)[0].strip()

    def _fill_selected_tag(self) -> None:
        selected = self._selected_tag()
        if selected is not None:
            self.tag_input.setText(selected)

    def _show_tag_error(self, error: ValueError) -> None:
        QMessageBox.warning(self, "\u6807\u7b7e\u64cd\u4f5c\u5931\u8d25", str(error))

    def _add_tag(self) -> None:
        try:
            self.service.add_tag(self.tag_input.text())
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.tag_input.clear()
        self.refresh()
        self.tags_changed.emit()

    def _rename_tag(self) -> None:
        if len(self.tag_list.selectedItems()) > 1:
            self._show_tag_error(ValueError("\u4fee\u6539\u6807\u7b7e\u65f6\u53ea\u80fd\u9009\u62e9\u4e00\u4e2a\u6807\u7b7e"))
            return
        old_tag = self._selected_tag()
        if old_tag is None:
            return
        try:
            self.service.rename_tag(old_tag, self.tag_input.text())
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.refresh()
        self.tags_changed.emit()

    def _delete_tag(self) -> None:
        tags = self._selected_tags()
        if not tags:
            return
        if DEFAULT_TAG in tags:
            self._show_tag_error(ValueError("\u672a\u5206\u7c7b\u662f\u7cfb\u7edf\u4fdd\u5e95\u6807\u7b7e\uff0c\u4e0d\u80fd\u5220\u9664\uff1b\u8bf7\u53d6\u6d88\u5bf9\u5b83\u7684\u9009\u62e9"))
            return
        label = f"\u6807\u7b7e\u201c{tags[0]}\u201d" if len(tags) == 1 else f"{len(tags)} \u4e2a\u6807\u7b7e"
        answer = QMessageBox.question(
            self,
            "\u5220\u9664\u6807\u7b7e\uff1f",
            f"\u5220\u9664{label}\u540e\uff0c\u4f7f\u7528\u5b83\u7684\u4efb\u52a1\u548c\u5386\u53f2\u8bb0\u5f55\u4f1a\u5f52\u5165\u201c{DEFAULT_TAG}\u201d\u3002\u7ee7\u7eed\u5417\uff1f",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_tags(tags)
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.tag_input.clear()
        self.refresh()
        self.tags_changed.emit()

    def _selected_tags(self) -> list[str]:
        tags: list[str] = []
        for item in self.tag_list.selectedItems():
            value = item.data(Qt.ItemDataRole.UserRole)
            tag = str(value) if value else item.text().split("  \u00b7  ", 1)[0].strip()
            if tag not in tags:
                tags.append(tag)
        return tags


class TaskManagerDialog(QDialog):
    tasks_changed = Signal()
    task_started = Signal(object)

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._deleted_task: Task | None = None
        self.setWindowTitle("WaitLAB \u00b7 Waiting Task")
        self.setMinimumSize(600, 720)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(0)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("taskManagerScroll")
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page = QWidget()
        page.setObjectName("taskManagerPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        title = QLabel("Waiting Task")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("\u7edf\u4e00\u7ef4\u62a4\u624b\u52a8\u4efb\u52a1\u548c\u56fa\u5b9a\u5faa\u73af\u4efb\u52a1\uff1b\u6709\u624b\u52a8\u4efb\u52a1\u65f6\u4f18\u5148\u4f7f\u7528\u624b\u52a8\u4efb\u52a1\u3002")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("\u4f8b\u5982\uff1a\u6838\u5bf9\u56fe 3 \u7684\u7edf\u8ba1\u6807\u6ce8")
        self.input.returnPressed.connect(self._add_task)
        self.manual_tag = TagChipBar(self.service.available_tags())
        self.manual_tag.setToolTip("\u4e3a\u65b0\u4efb\u52a1\u9009\u62e9\u6807\u7b7e")
        add_button = QPushButton("\u6dfb\u52a0")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_task)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(add_button)
        layout.addLayout(input_row)
        manual_tag_row = QHBoxLayout()
        manual_tag_label = QLabel("\u6807\u7b7e")
        manual_tag_label.setObjectName("muted")
        manual_tag_row.addWidget(manual_tag_label)
        manual_tag_row.addWidget(self.manual_tag, 1)
        layout.addLayout(manual_tag_row)

        manual_header = QHBoxLayout()
        manual_title = QLabel("\u6211\u7684\u4efb\u52a1")
        manual_title.setObjectName("sectionTitle")
        manual_header.addWidget(manual_title)
        manual_header.addStretch()
        manage_tags = QPushButton("\u7ba1\u7406\u6807\u7b7e")
        manage_tags.setObjectName("ghostButton")
        manage_tags.setToolTip("\u65b0\u589e\u3001\u4fee\u6539\u6216\u5220\u9664\u4efb\u52a1\u6807\u7b7e")
        manage_tags.clicked.connect(self._open_tag_manager)
        manual_header.addWidget(manage_tags)
        layout.addLayout(manual_header)

        filter_row = QHBoxLayout()
        self.task_search = QLineEdit()
        self.task_search.setPlaceholderText("\u641c\u7d22\u4efb\u52a1\u540d\u79f0\u2026")
        self.task_search.textChanged.connect(self.refresh)
        self.task_tag_filter = TagChipBar(
            ["\u5168\u90e8\u6807\u7b7e", *self.service.available_tags()],
            "\u5168\u90e8\u6807\u7b7e",
        )
        self.task_tag_filter.currentTextChanged.connect(lambda _text: self.refresh())
        self.task_sort = QComboBox()
        self.task_sort.addItems(["\u81ea\u5b9a\u4e49\u987a\u5e8f", "\u540d\u79f0 A-Z", "\u6807\u7b7e"])
        self.task_sort.currentTextChanged.connect(lambda _text: self.refresh())
        filter_row.addWidget(self.task_search, 1)
        filter_row.addWidget(self.task_sort)
        layout.addLayout(filter_row)
        tag_filter_row = QHBoxLayout()
        tag_filter_label = QLabel("\u7b5b\u9009\u6807\u7b7e")
        tag_filter_label.setObjectName("muted")
        tag_filter_row.addWidget(tag_filter_label)
        tag_filter_row.addWidget(self.task_tag_filter, 1)
        layout.addLayout(tag_filter_row)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._start_selected())
        layout.addWidget(self.list_widget, 1)

        action_row = QHBoxLayout()
        start_button = QPushButton("\u5f00\u59cb\u6240\u9009")
        start_button.setObjectName("primaryButton")
        start_button.clicked.connect(self._start_selected)
        delete_button = QPushButton("\u5220\u9664")
        delete_button.clicked.connect(self._delete_selected)
        self.undo_delete_button = QPushButton("\u64a4\u9500\u5220\u9664")
        self.undo_delete_button.setVisible(False)
        self.undo_delete_button.clicked.connect(self._undo_delete)
        action_row.addWidget(start_button)
        action_row.addWidget(delete_button)
        action_row.addWidget(self.undo_delete_button)
        action_row.addStretch()
        stats_button = QPushButton("\u7edf\u8ba1")
        stats_button.clicked.connect(self._open_stats)
        action_row.addWidget(stats_button)
        layout.addLayout(action_row)

        fixed_header = QHBoxLayout()
        fallback_title = QLabel("\u56fa\u5b9a\u5faa\u73af\u4efb\u52a1")
        fallback_title.setObjectName("sectionTitle")
        fixed_help = QLabel("\u52fe\u9009\u542f\u7528\uff1b\u987a\u5e8f\u5c31\u662f\u8f6e\u64ad\u987a\u5e8f")
        fixed_help.setObjectName("muted")
        fixed_header.addWidget(fallback_title)
        fixed_header.addStretch()
        fixed_header.addWidget(fixed_help)
        layout.addLayout(fixed_header)

        self.fixed_list = QListWidget()
        self.fixed_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fixed_list.itemDoubleClicked.connect(lambda _item: self._rename_selected_fixed())
        self.fixed_list.itemChanged.connect(lambda _item: self._persist_fixed())
        layout.addWidget(self.fixed_list, 1)

        fixed_controls = QHBoxLayout()
        fixed_add = QPushButton("\u6dfb\u52a0\u56fa\u5b9a")
        fixed_add.clicked.connect(self._add_fixed_task)
        fixed_rename = QPushButton("\u91cd\u547d\u540d")
        fixed_rename.clicked.connect(self._rename_selected_fixed)
        fixed_delete = QPushButton("\u5220\u9664")
        fixed_delete.clicked.connect(self._delete_selected_fixed)
        fixed_up = QPushButton("\u4e0a\u79fb")
        fixed_up.clicked.connect(lambda: self._move_selected_fixed(-1))
        fixed_down = QPushButton("\u4e0b\u79fb")
        fixed_down.clicked.connect(lambda: self._move_selected_fixed(1))
        self.fixed_tag = TagChipBar(self.service.available_tags())
        self.fixed_tag.setToolTip("\u5148\u9009\u6807\u7b7e\uff0c\u518d\u70b9\u51fb\u5e94\u7528\u6807\u7b7e")
        fixed_apply_tag = QPushButton("\u5e94\u7528\u6807\u7b7e")
        fixed_apply_tag.clicked.connect(self._apply_fixed_tag)
        fixed_reset = QPushButton("\u6062\u590d\u9ed8\u8ba4")
        fixed_reset.clicked.connect(self._reset_defaults)
        for button in (fixed_add, fixed_rename, fixed_delete, fixed_up, fixed_down):
            fixed_controls.addWidget(button)
        fixed_controls.addStretch()
        fixed_controls.addWidget(fixed_reset)
        layout.addLayout(fixed_controls)
        fixed_tag_row = QHBoxLayout()
        fixed_tag_label = QLabel("\u56fa\u5b9a\u4efb\u52a1\u6807\u7b7e")
        fixed_tag_label.setObjectName("muted")
        fixed_tag_row.addWidget(fixed_tag_label)
        fixed_tag_row.addWidget(self.fixed_tag, 1)
        fixed_tag_row.addWidget(fixed_apply_tag)
        layout.addLayout(fixed_tag_row)

        fallback_title = QLabel("\u65e0\u624b\u52a8\u4efb\u52a1\u65f6\u7684\u8f6e\u64ad\u9884\u89c8")
        fallback_title.setObjectName("muted")
        self.fallback = QLabel()
        self.fallback.setWordWrap(True)
        self.fallback.setObjectName("fallback")
        layout.addWidget(fallback_title)
        layout.addWidget(self.fallback)
        self.content_scroll.setWidget(page)
        outer_layout.addWidget(self.content_scroll)
        self.refresh()

    def refresh(self) -> None:
        entries = self.service.default_task_entries()
        enabled = [f"{entry.title}\uff08{entry.tag}\uff09" for entry in entries if entry.enabled]
        disabled_count = sum(not entry.enabled for entry in entries)
        if enabled:
            suffix = f"\uff08\u53e6\u6709 {disabled_count} \u9879\u5df2\u505c\u7528\uff09" if disabled_count else ""
            self.fallback.setText("  \u00b7  ".join(enabled) + suffix)
        else:
            self.fallback.setText("\u56fa\u5b9a\u4efb\u52a1\u5df2\u5168\u90e8\u505c\u7528\uff0c\u53ef\u5728\u4e0a\u65b9\u91cd\u65b0\u542f\u7528\u3002")
        self.list_widget.clear()
        query = self.task_search.text().strip()
        selected_tag = self.task_tag_filter.currentText()
        tasks = filter_and_sort_tasks(
            self.service.list_manual_tasks(),
            query=query,
            tag=selected_tag,
            sort_mode=self.task_sort.currentText(),
        )
        self._fill_fixed_tasks(entries)
        if not tasks:
            message = (
                "\u6ca1\u6709\u5339\u914d\u7684\u624b\u52a8\u4efb\u52a1"
                if query or selected_tag != "\u5168\u90e8\u6807\u7b7e"
                else "\u8fd8\u6ca1\u6709\u624b\u52a8\u4efb\u52a1\uff0c\u5c06\u4f7f\u7528\u56fa\u5b9a\u6eda\u52a8\u4efb\u52a1"
            )
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(placeholder)
            return
        for task in tasks:
            foreground, background = tag_tone_colors(tag_tone(task.tag))
            item = QListWidgetItem(f"{task.title}  \u00b7  \u25cf {task.tag}")
            item.setData(Qt.ItemDataRole.UserRole, task)
            item.setForeground(QColor(foreground))
            item.setBackground(QColor(background))
            self.list_widget.addItem(item)

    def _add_task(self) -> None:
        try:
            self.service.add_manual_task(self.input.text(), self.manual_tag.currentText())
        except ValueError:
            self.input.setFocus()
            return
        self.input.clear()
        self.refresh()
        self.tasks_changed.emit()

    def _open_tag_manager(self) -> None:
        dialog = TagManagerDialog(self.service, self)
        dialog.tags_changed.connect(self._refresh_tag_controls)
        dialog.exec()

    def _refresh_tag_controls(self) -> None:
        tags = self.service.available_tags()
        self.service.stats_cache.invalidate()
        self.manual_tag.set_tags(tags, self.manual_tag.currentText())
        self.fixed_tag.set_tags(tags, self.fixed_tag.currentText())
        selected_filter = self.task_tag_filter.currentText()
        self.task_tag_filter.set_tags(
            ["\u5168\u90e8\u6807\u7b7e", *tags],
            selected_filter if selected_filter in tags else "\u5168\u90e8\u6807\u7b7e",
        )
        self.refresh()
        self.tasks_changed.emit()

    def _selected_task(self) -> Task | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _start_selected(self) -> None:
        task = self._selected_task()
        if task is not None:
            self.task_started.emit(task)

    def _delete_selected(self) -> None:
        task = self._selected_task()
        if task is not None and task.id is not None:
            open_focuses = self.service.open_focuses()
            if any(
                focus.task.kind is task.kind
                and (
                    focus.task.id == task.id
                    if task.id is not None
                    else focus.task.title == task.title
                )
                for focus in open_focuses
            ):
                QMessageBox.information(self, "\u65e0\u6cd5\u5220\u9664", "\u5f53\u524d\u6b63\u5728\u8ba1\u65f6\u7684\u4efb\u52a1\u4e0d\u80fd\u5220\u9664\uff0c\u8bf7\u5148\u5b8c\u6210\u3001\u6682\u505c\u6216\u53d6\u6d88\u5b83\u3002")
                return
            deleted = self.service.delete_manual_task(task.id)
            if deleted is None:
                return
            self._deleted_task = deleted
            self.undo_delete_button.setVisible(True)
            self.refresh()
            self.tasks_changed.emit()

    def _undo_delete(self) -> None:
        if self._deleted_task is None:
            return
        self.service.add_manual_task(
            self._deleted_task.title,
            self._deleted_task.tag,
        )
        self._deleted_task = None
        self.undo_delete_button.setVisible(False)
        self.refresh()
        self.tasks_changed.emit()

    def _fill_fixed_tasks(self, entries: list[DefaultTaskEntry]) -> None:
        self.fixed_list.blockSignals(True)
        self.fixed_list.clear()
        for entry in entries:
            foreground, background = tag_tone_colors(tag_tone(entry.tag))
            item = QListWidgetItem(f"{entry.title}  \u00b7  \u25cf {entry.tag}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setForeground(QColor(foreground))
            item.setBackground(QColor(background))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked)
            self.fixed_list.addItem(item)
        self.fixed_list.blockSignals(False)

    @staticmethod
    def _fixed_item_values(item: QListWidgetItem) -> tuple[str, str]:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, DefaultTaskEntry):
            return entry.title, entry.tag
        return item.text().split("  \u00b7  ", 1)[0].strip(), DEFAULT_TAG

    def _persist_fixed(self) -> None:
        entries: list[DefaultTaskEntry] = []
        seen: set[str] = set()
        for index in range(self.fixed_list.count()):
            item = self.fixed_list.item(index)
            title, tag = self._fixed_item_values(item)
            title = " ".join(title.split())
            if not title or title in seen:
                continue
            seen.add(title)
            entries.append(DefaultTaskEntry(title, item.checkState() == Qt.CheckState.Checked, tag))
        self.service.set_default_task_entries(entries)
        self.refresh()
        self.tasks_changed.emit()

    def _add_fixed_task(self) -> None:
        title, accepted = QInputDialog.getText(self, "\u6dfb\u52a0\u56fa\u5b9a\u4efb\u52a1", "\u4efb\u52a1\u540d\u79f0\uff1a")
        clean_title = " ".join(title.strip().split())
        if not accepted or not clean_title or self._has_fixed_title(clean_title):
            return
        entry = DefaultTaskEntry(clean_title, True, self.fixed_tag.currentText())
        foreground, background = tag_tone_colors(tag_tone(entry.tag))
        item = QListWidgetItem(f"{entry.title}  \u00b7  \u25cf {entry.tag}")
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setForeground(QColor(foreground))
        item.setBackground(QColor(background))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.fixed_list.addItem(item)
        self._persist_fixed()

    def _rename_selected_fixed(self) -> None:
        item = self.fixed_list.currentItem()
        if item is None:
            return
        old_title, tag = self._fixed_item_values(item)
        title, accepted = QInputDialog.getText(self, "\u91cd\u547d\u540d\u56fa\u5b9a\u4efb\u52a1", "\u4efb\u52a1\u540d\u79f0\uff1a", text=old_title)
        clean_title = " ".join(title.strip().split())
        if not accepted or not clean_title or self._has_fixed_title(clean_title, item):
            return
        item.setData(Qt.ItemDataRole.UserRole, DefaultTaskEntry(clean_title, item.checkState() == Qt.CheckState.Checked, tag))
        item.setText(f"{clean_title}  \u00b7  \u25cf {tag}")
        self._persist_fixed()

    def _delete_selected_fixed(self) -> None:
        row = self.fixed_list.currentRow()
        if row >= 0:
            self.fixed_list.takeItem(row)
            self._persist_fixed()

    def _move_selected_fixed(self, offset: int) -> None:
        row = self.fixed_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.fixed_list.count():
            return
        item = self.fixed_list.takeItem(row)
        self.fixed_list.insertItem(target, item)
        self.fixed_list.setCurrentRow(target)
        self._persist_fixed()

    def _apply_fixed_tag(self) -> None:
        item = self.fixed_list.currentItem()
        if item is None:
            return
        title, _ = self._fixed_item_values(item)
        entry = DefaultTaskEntry(title, item.checkState() == Qt.CheckState.Checked, self.fixed_tag.currentText())
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setText(f"{title}  \u00b7  \u25cf {entry.tag}")
        foreground, background = tag_tone_colors(tag_tone(entry.tag))
        item.setForeground(QColor(foreground))
        item.setBackground(QColor(background))
        self._persist_fixed()

    def _reset_defaults(self) -> None:
        self._fill_fixed_tasks([DefaultTaskEntry(title, True, DEFAULT_TAG) for title in DEFAULT_TASKS])
        self._persist_fixed()

    def _has_fixed_title(self, title: str, except_item: QListWidgetItem | None = None) -> bool:
        return any(
            self.fixed_list.item(index) is not except_item
            and self._fixed_item_values(self.fixed_list.item(index))[0] == title
            for index in range(self.fixed_list.count())
        )

    def _open_stats(self) -> None:
        dialog = StatisticsDialog(self.service, self)
        dialog.exec()


class StatisticsDialog(QDialog):
    """Visual statistics view for today's allocation and daily trends."""

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("WaitLAB \u00b7 \u7edf\u8ba1")
        self.setMinimumSize(720, 700)
        self.resize(780, 760)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("\u65f6\u95f4\u7edf\u8ba1")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Waiting Task \u7edf\u8ba1\u5b9e\u9645\u4e13\u6ce8\u65f6\u95f4\uff1bCodex \u53ea\u4f5c\u4e3a\u6d3b\u52a8\u63d0\u9192\u6765\u6e90\uff0c\u4e0d\u8bb0\u5f55\u8fd0\u884c\u65f6\u957f\u3002")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        today_header = QHBoxLayout()
        today_title = QLabel("\u4eca\u65e5\u6807\u7b7e\u5206\u5e03")
        today_title.setObjectName("sectionTitle")
        today_header.addWidget(today_title)
        today_header.addStretch(1)
        self.today_total_label = QLabel()
        self.today_total_label.setObjectName("statValue")
        today_header.addWidget(self.today_total_label)
        layout.addLayout(today_header)

        today_content = QHBoxLayout()
        today_content.setSpacing(18)
        self.today_donut = TagDonutChart()
        today_content.addWidget(self.today_donut, 1)
        self.today_legend = QLabel()
        self.today_legend.setObjectName("chartLegend")
        self.today_legend.setWordWrap(True)
        self.today_legend.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.today_legend.setMinimumWidth(230)
        self.today_legend.setAccessibleName("\u4eca\u65e5\u6807\u7b7e\u65f6\u95f4\u660e\u7ec6")
        today_content.addWidget(self.today_legend, 1)
        layout.addLayout(today_content)

        series_header = QHBoxLayout()
        series_title = QLabel("\u6309\u5929\u6807\u7b7e\u65f6\u957f")
        series_title.setObjectName("sectionTitle")
        series_header.addWidget(series_title)
        series_header.addStretch(1)
        self.series_total_label = QLabel()
        self.series_total_label.setObjectName("statValue")
        series_header.addWidget(self.series_total_label)
        self.week_button = QPushButton("\u672c\u5468")
        self.week_button.setObjectName("periodButton")
        self.week_button.setCheckable(True)
        self.week_button.clicked.connect(lambda: self._set_period("week"))
        self.month_button = QPushButton("\u672c\u6708")
        self.month_button.setObjectName("periodButton")
        self.month_button.setCheckable(True)
        self.month_button.clicked.connect(lambda: self._set_period("month"))
        series_header.addWidget(self.week_button)
        series_header.addWidget(self.month_button)
        layout.addLayout(series_header)

        self.series_chart = DailyTagStackedChart()
        layout.addWidget(self.series_chart, 1)
        self.series_legend = QLabel()
        self.series_legend.setObjectName("chartLegend")
        self.series_legend.setWordWrap(True)
        self.series_legend.setAccessibleName("\u6309\u5929\u6807\u7b7e\u56fe\u4f8b")
        layout.addWidget(self.series_legend)

        self._period = "week"
        self.week_button.setChecked(True)
        close_button = QPushButton("\u5173\u95ed")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def refresh(self) -> None:
        day_snapshot = self.service.stats_cache.get("day")
        self.today_total_label.setText(format_duration(day_snapshot.waiting_seconds))
        self.today_donut.set_values(day_snapshot.tag_seconds)
        self.today_legend.setText(self._legend_html(day_snapshot.tag_seconds))
        self._refresh_series()

    def _set_period(self, period: str) -> None:
        self._period = period
        self.week_button.setChecked(period == "week")
        self.month_button.setChecked(period == "month")
        self._refresh_series()

    def _refresh_series(self) -> None:
        buckets = self.service.tag_waiting_daily_series(self._period)
        self.series_chart.set_data(self._period, buckets)
        totals: dict[str, float] = {}
        for bucket in buckets:
            for tag, seconds in bucket.tag_seconds.items():
                totals[tag] = totals.get(tag, 0.0) + seconds
        total_seconds = sum(totals.values())
        period_label = "\u672c\u5468" if self._period == "week" else "\u672c\u6708"
        self.series_total_label.setText(
            f"{period_label} {format_duration(total_seconds)}"
        )
        self.series_legend.setText(self._legend_html(totals))

    @staticmethod
    def _legend_html(values: dict[str, float]) -> str:
        positive = {
            tag: seconds for tag, seconds in values.items() if seconds > 0
        }
        if not positive:
            return "\u6682\u65e0\u6807\u7b7e\u8bb0\u5f55"
        total = sum(positive.values())
        parts = []
        for tag, seconds in sorted(
            positive.items(), key=lambda item: (-item[1], item[0])
        ):
            foreground, _background = tag_tone_colors(tag_tone(tag))
            percentage = seconds / total * 100 if total else 0
            parts.append(
                f'<span style="color:{foreground};">\u25cf</span> '
                f"{escape(tag)}  {format_duration(seconds)} ({percentage:.1f}%)"
            )
        return "\u3000".join(parts)


class SettingsDialog(QDialog):
    settings_changed = Signal()
    history_cleared = Signal()

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("WaitLAB \u00b7 \u8bbe\u7f6e")
        self.setMinimumSize(520, 520)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)

        title = QLabel("\u65e5\u7528\u8bbe\u7f6e")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("\u63a7\u5236\u684c\u5ba0\u7684\u63d0\u9192\u65b9\u5f0f\u3001\u7f6e\u9876\u884c\u4e3a\u548c\u65e5\u5e38\u63d0\u9192\u3002\u4efb\u52a1\u7edf\u4e00\u5728 Waiting Task \u4e2d\u7ef4\u62a4\u3002")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        behavior_title = QLabel("\u63d0\u9192\u4e0e\u542f\u52a8")
        behavior_title.setObjectName("sectionTitle")
        layout.addWidget(behavior_title)

        self.popup_mode = QComboBox()
        self.popup_mode.addItem("\u5f39\u51fa\u5e76\u7f6e\u9876", PopupMode.RAISE.value)
        self.popup_mode.addItem("\u9759\u9ed8\u663e\u793a\uff0c\u4e0d\u4e3b\u52a8\u7f6e\u9876", PopupMode.QUIET.value)
        self.in_app_notifications = QCheckBox("Codex \u8f93\u51fa\u3001\u5b8c\u6210\u6216\u4e2d\u65ad\u65f6\u5728 Cookie \u6c14\u6ce1\u5185\u63d0\u9192")
        self.notification_sound = QCheckBox("\u63d0\u9192\u65f6\u64ad\u653e\u63d0\u793a\u97f3")
        self.autostart = QCheckBox("\u767b\u5f55 Windows \u540e\u81ea\u52a8\u542f\u52a8 WaitLAB")
        self.always_on_top = QCheckBox("\u60ac\u6d6e\u7a97\u59cb\u7ec8\u7f6e\u9876\uff08\u53ef\u968f\u65f6\u62d6\u52a8\uff09")
        self.cookie_size = QSpinBox()
        self.cookie_size.setRange(48, 160)
        self.cookie_size.setSingleStep(8)
        self.cookie_size.setSuffix(" px")
        self.cookie_size.setToolTip("\u8c03\u6574 Cookie \u684c\u5ba0\u56fe\u6807\u5927\u5c0f")
        self.auto_check_updates = QCheckBox("\u542f\u52a8\u65f6\u68c0\u67e5 GitHub \u65b0\u7248\u672c")
        self.quiet_hours = QCheckBox("\u9759\u9ed8\u65f6\u6bb5\u4e0d\u64ad\u653e\u63d0\u793a\u97f3")
        self.quiet_start = QTimeEdit()
        self.quiet_end = QTimeEdit()
        self.quiet_start.setDisplayFormat("HH:mm")
        self.quiet_end.setDisplayFormat("HH:mm")
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(self.quiet_hours)
        quiet_row.addStretch()
        quiet_row.addWidget(QLabel("\u4ece"))
        quiet_row.addWidget(self.quiet_start)
        quiet_row.addWidget(QLabel("\u5230"))
        quiet_row.addWidget(self.quiet_end)
        layout.addWidget(QLabel("\u6536\u5230\u65b0\u7684 Codex \u6307\u4ee4\u65f6\uff1a"))
        layout.addWidget(self.popup_mode)
        layout.addWidget(self.in_app_notifications)
        layout.addWidget(self.notification_sound)
        layout.addWidget(self.autostart)
        layout.addWidget(self.always_on_top)
        cookie_size_row = QHBoxLayout()
        cookie_size_row.addWidget(QLabel("Cookie \u56fe\u6807\u5927\u5c0f"))
        cookie_size_row.addStretch()
        cookie_size_row.addWidget(self.cookie_size)
        layout.addLayout(cookie_size_row)
        layout.addWidget(self.auto_check_updates)
        layout.addLayout(quiet_row)

        history_title = QLabel("\u672c\u5730\u5386\u53f2\u8bb0\u5f55")
        history_title.setObjectName("sectionTitle")
        layout.addWidget(history_title)
        history_help = QLabel(
            "\u6e05\u7a7a\u5df2\u5b8c\u6210\u548c\u5df2\u53d6\u6d88\u7684 Waiting Task \u8ba1\u65f6\u8bb0\u5f55\uff0c\u5e76\u540c\u6b65\u6e05\u7a7a\u5468/\u6708\u7edf\u8ba1\u3002"
            "\u8fdb\u884c\u4e2d\u6216\u5df2\u6682\u505c\u7684\u4efb\u52a1\u3001\u4efb\u52a1\u5b9a\u4e49\u548c Codex \u4f1a\u8bdd\u8bb0\u5f55\u4e0d\u4f1a\u53d7\u5f71\u54cd\u3002"
        )
        history_help.setObjectName("muted")
        history_help.setWordWrap(True)
        layout.addWidget(history_help)
        history_row = QHBoxLayout()
        history_row.setContentsMargins(0, 0, 0, 0)
        history_row.addStretch()
        self.clear_history_button = QPushButton("\u6e05\u7a7a\u5386\u53f2\u8bb0\u5f55")
        self.clear_history_button.setObjectName("historyClearButton")
        self.clear_history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_history_button.clicked.connect(self._clear_history)
        history_row.addWidget(self.clear_history_button)
        layout.addLayout(history_row)
        self.history_status = QLabel()
        self.history_status.setObjectName("muted")
        self.history_status.setWordWrap(True)
        layout.addWidget(self.history_status)

        actions = QHBoxLayout()
        cancel_button = QPushButton("\u53d6\u6d88")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("\u4fdd\u5b58\u8bbe\u7f6e")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        actions.addStretch()
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        preferences = self.service.load_preferences()
        mode_index = self.popup_mode.findData(preferences.popup_mode.value)
        self.popup_mode.setCurrentIndex(max(0, mode_index))
        self.in_app_notifications.setChecked(preferences.in_app_notifications)
        self.notification_sound.setChecked(preferences.notification_sound)
        self.autostart.setChecked(is_autostart_enabled())
        self.always_on_top.setChecked(preferences.always_on_top)
        self.cookie_size.setValue(preferences.cookie_size)
        self.auto_check_updates.setChecked(preferences.auto_check_updates)
        self.quiet_hours.setChecked(preferences.quiet_hours_enabled)
        self.quiet_start.setTime(QTime.fromString(preferences.quiet_start, "HH:mm"))
        self.quiet_end.setTime(QTime.fromString(preferences.quiet_end, "HH:mm"))

    def _clear_history(self) -> None:
        """Clear terminal Waiting Task records after an explicit confirmation."""

        answer = QMessageBox.question(
            self,
            "\u6e05\u7a7a\u5386\u53f2\u8bb0\u5f55\uff1f",
            "\u8fd9\u4f1a\u6c38\u4e45\u5220\u9664\u6240\u6709\u5df2\u5b8c\u6210\u548c\u5df2\u53d6\u6d88\u7684 Waiting Task \u8ba1\u65f6\u8bb0\u5f55\uff0c"
            "\u5e76\u6e05\u7a7a\u5468/\u6708\u7edf\u8ba1\u3002\n"
            "\u6b63\u5728\u8fdb\u884c\u6216\u5df2\u6682\u505c\u7684\u4efb\u52a1\uff0c\u4ee5\u53ca\u4efb\u52a1\u5b9a\u4e49\u672c\u8eab\u4e0d\u4f1a\u53d7\u5f71\u54cd\u3002\u6b64\u64cd\u4f5c\u4e0d\u53ef\u64a4\u9500\u3002",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed_count = self.service.clear_focus_history()
        self.service.stats_cache.invalidate()
        self.history_status.setText(
            f"\u5df2\u6e05\u7a7a {removed_count} \u6761 Waiting Task \u8ba1\u65f6\u8bb0\u5f55\u3002"
            if removed_count
            else "\u6ca1\u6709\u53ef\u6e05\u7a7a\u7684\u5386\u53f2\u8bb0\u5f55\uff0c\u8fdb\u884c\u4e2d\u6216\u5df2\u6682\u505c\u7684\u4efb\u52a1\u672a\u53d7\u5f71\u54cd\u3002"
        )
        self.history_cleared.emit()

    def _save(self) -> None:
        preferences = Preferences(
            popup_mode=PopupMode(str(self.popup_mode.currentData())),
            in_app_notifications=self.in_app_notifications.isChecked(),
            # Kept in Preferences for backwards-compatible config reads. Codex
            # lifecycle prompts are now rendered only inside the Cookie bubble.
            completion_notifications=False,
            notification_sound=self.notification_sound.isChecked(),
            always_on_top=self.always_on_top.isChecked(),
            auto_check_updates=self.auto_check_updates.isChecked(),
            quiet_hours_enabled=self.quiet_hours.isChecked(),
            quiet_start=self.quiet_start.time().toString("HH:mm"),
            quiet_end=self.quiet_end.time().toString("HH:mm"),
            cookie_size=self.cookie_size.value(),
        )
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as exc:
            QMessageBox.critical(self, "\u5f00\u673a\u542f\u52a8\u8bbe\u7f6e\u5931\u8d25", str(exc))
            return
        self.service.save_preferences(preferences)
        self.settings_changed.emit()
        self.accept()
