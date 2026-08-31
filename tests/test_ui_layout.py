import os
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from waitlab.models import (
    DefaultTaskEntry,
    FocusOutcome,
    ServiceUpdate,
    TagTimeBucket,
    Task,
    TaskKind,
)
from waitlab.app import DesktopActivityReceiver
from waitlab.service import WaitLabService
from waitlab.storage import Storage
import waitlab.ui as ui_module
from waitlab.ui import (
    DailyTagStackedChart,
    PetWindow,
    StatisticsDialog,
    TagChipBar,
    TagDonutChart,
    TagManagerDialog,
    TaskManagerDialog,
)


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def pet_window(tmp_path, qt_app):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    window.show()
    qt_app.processEvents()
    yield window
    window.timer.stop()
    window.close()
    storage.close()


def _flush(qt_app):
    for _ in range(3):
        qt_app.processEvents()


class _PollEmitter(QObject):
    poll_ready = Signal(object, object, object, object, object)

    @Slot()
    def emit_once(self) -> None:
        self.poll_ready.emit([], True, None, None, ())


class _ReceiverWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.threads: list[QThread] = []

    def set_desktop_source_status(self, *_args: object) -> None:
        self.threads.append(QThread.currentThread())

    def set_desktop_snapshots(self, *_args: object) -> None:
        self.threads.append(QThread.currentThread())

    def handle_desktop_event(self, *_args: object) -> None:
        self.threads.append(QThread.currentThread())

    def apply_update(self, *_args: object) -> None:
        self.threads.append(QThread.currentThread())


class _ReceiverService:
    def reconcile_desktop_sessions(self, *_args: object) -> ServiceUpdate:
        return ServiceUpdate()


def test_desktop_activity_receiver_runs_on_gui_thread(qt_app):
    window = _ReceiverWindow()
    receiver = DesktopActivityReceiver(window, _ReceiverService())
    emitter = _PollEmitter()
    thread = QThread()
    emitter.moveToThread(thread)
    emitter.poll_ready.connect(receiver.handle_poll, Qt.ConnectionType.QueuedConnection)
    thread.started.connect(emitter.emit_once)
    thread.start()
    try:
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.start(500)
        while not window.threads and deadline.isActive():
            qt_app.processEvents()
        assert window.threads
        assert all(current == QThread.currentThread() for current in window.threads)
    finally:
        thread.quit()
        thread.wait(2000)


def test_player_keeps_long_title_and_controls_separated(pet_window, qt_app):
    task = pet_window.service.storage.add_manual_task(
        "这是一个很长的等待任务标题，用来验证操作按钮不会遮挡任务名称"
    )
    pet_window.start_focus(task)
    _flush(qt_app)

    assert pet_window.presentation_mode.value == "player"
    assert pet_window.bubble_card.width() >= pet_window.focus_card.width()
    buttons = pet_window.focus_card.findChildren(QPushButton)
    buttons = [button for button in buttons if button.objectName() in {
        "playerButton",
        "playerPrimaryButton",
        "playerCloseButton",
    }]
    assert len(buttons) == 3
    assert all(button.width() >= 94 and button.height() >= 44 for button in buttons)

    rectangles = [
        QRect(button.mapToGlobal(QPoint(0, 0)), button.size())
        for button in buttons
    ]
    assert not rectangles[0].intersects(rectangles[1])
    assert not rectangles[1].intersects(rectangles[2])
    assert not rectangles[0].intersects(rectangles[2])


def test_minimized_player_keeps_timer_and_cookie_restores_controls(pet_window, qt_app):
    task = pet_window.service.storage.add_manual_task("保持计时的任务")
    pet_window.start_focus(task)
    _flush(qt_app)

    pet_window.hide_page()
    _flush(qt_app)

    assert pet_window.page_hidden is True
    assert pet_window.presentation_mode.value == "compact_player"
    assert pet_window.focus_card.isVisible() is False
    assert pet_window.focus_controls.isVisible() is False
    assert pet_window.compact_timer_label.isVisible()
    assert pet_window.compact_timer_label.text() == pet_window.focus_time.text()
    assert task.title in pet_window.compact_timer_label.toolTip()

    pet_window.pet.clicked.emit()
    _flush(qt_app)

    assert pet_window.page_hidden is False
    assert pet_window.presentation_mode.value == "player"
    assert pet_window.focus_card.isVisible()
    assert pet_window.focus_controls.isVisible()


