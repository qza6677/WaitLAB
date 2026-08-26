import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from waitlab.models import DefaultTaskEntry, TaskKind
from waitlab.service import WaitLabService
from waitlab.storage import DEFAULT_TASKS, Storage


@pytest.fixture()
def service(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    instance = WaitLabService(storage)
    yield instance
    storage.close()


def moment(minutes: int = 0) -> datetime:
    return datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def test_manual_tasks_completely_replace_default_suggestions(service):
    defaults = service.suggested_tasks()
    assert [task.kind for task in defaults] == [TaskKind.DEFAULT] * 3

    service.storage.add_manual_task("修改 Discussion 第二段")
    suggestions = service.suggested_tasks()

    assert [task.title for task in suggestions] == ["修改 Discussion 第二段"]
    assert all(task.kind is TaskKind.MANUAL for task in suggestions)


def test_default_tasks_rotate_after_completion(service):
    first = service.suggested_tasks()[0]
    service.start_focus(first, when=moment())
    service.complete_focus(when=moment(2))

    assert service.suggested_tasks()[0].title == DEFAULT_TASKS[1]


def test_selected_default_task_moves_to_back_of_queue(service):
    second = service.suggested_tasks()[1]
    service.start_focus(second, when=moment())
    service.complete_focus(when=moment(2))

    titles = [task.title for task in service.suggested_tasks()]
    assert titles[0] == DEFAULT_TASKS[0]
    assert DEFAULT_TASKS[1] not in titles


def test_fixed_tasks_can_be_customized_reordered_and_disabled(service):
    service.storage.set_default_task_entries(
        [
            DefaultTaskEntry("任务 C", True),
            DefaultTaskEntry("任务 A", False),
            DefaultTaskEntry("任务 B", True),
        ]
    )

    suggestions = service.suggested_tasks()

    assert [task.title for task in suggestions] == ["任务 C", "任务 B"]
    service.start_focus(suggestions[0], when=moment())
    service.complete_focus(when=moment(1))
    assert [task.title for task in service.suggested_tasks()] == ["任务 B", "任务 C"]


def test_all_fixed_tasks_may_be_disabled(service):
    service.storage.set_default_task_entries(
        [DefaultTaskEntry("不在本轮显示", False)]
    )

    assert service.suggested_tasks() == []


def test_ai_completion_does_not_stop_focus_timer(service):
    task = service.storage.add_manual_task("核对图注")
    service.on_ai_started("thread-1", "turn-1", when=moment())
    service.start_focus(task, when=moment(1))

    update = service.on_ai_finished("turn-1", when=moment(3))

    assert update.ai_completed is True
    assert service.focus is not None
    assert service.focus.elapsed_seconds(moment(5)) == pytest.approx(240)


def test_permission_wait_and_resume_do_not_interrupt_focus_timer(service):
    task = service.storage.add_manual_task("检查实验输出")
    service.on_ai_started("thread-1", "turn-1", when=moment())
    service.start_focus(task, when=moment(1))

    attention = service.on_ai_needs_attention("thread-1", "turn-1", when=moment(2))
    waiting_session = service.storage.get_open_ai("turn-1")
    resumed = service.on_ai_resumed("turn-1")
    running_session = service.storage.get_open_ai("turn-1")

    assert attention.ai_needs_attention is True
    assert waiting_session is not None and waiting_session.status == "needs_attention"
    assert resumed.ai_resumed is True
    assert running_session is not None and running_session.status == "running"
    assert service.focus is not None
    assert service.focus.elapsed_seconds(moment(5)) == pytest.approx(240)


def test_new_ai_turn_does_not_open_picker_during_an_existing_focus(service):
    task = service.storage.add_manual_task("继续当前微任务")
    service.start_focus(task, when=moment())

    update = service.on_ai_started("thread-1", "turn-1", when=moment(1))

    assert update.show_task_picker is False
    assert service.focus is not None


def test_unrelated_post_tool_use_does_not_emit_resume(service):
    service.on_ai_started("thread-1", "turn-1", when=moment())

    update = service.on_ai_resumed("turn-1")

    assert update.ai_resumed is False


def test_desktop_attention_does_not_modify_an_unrelated_turn(service):
    service.on_ai_started("thread-1", "turn-1", when=moment())

    update = service.on_ai_needs_attention(
        "thread-2",
        "turn-2",
        when=moment(1),
        fallback_latest=False,
    )

    first = service.storage.get_open_ai("turn-1")
    second = service.storage.get_open_ai("turn-2")
    assert update.ai_needs_attention is True
    assert first is not None and first.status == "running"
    assert second is not None and second.status == "needs_attention"


def test_ai_completion_without_focus_uses_plain_message(service):
    service.on_ai_started("thread-1", "turn-1", when=moment())

    update = service.on_ai_finished("turn-1", when=moment(1))

    assert update.message == "Codex 已完成"


def test_skipping_ai_round_survives_repeated_start_event(service):
    first = service.on_ai_started("thread-1", "turn-1", when=moment())
    skipped = service.skip_current_ai_round()
    repeated = service.on_ai_started("thread-1", "turn-1", when=moment(1))

    assert first.show_task_picker is True
    assert "本轮已跳过" in (skipped.message or "")
    assert repeated.show_task_picker is False
    session = service.storage.get_open_ai("turn-1")
    assert session is not None and session.picker_skipped is True


def test_exact_desktop_completion_does_not_finish_an_unrelated_turn(service):
    service.on_ai_started("thread-1", "turn-1", when=moment())

    update = service.on_ai_finished(
        "turn-missing",
        when=moment(1),
        fallback_latest=False,
    )

    assert update.ai_completed is False
    assert service.storage.get_open_ai("turn-1") is not None


def test_discovered_fast_completion_is_created_once_without_a_picker(service):
    first = service.on_ai_finished(
        "turn-fast",
        when=moment(1),
        status="completed",
        fallback_latest=False,
        session_id="thread-fast",
        started_at=moment(),
        create_if_missing=True,
    )
    duplicate = service.on_ai_finished(
        "turn-fast",
        when=moment(1),
        status="completed",
        fallback_latest=False,
        session_id="thread-fast",
        started_at=moment(),
        create_if_missing=True,
    )

    assert first.ai_completed is True
    assert first.show_task_picker is False
    assert duplicate.ai_completed is False
    assert service.last_ai_completion_seconds == pytest.approx(60)


def test_failed_ai_turn_reports_blocked_and_keeps_focus_running(service):
    task = service.storage.add_manual_task("整理失败日志")
    service.on_ai_started("thread-1", "turn-1", when=moment())
    service.start_focus(task, when=moment(1))

    update = service.on_ai_finished(
        "turn-1",
        when=moment(2),
        status="failed",
        fallback_latest=False,
    )

    assert update.ai_completed is False
    assert update.ai_blocked is True
    assert service.focus is not None
    assert service.focus.elapsed_seconds(moment(3)) == pytest.approx(120)


def test_focus_pause_only_changes_focus_clock(service):
    task = service.storage.add_manual_task("补实验日志")
    service.on_ai_started("thread-1", "turn-1", when=moment())
    service.start_focus(task, when=moment(1))
    service.toggle_focus_pause(when=moment(2))
    service.on_ai_finished("turn-1", when=moment(3))

    assert service.focus is not None
    assert service.focus.is_paused is True
    assert service.focus.elapsed_seconds(moment(5)) == pytest.approx(60)


def test_manual_task_is_removed_after_focus_completion(service):
    task = service.storage.add_manual_task("补充方法部分")
    service.start_focus(task, when=moment())
    service.complete_focus(when=moment(3))

    assert service.storage.list_manual_tasks() == []
    assert service.suggested_tasks()[0].kind is TaskKind.DEFAULT


def test_open_focus_is_restored_from_database(tmp_path):
    path = tmp_path / "waitlab.db"
    first_storage = Storage(path)
    first_service = WaitLabService(first_storage)
    task = first_storage.add_manual_task("恢复测试")
    first_service.start_focus(task, when=moment())
    first_service.toggle_focus_pause(when=moment(1))
    first_storage.close()

    second_storage = Storage(path)
    second_service = WaitLabService(second_storage)

    assert second_service.focus is not None
    assert second_service.focus.task.title == "恢复测试"
    assert second_service.focus.is_paused is True
    second_storage.close()


def test_unclean_restart_pauses_at_last_heartbeat(tmp_path):
    path = tmp_path / "waitlab.db"
    first_storage = Storage(path)
    first_service = WaitLabService(first_storage)
    task = first_storage.add_manual_task("崩溃恢复测试")
    first_service.start_focus(task, when=moment())
    first_service.heartbeat(when=moment(5))
    first_storage.close()

    second_storage = Storage(path)
    second_service = WaitLabService(second_storage)

    assert second_service.has_recovered_focus is True
    assert second_service.focus is not None
    assert second_service.focus.is_paused is True
    assert second_service.focus.elapsed_seconds(moment(30)) == pytest.approx(300)
    second_storage.close()


def test_recovered_focus_only_counts_time_after_explicit_resume(tmp_path):
    path = tmp_path / "waitlab.db"
    first_storage = Storage(path)
    first_service = WaitLabService(first_storage)
    task = first_storage.add_manual_task("恢复后继续")
    first_service.start_focus(task, when=moment())
    first_service.heartbeat(when=moment(5))
    first_storage.close()

    second_storage = Storage(path)
    second_service = WaitLabService(second_storage)
    second_service.resume_focus(when=moment(20))

    assert second_service.focus is not None
    assert second_service.focus.elapsed_seconds(moment(22)) == pytest.approx(420)
    second_storage.close()


def test_clean_shutdown_pause_excludes_offline_time(service):
    task = service.storage.add_manual_task("退出暂停")
    service.start_focus(task, when=moment())
    service.pause_focus(when=moment(3), message="退出时已暂停")

    assert service.focus is not None
    assert service.focus.elapsed_seconds(moment(30)) == pytest.approx(180)


def test_existing_database_is_migrated_with_heartbeat_column(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE focus_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            task_title TEXT NOT NULL,
            task_kind TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            paused_seconds REAL NOT NULL DEFAULT 0,
            paused_at TEXT,
            outcome TEXT
        )
        """
    )
    connection.commit()
    connection.close()

    storage = Storage(path)
    columns = {
        row["name"]
        for row in storage._connection.execute("PRAGMA table_info(focus_sessions)").fetchall()
    }

    assert "last_heartbeat_at" in columns
    storage.close()


def test_existing_database_is_migrated_with_picker_skipped_column(tmp_path):
    path = tmp_path / "legacy-ai.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE ai_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'running'
        )
        """
    )
    connection.commit()
    connection.close()

    storage = Storage(path)
    columns = {
        row["name"]
        for row in storage._connection.execute("PRAGMA table_info(ai_sessions)").fetchall()
    }

    assert "picker_skipped" in columns
    storage.close()
