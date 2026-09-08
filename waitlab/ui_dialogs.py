"""Qt dialog windows for task, tag, statistics, and preference management.

Dialogs emit user intent and consume application services. They do not own the
main window lifecycle or database connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from PySide6.QtCore import QDate, QDateTime, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDateTimeEdit,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .autostart import is_autostart_enabled, set_autostart
from .models import (
    CompletedFocusRecord,
    DEFAULT_TAG,
    DailyTask,
    DefaultTaskEntry,
    RepeatRule,
    Task,
    TaskPlanningEvent,
    local_date_key,
)
from .preferences import PopupMode, Preferences
from .service import WaitLabService
from .storage_defaults import DEFAULT_TASKS
from .task_filters import filter_and_sort_tasks
from .ui_charts import DailyTagStackedChart, TagDonutChart
from .ui_primitives import (
    TAG_TONES,
    app_icon,
    format_duration,
    tag_palette_for_color,
    tag_palette_for_tag,
    tag_tone,
)
from .ui_styles import dialog_stylesheet
from .ui_widgets import (
    ColorWheelWidget,
    FixedTaskRowWidget,
    TagChipBar,
    TagPickerButton,
    TagPickerPopup,
    TaskRowWidget,
)


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


class TaskEditDialog(QDialog):
    """Edit one manual or fixed task without hiding its tag choice."""

    def __init__(
        self,
        title: str,
        tag: str,
        tags: list[str],
        parent: QWidget | None = None,
        planned_date: str | None = None,
        priority: int = 0,
        due_date: str | None = None,
        repeat_rule: str | None = None,
        repeat_weekday: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑任务")
        self.setMinimumWidth(380)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        heading = QLabel("编辑任务")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        layout.addWidget(QLabel("任务内容"))
        self.title_edit = QLineEdit(title)
        self.title_edit.selectAll()
        layout.addWidget(self.title_edit)
        tag_row = QHBoxLayout()
        tag_label = QLabel("任务标签")
        tag_label.setObjectName("muted")
        tag_row.addWidget(tag_label)
        self.tag_bar = TagChipBar(tags, tag)
        tag_row.addWidget(self.tag_bar, 1)
        layout.addLayout(tag_row)
        self.date_edit: QDateEdit | None = None
        # Kept as a compatibility attribute for integrations that inspected
        # the old dialog; priority is no longer exposed or edited in the UI.
        self.priority_combo: QComboBox | None = None
        self.due_enabled: QCheckBox | None = None
        self.due_date_edit: QDateEdit | None = None
        self.repeat_rule_combo: QComboBox | None = None
        self.repeat_weekday_combo: QComboBox | None = None
        if planned_date is not None:
            date_row = QHBoxLayout()
            date_label = QLabel("计划日期")
            date_label.setObjectName("muted")
            date_row.addWidget(date_label)
            self.date_edit = QDateEdit()
            self.date_edit.setCalendarPopup(True)
            self.date_edit.setDisplayFormat("yyyy-MM-dd")
            selected_date = QDate.fromString(planned_date[:10], Qt.DateFormat.ISODate)
            self.date_edit.setDate(selected_date if selected_date.isValid() else QDate.currentDate())
            date_row.addWidget(self.date_edit, 1)
            layout.addLayout(date_row)

            due_row = QHBoxLayout()
            self.due_enabled = QCheckBox("设置截止日期")
            self.due_enabled.setChecked(bool(due_date))
            self.due_date_edit = QDateEdit()
            self.due_date_edit.setCalendarPopup(True)
            self.due_date_edit.setDisplayFormat("yyyy-MM-dd")
            selected_due_date = QDate.fromString(
                (due_date or planned_date)[:10],
                Qt.DateFormat.ISODate,
            )
            self.due_date_edit.setDate(
                selected_due_date if selected_due_date.isValid() else QDate.currentDate()
            )
            self.due_date_edit.setEnabled(bool(due_date))
            self.due_enabled.toggled.connect(self.due_date_edit.setEnabled)
            due_row.addWidget(self.due_enabled)
            due_row.addStretch()
            due_row.addWidget(self.due_date_edit)
            layout.addLayout(due_row)
        if repeat_rule is not None:
            schedule_row = QHBoxLayout()
            schedule_label = QLabel("重复规则")
            schedule_label.setObjectName("muted")
            schedule_row.addWidget(schedule_label)
            self.repeat_rule_combo = QComboBox()
            for label, rule_value in (
                ("轮播", RepeatRule.ROTATION.value),
                ("每天", RepeatRule.DAILY.value),
                ("工作日", RepeatRule.WEEKDAYS.value),
                ("每周指定日", RepeatRule.WEEKLY.value),
            ):
                self.repeat_rule_combo.addItem(label, rule_value)
            index = self.repeat_rule_combo.findData(repeat_rule)
            self.repeat_rule_combo.setCurrentIndex(index if index >= 0 else 0)
            schedule_row.addWidget(self.repeat_rule_combo, 1)
            self.repeat_weekday_combo = QComboBox()
            for label, value in (
                ("周一", 0),
                ("周二", 1),
                ("周三", 2),
                ("周四", 3),
                ("周五", 4),
                ("周六", 5),
                ("周日", 6),
            ):
                self.repeat_weekday_combo.addItem(label, value)
            weekday_index = self.repeat_weekday_combo.findData(
                repeat_weekday if repeat_weekday is not None else 0
            )
            self.repeat_weekday_combo.setCurrentIndex(max(0, weekday_index))
            schedule_row.addWidget(self.repeat_weekday_combo)
            layout.addLayout(schedule_row)

            def sync_weekday(enabled_index: int) -> None:
                if self.repeat_weekday_combo is not None and self.repeat_rule_combo is not None:
                    self.repeat_weekday_combo.setEnabled(
                        self.repeat_rule_combo.itemData(enabled_index) == RepeatRule.WEEKLY.value
                    )

            self.repeat_rule_combo.currentIndexChanged.connect(sync_weekday)
            sync_weekday(self.repeat_rule_combo.currentIndex())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.title_edit.text(), self.tag_bar.currentText() or DEFAULT_TAG

    def schedule_values(self) -> tuple[str, str, str, int | None]:
        title, tag = self.values()
        if self.repeat_rule_combo is None:
            return title, tag, RepeatRule.ROTATION.value, None
        rule = str(self.repeat_rule_combo.currentData() or RepeatRule.ROTATION.value)
        weekday = (
            int(self.repeat_weekday_combo.currentData())
            if rule == RepeatRule.WEEKLY.value and self.repeat_weekday_combo is not None
            else None
        )
        return title, tag, rule, weekday

    def daily_values(self) -> tuple[str, str, str, int, str | None]:
        title, tag = self.values()
        date = (
            self.date_edit.date().toString(Qt.DateFormat.ISODate)
            if self.date_edit is not None
            else local_date_key()
        )
        due_date = (
            self.due_date_edit.date().toString(Qt.DateFormat.ISODate)
            if self.due_enabled is not None
            and self.due_date_edit is not None
            and self.due_enabled.isChecked()
            else None
        )
        return title, tag, date, 0, due_date


class TaskPlanningHistoryDialog(QDialog):
    """Show the date changes that led a task to its current planned day."""

    def __init__(
        self,
        task: DailyTask,
        events: list[TaskPlanningEvent],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("任务轨迹")
        self.setMinimumSize(460, 300)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)
        title = QLabel("任务轨迹")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        summary = QLabel(
            f"{task.title}\n初始计划：{task.initial_planned_date} · 当前计划：{task.planned_date}"
        )
        summary.setObjectName("muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.event_list = QListWidget()
        for event in events:
            created = event.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            self.event_list.addItem(
                f"{created}  ·  {event.label}：{event.from_date} → {event.to_date}"
            )
        layout.addWidget(self.event_list, 1)
        close_button = QPushButton("完成")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)


class TagColorDialog(QDialog):
    """Choose an arbitrary tag color with an HSV wheel and HEX fallback."""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择标签颜色")
        self.setMinimumSize(360, 360)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("选择标签颜色")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self.wheel = ColorWheelWidget(color, self)
        layout.addWidget(self.wheel, 0, Qt.AlignmentFlag.AlignHCenter)

        brightness_row = QHBoxLayout()
        brightness_row.addWidget(QLabel("明度"))
        self.brightness = QSpinBox()
        self.brightness.setRange(5, 100)
        self.brightness.setSuffix("%")
        self.brightness.setValue(max(5, round(self.wheel.color().valueF() * 100)))
        self.brightness.valueChanged.connect(self._brightness_changed)
        brightness_row.addWidget(self.brightness, 1)
        layout.addLayout(brightness_row)

        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("HEX"))
        self.hex_edit = QLineEdit(self.wheel.color().name().upper())
        self.hex_edit.setMaxLength(7)
        self.hex_edit.textEdited.connect(self._hex_edited)
        hex_row.addWidget(self.hex_edit, 1)
        layout.addLayout(hex_row)

        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("预览"))
        self.preview = QFrame()
        self.preview.setObjectName("tagColorPreview")
        self.preview.setMinimumHeight(28)
        preview_row.addWidget(self.preview, 1)
        layout.addLayout(preview_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.wheel.color_changed.connect(self._wheel_changed)
        self._sync_from_color(self.wheel.color())

    def _sync_from_color(self, color: QColor) -> None:
        self._updating = True
        self.hex_edit.setText(color.name().upper())
        self.brightness.setValue(max(5, round(color.valueF() * 100)))
        self.preview.setStyleSheet(
            f"QFrame#tagColorPreview {{ background: {color.name()}; "
            f"border: 1px solid {color.lighter(125).name()}; border-radius: 7px; }}"
        )
        self._updating = False

    def _wheel_changed(self, color: QColor) -> None:
        if not self._updating:
            self._sync_from_color(color)

    def _brightness_changed(self, value: int) -> None:
        if self._updating:
            return
        current = self.wheel.color()
        hue = current.hsvHueF()
        saturation = current.hsvSaturationF()
        next_color = QColor.fromHsvF(
            0.0 if hue < 0 else hue,
            saturation,
            max(0.05, value / 100.0),
        )
        self.wheel.set_color(next_color, emit=False)
        self._sync_from_color(next_color)

    def _hex_edited(self, text: str) -> None:
        if self._updating:
            return
        color = QColor(text.strip())
        if not color.isValid() or len(text.strip()) not in {4, 7}:
            return
        self.wheel.set_color(color, emit=False)
        self._sync_from_color(color)

    def selected_color(self) -> str:
        return self.wheel.color().name().upper()


class TagManagerDialog(QDialog):
    """Manage the shared labels used by manual and fixed Waiting Tasks."""

    tags_changed = Signal()

    def __init__(
        self,
        service: WaitLabService,
        parent: QWidget | None = None,
        *,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._embedded = embedded
        self.setWindowTitle("Waiting Task \u00b7 \u6807\u7b7e\u7ba1\u7406")
        # The standalone tag manager keeps a comfortable dialog width; when
        # embedded as a task-management tab it must follow the parent width.
        self.setMinimumSize(0 if embedded else 420, 0 if embedded else 380)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
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
        if embedded:
            layout.setContentsMargins(0, 0, 0, 0)
            title.hide()
            subtitle.hide()

        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.tag_list.itemSelectionChanged.connect(self._fill_selected_tag)
        layout.addWidget(self.tag_list, 1)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("\u8f93\u5165\u65b0\u6807\u7b7e\uff0c\u6216\u9009\u62e9\u6807\u7b7e\u540e\u8f93\u5165\u65b0\u540d\u79f0\u2026")
        self.tag_input.returnPressed.connect(self._add_tag)
        layout.addWidget(self.tag_input)

        color_row = QHBoxLayout()
        color_label = QLabel("标签颜色")
        color_label.setObjectName("muted")
        color_row.addWidget(color_label)
        self.color_combo = QComboBox()
        tone_labels = {
            "purple": "紫色",
            "blue": "蓝色",
            "teal": "青绿色",
            "orange": "橙色",
            "yellow": "黄色",
            "red": "红色",
            "slate": "灰蓝色",
        }
        for tone, _foreground, _background in TAG_TONES:
            self.color_combo.addItem(tone_labels.get(tone, tone), tone)
        # Kept as a hidden compatibility control for integrations that used
        # the old tone-based selector.  New users choose an arbitrary color
        # through the wheel dialog below.
        self.color_combo.hide()
        self.color_preview = QFrame()
        self.color_preview.setObjectName("tagColorSwatch")
        self.color_preview.setFixedSize(26, 26)
        color_row.addWidget(self.color_preview)
        self.choose_color_button = QPushButton("选择颜色")
        self.choose_color_button.clicked.connect(self._choose_tag_color)
        color_row.addWidget(self.choose_color_button)
        layout.addLayout(color_row)

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
        self.close_button = QPushButton("\u5b8c\u6210")
        self.close_button.clicked.connect(self.accept)
        self.close_button.setVisible(not embedded)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        selected = self._selected_tag()
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        usage = self.service.tag_usage_counts()
        color_map = self.service.tag_colors()
        for tag in self.service.available_tags():
            accent, _foreground, background, _border = tag_palette_for_tag(tag, color_map)
            item = QListWidgetItem(f"\u25cf  {tag}  \u00b7  {usage.get(tag, 0)} \u4e2a\u4efb\u52a1")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setForeground(QColor(accent))
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
            color_value = self.service.tag_colors().get(selected, tag_tone(selected))
            index = self.color_combo.findData(color_value)
            if index >= 0:
                self.color_combo.setCurrentIndex(index)
            accent, _foreground, _background, border = tag_palette_for_color(color_value)
            self.color_preview.setStyleSheet(
                f"QFrame#tagColorSwatch {{ background: {accent}; "
                f"border: 1px solid {border}; border-radius: 13px; }}"
            )

    def _set_tag_color(self) -> None:
        self._choose_tag_color()

    def _choose_tag_color(self) -> None:
        selected = self._selected_tag()
        if selected is None:
            return
        current = self.service.tag_colors().get(selected, tag_tone(selected))
        accent, _foreground, _background, _border = tag_palette_for_color(current)
        dialog = TagColorDialog(accent, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.set_tag_color(selected, dialog.selected_color())
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.refresh()
        self.tags_changed.emit()

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
        self._planning_date = local_date_key()
        self.setWindowTitle("WaitLAB \u00b7 任务管理")
        # The default size is the supported working size.  Users should not
        # need to widen the dialog before controls become usable.
        self.setMinimumSize(420, 560)
        self.resize(420, 600)
        self._position_checked = False
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
        # The compact dialog uses the outer scroll margins for breathing room;
        # keeping the page itself flush prevents a hidden tab's minimum width
        # from forcing horizontal clipping.
        layout.setContentsMargins(4, 8, 4, 10)
        layout.setSpacing(9)

        self.page_title = QLabel("任务管理")
        self.page_title.setObjectName("taskManagerTitle")
        subtitle = QLabel("安排、整理并追踪每天的任务。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(False)
        layout.addWidget(self.page_title)
        layout.addWidget(subtitle)

        date_nav = QHBoxLayout()
        date_nav.setSpacing(6)
        previous_day = QPushButton("‹")
        previous_day.setObjectName("iconButton")
        previous_day.setFixedWidth(32)
        previous_day.setToolTip("查看前一天")
        previous_day.clicked.connect(lambda: self._shift_planning_date(-1))
        next_day = QPushButton("›")
        next_day.setObjectName("iconButton")
        next_day.setFixedWidth(32)
        next_day.setToolTip("查看后一天")
        next_day.clicked.connect(lambda: self._shift_planning_date(1))
        self.planning_date_edit = QDateEdit()
        self.planning_date_edit.setCalendarPopup(True)
        self.planning_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.planning_date_edit.setDate(QDate.currentDate())
        self.planning_date_edit.setFixedWidth(124)
        self.planning_date_edit.dateChanged.connect(self._planning_date_changed)
        self.today_button = QPushButton("回到今天")
        self.today_button.setObjectName("compactLinkButton")
        self.today_button.clicked.connect(self._show_today)
        self.planning_date_hint = QLabel()
        self.planning_date_hint.setObjectName("muted")
        date_nav.addWidget(previous_day)
        date_nav.addWidget(self.planning_date_edit)
        date_nav.addWidget(next_day)
        date_nav.addWidget(self.today_button)
        date_nav.addStretch()
        self.date_nav_widget = QWidget(page)
        self.date_nav_widget.setLayout(date_nav)
        layout.addWidget(self.date_nav_widget)

        self.action_notice = QLabel()
        self.action_notice.setObjectName("muted")
        self.action_notice.setVisible(False)
        layout.addWidget(self.action_notice)

        overdue_header = QHBoxLayout()
        self.overdue_title = QLabel("过往未完成")
        self.overdue_title.setObjectName("sectionTitle")
        overdue_header.addWidget(self.overdue_title)
        overdue_header.addStretch()
        self.overdue_hint = QLabel()
        self.overdue_hint.setObjectName("muted")
        overdue_header.addWidget(self.overdue_hint)
        self.carry_all_overdue_button = QPushButton("全部延续")
        self.carry_all_overdue_button.setObjectName("compactLinkButton")
        self.carry_all_overdue_button.setToolTip("将全部过往未完成任务延续到当前日期")
        self.carry_all_overdue_button.clicked.connect(self._carry_all_overdue)
        overdue_header.addWidget(self.carry_all_overdue_button)
        self.overdue_header_widget = QWidget(page)
        self.overdue_header_widget.setLayout(overdue_header)
        layout.addWidget(self.overdue_header_widget)
        self.overdue_list = QListWidget()
        self._configure_task_list(self.overdue_list)
        self.overdue_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.overdue_list.itemDoubleClicked.connect(lambda _item: self._edit_selected_overdue())
        layout.addWidget(self.overdue_list)
        self.carry_overdue_button = QPushButton()
        self.carry_overdue_button.setObjectName("primaryButton")
        self.carry_overdue_button.clicked.connect(self._carry_selected_overdue)
        # Kept hidden for API compatibility; overdue rows now expose their
        # own explicit actions so selection is no longer required.
        self.carry_overdue_button.setVisible(False)
        self.overdue_actions_panel = QWidget()
        self.overdue_actions_panel.setVisible(False)
        layout.addWidget(self.overdue_actions_panel)

        composer_header = QHBoxLayout()
        composer_header.setSpacing(8)
        composer_title = QLabel("添加任务")
        composer_title.setObjectName("sectionTitle")
        composer_header.addWidget(composer_title)
        composer_hint = QLabel("Ctrl+Enter 添加 · 支持多行粘贴")
        composer_hint.setObjectName("muted")
        composer_header.addWidget(composer_hint)
        composer_header.addStretch()
        layout.addLayout(composer_header)

        input_row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("输入任务；粘贴多行内容可批量添加")
        self.input.setFixedHeight(46)
        self.input.setToolTip("每行一个任务；可一次粘贴多行，按 Ctrl+Enter 或点击添加")
        QShortcut(QKeySequence("Ctrl+Return"), self.input, activated=self._add_task)
        self.manual_tag = TagPickerButton(
            self.service.available_tags(),
            DEFAULT_TAG,
            tone_map=self.service.tag_colors(),
        )
        self.manual_tag.setToolTip("为新任务选择标签")
        self.manual_tag.manage_tags_requested.connect(self._open_tag_manager)
        self.manual_tag.popup_requested.connect(self._open_tag_popup)
        add_button = QPushButton("\u6dfb\u52a0")
        add_button.setObjectName("primaryButton")
        add_button.setFixedHeight(46)
        add_button.setFixedWidth(58)
        add_button.clicked.connect(self._add_task)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(add_button)
        layout.addLayout(input_row)
        manual_options_row = QHBoxLayout()
        manual_options_row.setSpacing(8)
        self.manual_due_enabled = QCheckBox("设置截止日期")
        self.manual_due_enabled.setToolTip("为新任务设置可选截止日期")
        self.manual_due_date = QDateEdit(QDate.currentDate())
        self.manual_due_date.setCalendarPopup(True)
        self.manual_due_date.setDisplayFormat("yyyy-MM-dd")
        self.manual_due_date.setEnabled(False)
        self.manual_due_date.setVisible(False)
        self.manual_due_enabled.toggled.connect(self._toggle_manual_due_date)
        manual_options_row.addWidget(self.manual_tag)
        manual_options_row.addWidget(self.manual_due_enabled)
        manual_options_row.addWidget(self.manual_due_date)
        manual_options_row.addStretch()
        layout.addLayout(manual_options_row)

        manual_header = QHBoxLayout()
        manual_header.setSpacing(10)
        self.manual_title = QLabel("今日任务")
        self.manual_title.setObjectName("sectionTitle")
        manual_header.addWidget(self.manual_title)
        self.today_hint = QLabel()
        self.today_hint.setObjectName("muted")
        manual_header.addWidget(self.today_hint)
        manual_header.addStretch()
        self.stats_button = QPushButton("统计")
        self.stats_button.setObjectName("compactLinkButton")
        self.stats_button.clicked.connect(self._open_stats)
        manual_header.addWidget(self.stats_button)
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
        self.filter_panel = QWidget()
        self.filter_panel.setLayout(filter_row)
        self.filter_panel.setVisible(False)
        layout.addWidget(self.filter_panel)
        tag_filter_row = QHBoxLayout()
        tag_filter_label = QLabel("\u7b5b\u9009\u6807\u7b7e")
        tag_filter_label.setObjectName("muted")
        tag_filter_row.addWidget(tag_filter_label)
        tag_filter_row.addWidget(self.task_tag_filter, 1)
        self.tag_filter_panel = QWidget()
        self.tag_filter_panel.setLayout(tag_filter_row)
        self.tag_filter_panel.setVisible(False)
        layout.addWidget(self.tag_filter_panel)
        self.list_widget = QListWidget()
        self._configure_task_list(self.list_widget)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.itemChanged.connect(self._today_item_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._start_selected())
        layout.addWidget(self.list_widget)

        action_row = QHBoxLayout()
        self.undo_delete_button = QPushButton("\u64a4\u9500\u5220\u9664")
        self.undo_delete_button.setVisible(False)
        self.undo_delete_button.clicked.connect(self._undo_delete)
        action_row.addWidget(self.undo_delete_button)
        action_row.addStretch()
        self.undo_delete_panel = QWidget()
        self.undo_delete_panel.setLayout(action_row)
        self.undo_delete_panel.setVisible(False)
        layout.addWidget(self.undo_delete_panel)

        fixed_header = QHBoxLayout()
        fallback_title = QLabel("\u56fa\u5b9a\u5faa\u73af\u4efb\u52a1")
        fallback_title.setObjectName("sectionTitle")
        fixed_help = QLabel("勾选启用；可为每项设置轮播、每天、工作日或每周规则")
        fixed_help.setObjectName("muted")
        fixed_header.addWidget(fallback_title)
        fixed_header.addStretch()
        fixed_help.setWordWrap(True)
        fixed_help.setMaximumWidth(190)
        fixed_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        fixed_header.addWidget(fixed_help)
        layout.addLayout(fixed_header)

        self.fixed_list = QListWidget()
        self._configure_task_list(self.fixed_list)
        self.fixed_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.fixed_list)

        fixed_controls = QHBoxLayout()
        fixed_add = QPushButton("\u6dfb\u52a0\u56fa\u5b9a")
        fixed_add.clicked.connect(self._add_fixed_task)
        fixed_reset = QPushButton("\u6062\u590d\u9ed8\u8ba4")
        fixed_reset.clicked.connect(self._reset_defaults)
        fixed_controls.addWidget(fixed_add)
        fixed_controls.addStretch()
        fixed_controls.addWidget(fixed_reset)
        layout.addLayout(fixed_controls)
        self.fallback_title = QLabel("无今日任务时的循环预览")
        self.fallback_title.setObjectName("muted")
        self.fallback = QLabel()
        self.fallback.setWordWrap(True)
        self.fallback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.fallback.setObjectName("fallback")
        layout.addWidget(self.fallback_title)
        layout.addWidget(self.fallback)

        # The original page was built as one long vertical stack.  Reuse the
        # already-created controls, but move their layout items into focused
        # pages so the dialog never shows three management workflows at once.
        section_items = []
        while layout.count() > 3:
            section_items.append(layout.takeAt(3))
        today_page = QWidget(self)
        today_page.setObjectName("todayTasksPage")
        today_layout = QVBoxLayout(today_page)
        today_layout.setContentsMargins(0, 6, 0, 0)
        today_layout.setSpacing(8)
        fixed_page = QWidget(self)
        fixed_page.setObjectName("fixedTasksPage")
        fixed_layout = QVBoxLayout(fixed_page)
        fixed_layout.setContentsMargins(0, 6, 0, 0)
        fixed_layout.setSpacing(8)

        # Split at the named fixed-task header instead of relying on a fragile
        # item count; compacting the daily composer must not move controls to
        # the wrong tab.
        fixed_start = next(
            (
                index
                for index, item in enumerate(section_items)
                if item.layout() is fixed_header
            ),
            len(section_items),
        )
        for item in section_items[:fixed_start]:
            self._adopt_layout_item(item, today_layout, today_page)
        today_layout.addStretch(1)
        for item in section_items[fixed_start:]:
            self._adopt_layout_item(item, fixed_layout, fixed_page)
        fixed_layout.addStretch(1)

        self.section_tabs = QTabWidget(page)
        self.section_tabs.setObjectName("taskManagerTabs")
        self.section_tabs.tabBar().setObjectName("taskManagerTabsBar")
        self.section_tabs.addTab(today_page, "我的任务")
        self.section_tabs.addTab(fixed_page, "固定循环")
        tags_page = QWidget(self.section_tabs)
        tags_page.setObjectName("tagsPage")
        tags_layout = QVBoxLayout(tags_page)
        tags_layout.setContentsMargins(0, 10, 0, 0)
        tags_layout.setSpacing(8)
        tags_intro = QLabel("标签会同步应用到手动任务、循环任务和历史记录。")
        tags_intro.setObjectName("muted")
        tags_intro.setWordWrap(True)
        tags_intro.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tags_layout.addWidget(tags_intro)
        self.tag_manager_page = TagManagerDialog(self.service, self, embedded=True)
        self.tag_manager_page.tags_changed.connect(self._refresh_tag_controls)
        tags_layout.addWidget(self.tag_manager_page, 1)
        self.section_tabs.addTab(tags_page, "标签管理")
        self.section_tabs.currentChanged.connect(self._section_changed)
        layout.insertWidget(3, self.section_tabs, 1)
        self.content_scroll.setWidget(page)
        outer_layout.addWidget(self.content_scroll)
        self._active_tag_button: TagPickerButton | None = None
        self.tag_popup = TagPickerPopup(self)
        self.tag_popup.tag_selected.connect(self._apply_tag_popup_selection)
        self.tag_popup.manage_tags_requested.connect(self._open_tag_manager)
        self._update_planning_date_controls()
        self._section_changed(0)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # A dialog parented to the desktop pet can inherit a position close to
        # the screen edge.  Clamp it after the first layout pass so the user
        # never sees a page whose left or right controls are cut off.
        screen = None
        parent = self.parentWidget()
        if parent is not None:
            screen = QApplication.screenAt(parent.frameGeometry().center())
        screen = screen or QApplication.screenAt(self.frameGeometry().center())
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        inside = (
            frame.left() >= available.left()
            and frame.top() >= available.top()
            and frame.right() <= available.right()
            and frame.bottom() <= available.bottom()
        )
        if inside and self._position_checked:
            return
        x = available.left() + max(0, (available.width() - frame.width()) // 2)
        y = available.top() + max(0, (available.height() - frame.height()) // 2)
        self.move(x, y)
        self._position_checked = True

    def hideEvent(self, event) -> None:  # noqa: N802
        self._close_tag_popup()
        super().hideEvent(event)

    @staticmethod
    def _reparent_layout_widgets(layout: QLayout, parent: QWidget) -> None:
        """Keep nested header controls owned by their new tab page."""

        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None:
                widget.setParent(parent)
            elif item.layout() is not None:
                TaskManagerDialog._reparent_layout_widgets(item.layout(), parent)

    @staticmethod
    def _adopt_layout_item(item, target_layout, parent: QWidget) -> None:
        target_layout.addItem(item)
        if item.widget() is not None:
            item.widget().setParent(parent)
        elif item.layout() is not None:
            TaskManagerDialog._reparent_layout_widgets(item.layout(), parent)

    @staticmethod
    def _configure_task_list(list_widget: QListWidget) -> None:
        """Make task rows flow with their content instead of a nested box."""

        list_widget.setObjectName("taskRows")
        list_widget.setFrameShape(QFrame.Shape.NoFrame)
        list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        list_widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)

    @staticmethod
    def _fit_task_list_height(list_widget: QListWidget) -> None:
        """Keep each task section only as tall as its rows."""

        total = 0
        for index in range(list_widget.count()):
            item = list_widget.item(index)
            row = list_widget.itemWidget(item)
            if row is not None:
                total += max(40, row.sizeHint().height())
            else:
                total += max(28, list_widget.sizeHintForRow(index))
        height = total + 6 if total else 0
        list_widget.setMinimumHeight(height)
        list_widget.setMaximumHeight(height)

    def _planning_date_changed(self, date: QDate) -> None:
        selected = date.toString(Qt.DateFormat.ISODate)
        if not selected or selected == self._planning_date:
            return
        self._planning_date = selected
        self._update_planning_date_controls()
        self.refresh()

    def _shift_planning_date(self, days: int) -> None:
        current = QDate.fromString(self._planning_date, Qt.DateFormat.ISODate)
        if not current.isValid():
            current = QDate.currentDate()
        self.planning_date_edit.setDate(current.addDays(int(days)))

    def _show_today(self) -> None:
        self.planning_date_edit.setDate(QDate.currentDate())

    def _update_planning_date_controls(self) -> None:
        today = local_date_key()
        is_today = self._planning_date == today
        label = "今天" if is_today else self._planning_date
        page_label = "今日任务" if is_today else f"{self._planning_date} 任务"
        # The dialog is the task-management surface; the selected day is
        # shown by the date navigator and the list section heading.
        self.page_title.setText("任务管理")
        self.manual_title.setText(page_label)
        self.setWindowTitle("WaitLAB · 任务管理")
        self.planning_date_hint.setText(
            "今天" if is_today else f"当前查看：{self._planning_date}"
        )
        self.today_button.setVisible(not is_today)
        self.carry_overdue_button.setText(f"延续选中到{label}")
        self.carry_all_overdue_button.setText("全部延续")
        self.carry_all_overdue_button.setToolTip(f"将全部过往未完成任务延续到{label}")
        self.fallback_title.setText(f"无{label}任务时的循环预览")
        self.input.setPlaceholderText("输入任务；粘贴多行内容可批量添加")

    def _toggle_manual_due_date(self, enabled: bool) -> None:
        self.manual_due_date.setEnabled(enabled)
        self.manual_due_date.setVisible(enabled)
        QTimer.singleShot(0, self._schedule_task_manager_layout)

    def _section_changed(self, index: int) -> None:
        """Switch between focused task-management workflows."""

        self._close_tag_popup()
        is_today = index == 0
        self.date_nav_widget.setVisible(is_today)
        if index == 2:
            self.tag_manager_page.refresh()
        QTimer.singleShot(0, self._schedule_task_manager_layout)

    def _schedule_task_manager_layout(self) -> None:
        """Let the page-level scroll area recalculate after a tab switch."""

        page = self.content_scroll.widget()
        if page is not None:
            page.adjustSize()
        self.content_scroll.updateGeometry()

    @staticmethod
    def _daily_task_meta(task: DailyTask, *, overdue: bool = False) -> str:
        parts: list[str] = []
        if overdue:
            parts.append(task.planned_date)
        if task.due_date:
            parts.append(f"截止 {task.due_date}")
        if overdue and task.rollover_count:
            parts.append(f"已顺延 {task.rollover_count} 次")
        if not overdue and task.carried_from_date:
            parts.append(f"由 {task.carried_from_date} 延续")
        return "  ·  ".join(parts)

    def _show_action_notice(self, message: str) -> None:
        self.action_notice.setText(message)
        self.action_notice.setVisible(bool(message))

    def refresh(self) -> None:
        self._close_tag_popup()
        selected_date = self._planning_date
        overdue = self.service.list_overdue_tasks(selected_date)
        self.overdue_list.blockSignals(True)
        self.overdue_list.clear()
        self.overdue_hint.setText(f"{len(overdue)} 项待处理" if overdue else "")
        self.overdue_header_widget.setVisible(bool(overdue))
        self.overdue_title.setVisible(bool(overdue))
        self.overdue_hint.setVisible(bool(overdue))
        self.carry_all_overdue_button.setVisible(bool(overdue))
        self.overdue_list.setVisible(bool(overdue))
        self.overdue_actions_panel.setVisible(False)
        available_tags = self.service.available_tags()
        tone_map = self.service.tag_colors()
        for task in overdue:
            meta = self._daily_task_meta(task, overdue=True)
            # The visible content is rendered by TaskRowWidget below.  Keep
            # the backing QListWidgetItem text-free so Qt does not paint a
            # second title/checkbox underneath the custom row.
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, task)
            tone = self.service.tag_colors().get(task.tag, tag_tone(task.tag))
            row = TaskRowWidget(
                task.title,
                task.tag,
                tone,
                meta=meta,
                overdue=True,
                has_history=task.rollover_count > 0,
                carry_label=(
                    "今天"
                    if self._planning_date == local_date_key()
                    else self._planning_date
                ),
                tags=available_tags,
                tone_map=tone_map,
            )
            row.action_requested.connect(
                lambda action, value=task: self._handle_overdue_action(value, action)
            )
            row.checked_changed.connect(
                lambda checked, value=task: self._set_daily_task_completed(value, checked)
            )
            row.tag_changed.connect(
                lambda tag, value=task: self._change_daily_task_tag(value, tag)
            )
            row.manage_tags_requested.connect(self._open_tag_manager)
            row.tag_popup_requested.connect(self._open_tag_popup)
            self.overdue_list.addItem(item)
            item.setSizeHint(row.sizeHint())
            self.overdue_list.setItemWidget(item, row)
        self._fit_task_list_height(self.overdue_list)
        self.overdue_list.blockSignals(False)

        entries = self.service.default_task_entries()
        due_entries = self.service.due_default_task_entries(selected_date)
        enabled = [f"{entry.title}\uff08{entry.tag}\uff09" for entry in due_entries]
        disabled_count = sum(not entry.enabled for entry in entries)
        if enabled:
            suffix = f"\uff08\u53e6\u6709 {disabled_count} \u9879\u5df2\u505c\u7528\uff09" if disabled_count else ""
            self.fallback.setText("  \u00b7  ".join(enabled) + suffix)
        else:
            self.fallback.setText(
                "\u5f53\u524d\u65e5\u671f\u6ca1\u6709\u5e94\u6267\u884c\u7684\u56fa\u5b9a\u4efb\u52a1\uff0c\u53ef\u5728\u4e0b\u65b9\u8bbe\u7f6e\u91cd\u590d\u89c4\u5219\u3002"
                if any(entry.enabled for entry in entries)
                else "\u56fa\u5b9a\u4efb\u52a1\u5df2\u5168\u90e8\u505c\u7528\uff0c\u53ef\u5728\u4e0a\u65b9\u91cd\u65b0\u542f\u7528\u3002"
            )
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        query = self.task_search.text().strip()
        selected_tag = self.task_tag_filter.currentText()
        daily_tasks = self.service.list_daily_tasks(selected_date)
        completed_count = sum(task.is_completed for task in daily_tasks)
        self.today_hint.setText(f"{completed_count} / {len(daily_tasks)} 已完成")
        # These controls are hidden in the new daily view but remain wired for
        # compatibility with older integrations and saved UI tests.
        if query or selected_tag != "\u5168\u90e8\u6807\u7b7e":
            tasks = filter_and_sort_tasks(
                [task.as_task() for task in daily_tasks if not task.is_completed],
                query=query,
                tag=selected_tag,
                sort_mode=self.task_sort.currentText(),
            )
            task_by_id = {task.id: task for task in daily_tasks}
            visible_daily = [task_by_id[task.id] for task in tasks if task.id in task_by_id]
        else:
            visible_daily = daily_tasks
        self._fill_fixed_tasks(entries)
        if not visible_daily:
            message = (
                "\u6ca1\u6709\u5339\u914d\u7684\u624b\u52a8\u4efb\u52a1"
                if query or selected_tag != "\u5168\u90e8\u6807\u7b7e"
                else f"{selected_date} 还没有任务，先添加一项吧"
            )
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(placeholder)
            self._fit_task_list_height(self.list_widget)
            self.list_widget.blockSignals(False)
            return
        completed_header_added = False
        for daily_task in visible_daily:
            if daily_task.is_completed and not completed_header_added:
                completed_header = QListWidgetItem(f"已完成 · {completed_count} 项")
                completed_header.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(completed_header)
                completed_header_added = True
            meta = self._daily_task_meta(daily_task)
            # TaskRowWidget owns the checkbox and title for this item.
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, daily_task)
            self.list_widget.addItem(item)
            row = TaskRowWidget(
                daily_task.title,
                daily_task.tag,
                self.service.tag_colors().get(
                    daily_task.tag,
                    tag_tone(daily_task.tag),
                ),
                completed=daily_task.is_completed,
                meta=meta,
                has_history=daily_task.rollover_count > 0,
                tags=available_tags,
                tone_map=tone_map,
            )
            row.action_requested.connect(
                lambda action, value=daily_task: self._handle_today_action(value, action)
            )
            row.checked_changed.connect(
                lambda checked, value=daily_task: self._set_daily_task_completed(value, checked)
            )
            row.tag_changed.connect(
                lambda tag, value=daily_task: self._change_daily_task_tag(value, tag)
            )
            row.manage_tags_requested.connect(self._open_tag_manager)
            row.tag_popup_requested.connect(self._open_tag_popup)
            item.setSizeHint(row.sizeHint())
            self.list_widget.setItemWidget(item, row)
        self._fit_task_list_height(self.list_widget)
        self.list_widget.blockSignals(False)

    def _daily_task_from_item(self, item: QListWidgetItem | None) -> DailyTask | None:
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(value, DailyTask):
            return value
        return None

    def _set_daily_task_completed(self, task: DailyTask, completed: bool) -> None:
        if (
            completed
            and self.service.focus is not None
            and self.service.focus.task.id == task.id
        ):
            self.service.complete_focus()
        else:
            self.service.set_manual_task_completed(task.id, completed)
        self.refresh()
        self.tasks_changed.emit()

    def _handle_today_action(self, task: DailyTask, action: str) -> None:
        if action == "start" and not task.is_completed:
            self.task_started.emit(task.as_task())
        elif action == "edit":
            self._edit_daily_task(task)
        elif action == "history":
            self._show_task_history(task)
        elif action == "delete":
            self._delete_task(task.as_task())

    def _change_daily_task_tag(self, task: DailyTask, tag: str) -> None:
        """Persist a row-level tag change without opening the full editor."""

        try:
            self.service.update_manual_task(
                task.id,
                task.title,
                tag,
                due_date=task.due_date,
            )
        except ValueError as error:
            QMessageBox.warning(self, "修改标签失败", str(error))
        self.refresh()
        self.tasks_changed.emit()

    def _handle_overdue_action(self, task: DailyTask, action: str) -> None:
        if action == "complete":
            self.service.set_manual_task_completed(task.id, True)
            self._show_action_notice(f"已标记完成：{task.title}")
            self.refresh()
            self.tasks_changed.emit()
        elif action == "carry":
            self.service.carry_manual_task(task.id, self._planning_date)
            self._show_action_notice(f"已延续到{self._planning_date}：{task.title}")
            self.refresh()
            self.tasks_changed.emit()
        elif action == "edit":
            self._edit_daily_task(task)
        elif action == "history":
            self._show_task_history(task)
        elif action == "delete":
            self._delete_task(task.as_task())

    def _overdue_item_changed(self, item: QListWidgetItem) -> None:
        task = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(task, DailyTask) or item.checkState() != Qt.CheckState.Checked:
            return
        self._set_daily_task_completed(task, True)

    def _selected_overdue(self) -> list[DailyTask]:
        tasks: list[DailyTask] = []
        for item in self.overdue_list.selectedItems():
            task = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(task, DailyTask):
                tasks.append(task)
        return tasks

    def _carry_selected_overdue(self) -> None:
        tasks = self._selected_overdue()
        if not tasks:
            return
        self.service.carry_manual_tasks([task.id for task in tasks], self._planning_date)
        self.refresh()
        self.tasks_changed.emit()

    def _carry_all_overdue(self) -> None:
        tasks = self.service.list_overdue_tasks(self._planning_date)
        if not tasks:
            return
        target_label = (
            "今天" if self._planning_date == local_date_key() else self._planning_date
        )
        answer = QMessageBox.question(
            self,
            "延续全部过往任务？",
            f"将 {len(tasks)} 项任务加入{target_label}，继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.service.carry_manual_tasks([task.id for task in tasks], self._planning_date)
        self.refresh()
        self.tasks_changed.emit()

    def _complete_selected_overdue(self) -> None:
        tasks = self._selected_overdue()
        for task in tasks:
            self.service.set_manual_task_completed(task.id, True)
        if tasks:
            self.refresh()
            self.tasks_changed.emit()

    def _edit_selected_overdue(self) -> None:
        tasks = self._selected_overdue()
        if tasks:
            self._edit_daily_task(tasks[0])

    def _delete_selected_overdue(self) -> None:
        tasks = self._selected_overdue()
        for task in tasks:
            self._delete_task(task.as_task())
        if tasks:
            self.refresh()
            self.tasks_changed.emit()

    def _edit_daily_task(self, daily_task: DailyTask) -> None:
        dialog = TaskEditDialog(
            daily_task.title,
            daily_task.tag,
            self.service.available_tags(),
            self,
            planned_date=daily_task.planned_date,
            due_date=daily_task.due_date,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, tag, planned_date, _legacy_priority, due_date = dialog.daily_values()
        try:
            self.service.update_manual_task(
                daily_task.id,
                title,
                tag,
                due_date=due_date,
            )
            self.service.reschedule_manual_task(daily_task.id, planned_date)
        except ValueError as error:
            QMessageBox.warning(self, "修改任务失败", str(error))
            return
        self.refresh()
        self.tasks_changed.emit()

    def _show_task_history(self, daily_task: DailyTask) -> None:
        events = self.service.list_task_planning_events(daily_task.id)
        if not events:
            return
        TaskPlanningHistoryDialog(daily_task, events, self).exec()

    def _edit_selected(self) -> None:
        daily_task = self._daily_task_from_item(self.list_widget.currentItem())
        if daily_task is not None:
            self._edit_daily_task(daily_task)

    def _today_item_changed(self, item: QListWidgetItem) -> None:
        daily_task = self._daily_task_from_item(item)
        if daily_task is None:
            return
        completed = item.checkState() == Qt.CheckState.Checked
        self._set_daily_task_completed(daily_task, completed)

    def _add_task(self) -> None:
        titles = [
            " ".join(line.strip().split())
            for line in self.input.toPlainText().splitlines()
            if line.strip()
        ]
        if not titles:
            self._show_action_notice("请输入至少一项任务")
            self.input.setFocus()
            return
        try:
            for title in titles:
                self.service.add_manual_task(
                    title,
                    self.manual_tag.currentText(),
                    planned_date=self._planning_date,
                    due_date=(
                        self.manual_due_date.date().toString(Qt.DateFormat.ISODate)
                        if self.manual_due_enabled.isChecked()
                        else None
                    ),
                )
        except ValueError as error:
            self._show_action_notice(str(error) or "任务名称不能为空")
            self.input.setFocus()
            return
        self.input.clear()
        self._show_action_notice(f"已添加 {len(titles)} 项任务")
        self.refresh()
        self.tasks_changed.emit()

    def _open_tag_manager(self) -> None:
        self._close_tag_popup()
        self.section_tabs.setCurrentIndex(2)
        self.tag_manager_page.refresh()

    def _open_tag_popup(self, button: object) -> None:
        if not isinstance(button, TagPickerButton):
            return
        if self.tag_popup.isVisible() and self._active_tag_button is button:
            self._close_tag_popup()
            return
        self._active_tag_button = button
        self.tag_popup.open_for(button)

    def _apply_tag_popup_selection(self, tag: str) -> None:
        button = self._active_tag_button
        self._active_tag_button = None
        if button is not None:
            button.setCurrentText(tag)

    def _close_tag_popup(self) -> None:
        if hasattr(self, "tag_popup"):
            self.tag_popup.hide()
        self._active_tag_button = None

    def _refresh_tag_controls(self) -> None:
        tags = self.service.available_tags()
        self.service.stats_cache.invalidate()
        tone_map = self.service.tag_colors()
        self.manual_tag.set_tone_map(tone_map)
        self.task_tag_filter.set_tone_map(tone_map)
        self.manual_tag.set_tags(tags, self.manual_tag.currentText())
        selected_filter = self.task_tag_filter.currentText()
        self.task_tag_filter.set_tags(
            ["\u5168\u90e8\u6807\u7b7e", *tags],
            selected_filter if selected_filter in tags else "\u5168\u90e8\u6807\u7b7e",
        )
        self.tag_manager_page.refresh()
        self.refresh()
        self.tasks_changed.emit()

    def _selected_task(self) -> Task | None:
        item = self.list_widget.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(value, DailyTask):
            return value.as_task()
        return value if isinstance(value, Task) else None

    def _start_selected(self) -> None:
        item = self.list_widget.currentItem()
        daily_task = self._daily_task_from_item(item)
        task = daily_task.as_task() if daily_task is not None else None
        if daily_task is not None and task is not None and not daily_task.is_completed:
            self.task_started.emit(task)

    def _delete_selected(self) -> None:
        task = self._selected_task()
        if task is not None:
            self._delete_task(task)

    def _delete_task(self, task: Task) -> None:
        if task.id is None:
            return
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
        self.undo_delete_panel.setVisible(True)
        self.refresh()
        self.tasks_changed.emit()

    def _undo_delete(self) -> None:
        if self._deleted_task is None or self._deleted_task.id is None:
            return
        self.service.restore_manual_task(self._deleted_task.id)
        self._deleted_task = None
        self.undo_delete_button.setVisible(False)
        self.undo_delete_panel.setVisible(False)
        self.refresh()
        self.tasks_changed.emit()

    def _fill_fixed_tasks(self, entries: list[DefaultTaskEntry]) -> None:
        self.fixed_list.blockSignals(True)
        self.fixed_list.clear()
        available_tags = self.service.available_tags()
        tone_map = self.service.tag_colors()
        for entry in entries:
            tone = tone_map.get(entry.tag, tag_tone(entry.tag))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            row = FixedTaskRowWidget(
                entry,
                tone,
                tags=available_tags,
                tone_map=tone_map,
            )
            row.enabled_changed.connect(
                lambda enabled, value=item: self._set_fixed_item_enabled(value, enabled)
            )
            row.action_requested.connect(
                lambda action, value=item: self._handle_fixed_row_action(value, action)
            )
            row.tag_changed.connect(
                lambda tag, value=item: self._change_fixed_task_tag(value, tag)
            )
            row.manage_tags_requested.connect(self._open_tag_manager)
            row.tag_popup_requested.connect(self._open_tag_popup)
            self.fixed_list.addItem(item)
            item.setSizeHint(row.sizeHint())
            self.fixed_list.setItemWidget(item, row)
        self.fixed_list.blockSignals(False)
        self._fit_task_list_height(self.fixed_list)

    @staticmethod
    def _fixed_item_values(item: QListWidgetItem) -> tuple[str, str]:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, DefaultTaskEntry):
            return entry.title, entry.tag
        return item.text().split("  \u00b7  ", 1)[0].strip(), DEFAULT_TAG

    @staticmethod
    def _fixed_entry(item: QListWidgetItem) -> DefaultTaskEntry:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, DefaultTaskEntry):
            row = item.listWidget().itemWidget(item) if item.listWidget() is not None else None
            if isinstance(row, FixedTaskRowWidget):
                return DefaultTaskEntry(
                    entry.title,
                    row.enabled_checkbox.isChecked(),
                    entry.tag,
                    entry.repeat_rule,
                    entry.repeat_weekday,
                )
            return entry
        title, tag = TaskManagerDialog._fixed_item_values(item)
        return DefaultTaskEntry(title, item.checkState() == Qt.CheckState.Checked, tag)

    def _set_fixed_item_enabled(self, item: QListWidgetItem, enabled: bool) -> None:
        entry = self._fixed_entry(item)
        item.setData(
            Qt.ItemDataRole.UserRole,
            DefaultTaskEntry(
                entry.title,
                enabled,
                entry.tag,
                entry.repeat_rule,
                entry.repeat_weekday,
            ),
        )
        self._persist_fixed()

    def _change_fixed_task_tag(self, item: QListWidgetItem, tag: str) -> None:
        entry = self._fixed_entry(item)
        item.setData(
            Qt.ItemDataRole.UserRole,
            DefaultTaskEntry(
                entry.title,
                entry.enabled,
                tag,
                entry.repeat_rule,
                entry.repeat_weekday,
            ),
        )
        self._persist_fixed()

    def _handle_fixed_row_action(self, item: QListWidgetItem, action: str) -> None:
        if action == "edit":
            self._rename_fixed_item(item)
        elif action == "delete":
            self._delete_fixed_item(item)

    def _persist_fixed(self) -> None:
        entries: list[DefaultTaskEntry] = []
        seen: set[str] = set()
        for index in range(self.fixed_list.count()):
            item = self.fixed_list.item(index)
            entry = self._fixed_entry(item)
            title, tag = entry.title, entry.tag
            title = " ".join(title.split())
            if not title or title in seen:
                continue
            seen.add(title)
            entries.append(
                DefaultTaskEntry(
                    title,
                    entry.enabled,
                    tag,
                    entry.repeat_rule,
                    entry.repeat_weekday,
                )
            )
        self.service.set_default_task_entries(entries)
        self.refresh()
        self.tasks_changed.emit()

    def _add_fixed_task(self) -> None:
        dialog = TaskEditDialog(
            "",
            DEFAULT_TAG,
            self.service.available_tags(),
            self,
            repeat_rule=RepeatRule.ROTATION.value,
        )
        dialog.setWindowTitle("添加循环任务")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, tag, repeat_rule, repeat_weekday = dialog.schedule_values()
        clean_title = " ".join(title.strip().split())
        if not clean_title or self._has_fixed_title(clean_title):
            return
        entry = DefaultTaskEntry(clean_title, True, tag, repeat_rule, repeat_weekday)
        self._append_fixed_entry(entry)
        self._persist_fixed()

    def _append_fixed_entry(self, entry: DefaultTaskEntry) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, entry)
        row = FixedTaskRowWidget(
            entry,
            self.service.tag_colors().get(entry.tag, tag_tone(entry.tag)),
            tags=self.service.available_tags(),
            tone_map=self.service.tag_colors(),
        )
        row.enabled_changed.connect(
            lambda enabled, value=item: self._set_fixed_item_enabled(value, enabled)
        )
        row.action_requested.connect(
            lambda action, value=item: self._handle_fixed_row_action(value, action)
        )
        row.tag_changed.connect(
            lambda tag, value=item: self._change_fixed_task_tag(value, tag)
        )
        row.manage_tags_requested.connect(self._open_tag_manager)
        row.tag_popup_requested.connect(self._open_tag_popup)
        self.fixed_list.addItem(item)
        item.setSizeHint(row.sizeHint())
        self.fixed_list.setItemWidget(item, row)
        self._fit_task_list_height(self.fixed_list)

    def _rename_selected_fixed(self) -> None:
        item = self.fixed_list.currentItem()
        if item is None:
            return
        self._rename_fixed_item(item)

    def _rename_fixed_item(self, item: QListWidgetItem) -> None:
        current_entry = self._fixed_entry(item)
        old_title, tag = current_entry.title, current_entry.tag
        dialog = TaskEditDialog(
            old_title,
            tag,
            self.service.available_tags(),
            self,
            repeat_rule=current_entry.repeat_rule,
            repeat_weekday=current_entry.repeat_weekday,
        )
        dialog.setWindowTitle("编辑循环任务")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, next_tag, repeat_rule, repeat_weekday = dialog.schedule_values()
        clean_title = " ".join(title.strip().split())
        if not clean_title or self._has_fixed_title(clean_title, item):
            return
        next_entry = DefaultTaskEntry(
            clean_title,
            current_entry.enabled,
            next_tag,
            repeat_rule,
            repeat_weekday,
        )
        item.setData(
            Qt.ItemDataRole.UserRole,
            next_entry,
        )
        row = self.fixed_list.itemWidget(item)
        if isinstance(row, FixedTaskRowWidget):
            row.title_label.setText(clean_title)
            row.title_label.setToolTip(clean_title)
            row.tag_label.set_tags(self.service.available_tags(), next_tag)
            row.tag_label.set_tone_map(self.service.tag_colors())
            row.schedule_label.setText(next_entry.schedule_label)
        item.setSizeHint(row.sizeHint() if isinstance(row, FixedTaskRowWidget) else item.sizeHint())
        self._persist_fixed()

    def _delete_selected_fixed(self) -> None:
        row = self.fixed_list.currentRow()
        if row >= 0:
            self._delete_fixed_item(self.fixed_list.item(row))

    def _delete_fixed_item(self, item: QListWidgetItem) -> None:
        row = self.fixed_list.row(item)
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

    def _reset_defaults(self) -> None:
        current = self.service.default_task_entries()
        has_customization = [entry.title for entry in current] != list(DEFAULT_TASKS)
        if has_customization:
            answer = QMessageBox.question(
                self,
                "恢复默认循环任务？",
                "这会替换当前循环任务的名称、标签、启用状态和循环规则。继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._fill_fixed_tasks([DefaultTaskEntry(title, True, DEFAULT_TAG) for title in DEFAULT_TASKS])
        self._persist_fixed()
        self._show_action_notice("已恢复默认循环任务")

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
        color_map = self.service.tag_colors()
        self.today_total_label.setText(format_duration(day_snapshot.waiting_seconds))
        self.today_donut.set_color_map(color_map)
        self.today_donut.set_values(day_snapshot.tag_seconds)
        self.today_legend.setText(self._legend_html(day_snapshot.tag_seconds, color_map))
        self._refresh_series()

    def _set_period(self, period: str) -> None:
        self._period = period
        self.week_button.setChecked(period == "week")
        self.month_button.setChecked(period == "month")
        self._refresh_series()

    def _refresh_series(self) -> None:
        buckets = self.service.tag_waiting_daily_series(self._period)
        color_map = self.service.tag_colors()
        self.series_chart.set_color_map(color_map)
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
        self.series_legend.setText(self._legend_html(totals, color_map))

    @staticmethod
    def _legend_html(
        values: dict[str, float],
        color_map: dict[str, str] | None = None,
    ) -> str:
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
            accent, _foreground, _background, _border = tag_palette_for_tag(
                tag, color_map
            )
            percentage = seconds / total * 100 if total else 0
            parts.append(
                f'<span style="color:{accent};">\u25cf</span> '
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
        self.setMinimumSize(560, 420)
        self.resize(620, 480)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)

        title = QLabel("\u65e5\u7528\u8bbe\u7f6e")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("\u63a7\u5236\u63d0\u9192\u3001\u542f\u52a8\u3001\u5916\u89c2\u548c\u672c\u5730\u6570\u636e\u3002\u4efb\u52a1\u7edf\u4e00\u5728\u4efb\u52a1\u7ba1\u7406\u4e2d\u7ef4\u62a4\u3002")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        behavior_title = QLabel("\u63d0\u9192\u4e0e\u884c\u4e3a")
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
        self.focus_guard_minutes = QSpinBox()
        self.focus_guard_minutes.setRange(0, 720)
        self.focus_guard_minutes.setSingleStep(30)
        self.focus_guard_minutes.setSuffix(" 分钟")
        self.focus_guard_minutes.setToolTip(
            "单次连续计时达到此时长后自动暂停；设为 0 可关闭保护"
        )
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
        focus_guard_row = QHBoxLayout()
        focus_guard_row.addWidget(QLabel("连续计时保护"))
        focus_guard_row.addStretch()
        focus_guard_row.addWidget(self.focus_guard_minutes)
        layout.addLayout(focus_guard_row)
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
        actions_item = layout.takeAt(layout.count() - 1)

        # Present settings as focused sections.  The controls above are kept
        # intact (including their existing object attributes and signal
        # connections), while their layout items are moved into a west-tab
        # navigation so users do not have to scan one long settings form.
        setting_items = []
        while layout.count() > 2:
            setting_items.append(layout.takeAt(2))

        settings_pages = QStackedWidget(self)
        settings_pages.setObjectName("settingsPages")
        settings_pages.currentChanged.connect(self._sync_settings_nav)
        # Keep the historical ``settings_tabs`` attribute as a stack-like
        # compatibility handle; the visible navigation is now custom so its
        # labels stay horizontal.
        self.settings_tabs = settings_pages

        settings_sidebar = QWidget(self)
        settings_sidebar.setObjectName("settingsSidebar")
        settings_sidebar.setFixedWidth(132)
        sidebar_layout = QVBoxLayout(settings_sidebar)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)
        sidebar_layout.setSpacing(4)
        settings_nav = QButtonGroup(self)
        settings_nav.setExclusive(True)
        self.settings_nav = settings_nav
        self.settings_nav_buttons: list[QPushButton] = []
        for index, label in enumerate(("提醒与行为", "启动与外观", "数据")):
            button = QPushButton(label, settings_sidebar)
            button.setObjectName("settingsNavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked, value=index: self._set_settings_section(value)
            )
            settings_nav.addButton(button, index)
            sidebar_layout.addWidget(button)
            self.settings_nav_buttons.append(button)
        sidebar_layout.addStretch(1)

        def make_page(
            object_name: str,
            title_text: str | None,
            item_indexes: tuple[int, ...],
        ) -> QWidget:
            page = QWidget(settings_pages)
            page.setObjectName(object_name)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(16, 12, 12, 12)
            page_layout.setSpacing(12)
            if title_text:
                page_title = QLabel(title_text)
                page_title.setObjectName("sectionTitle")
                page_layout.addWidget(page_title)
            for item_index in item_indexes:
                TaskManagerDialog._adopt_layout_item(
                    setting_items[item_index], page_layout, page
                )
            page_layout.addStretch(1)
            return page

        behavior_page = make_page(
            "settingsBehaviorPage",
            None,
            (0, 1, 2, 3, 4, 8, 10),
        )
        startup_page = make_page(
            "settingsStartupPage",
            "\u542f\u52a8\u4e0e\u5916\u89c2",
            (5, 6, 7, 9),
        )
        data_page = make_page(
            "settingsDataPage",
            None,
            (11, 12, 13, 14),
        )
        settings_pages.addWidget(behavior_page)
        settings_pages.addWidget(startup_page)
        settings_pages.addWidget(data_page)
        settings_body = QHBoxLayout()
        settings_body.setContentsMargins(0, 0, 0, 0)
        settings_body.setSpacing(12)
        settings_body.addWidget(settings_sidebar)
        settings_body.addWidget(settings_pages, 1)
        layout.addLayout(settings_body, 1)
        layout.addItem(actions_item)
        self._set_settings_section(0)
        self.refresh()

    def _set_settings_section(self, index: int) -> None:
        index = max(0, min(int(index), self.settings_tabs.count() - 1))
        self.settings_tabs.setCurrentIndex(index)
        self._sync_settings_nav(index)

    def _sync_settings_nav(self, index: int) -> None:
        for button_index, button in enumerate(self.settings_nav_buttons):
            button.setChecked(button_index == index)

    def refresh(self) -> None:
        preferences = self.service.load_preferences()
        mode_index = self.popup_mode.findData(preferences.popup_mode.value)
        self.popup_mode.setCurrentIndex(max(0, mode_index))
        self.in_app_notifications.setChecked(preferences.in_app_notifications)
        self.notification_sound.setChecked(preferences.notification_sound)
        self.autostart.setChecked(is_autostart_enabled())
        self.always_on_top.setChecked(preferences.always_on_top)
        self.cookie_size.setValue(preferences.cookie_size)
        self.focus_guard_minutes.setValue(preferences.focus_guard_minutes)
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
            focus_guard_minutes=self.focus_guard_minutes.value(),
        )
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as exc:
            QMessageBox.critical(self, "\u5f00\u673a\u542f\u52a8\u8bbe\u7f6e\u5931\u8d25", str(exc))
            return
        self.service.save_preferences(preferences)
        self.settings_changed.emit()
        self.accept()