def test_paused_player_can_open_switcher_and_start_another_task(pet_window, qt_app):
    first = pet_window.service.storage.add_manual_task("当前任务")
    second = pet_window.service.storage.add_manual_task("切换后的任务")
    pet_window.start_focus(first)
    _flush(qt_app)

    pause_button = pet_window.focus_card.findChild(QPushButton, "playerButton")
    switch_button = pet_window.focus_card.findChild(QPushButton, "playerSwitchButton")
    assert pause_button is not None and switch_button is not None
    assert switch_button.isEnabled() is True

    switch_button.click()
    _flush(qt_app)
    assert pet_window.service.focus is not None
    assert pet_window.service.focus.is_paused is True
    assert pet_window.task_picker_open is True
    assert pet_window.presentation_mode.value == "picker"
    second_button = next(
        button
        for button in pet_window.suggestion_container.findChildren(QPushButton)
        if button.objectName() == "taskButton" and second.title in button.text()
    )
    second_button.click()
    _flush(qt_app)

    assert pet_window.service.focus is not None
    assert pet_window.service.focus.task.id == second.id
    assert [session.task.id for session in pet_window.service.paused_focuses()] == [first.id]


def test_switcher_includes_fixed_cycle_tasks_with_manual_tasks(pet_window, qt_app):
    current = pet_window.service.storage.add_manual_task("当前手动任务")
    fixed_titles = {
        task.title for task in pet_window.service.fixed_cycle_tasks(limit=100)
    }
    pet_window.start_focus(current)
    _flush(qt_app)

    switch_button = pet_window.focus_card.findChild(QPushButton, "playerSwitchButton")
    assert switch_button is not None
    switch_button.click()
    _flush(qt_app)

    fixed_button = next(
        button
        for button in pet_window.suggestion_container.findChildren(QPushButton)
        if button.objectName() == "taskButton"
        and any(title in button.text() for title in fixed_titles)
    )
    fixed_button.click()
    _flush(qt_app)

    assert pet_window.service.focus is not None
    assert pet_window.service.focus.task.kind is TaskKind.DEFAULT
    assert pet_window.service.focus.task.title in fixed_titles


def test_home_picker_shows_manual_and_stable_fixed_sample(pet_window, qt_app):
    manual = pet_window.service.storage.add_manual_task("主页具体任务")
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    first_sample = [task.title for task in pet_window._fixed_cycle_candidates or []]
    task_buttons = [
        button
        for button in pet_window.suggestion_container.findChildren(QPushButton)
        if button.objectName() == "taskButton"
    ]
    assert manual.title in " ".join(button.text() for button in task_buttons)
    assert first_sample
    assert all(
        any(title in button.text() for button in task_buttons)
        for title in first_sample
    )

    pet_window.refresh()
    _flush(qt_app)
    assert [task.title for task in pet_window._fixed_cycle_candidates or []] == first_sample


def test_fixed_task_button_can_start_without_manual_tasks(pet_window, qt_app):
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    fixed_button = next(
        button
        for button in pet_window.suggestion_container.findChildren(QPushButton)
        if button.objectName() == "taskButton"
    )
    fixed_button.click()
    _flush(qt_app)

    assert pet_window.service.focus is not None
    assert pet_window.service.focus.task.kind is TaskKind.DEFAULT


