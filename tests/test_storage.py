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


def test_completed_focus_end_time_shortens_segments_and_updates_task(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        started = now - timedelta(minutes=90)
        original_end = now - timedelta(minutes=10)
        corrected_end = now - timedelta(minutes=25)
        task = storage.add_manual_task("可修正结束时间")
        session = storage.start_focus(task, when=started)

        session.paused_at = started + timedelta(minutes=20)
        session.last_heartbeat_at = session.paused_at
        storage.save_focus_pause(session)
        session.paused_at = None
        session.last_heartbeat_at = started + timedelta(minutes=50)
        storage.save_focus_pause(session)
        storage.finish_focus_and_task(session, FocusOutcome.COMPLETED, when=original_end)

        assert storage.update_completed_focus_end_time(session.id, corrected_end) is True
        record = storage.get_completed_focus_record(session.id)
        assert record is not None
        assert record.ended_at == corrected_end
        assert record.duration_seconds == pytest.approx(35 * 60)

        row = storage._connection.execute(
            "SELECT ended_at, paused_seconds FROM focus_sessions WHERE id = ?",
            (session.id,),
        ).fetchone()
        assert row["ended_at"] == corrected_end.isoformat()
        assert row["paused_seconds"] == pytest.approx(30 * 60)
        segments = storage._connection.execute(
            """
            SELECT started_at, ended_at FROM focus_segments
            WHERE focus_session_id = ? ORDER BY id
            """,
            (session.id,),
        ).fetchall()
        assert len(segments) == 2
        assert segments[0]["ended_at"] == (started + timedelta(minutes=20)).isoformat()
        assert segments[1]["ended_at"] == corrected_end.isoformat()
        task_row = storage._connection.execute(
            "SELECT completed_at FROM tasks WHERE id = ?", (task.id,)
        ).fetchone()
        assert task_row["completed_at"] == corrected_end.isoformat()
    finally:
        storage.close()


def test_completed_focus_end_time_rejects_invalid_changes_without_mutating(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        started = now - timedelta(minutes=10)
        original_end = now - timedelta(minutes=5)
        task = storage.add_manual_task("结束时间校验")
        session = storage.start_focus(task, when=started)
        storage.finish_focus_and_task(session, FocusOutcome.COMPLETED, when=original_end)

        with pytest.raises(ValueError, match="早于开始"):
            storage.update_completed_focus_end_time(session.id, started - timedelta(seconds=1))
        with pytest.raises(ValueError, match="晚于当前"):
            storage.update_completed_focus_end_time(session.id, now + timedelta(minutes=1))
        with pytest.raises(ValueError, match="只能向前"):
            storage.update_completed_focus_end_time(session.id, original_end + timedelta(seconds=1))

        unchanged = storage.get_completed_focus_record(session.id)
        assert unchanged is not None
        assert unchanged.ended_at == original_end

        open_task = storage.add_manual_task("仍在计时")
        open_session = storage.start_focus(open_task, when=started)
        assert storage.update_completed_focus_end_time(open_session.id, original_end) is False
    finally:
        storage.close()


def test_completed_focus_end_time_can_move_record_out_of_today(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now().astimezone().replace(
            hour=0,
            minute=30,
            second=0,
            microsecond=0,
        )
        started = now - timedelta(minutes=90)
        original_end = now - timedelta(minutes=10)
        corrected_end = now - timedelta(minutes=45)
        task = storage.add_manual_task("跨日期修正")
        session = storage.start_focus(task, when=started)
        storage.finish_focus_and_task(session, FocusOutcome.COMPLETED, when=original_end)
        assert len(storage.today_completed_tasks(now)) == 1

        assert storage.update_completed_focus_end_time(session.id, corrected_end) is True
        assert storage.today_completed_tasks(now) == []
        assert storage.waiting_seconds("day", now) == pytest.approx(0)
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


def test_daily_tag_series_splits_sessions_and_matches_period_totals(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        local_now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).astimezone()
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        monday = day_start - timedelta(days=2)
        reading = storage.add_manual_task("周一阅读", "文献阅读")
        session = storage.start_focus(reading, when=monday.replace(hour=10))
        storage.end_focus(
            session,
            FocusOutcome.COMPLETED,
            when=monday.replace(hour=11),
        )

        cross_midnight = storage.add_manual_task("跨日写作", "论文写作")
        session = storage.start_focus(
            cross_midnight,
            when=(day_start - timedelta(days=1)).replace(hour=23, minute=30),
        )
        storage.end_focus(
            session,
            FocusOutcome.COMPLETED,
            when=day_start.replace(minute=30),
        )

        series = storage.tag_waiting_daily_series("week", local_now)
        assert len(series) == 7
        assert series[0].tag_seconds["文献阅读"] == pytest.approx(60 * 60)
        assert series[1].tag_seconds["论文写作"] == pytest.approx(30 * 60)
        assert series[2].tag_seconds["论文写作"] == pytest.approx(30 * 60)

        weekly_totals = storage.tag_waiting_seconds("week", local_now)
        series_totals: dict[str, float] = {}
        for bucket in series:
            for tag, seconds in bucket.tag_seconds.items():
                series_totals[tag] = series_totals.get(tag, 0.0) + seconds
        assert series_totals == pytest.approx(weekly_totals)
    finally:
        storage.close()


def test_daily_tag_series_returns_every_day_of_month(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        local_now = datetime(2026, 2, 12, 12, 0, tzinfo=timezone.utc).astimezone()
        series = storage.tag_waiting_daily_series("month", local_now)
        assert len(series) == 28
        assert series[0].start.day == 1
        assert series[-1].start.day == 28
        assert all(bucket.tag_seconds == {} for bucket in series)
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


def test_clear_focus_history_removes_terminal_records_but_keeps_open_focus(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)

        completed_task = storage.add_manual_task("已完成记录")
        completed = storage.start_focus(completed_task, when=now - timedelta(minutes=8))
        storage.end_focus(completed, FocusOutcome.COMPLETED, when=now - timedelta(minutes=7))

        abandoned_task = storage.add_manual_task("已取消记录")
        abandoned = storage.start_focus(abandoned_task, when=now - timedelta(minutes=6))
        storage.end_focus(abandoned, FocusOutcome.ABANDONED, when=now - timedelta(minutes=5))

        archived_task = storage.add_manual_task("已归档记录")
        archived = storage.start_focus(archived_task, when=now - timedelta(minutes=4))
        storage.end_focus(archived, FocusOutcome.COMPLETED, when=now - timedelta(minutes=3))
        assert storage.archive_focus_session(archived.id) is True

        paused_task = storage.add_manual_task("保留的暂停任务")
        paused = storage.start_focus(paused_task, when=now - timedelta(minutes=2))
        paused.paused_at = now - timedelta(minutes=1)
        paused.last_heartbeat_at = paused.paused_at
        storage.save_focus_pause(paused)

        running_task = storage.add_manual_task("保留的进行中任务")
        running = storage.start_focus(running_task, when=now - timedelta(seconds=30))

        assert storage.clear_focus_history() == 3
        assert storage.today_completed_tasks(now) == []
        assert storage.get_completed_focus_record(completed.id) is None
        assert storage.get_completed_focus_record(archived.id) is None
        assert storage.get_running_focus() is not None
        assert {session.id for session in storage.list_open_focuses()} == {paused.id, running.id}
        assert storage._connection.execute(
            "SELECT COUNT(*) FROM deleted_focus_sessions"
        ).fetchone()[0] == 0
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

def test_ai_lifecycle_rows_are_retained_with_age_and_count_limits(tmp_path):
    storage = Storage(tmp_path / "waitlab.db", track_ai_time=False)
    try:
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        storage.start_ai_session("thread", "old", when=now - timedelta(days=40))
        storage.finish_ai_session("old", when=now - timedelta(days=40) + timedelta(minutes=1))
        for index in range(3):
            turn_id = f"recent-{index}"
            storage.start_ai_session("thread", turn_id, when=now - timedelta(days=index))
            storage.finish_ai_session(turn_id, when=now - timedelta(days=index) + timedelta(minutes=1))

        assert storage.purge_ai_sessions(max_age_days=30, max_rows=2, now=now) == 2
        rows = storage._connection.execute(
            "SELECT turn_id FROM ai_sessions ORDER BY ended_at DESC"
        ).fetchall()
        assert [row["turn_id"] for row in rows] == ["recent-0", "recent-1"]
    finally:
        storage.close()
