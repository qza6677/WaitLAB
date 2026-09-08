from __future__ import annotations

from datetime import timedelta

import os
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from waitlab.models import local_date_key
from waitlab.service import WaitLabService
from waitlab.storage import Storage
from waitlab.ui import PetWindow


@pytest.fixture(scope="session")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def _flush(app: QApplication) -> None:
    for _ in range(4):
        app.processEvents()


def test_picker_add_button_saves_without_starting_focus(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        window.quick_task_input.setText("只添加不计时")
        window.quick_add_button.click()
        _flush(qt_app)

        assert service.focus is None
        assert any(task.title == "只添加不计时" for task in storage.list_manual_tasks())
        assert window.quick_task_error.isHidden()
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_picker_add_and_start_button_starts_the_new_task(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        window.quick_task_input.setText("添加后立即专注")
        window.quick_add_start_button.click()
        _flush(qt_app)

        assert service.focus is not None
        assert service.focus.task.title == "添加后立即专注"
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_picker_routes_task_creation_to_task_manager(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)

        assert not window.quick_compose.isVisible()
        window.picker_all_button.click()
        _flush(qt_app)

        assert window.task_dialog is not None
        assert window.task_dialog.isVisible()
        assert window.task_dialog.input is not None
    finally:
        if window.task_dialog is not None:
            window.task_dialog.close()
        window.timer.stop()
        window.close()
        storage.close()


def test_cycle_tasks_are_inline_direct_action_links(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)

        cycle_buttons = [
            button
            for button in window.cycle_choices_widget.findChildren(QPushButton)
            if button.property("cycle") is True
        ]
        assert cycle_buttons
        assert all(button.objectName() == "taskButton" for button in cycle_buttons)
        assert not window.cycle_header.findChildren(QPushButton, "pickerStartButton")

        cycle_buttons[0].click()
        _flush(qt_app)
        assert service.focus is not None
        assert service.focus.task.title in cycle_buttons[0].text()
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_picker_caps_recommendations_and_keeps_many_tasks_on_screen(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    for index in range(20):
        storage.add_manual_task(f"大量任务 {index + 1}")
    window = PetWindow(service)
    try:
        window.task_picker_open = True
        window.refresh()
        _flush(qt_app)
        task_buttons = [
            button
            for button in window.suggestion_container.findChildren(QPushButton)
            if button.objectName() == "taskButton"
        ]
        assert len(task_buttons) <= 6
        assert window.height() <= QApplication.primaryScreen().availableGeometry().height()
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_completion_notice_preempts_daily_prompt(qt_app, tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("notification_sound", "0")
    service = WaitLabService(storage)
    window = PetWindow(service)
    try:
        window.maybe_show_daily_planning_prompt()
        assert window._daily_prompt_active is True
        window.apply_update(service.on_ai_started("thread", "turn-priority"))
        window.apply_update(service.on_ai_finished("turn-priority"))
        _flush(qt_app)

        assert window.notice_title_label.text() == "Codex 已完成"
        assert window._daily_prompt_deferred is True
        assert window._active_completion_turn_id == "turn-priority"
    finally:
        window.timer.stop()
        window.close()
        storage.close()


def test_task_undo_restores_identity_and_planning_history(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    service = WaitLabService(storage)
    task = storage.add_manual_task("保留任务身份", planned_date=local_date_key())
    # Use a deterministic next day through the repository's public date path.
    from datetime import date

    target = (date.fromisoformat(local_date_key()) + timedelta(days=1)).isoformat()
    assert service.carry_manual_task(task.id, target)
    history_before = service.list_task_planning_events(task.id)
    deleted = service.delete_manual_task(task.id)
    assert deleted is not None
    restored = service.restore_manual_task(task.id)

    assert restored is not None
    assert restored.id == task.id
    assert restored.planned_date == target
    assert service.list_task_planning_events(task.id) == history_before
    storage.close()