def test_home_picker_can_reenable_disabled_fixed_tasks_without_overwriting_titles(
    pet_window, qt_app
):
    fixed_title = "保留的固定任务"
    pet_window.service.storage.set_default_task_entries(
        [DefaultTaskEntry(fixed_title, False, "未分类")]
    )
    pet_window._invalidate_fixed_cycle_candidates()
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    assert pet_window.picker_source.text() == "固定任务未启用"
    assert pet_window.enable_fixed_tasks_button.isVisible()
    assert not any(
        button.objectName() == "taskButton"
        for button in pet_window.suggestion_container.findChildren(QPushButton)
    )

    pet_window.enable_fixed_tasks_button.click()
    _flush(qt_app)

    assert not pet_window.enable_fixed_tasks_button.isVisible()
    fixed_button = next(
        button
        for button in pet_window.suggestion_container.findChildren(QPushButton)
        if button.objectName() == "taskButton"
    )
    assert fixed_title in fixed_button.text()


def test_statistics_dialog_shows_donut_and_daily_stacked_chart(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    task = storage.add_manual_task("统计界面任务", "阅读")
    now = datetime.now().astimezone().replace(microsecond=0)
    session = storage.start_focus(task, when=now - timedelta(minutes=20))
    storage.end_focus(session, FocusOutcome.COMPLETED, when=now - timedelta(minutes=5))
    dialog = StatisticsDialog(service)
    try:
        assert isinstance(dialog.today_donut, TagDonutChart)
        assert isinstance(dialog.series_chart, DailyTagStackedChart)
        dialog.show()
        _flush(qt_app)

        assert dialog.today_donut._total == pytest.approx(15 * 60, abs=2)
        assert len(dialog.series_chart._buckets) == 7
        assert dialog.week_button.isChecked()
        assert "阅读" in dialog.today_legend.text()

        dialog.month_button.click()
        _flush(qt_app)
        assert dialog._period == "month"
        assert len(dialog.series_chart._buckets) in {28, 29, 30, 31}
    finally:
        dialog.close()
        storage.close()


def test_daily_stacked_chart_hits_segments_and_locks_details(qt_app):
    chart = DailyTagStackedChart()
    start = datetime(2026, 8, 24, tzinfo=datetime.now().astimezone().tzinfo)
    chart.resize(640, 290)
    chart.set_data(
        "week",
        [
            TagTimeBucket(
                start,
                start + timedelta(days=1),
                {"写作": 3600, "编码": 1800},
            ),
            TagTimeBucket(
                start + timedelta(days=1),
                start + timedelta(days=2),
                {"阅读": 900},
            ),
        ],
    )
    chart.show()
    try:
        _flush(qt_app)
        assert len(chart._segments) == 3
        segment = chart._segments[0]
        assert segment.rect.width() > 0
        assert segment.rect.height() > 0

        point = segment.rect.center().toPoint()
        QTest.mouseMove(chart, point)
        _flush(qt_app)
        assert chart._hovered_index == 0
        assert "日期：" in chart.toolTip()
        assert f"标签：{segment.tag}" in chart.toolTip()
        assert "时长：" in chart.toolTip()
        assert "当日占比：" in chart.toolTip()

        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=point)
        _flush(qt_app)
        assert chart._locked_index == 0

        blank = QPoint(8, 8)
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=blank)
        _flush(qt_app)
        assert chart._locked_index == -1
        assert chart.toolTip() == ""
    finally:
        chart.close()


def test_minimized_picker_restores_by_clicking_cookie(pet_window, qt_app):
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)
    assert pet_window.presentation_mode.value == "picker"

    pet_window.hide_page()
    _flush(qt_app)
    assert pet_window.page_hidden is True
    assert pet_window.presentation_mode.value == "icon"

    pet_window.pet.clicked.emit()
    _flush(qt_app)

    assert pet_window.page_hidden is False
    assert pet_window.task_picker_open is True
    assert pet_window.presentation_mode.value == "picker"


def test_home_quick_add_can_assign_a_tag(pet_window, qt_app):
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    pet_window.quick_task_tag.setCurrentText("编码")
    pet_window.quick_task_input.setText("整理一个小脚本")
    pet_window._add_quick_task()
    _flush(qt_app)

    assert pet_window.service.focus is not None
    assert pet_window.service.focus.task.title == "整理一个小脚本"
    assert pet_window.service.focus.task.tag == "编码"
    assert pet_window.service.storage.list_manual_tasks()[0].tag == "编码"


