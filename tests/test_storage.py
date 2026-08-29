import json
from datetime import datetime, timedelta, timezone

import pytest

from waitlab.models import DEFAULT_TAG, DefaultTaskEntry, FocusOutcome, TaskKind
from waitlab.storage import DEFAULT_TASKS, LEGACY_DEFAULT_TASKS, Storage


def test_today_completed_tasks_groups_sessions_and_subtracts_pauses(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        task = storage.add_manual_task("整理一条笔记")

        first = storage.start_focus(task, when=now - timedelta(hours=2))
        first.paused_seconds = 60
        storage.end_focus(first, FocusOutcome.COMPLETED, when=now - timedelta(hours=1, minutes=30))

        second = storage.start_focus(task, when=now - timedelta(hours=1))
        storage.end_focus(second, FocusOutcome.COMPLETED, when=now - timedelta(minutes=30))

        yesterday = storage.start_focus(
            storage.add_manual_task("昨天的任务"),
            when=now - timedelta(days=1, hours=1),
        )
        storage.end_focus(yesterday, FocusOutcome.COMPLETED, when=now - timedelta(days=1))

        summaries = storage.today_completed_tasks(now)
        assert len(summaries) == 1
        assert summaries[0].title == "整理一条笔记"
        assert summaries[0].completed_count == 2
        assert summaries[0].total_seconds == pytest.approx(29 * 60 + 30 * 60)
    finally:
        storage.close()


def test_fresh_storage_uses_general_waiting_task_defaults(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        entries = storage.default_task_entries()
        assert [entry.title for entry in entries] == list(DEFAULT_TASKS)
        assert {"写作", "阅读", "编码", "整理", "工作/项目", "未分类"}.issubset(
            set(storage.available_tags())
        )
    finally:
        storage.close()


def test_legacy_builtin_defaults_are_migrated_without_dropping_legacy_tags(tmp_path):
    path = tmp_path / "waitlab.db"
    storage = Storage(path)
    try:
        legacy_entries = [
            {
                "title": title,
                "enabled": True,
                "tag": tag,
            }
            for title, tag in zip(
                LEGACY_DEFAULT_TASKS,
                ("文献阅读", "论文写作", "论文写作", "文献阅读", "论文写作", "Vibe coding", "论文写作"),
            )
        ]
        storage.set_setting("default_tasks_v2", json.dumps(legacy_entries, ensure_ascii=False))
        storage.set_setting("task_tags", json.dumps(["论文写作", "文献阅读", "Vibe coding", "未分类"], ensure_ascii=False))
        storage.set_setting("default_content_version", "")
    finally:
        storage.close()

    migrated = Storage(path)
    try:
        assert [entry.title for entry in migrated.default_task_entries()] == list(DEFAULT_TASKS)
        assert {"写作", "阅读", "编码", "整理", "工作/项目"}.issubset(
            set(migrated.available_tags())
        )
        assert {"论文写作", "文献阅读", "Vibe coding"}.issubset(
            set(migrated.available_tags())
        )
        assert migrated.get_setting("default_content_version") == "2"
    finally:
        migrated.close()


def test_custom_fixed_tasks_are_preserved_during_default_content_migration(tmp_path):
    path = tmp_path / "waitlab.db"
    storage = Storage(path)
    try:
        storage.set_default_task_entries([DefaultTaskEntry("我的固定任务", False, "我的标签")])
        storage.set_setting("default_content_version", "")
    finally:
        storage.close()

    migrated = Storage(path)
    try:
        assert migrated.default_task_entries() == [DefaultTaskEntry("我的固定任务", False, "我的标签")]
    finally:
        migrated.close()


def test_tags_are_persisted_and_completed_segments_can_be_deleted(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        task = storage.add_manual_task("写作任务", "论文写作")
        session = storage.start_focus(task, when=now - timedelta(minutes=5))
        storage.end_focus(session, FocusOutcome.COMPLETED, when=now)
        summary = storage.today_completed_tasks(now)[0]
        assert task.tag == "论文写作"
        assert summary.tag == "论文写作"
        records = storage.completed_focus_records(summary.task_id, summary.title, TaskKind.MANUAL, now=now)
        assert len(records) == 1
        storage.delete_focus_session(records[0].id)
        assert storage.today_completed_tasks(now) == []
    finally:
        storage.close()


def test_tags_can_be_added_renamed_and_deleted_with_reassignment(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        storage.add_tag("实验分析")
        storage.add_manual_task("分析结果", "实验分析")
        storage.set_default_task_entries(
            [DefaultTaskEntry("固定分析", True, "实验分析")]
        )
        assert "实验分析" in storage.available_tags()

        storage.rename_tag("实验分析", "结果分析")
        assert "实验分析" not in storage.available_tags()
        assert "结果分析" in storage.available_tags()
        assert storage.list_manual_tasks()[0].tag == "结果分析"
        assert storage.default_task_entries()[0].tag == "结果分析"

        storage.delete_tag("结果分析")
        assert "结果分析" not in storage.available_tags()
        assert storage.list_manual_tasks()[0].tag == "未分类"
        assert storage.default_task_entries()[0].tag == "未分类"
    finally:
        storage.close()


def test_multiple_tags_can_be_deleted_atomically_with_reassignment(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        storage.add_tag("标签一")
        storage.add_tag("标签二")
        storage.add_manual_task("任务一", "标签一")
        storage.add_manual_task("任务二", "标签二")
        storage.set_default_task_entries(
            [
                DefaultTaskEntry("固定一", True, "标签一"),
                DefaultTaskEntry("固定二", True, "标签二"),
            ]
        )

        assert storage.delete_tags(["标签一", "标签二", "标签一"]) == 2
        assert "标签一" not in storage.available_tags()
        assert "标签二" not in storage.available_tags()
        assert all(task.tag == DEFAULT_TAG for task in storage.list_manual_tasks())
        assert all(entry.tag == DEFAULT_TAG for entry in storage.default_task_entries())
    finally:
        storage.close()


def test_period_stats_include_codex_and_waiting_task_time(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        task = storage.add_manual_task("统计任务", "Vibe coding")
        focus = storage.start_focus(task, when=now - timedelta(minutes=10))
        storage.end_focus(focus, FocusOutcome.COMPLETED, when=now - timedelta(minutes=5))
        storage.start_ai_session("s", "t", when=now - timedelta(minutes=4))
        storage.finish_ai_session("t", when=now)
        assert storage.waiting_seconds("day", now) == pytest.approx(5 * 60, abs=2)
        assert storage.codex_active_seconds("day", now) == pytest.approx(4 * 60, abs=2)
        assert storage.tag_waiting_seconds("day", now)["Vibe coding"] == pytest.approx(5 * 60, abs=2)
    finally:
        storage.close()


def test_waiting_stats_handle_cross_midnight_and_open_session(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        local_now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        day_start = local_now.replace(hour=0)
        task = storage.add_manual_task("跨午夜任务", "文献阅读")
        session = storage.start_focus(task, when=day_start - timedelta(minutes=30))
        storage.end_focus(session, FocusOutcome.COMPLETED, when=day_start + timedelta(minutes=30))
        assert storage.waiting_seconds("day", local_now) == pytest.approx(30 * 60, abs=2)

        open_task = storage.add_manual_task("进行中任务", "论文写作")
        storage.start_focus(open_task, when=local_now - timedelta(minutes=10))
        assert storage.today_focus_seconds(local_now) == pytest.approx(40 * 60, abs=2)
    finally:
        storage.close()


def test_codex_duration_writes_can_be_disabled_for_application_storage(tmp_path):
    storage = Storage(tmp_path / "waitlab.db", track_ai_time=False)
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        storage.start_ai_session("thread", "turn", when=now - timedelta(minutes=4))
        storage.set_ai_status("turn", "needs_attention", when=now - timedelta(minutes=2))
        storage.finish_ai_session("turn", when=now)

        row = storage._connection.execute(
            "SELECT active_seconds, running_since FROM ai_sessions WHERE turn_id = ?",
            ("turn",),
        ).fetchone()
        segments = storage._connection.execute(
            "SELECT COUNT(*) AS count FROM ai_activity_segments"
        ).fetchone()
        assert row["active_seconds"] == 0
        assert row["running_since"] is None
        assert segments["count"] == 0
    finally:
        storage.close()


def test_completed_focus_history_can_be_archived_and_restored_atomically(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        task = storage.add_manual_task("可撤销的计时记录", "阅读")
        session = storage.start_focus(task, when=now - timedelta(minutes=10))
        session.paused_at = now - timedelta(minutes=5)
        session.last_heartbeat_at = session.paused_at
        storage.save_focus_pause(session)
        storage.end_focus(session, FocusOutcome.COMPLETED, when=now)

        record = storage.get_completed_focus_record(session.id)
        assert record is not None
        assert record.duration_seconds == pytest.approx(5 * 60)
        assert storage.archive_focus_session(session.id) is True
        assert storage.get_completed_focus_record(session.id) is None
        assert storage.today_completed_tasks(now) == []

        assert storage.restore_archived_focus_session(session.id) is True
        restored = storage.get_completed_focus_record(session.id)
        assert restored is not None
        assert restored.duration_seconds == pytest.approx(record.duration_seconds)
        assert storage.restore_archived_focus_session(session.id) is False
    finally:
        storage.close()


def test_expired_history_archives_are_purged_with_segments(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        task = storage.add_manual_task("过期归档记录")
        session = storage.start_focus(task, when=now - timedelta(minutes=2))
        storage.end_focus(session, FocusOutcome.COMPLETED, when=now)
        assert storage.archive_focus_session(session.id) is True
        storage._connection.execute(
            "UPDATE deleted_focus_sessions SET deleted_at = ? WHERE id = ?",
            ((now - timedelta(days=10)).isoformat(), session.id),
        )
        storage._connection.commit()

        assert storage.purge_archived_focus_sessions(max_age_days=7) == 1
        assert storage._connection.execute(
            "SELECT 1 FROM deleted_focus_sessions WHERE id = ?", (session.id,)
        ).fetchone() is None
        assert storage._connection.execute(
            "SELECT 1 FROM deleted_focus_segments WHERE focus_session_id = ?", (session.id,)
        ).fetchone() is None
    finally:
        storage.close()

