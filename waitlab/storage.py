from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import (
    AiSession,
    CompletedFocusRecord,
    DefaultTaskEntry,
    CompletedTaskSummary,
    DEFAULT_TAG,
    FocusOutcome,
    FocusSession,
    TagTimeBucket,
    Task,
    TaskKind,
)
from .storage_schema import create_base_schema


from .storage_defaults import (
    DEFAULT_CONTENT_VERSION,
    DEFAULT_TAGS,
    DEFAULT_TASKS as DEFAULT_TASKS,
    DEFAULT_TASK_TAGS as DEFAULT_TASK_TAGS,
    LEGACY_DEFAULT_TAGS as LEGACY_DEFAULT_TAGS,
    LEGACY_DEFAULT_TASKS,
    LEGACY_DEFAULT_TASK_TAGS,
)
from .storage_stats import StatsRepository
from .storage_tasks import TaskRepository
from .storage_focus import FocusRepository
from .storage_ai import AiRepository


class Storage:
    def __init__(self, path: str | Path, *, track_ai_time: bool = True) -> None:
        self.path = Path(path)
        # Legacy installations keep these columns for compatibility. New
        # application instances can disable Codex duration writes entirely.
        self.track_ai_time = bool(track_ai_time)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        # Avoid surfacing transient reader/backup locks as UI errors while
        # keeping local database operations responsive.
        self._connection.execute("PRAGMA busy_timeout = 1000")
        self._tasks = TaskRepository(
            self._connection,
            self.get_setting,
            self._set_setting_uncommitted,
            self.normalize_tag,
        )
        self._focus = FocusRepository(
            self._connection,
            self._complete_manual_task_uncommitted,
            self.default_task_entries,
            self._set_default_task_entries_uncommitted,
            self.normalize_tag,
        )
        self._ai = AiRepository(self._connection, self.track_ai_time)
        self._migrate()
        self._stats = StatsRepository(self._connection, self.normalize_tag)

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        create_base_schema(self._connection)
        focus_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(focus_sessions)").fetchall()
        }
        if "last_heartbeat_at" not in focus_columns:
            self._connection.execute(
                "ALTER TABLE focus_sessions ADD COLUMN last_heartbeat_at TEXT"
            )
        if "task_tag" not in focus_columns:
            self._connection.execute(
                "ALTER TABLE focus_sessions ADD COLUMN task_tag TEXT NOT NULL DEFAULT '未分类'"
            )
        # Compatibility ALTERs above must run before this index is created.
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_focus_sessions_task_end
            ON focus_sessions(task_id, task_kind, task_title, task_tag, ended_at)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_focus_sessions_time
            ON focus_sessions(started_at, ended_at, outcome)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ai_activity_segments_state_time
            ON ai_activity_segments(state, started_at, ended_at)
            """
        )
        task_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "tag" not in task_columns:
            self._connection.execute(
                "ALTER TABLE tasks ADD COLUMN tag TEXT NOT NULL DEFAULT '未分类'"
            )
        ai_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(ai_sessions)").fetchall()
        }
        if "picker_skipped" not in ai_columns:
            self._connection.execute(
                "ALTER TABLE ai_sessions ADD COLUMN picker_skipped INTEGER NOT NULL DEFAULT 0"
            )
        if "active_seconds" not in ai_columns:
            self._connection.execute(
                "ALTER TABLE ai_sessions ADD COLUMN active_seconds REAL NOT NULL DEFAULT 0"
            )
        if "running_since" not in ai_columns:
            self._connection.execute(
                "ALTER TABLE ai_sessions ADD COLUMN running_since TEXT"
            )
        # Backfill records created before active-time accounting existed.
        # Completed rows have an exact wall-clock duration; open rows only
        # resume from their original start when they were still running.
        self._connection.execute(
            """
            UPDATE ai_sessions
            SET active_seconds = MAX(
                    0,
                    (julianday(ended_at) - julianday(started_at)) * 86400
                )
            WHERE ended_at IS NOT NULL AND active_seconds = 0
            """
        )
        self._connection.execute(
            """
            UPDATE ai_sessions
            SET running_since = started_at
            WHERE ended_at IS NULL
              AND lower(status) IN ('running', 'inprogress')
              AND running_since IS NULL
            """
        )
        self._migrate_default_content()
        self._connection.execute("PRAGMA user_version = 3")
        self._connection.commit()

    def _migrate_default_content(self) -> None:
        """Broaden built-in content without overwriting user customisation."""

        if self.get_setting("default_content_version", "") == DEFAULT_CONTENT_VERSION:
            return

        raw_tags = self.get_setting("task_tags", "")
        if raw_tags:
            try:
                stored_tags = json.loads(raw_tags)
            except json.JSONDecodeError:
                stored_tags = None
            if isinstance(stored_tags, list):
                tags = [
                    self.normalize_tag(value)
                    for value in stored_tags
                    if str(value).strip()
                ]
                merged_tags = list(DEFAULT_TAGS)
                merged_tags.extend(tag for tag in tags if tag not in merged_tags)
                self._save_available_tags_uncommitted(merged_tags)

        raw_entries = self.get_setting("default_tasks_v2", "")
        entries = self._parse_default_task_entries(raw_entries) if raw_entries else []
        if entries and self._is_legacy_default_entries(entries):
            self._set_default_task_entries_uncommitted(
                self._map_legacy_default_entries(entries)
            )
        elif not entries:
            raw_order = self.get_setting("default_task_order", "")
            try:
                stored_order = json.loads(raw_order) if raw_order else []
            except json.JSONDecodeError:
                stored_order = []
            if isinstance(stored_order, list):
                titles = [
                    " ".join(str(value or "").strip().split())
                    for value in stored_order
                    if str(value or "").strip()
                ]
                if set(titles) == set(LEGACY_DEFAULT_TASKS) and len(titles) == len(LEGACY_DEFAULT_TASKS):
                    legacy_entries = [
                        DefaultTaskEntry(title, True, LEGACY_DEFAULT_TASK_TAGS[title])
                        for title in titles
                    ]
                    self._set_default_task_entries_uncommitted(
                        self._map_legacy_default_entries(legacy_entries)
                    )
                elif titles:
                    # Very old versions only stored titles. Preserve any
                    # custom entries rather than dropping them during upgrade.
                    self._set_default_task_entries_uncommitted(
                        [DefaultTaskEntry(title, True, DEFAULT_TAG) for title in titles]
                    )

        self._set_setting_uncommitted(
            "default_content_version",
            DEFAULT_CONTENT_VERSION,
        )

    @staticmethod
    def _is_legacy_default_entries(entries: list[DefaultTaskEntry]) -> bool:
        return TaskRepository._is_legacy_default_entries(entries)


    @staticmethod
    def _map_legacy_default_entries(
        entries: list[DefaultTaskEntry],
    ) -> list[DefaultTaskEntry]:
        return TaskRepository._map_legacy_default_entries(entries)


    def add_manual_task(self, title: str, tag: str = DEFAULT_TAG) -> Task:
        return self._tasks.add_manual_task(title, tag)


    def list_manual_tasks(self) -> list[Task]:
        return self._tasks.list_manual_tasks()


    @staticmethod
    def normalize_tag(tag: str | None) -> str:
        clean = " ".join(str(tag or "").strip().split())
        return clean or DEFAULT_TAG

    def available_tags(self) -> list[str]:
        return self._tasks.available_tags()


    def _save_available_tags_uncommitted(self, tags: list[str]) -> None:
        self._tasks._save_available_tags_uncommitted(tags)


    def add_tag(self, tag: str) -> str:
        return self._tasks.add_tag(tag)


    def rename_tag(self, old_tag: str, new_tag: str) -> str:
        return self._tasks.rename_tag(old_tag, new_tag)


    def delete_tag(self, tag: str) -> None:
        self._tasks.delete_tag(tag)


    def delete_tags(self, tags: list[str]) -> int:
        return self._tasks.delete_tags(tags)


    def tag_usage_counts(self) -> dict[str, int]:
        return self._tasks.tag_usage_counts()


    def complete_manual_task(self, task_id: int, when: datetime | None = None) -> None:
        self._tasks.complete_manual_task(task_id, when)


    def _complete_manual_task_uncommitted(self, task_id: int, when: datetime) -> None:
        self._tasks._complete_manual_task_uncommitted(task_id, when)


    def delete_manual_task(self, task_id: int) -> Task | None:
        return self._tasks.delete_manual_task(task_id)


    def suggested_tasks(self, limit: int = 3) -> list[Task]:
        return self._tasks.suggested_tasks(limit)


    def advance_default_task(self, selected_title: str | None = None) -> None:
        self._tasks.advance_default_task(selected_title)


    def default_task_entries(self) -> list[DefaultTaskEntry]:
        return self._tasks.default_task_entries()


    def _parse_default_task_entries(self, raw: str) -> list[DefaultTaskEntry]:
        return self._tasks._parse_default_task_entries(raw)


    def set_default_task_entries(self, entries: list[DefaultTaskEntry]) -> None:
        self._tasks.set_default_task_entries(entries)


    def _set_default_task_entries_uncommitted(self, entries: list[DefaultTaskEntry]) -> None:
        self._tasks._set_default_task_entries_uncommitted(entries)


    def _default_task_order(self) -> list[str]:
        return self._tasks._default_task_order()


    def start_focus(self, task: Task, when: datetime | None = None) -> FocusSession:
        return self._focus.start_focus(task, when)


    def get_open_focus(self) -> FocusSession | None:
        return self._focus.get_open_focus()


    def list_open_focuses(self) -> list[FocusSession]:
        return self._focus.list_open_focuses()


    def get_running_focus(self) -> FocusSession | None:
        return self._focus.get_running_focus()


    def get_open_focus_for_task(self, task: Task) -> FocusSession | None:
        return self._focus.get_open_focus_for_task(task)


    @staticmethod
    def _focus_from_row(row: sqlite3.Row | None) -> FocusSession | None:
        return FocusRepository._focus_from_row(row)


    def save_focus_pause(self, session: FocusSession) -> None:
        self._focus.save_focus_pause(session)


    def heartbeat_focus(
        self,
        session: FocusSession,
        when: datetime | None = None,
    ) -> None:
        self._focus.heartbeat_focus(session, when)


    def recover_open_focus(self, session: FocusSession) -> None:
        self._focus.recover_open_focus(session)


    def end_focus(
        self,
        session: FocusSession,
        outcome: FocusOutcome,
        when: datetime | None = None,
    ) -> None:
        self._focus.end_focus(session, outcome, when)


    def finish_focus_and_task(
        self,
        session: FocusSession,
        outcome: FocusOutcome,
        when: datetime | None = None,
    ) -> None:
        self._focus.finish_focus_and_task(session, outcome, when)


    def start_ai_session(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
    ) -> AiSession:
        return self._ai.start_ai_session(session_id, turn_id, when)


    @staticmethod
    def _ai_session_from_row(row: sqlite3.Row) -> AiSession:
        return AiRepository._ai_session_from_row(row)


    def get_open_ai(self, turn_id: str | None = None) -> AiSession | None:
        return self._ai.get_open_ai(turn_id)


    def list_open_ai(self) -> list[AiSession]:
        return self._ai.list_open_ai()


    def close_open_ai_sessions(
        self,
        status: str = "stale",
        when: datetime | None = None,
    ) -> int:
        return self._ai.close_open_ai_sessions(status, when)


    def purge_ai_sessions(
        self,
        max_age_days: int = 30,
        max_rows: int = 500,
        now: datetime | None = None,
    ) -> int:
        return self._ai.purge_ai_sessions(max_age_days, max_rows, now)


    def get_ai_session(self, turn_id: str) -> AiSession | None:
        return self._ai.get_ai_session(turn_id)


    def finish_ai_session(
        self,
        turn_id: str,
        status: str = "completed",
        when: datetime | None = None,
        fallback_latest: bool = True,
    ) -> AiSession | None:
        return self._ai.finish_ai_session(turn_id, status, when, fallback_latest)


    def skip_ai_picker(self, turn_id: str) -> AiSession | None:
        return self._ai.skip_ai_picker(turn_id)


    def set_ai_status(
        self,
        turn_id: str,
        status: str,
        fallback_latest: bool = True,
        when: datetime | None = None,
    ) -> AiSession | None:
        return self._ai.set_ai_status(turn_id, status, fallback_latest, when)


    @staticmethod
    def _today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
        return StatsRepository._today_window(now)


    def today_focus_seconds(self, now: datetime | None = None) -> float:
        return self._stats.today_focus_seconds(now)


    def today_completed_tasks(
        self,
        now: datetime | None = None,
    ) -> list[CompletedTaskSummary]:
        return self._stats.today_completed_tasks(now)


    def completed_focus_records(
        self,
        task_id: int | None,
        title: str,
        kind: TaskKind,
        tag: str | None = None,
        now: datetime | None = None,
    ) -> list[CompletedFocusRecord]:
        return self._stats.completed_focus_records(task_id, title, kind, tag, now)


    def get_completed_focus_record(self, session_id: int) -> CompletedFocusRecord | None:
        return self._stats.get_completed_focus_record(session_id)


    def update_completed_focus_end_time(
        self,
        session_id: int,
        ended_at: datetime,
    ) -> bool:
        return self._focus.update_completed_focus_end_time(session_id, ended_at)


    def archive_focus_session(self, session_id: int) -> bool:
        return self._focus.archive_focus_session(session_id)


    def restore_archived_focus_session(self, session_id: int) -> bool:
        return self._focus.restore_archived_focus_session(session_id)


    def purge_archived_focus_sessions(self, max_age_days: int = 7) -> int:
        return self._focus.purge_archived_focus_sessions(max_age_days)


    def delete_focus_session(self, session_id: int) -> None:
        self._focus.delete_focus_session(session_id)


    def clear_focus_history(self) -> int:
        return self._focus.clear_focus_history()


    @staticmethod
    def _period_window(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
        return StatsRepository._period_window(period, now)


    @staticmethod
    def _overlap_seconds(
        started_at: datetime,
        ended_at: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        return StatsRepository._overlap_seconds(
            started_at,
            ended_at,
            window_start,
            window_end,
        )


    def codex_active_seconds(self, period: str, now: datetime | None = None) -> float:
        return self._stats.codex_active_seconds(period, now)


    def waiting_seconds(self, period: str, now: datetime | None = None) -> float:
        return self._stats.waiting_seconds(period, now)


    def _tag_waiting_seconds_for_windows(
        self,
        windows: list[tuple[datetime, datetime]],
        current: datetime,
    ) -> list[dict[str, float]]:
        return self._stats._tag_waiting_seconds_for_windows(windows, current)


    def tag_waiting_seconds(self, period: str, now: datetime | None = None) -> dict[str, float]:
        return self._stats.tag_waiting_seconds(period, now)


    def tag_waiting_daily_series(
        self,
        period: str,
        now: datetime | None = None,
    ) -> list[TagTimeBucket]:
        return self._stats.tag_waiting_daily_series(period, now)


    def today_completed_titles(self, now: datetime | None = None) -> list[str]:
        return self._stats.today_completed_titles(now)


    def get_setting(self, key: str, default: str = "") -> str:
        row = self._connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def _set_setting_uncommitted(self, key: str, value: str) -> None:
        self._connection.execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def set_setting(self, key: str, value: str) -> None:
        self._set_setting_uncommitted(key, value)
        self._connection.commit()