def test_home_and_task_pool_use_colored_tag_chips(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    dialog = TaskManagerDialog(service)
    try:
        assert isinstance(window.quick_task_tag, TagChipBar)
        assert isinstance(dialog.manual_tag, TagChipBar)
        assert isinstance(dialog.task_tag_filter, TagChipBar)
        assert isinstance(dialog.fixed_tag, TagChipBar)
        window.quick_task_tag.setCurrentText("编码")
        assert window.quick_task_tag.currentText() == "编码"
        dialog.task_tag_filter.setCurrentText("阅读")
        assert dialog.task_tag_filter.currentText() == "阅读"
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        chips = window.quick_task_tag.findChildren(QPushButton, "tagChip")
        assert chips
        assert all(button.geometry().bottom() <= window.quick_task_tag.height() for button in chips)
        assert all(button.height() <= 24 for button in chips)
        assert dialog.content_scroll.widget().height() >= dialog.fixed_list.sizeHint().height()
    finally:
        window.timer.stop()
        window.close()
        dialog.close()
        storage.close()


def test_home_tag_picker_is_single_line_and_scrollable(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)

        bar = window.quick_task_tag
        scroll = window.quick_task_tag_scroll
        assert bar._layout.wraps() is False
        assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert len({button.geometry().y() for button in bar.findChildren(QPushButton, "tagChip")}) == 1
        assert scroll.horizontalScrollBar().maximum() > 0

        bar.set_tags(["很长的标签一", "很长的标签二", "很长的标签三", "很长的标签四"])
        _flush(qt_app)
        assert bar.width() == bar.sizeHint().width()
        assert scroll.horizontalScrollBar().maximum() > 0
        assert len({button.geometry().y() for button in bar.findChildren(QPushButton, "tagChip")}) == 1
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_compact_tag_bar_height_update_is_idempotent(qt_app):
    bar = TagChipBar(["未分类", "论文写作", "文献阅读", "Vibe coding", "摸鱼", "会议准备"])
    bar.resize(120, 40)
    bar.show()
    emissions: list[int] = []
    bar.geometry_changed.connect(lambda: emissions.append(1))
    try:
        bar.set_compact(True)
        _flush(qt_app)
        first_count = len(emissions)
        # This mirrors PetWindow._fit_to_content: repeated compact styling
        # must not reset a wrapped bar's minimum height and emit forever.
        for _ in range(8):
            bar.set_compact(True)
            bar.sync_height()
            _flush(qt_app)
        assert len(emissions) == first_count
        assert first_count <= 1
    finally:
        bar.close()


def test_legacy_tray_mode_uses_cookie_picker(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("popup_mode", "tray_only")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.show()
        window.apply_update(ServiceUpdate(show_task_picker=True, ai_turn_id="legacy-turn"))
        _flush(qt_app)
        assert window.task_picker_open is True
        assert window.presentation_mode.value == "picker"
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_header_title_and_actions_have_separate_geometry(pet_window, qt_app):
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    assert pet_window.state_label.toolTip() == pet_window._state_full_title
    action_buttons = [
        button
        for button in pet_window.header_details.findChildren(QPushButton)
        if button.text() in {"任务", "设置"}
    ]
    assert len(action_buttons) == 2
    title_rect = QRect(
        pet_window.state_label.mapToGlobal(QPoint(0, 0)),
        pet_window.state_label.size(),
    )
    for button in action_buttons:
        button_rect = QRect(button.mapToGlobal(QPoint(0, 0)), button.size())
        assert not title_rect.intersects(button_rect)


def test_home_picker_uses_compact_rows_without_shrinking_click_targets(
    pet_window, qt_app
):
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    task_buttons = [
        button
        for button in pet_window.suggestion_container.findChildren(QPushButton)
        if button.objectName() == "taskButton"
    ]
    assert task_buttons
    assert all(30 <= button.height() <= 34 for button in task_buttons)
    assert pet_window.quick_task_tag.height() <= 54
    assert pet_window.picker.layout().spacing() <= 2
    assert pet_window.header_details.height() <= 70


def test_home_header_rows_stay_tight_after_notice_mode_polish(pet_window, qt_app):
    pet_window.task_picker_open = True
    pet_window.refresh()
    _flush(qt_app)

    pet_window.task_picker_open = False
    pet_window.show_notice("notice", "body", duration=30)
    _flush(qt_app)

    action_buttons = [
        button
        for button in pet_window.header_details.findChildren(QPushButton)
        if button.objectName() == "ghostButton"
    ]
    assert len(action_buttons) == 3
    assert all(button.height() == 28 for button in action_buttons)
    assert pet_window.header_details.height() <= 70


@pytest.mark.parametrize("cookie_size", [48, 88, 160])
def test_cookie_size_controls_bubble_geometry(cookie_size, tmp_path, qt_app):
    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("cookie_size", str(cookie_size))
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.show()
        _flush(qt_app)
        assert window.pet.width() == cookie_size

        task = Task(None, "尺寸测试", service.suggested_tasks()[0].kind)
        window.start_focus(task)
        _flush(qt_app)
        assert window.bubble_card.width() >= window.focus_card.width()
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_completed_history_row_can_restart_same_task(tmp_path, qt_app):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    task = storage.add_manual_task("继续已完成的整理任务")
    now = datetime.now().astimezone().replace(microsecond=0)
    session = storage.start_focus(task, when=now - timedelta(minutes=4))
    storage.end_focus(session, FocusOutcome.COMPLETED, when=now)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        assert window.today_completed_list.count() == 1
        row = window.today_completed_list.itemWidget(window.today_completed_list.item(0))
        assert row is not None
        continue_button = row.findChild(QPushButton, "completedContinueButton")
        assert continue_button is not None
        continue_button.click()
        _flush(qt_app)
        assert service.focus is not None
        assert service.focus.task.id == task.id
        assert service.focus.task.title == task.title
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_completed_history_can_edit_end_time(tmp_path, qt_app, monkeypatch):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    task = storage.add_manual_task("修改历史结束时间")
    now = datetime.now().astimezone().replace(microsecond=0)
    original_end = now - timedelta(minutes=3)
    corrected_end = now - timedelta(minutes=8)
    session = storage.start_focus(task, when=now - timedelta(minutes=12))
    storage.finish_focus_and_task(session, FocusOutcome.COMPLETED, when=original_end)

    class FakeEndTimeDialog:
        def __init__(self, record, parent):
            self.record = record

        def exec(self):
            return QDialog.DialogCode.Accepted

        def ended_at(self):
            return corrected_end.astimezone(timezone.utc)

    monkeypatch.setattr(ui_module, "FocusEndTimeDialog", FakeEndTimeDialog)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        row = window.today_completed_list.itemWidget(window.today_completed_list.item(0))
        assert row is not None
        edit_button = row.findChild(QPushButton, "completedEditButton")
        assert edit_button is not None
        edit_button.click()
        _flush(qt_app)

        record = storage.get_completed_focus_record(session.id)
        assert record is not None
        assert record.ended_at == corrected_end.astimezone(timezone.utc)
        assert record.duration_seconds == pytest.approx(4 * 60)
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_completed_history_continue_switches_from_active_focus(tmp_path, qt_app):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    current = storage.add_manual_task("当前正在进行的任务")
    completed = storage.add_manual_task("从历史记录继续的任务")
    now = datetime.now().astimezone().replace(microsecond=0)
    session = storage.start_focus(completed, when=now - timedelta(minutes=4))
    storage.end_focus(session, FocusOutcome.COMPLETED, when=now)
    service.start_focus(current, when=now)
    service.pause_focus(when=now + timedelta(seconds=1))
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        assert window.today_completed_list.count() == 1
        row = window.today_completed_list.itemWidget(window.today_completed_list.item(0))
        assert row is not None
        continue_button = row.findChild(QPushButton, "completedContinueButton")
        assert continue_button is not None
        continue_button.click()
        _flush(qt_app)
        assert service.focus is not None
        assert service.focus.task.id == completed.id
        assert service.focus.is_paused is False
        paused = service.paused_focuses()
        assert [session.task.id for session in paused] == [current.id]
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_task_pool_search_and_undo_delete(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    storage.add_manual_task("整理图表", "论文写作")
    storage.add_manual_task("阅读方法", "文献阅读")
    dialog = TaskManagerDialog(service)
    try:
        dialog.show()
        qt_app.processEvents()
        dialog.task_search.setText("图表")
        qt_app.processEvents()
        assert dialog.list_widget.count() == 1
        assert "整理图表" in dialog.list_widget.item(0).text()
        dialog.task_search.clear()
        dialog.list_widget.setCurrentRow(0)
        dialog._delete_selected()
        assert len(storage.list_manual_tasks()) == 1
        dialog._undo_delete()
        assert len(storage.list_manual_tasks()) == 2
    finally:
        dialog.close()
        storage.close()


def test_completed_history_delete_can_be_undone_inside_cookie_notice(qt_app, tmp_path, monkeypatch):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    task = storage.add_manual_task("可撤销历史")
    now = datetime.now().astimezone().replace(microsecond=0)
    session = storage.start_focus(task, when=now - timedelta(minutes=3))
    storage.end_focus(session, FocusOutcome.COMPLETED, when=now)
    window = PetWindow(service)
    try:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        window._delete_completed_record(session.id)
        _flush(qt_app)
        assert window.today_completed_list.count() == 0
        assert window.notice_undo_button.isVisible()

        window.notice_undo_button.click()
        _flush(qt_app)
        assert window.today_completed_list.count() == 1
        assert storage.get_completed_focus_record(session.id) is not None
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_completed_history_can_be_cleared_from_settings(qt_app, tmp_path, monkeypatch):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    task = storage.add_manual_task("批量清理历史")
    now = datetime.now().astimezone().replace(microsecond=0)
    session = storage.start_focus(task, when=now - timedelta(minutes=3))
    storage.end_focus(session, FocusOutcome.COMPLETED, when=now)
    window = PetWindow(service)
    try:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )
        window.show()
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        assert not hasattr(window, "clear_history_button")
        assert window.today_completed_list.count() == 1

        window.open_settings()
        dialog = window.settings_dialog
        assert dialog is not None
        assert dialog.clear_history_button.isVisible()

        dialog.clear_history_button.click()
        _flush(qt_app)

        assert storage.today_completed_tasks() == []
        assert storage.get_completed_focus_record(session.id) is None
        assert dialog.history_status.text() == "已清空 1 条 Waiting Task 计时记录。"
        assert window.today_completed_list.count() == 0
    finally:
        if window.settings_dialog is not None:
            window.settings_dialog.close()
        window.timer.stop()
        window.close()
        storage.close()


def test_codex_completion_actions_stay_inside_cookie_bubble(qt_app, tmp_path):
    class _FakeTray:
        def __init__(self):
            self.messages = []

        def showMessage(self, *args):
            self.messages.append(args)

    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("notification_sound", "0")
    service = WaitLabService(storage)
    task = storage.add_manual_task("Codex 完成后继续的任务")
    window = PetWindow(service)
    tray = _FakeTray()
    try:
        window.show()
        window.set_tray(tray)
        window.start_focus(task)
        service.on_ai_started("thread", "turn")
        update = service.on_ai_finished("turn")
        window.apply_update(update)
        _flush(qt_app)

        assert not hasattr(window, "ai_time_label")
        assert window.notice_card.isVisible()
        assert window.notice_continue_button.isVisible()
        assert window.notice_pause_button.isVisible()
        assert window.notice_complete_button.isVisible()
        assert tray.messages == []
        window.notice_pause_button.click()
        _flush(qt_app)
        assert service.focus is not None and service.focus.is_paused
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_codex_completion_notice_is_not_repeated_in_header_or_ai_card(
    qt_app, tmp_path
):
    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("notification_sound", "0")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.show()
        window.apply_update(service.on_ai_started("thread", "turn", show_task_picker=False))
        window.apply_update(service.on_ai_finished("turn"))
        _flush(qt_app)

        assert window.notice_card.isVisible()
        assert window.notice_title_label.text()
        assert window.state_label.text() != window.notice_title_label.text()
        assert not window.message_label.isVisible()
        assert not window.ai_card.isVisible()
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_completion_reminders_are_queued_per_codex_turn(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("notification_sound", "0")
    service = WaitLabService(storage)
    task = storage.add_manual_task("连续 Codex 输出测试")
    window = PetWindow(service)
    try:
        window.show()
        window.start_focus(task)
        first = service.on_ai_started("thread", "turn-1")
        window.apply_update(first)
        window.apply_update(service.on_ai_finished("turn-1"))
        second = service.on_ai_started("thread", "turn-2")
        window.apply_update(second)
        window.apply_update(service.on_ai_finished("turn-2"))
        _flush(qt_app)

        assert window._active_completion_turn_id == "turn-1"
        assert len(window._completion_queue) == 1
        window.notice_continue_button.click()
        _flush(qt_app)
        assert window._active_completion_turn_id == "turn-2"
        assert not window._completion_queue
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_lost_desktop_source_enters_unknown_state_and_recovers(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    storage.add_manual_task("数据库失联测试")
    window = PetWindow(service)
    try:
        window.show()
        service.on_ai_started("thread", "turn-db", show_task_picker=False)
        window._desktop_turn_ids.add("turn-db")
        window.set_desktop_source_status(False, "database unavailable", tmp_path / "thread.sqlite")
        window._desktop_unavailable_since = time.monotonic() - 30
        window.refresh()
        _flush(qt_app)
        assert window._state_full_title == "Codex 状态待确认"
        assert "状态待确认" in window.ai_status_label.text()

        window.set_desktop_source_status(True, None, tmp_path / "thread.sqlite")
        window.refresh()
        _flush(qt_app)
        assert window._state_full_title == "Codex 对话进行中"
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_tag_manager_shows_usage_and_renames_tag(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    storage.add_manual_task("带标签任务", "论文写作")
    dialog = TagManagerDialog(service)
    try:
        dialog.show()
        qt_app.processEvents()
        item = next(
            (
                dialog.tag_list.item(index)
                for index in range(dialog.tag_list.count())
                if "论文写作" in dialog.tag_list.item(index).text()
            ),
            None,
        )
        assert item is not None
        assert "论文写作" in item.text()
        dialog.tag_list.setCurrentItem(item)
        dialog.tag_input.setText("自定义写作")
        dialog._rename_tag()
        assert "自定义写作" in storage.available_tags()
        assert storage.list_manual_tasks()[0].tag == "自定义写作"
    finally:
        dialog.close()
        storage.close()


def test_tag_manager_deletes_multiple_selected_tags(qt_app, tmp_path, monkeypatch):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    storage.add_tag("批量一")
    storage.add_tag("批量二")
    storage.add_manual_task("任务一", "批量一")
    storage.add_manual_task("任务二", "批量二")
    dialog = TagManagerDialog(service)
    try:
        dialog.show()
        qt_app.processEvents()
        selected = [
            dialog.tag_list.item(index)
            for index in range(dialog.tag_list.count())
            if any(
                tag in dialog.tag_list.item(index).text()
                for tag in ("批量一", "批量二")
            )
        ]
        assert len(selected) == 2
        for item in selected:
            item.setSelected(True)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )
        dialog._delete_tag()
        assert "批量一" not in storage.available_tags()
        assert "批量二" not in storage.available_tags()
        assert all(task.tag == "未分类" for task in storage.list_manual_tasks())
    finally:
        dialog.close()
        storage.close()
