from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
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
    from_iso,
    to_iso,
    utc_now,
)


# Kept only so upgrades can recognize and migrate the old research-focused
# built-ins. These values are never inserted into a fresh database.
LEGACY_DEFAULT_TASKS: tuple[str, ...] = (
    "精读并标记一段论文",
    "补写一条实验记录",
    "检查并完善一个图注",
    "整理一条参考文献",
    "写下当前研究的三个下一步",
    "清理一个代码 TODO",
    "修改一段论文表述",
)

DEFAULT_TASKS: tuple[str, ...] = (
    "处理一个五分钟待办",
    "整理一条笔记",
    "阅读几页内容并记下要点",
    "清理一个代码 TODO",
    "回复一条重要消息",
    "整理一个文件夹",
    "写下当前事情的下一步",
)

LEGACY_DEFAULT_TAGS: tuple[str, ...] = ("论文写作", "文献阅读", "Vibe coding", DEFAULT_TAG)
DEFAULT_TAGS: tuple[str, ...] = ("写作", "阅读", "编码", "整理", "工作/项目", DEFAULT_TAG)
DEFAULT_CONTENT_VERSION = "2"

LEGACY_DEFAULT_TASK_TAGS: dict[str, str] = {
    LEGACY_DEFAULT_TASKS[0]: "文献阅读",
    LEGACY_DEFAULT_TASKS[1]: "论文写作",
    LEGACY_DEFAULT_TASKS[2]: "论文写作",
    LEGACY_DEFAULT_TASKS[3]: "文献阅读",
    LEGACY_DEFAULT_TASKS[4]: "论文写作",
    LEGACY_DEFAULT_TASKS[5]: "Vibe coding",
    LEGACY_DEFAULT_TASKS[6]: "论文写作",
}
DEFAULT_TASK_TAGS: dict[str, str] = {
    DEFAULT_TASKS[0]: "工作/项目",
    DEFAULT_TASKS[1]: "整理",
    DEFAULT_TASKS[2]: "阅读",
    DEFAULT_TASKS[3]: "编码",
    DEFAULT_TASKS[4]: "工作/项目",
    DEFAULT_TASKS[5]: "整理",
    DEFAULT_TASKS[6]: "工作/项目",
}

AI_RUNNING_STATUSES = {"running", "inprogress"}


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
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
                ,tag TEXT NOT NULL DEFAULT '未分类'
            );

            CREATE TABLE IF NOT EXISTS focus_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                task_title TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                paused_seconds REAL NOT NULL DEFAULT 0,
                paused_at TEXT,
                last_heartbeat_at TEXT,
                outcome TEXT,
                task_tag TEXT NOT NULL DEFAULT '未分类',
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS focus_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                focus_session_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY(focus_session_id) REFERENCES focus_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_focus_segments_session
                ON focus_segments(focus_session_id, started_at);

            CREATE INDEX IF NOT EXISTS idx_focus_sessions_outcome_end
                ON focus_sessions(outcome, ended_at);

            CREATE INDEX IF NOT EXISTS idx_focus_segments_time
                ON focus_segments(started_at, ended_at);

            CREATE TABLE IF NOT EXISTS deleted_focus_sessions (
                id INTEGER PRIMARY KEY,
                task_id INTEGER,
                task_title TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                paused_seconds REAL NOT NULL DEFAULT 0,
                paused_at TEXT,
                last_heartbeat_at TEXT,
                outcome TEXT NOT NULL,
                task_tag TEXT NOT NULL DEFAULT '未分类',
                deleted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deleted_focus_segments (
                id INTEGER PRIMARY KEY,
                focus_session_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_deleted_focus_sessions_time
                ON deleted_focus_sessions(deleted_at);

            CREATE INDEX IF NOT EXISTS idx_deleted_focus_segments_session
                ON deleted_focus_segments(focus_session_id, started_at);

            CREATE TABLE IF NOT EXISTS ai_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                picker_skipped INTEGER NOT NULL DEFAULT 0,
                active_seconds REAL NOT NULL DEFAULT 0,
                running_since TEXT
            );

            CREATE TABLE IF NOT EXISTS ai_activity_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ai_session_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY(ai_session_id) REFERENCES ai_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_ai_activity_segments_time
                ON ai_activity_segments(started_at, ended_at, state);

            CREATE INDEX IF NOT EXISTS idx_ai_sessions_turn
                ON ai_sessions(turn_id, status);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
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
        if len(entries) != len(LEGACY_DEFAULT_TASKS):
            return False
        if {entry.title for entry in entries} != set(LEGACY_DEFAULT_TASKS):
            return False
        return all(
            entry.enabled and entry.tag == LEGACY_DEFAULT_TASK_TAGS[entry.title]
            for entry in entries
        )

    @staticmethod
    def _map_legacy_default_entries(
        entries: list[DefaultTaskEntry],
    ) -> list[DefaultTaskEntry]:
        title_map = dict(zip(LEGACY_DEFAULT_TASKS, DEFAULT_TASKS))
        return [
            DefaultTaskEntry(
                title_map[entry.title],
                True,
                DEFAULT_TASK_TAGS[title_map[entry.title]],
            )
            for entry in entries
        ]

    def add_manual_task(self, title: str, tag: str = DEFAULT_TAG) -> Task:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("任务名称不能为空")
        next_order = self._connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open'"
        ).fetchone()[0]
        clean_tag = self.normalize_tag(tag)
        if clean_tag not in self.available_tags():
            # A task imported from an older profile may carry a tag that is no
            # longer part of the built-in list. Keep it selectable and visible
            # in the tag manager instead of silently hiding it.
            self._save_available_tags_uncommitted(self.available_tags() + [clean_tag])
        cursor = self._connection.execute(
            "INSERT INTO tasks(title, status, sort_order, created_at, tag) VALUES (?, 'open', ?, ?, ?)",
            (clean_title, next_order, to_iso(utc_now()), clean_tag),
        )
        self._connection.commit()
        task_id = cursor.lastrowid
        if task_id is None:
            raise RuntimeError("无法创建任务")
        return Task(int(task_id), clean_title, TaskKind.MANUAL, next_order, clean_tag)

    def list_manual_tasks(self) -> list[Task]:
        rows = self._connection.execute(
            "SELECT id, title, sort_order, tag FROM tasks WHERE status = 'open' ORDER BY sort_order, id"
        ).fetchall()
        return [Task(row["id"], row["title"], TaskKind.MANUAL, row["sort_order"], row["tag"] or DEFAULT_TAG) for row in rows]

    @staticmethod
    def normalize_tag(tag: str | None) -> str:
        clean = " ".join(str(tag or "").strip().split())
        return clean or DEFAULT_TAG

    def available_tags(self) -> list[str]:
        raw = self.get_setting("task_tags", "")
        tags: list[str] = []
        if raw:
            try:
                stored = json.loads(raw)
            except json.JSONDecodeError:
                stored = []
            if isinstance(stored, list):
                tags = [self.normalize_tag(value) for value in stored if str(value).strip()]
        if not tags:
            tags = list(DEFAULT_TAGS)
        elif DEFAULT_TAG not in tags:
            # The fallback tag is always available so deleting a custom tag
            # never leaves existing tasks without a valid destination.
            tags.append(DEFAULT_TAG)
        return list(dict.fromkeys(tags))

    def _save_available_tags_uncommitted(self, tags: list[str]) -> None:
        cleaned: list[str] = []
        for value in tags:
            tag = self.normalize_tag(value)
            if tag not in cleaned:
                cleaned.append(tag)
        if DEFAULT_TAG not in cleaned:
            cleaned.append(DEFAULT_TAG)
        self._set_setting_uncommitted(
            "task_tags",
            json.dumps(cleaned, ensure_ascii=False),
        )

    def add_tag(self, tag: str) -> str:
        clean_tag = self.normalize_tag(tag)
        if not str(tag or "").strip():
            raise ValueError("标签名称不能为空")
        tags = self.available_tags()
        if clean_tag in tags:
            raise ValueError("标签已存在")
        tags.append(clean_tag)
        self._save_available_tags_uncommitted(tags)
        self._connection.commit()
        return clean_tag

    def rename_tag(self, old_tag: str, new_tag: str) -> str:
        old = self.normalize_tag(old_tag)
        if not str(new_tag or "").strip():
            raise ValueError("标签名称不能为空")
        new = self.normalize_tag(new_tag)
        if old == DEFAULT_TAG:
            raise ValueError("未分类是系统保底标签，不能重命名")
        tags = self.available_tags()
        if old not in tags:
            raise ValueError("要修改的标签不存在")
        if new in tags and new != old:
            raise ValueError("标签已存在")
        if old == new:
            return new
        renamed = [new if tag == old else tag for tag in tags]
        entries = self.default_task_entries()
        entries = [
            DefaultTaskEntry(entry.title, entry.enabled, new if entry.tag == old else entry.tag)
            for entry in entries
        ]
        with self._connection:
            self._save_available_tags_uncommitted(renamed)
            self._connection.execute(
                "UPDATE tasks SET tag = ? WHERE tag = ?",
                (new, old),
            )
            self._connection.execute(
                "UPDATE focus_sessions SET task_tag = ? WHERE task_tag = ?",
                (new, old),
            )
            self._set_default_task_entries_uncommitted(entries)
        return new

    def delete_tag(self, tag: str) -> None:
        clean_tag = self.normalize_tag(tag)
        if clean_tag == DEFAULT_TAG:
            raise ValueError("未分类是系统保底标签，不能删除")
        self.delete_tags([clean_tag])

    def delete_tags(self, tags: list[str]) -> int:
        """Delete several tags atomically and reassign their data to fallback."""

        available = set(self.available_tags())
        clean_tags: list[str] = []
        for value in tags:
            clean_tag = self.normalize_tag(value)
            if clean_tag == DEFAULT_TAG:
                raise ValueError("未分类是系统保底标签，不能删除")
            if clean_tag in available and clean_tag not in clean_tags:
                clean_tags.append(clean_tag)
        if not clean_tags:
            return 0

        remaining = [value for value in self.available_tags() if value not in clean_tags]
        entries = self.default_task_entries()
        entries = [
            DefaultTaskEntry(
                entry.title,
                entry.enabled,
                DEFAULT_TAG if entry.tag in clean_tags else entry.tag,
            )
            for entry in entries
        ]
        with self._connection:
            self._save_available_tags_uncommitted(remaining)
            for clean_tag in clean_tags:
                self._connection.execute(
                    "UPDATE tasks SET tag = ? WHERE tag = ?",
                    (DEFAULT_TAG, clean_tag),
                )
                self._connection.execute(
                    "UPDATE focus_sessions SET task_tag = ? WHERE task_tag = ?",
                    (DEFAULT_TAG, clean_tag),
                )
            self._set_default_task_entries_uncommitted(entries)
        return len(clean_tags)

    def tag_usage_counts(self) -> dict[str, int]:
        """Return current task counts for the tag management view."""

        counts = {tag: 0 for tag in self.available_tags()}
        rows = self._connection.execute(
            "SELECT tag, COUNT(*) AS count FROM tasks WHERE status = 'open' GROUP BY tag"
        ).fetchall()
        for row in rows:
            tag = self.normalize_tag(row["tag"])
            counts[tag] = counts.get(tag, 0) + int(row["count"])
        for entry in self.default_task_entries():
            if entry.enabled:
                counts[entry.tag] = counts.get(entry.tag, 0) + 1
        return counts

    def complete_manual_task(self, task_id: int, when: datetime | None = None) -> None:
        self._connection.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'open'",
            (to_iso(when or utc_now()), task_id),
        )
        self._connection.commit()

    def _complete_manual_task_uncommitted(self, task_id: int, when: datetime) -> None:
        self._connection.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'open'",
            (to_iso(when), task_id),
        )

    def delete_manual_task(self, task_id: int) -> Task | None:
        row = self._connection.execute(
            "SELECT id, title, sort_order, tag FROM tasks WHERE id = ? AND status = 'open'",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        deleted = Task(
            int(row["id"]),
            row["title"],
            TaskKind.MANUAL,
            int(row["sort_order"]),
            row["tag"] or DEFAULT_TAG,
        )
        self._connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._connection.commit()
        return deleted

    def suggested_tasks(self, limit: int = 3) -> list[Task]:
        manual_tasks = self.list_manual_tasks()
        if manual_tasks:
            return manual_tasks[:limit]
        entries = [entry for entry in self.default_task_entries() if entry.enabled]
        return [
            Task(None, entry.title, TaskKind.DEFAULT, offset, entry.tag)
            for offset, entry in enumerate(entries[:limit])
        ]

    def advance_default_task(self, selected_title: str | None = None) -> None:
        entries = self.default_task_entries()
        enabled_titles = [entry.title for entry in entries if entry.enabled]
        if not enabled_titles:
            return
        selected = selected_title if selected_title in enabled_titles else enabled_titles[0]
        selected_entry = next(entry for entry in entries if entry.title == selected)
        entries.remove(selected_entry)
        entries.append(selected_entry)
        self.set_default_task_entries(entries)

    def default_task_entries(self) -> list[DefaultTaskEntry]:
        raw = self.get_setting("default_tasks_v2", "")
        if raw:
            entries = self._parse_default_task_entries(raw)
            if entries:
                return entries
        return [DefaultTaskEntry(title, True, DEFAULT_TASK_TAGS.get(title, DEFAULT_TAG)) for title in self._default_task_order()]

    def _parse_default_task_entries(self, raw: str) -> list[DefaultTaskEntry]:
        try:
            stored = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(stored, list):
            return []
        entries: list[DefaultTaskEntry] = []
        seen: set[str] = set()
        for item in stored:
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").strip().split())
            if not title or title in seen:
                continue
            seen.add(title)
            entries.append(DefaultTaskEntry(
                title,
                bool(item.get("enabled", True)),
                self.normalize_tag(item.get("tag") or DEFAULT_TASK_TAGS.get(title)),
            ))
        return entries

    def set_default_task_entries(self, entries: list[DefaultTaskEntry]) -> None:
        self._set_default_task_entries_uncommitted(entries)
        self._connection.commit()

    def _set_default_task_entries_uncommitted(self, entries: list[DefaultTaskEntry]) -> None:
        cleaned: list[dict[str, object]] = []
        seen: set[str] = set()
        for entry in entries:
            title = " ".join(entry.title.strip().split())
            if not title or title in seen:
                continue
            seen.add(title)
            cleaned.append({"title": title, "enabled": bool(entry.enabled), "tag": self.normalize_tag(entry.tag)})
        self._set_setting_uncommitted("default_tasks_v2", json.dumps(cleaned, ensure_ascii=False))

    def _default_task_order(self) -> list[str]:
        raw = self.get_setting("default_task_order", "")
        try:
            stored = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            stored = []
        valid = [title for title in stored if title in DEFAULT_TASKS]
        for title in DEFAULT_TASKS:
            if title not in valid:
                valid.append(title)
        return valid

    def start_focus(self, task: Task, when: datetime | None = None) -> FocusSession:
        if self.get_running_focus() is not None:
            raise RuntimeError("已有正在进行的微任务")
        started_at = when or utc_now()
        cursor = self._connection.execute(
            """
            INSERT INTO focus_sessions(
                task_id, task_title, task_kind, task_tag, started_at, last_heartbeat_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.title,
                task.kind.value,
                self.normalize_tag(task.tag),
                to_iso(started_at),
                to_iso(started_at),
            ),
        )
        self._connection.execute(
            "INSERT INTO focus_segments(focus_session_id, started_at) VALUES (?, ?)",
            (cursor.lastrowid, to_iso(started_at)),
        )
        self._connection.commit()
        focus_id = cursor.lastrowid
        if focus_id is None:
            raise RuntimeError("无法创建 Waiting Task 记录")
        return FocusSession(
            int(focus_id),
            task,
            started_at,
            last_heartbeat_at=started_at,
        )

    def get_open_focus(self) -> FocusSession | None:
        sessions = self.list_open_focuses()
        return sessions[-1] if sessions else None

    def list_open_focuses(self) -> list[FocusSession]:
        rows = self._connection.execute(
            "SELECT * FROM focus_sessions WHERE ended_at IS NULL ORDER BY id"
        ).fetchall()
        sessions: list[FocusSession] = []
        for row in rows:
            session = self._focus_from_row(row)
            if session is not None:
                sessions.append(session)
        return sessions

    def get_running_focus(self) -> FocusSession | None:
        row = self._connection.execute(
            """
            SELECT * FROM focus_sessions
            WHERE ended_at IS NULL AND paused_at IS NULL
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return self._focus_from_row(row) if row is not None else None

    def get_open_focus_for_task(self, task: Task) -> FocusSession | None:
        if task.id is not None:
            row = self._connection.execute(
                """
                SELECT * FROM focus_sessions
                WHERE ended_at IS NULL AND task_id = ? AND task_kind = ?
                ORDER BY id DESC LIMIT 1
                """,
                (task.id, task.kind.value),
            ).fetchone()
        else:
            row = self._connection.execute(
                """
                SELECT * FROM focus_sessions
                WHERE ended_at IS NULL AND task_id IS NULL
                  AND task_kind = ? AND task_title = ?
                ORDER BY id DESC LIMIT 1
                """,
                (task.kind.value, task.title),
            ).fetchone()
        return self._focus_from_row(row) if row is not None else None

    @staticmethod
    def _focus_from_row(row: sqlite3.Row | None) -> FocusSession | None:
        if row is None:
            return None
        task_id = int(row["task_id"]) if row["task_id"] is not None else None
        task = Task(task_id, row["task_title"], TaskKind(row["task_kind"]), 0, row["task_tag"] or DEFAULT_TAG)
        started_at = from_iso(row["started_at"])
        if started_at is None:
            return None
        return FocusSession(
            id=int(row["id"]),
            task=task,
            started_at=started_at,
            paused_seconds=float(row["paused_seconds"]),
            paused_at=from_iso(row["paused_at"]),
            last_heartbeat_at=from_iso(row["last_heartbeat_at"]),
        )

    def save_focus_pause(self, session: FocusSession) -> None:
        segment = self._connection.execute(
            "SELECT id FROM focus_segments WHERE focus_session_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (session.id,),
        ).fetchone()
        if session.paused_at is not None and segment is not None:
            self._connection.execute(
                "UPDATE focus_segments SET ended_at = ? WHERE id = ?",
                (to_iso(session.paused_at), segment["id"]),
            )
        elif session.paused_at is None and segment is None:
            resumed_at = session.last_heartbeat_at or utc_now()
            self._connection.execute(
                "INSERT INTO focus_segments(focus_session_id, started_at) VALUES (?, ?)",
                (session.id, to_iso(resumed_at)),
            )
        self._connection.execute(
            """
            UPDATE focus_sessions
            SET paused_seconds = ?, paused_at = ?, last_heartbeat_at = ?
            WHERE id = ?
            """,
            (
                session.paused_seconds,
                to_iso(session.paused_at),
                to_iso(session.last_heartbeat_at),
                session.id,
            ),
        )
        self._connection.commit()

    def heartbeat_focus(
        self,
        session: FocusSession,
        when: datetime | None = None,
    ) -> None:
        if session.paused_at is not None:
            return
        heartbeat_at = when or utc_now()
        session.last_heartbeat_at = heartbeat_at
        self._connection.execute(
            "UPDATE focus_sessions SET last_heartbeat_at = ? WHERE id = ? AND ended_at IS NULL",
            (to_iso(heartbeat_at), session.id),
        )
        self._connection.commit()

    def recover_open_focus(self, session: FocusSession) -> None:
        """Pause an uncleanly interrupted session at its last known heartbeat."""
        if session.paused_at is not None:
            return
        recovered_pause = session.last_heartbeat_at or session.started_at
        if recovered_pause < session.started_at:
            recovered_pause = session.started_at
        session.paused_at = recovered_pause
        session.last_heartbeat_at = recovered_pause
        self.save_focus_pause(session)

    def end_focus(
        self,
        session: FocusSession,
        outcome: FocusOutcome,
        when: datetime | None = None,
    ) -> None:
        ended_at = when or utc_now()
        paused_seconds = session.paused_seconds
        if session.paused_at is not None:
            paused_seconds += max(0.0, (ended_at - session.paused_at).total_seconds())
        self._connection.execute(
            "UPDATE focus_segments SET ended_at = ? WHERE focus_session_id = ? AND ended_at IS NULL",
            (to_iso(ended_at), session.id),
        )
        self._connection.execute(
            """
            UPDATE focus_sessions
            SET ended_at = ?, paused_seconds = ?, paused_at = NULL, outcome = ?
            WHERE id = ?
            """,
            (to_iso(ended_at), paused_seconds, outcome.value, session.id),
        )
        self._connection.commit()

    def finish_focus_and_task(
        self,
        session: FocusSession,
        outcome: FocusOutcome,
        when: datetime | None = None,
    ) -> None:
        """Atomically close a focus session and update its task/rotation."""

        ended_at = when or utc_now()
        paused_seconds = session.paused_seconds
        if session.paused_at is not None:
            paused_seconds += max(0.0, (ended_at - session.paused_at).total_seconds())
        try:
            with self._connection:
                self._connection.execute(
                    """
                    UPDATE focus_sessions
                    SET ended_at = ?, paused_seconds = ?, paused_at = NULL, outcome = ?
                    WHERE id = ? AND ended_at IS NULL
                    """,
                    (to_iso(ended_at), paused_seconds, outcome.value, session.id),
                )
                self._connection.execute(
                    "UPDATE focus_segments SET ended_at = ? WHERE focus_session_id = ? AND ended_at IS NULL",
                    (to_iso(ended_at), session.id),
                )
                if outcome is FocusOutcome.COMPLETED and session.task.kind is TaskKind.MANUAL and session.task.id is not None:
                    self._complete_manual_task_uncommitted(session.task.id, ended_at)
                elif session.task.kind is TaskKind.DEFAULT:
                    entries = self.default_task_entries()
                    enabled_titles = [entry.title for entry in entries if entry.enabled]
                    if enabled_titles:
                        selected = session.task.title if session.task.title in enabled_titles else enabled_titles[0]
                        selected_entry = next(entry for entry in entries if entry.title == selected)
                        entries.remove(selected_entry)
                        entries.append(selected_entry)
                        self._set_default_task_entries_uncommitted(entries)
        except Exception:
            self._connection.rollback()
            raise

    def start_ai_session(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
    ) -> AiSession:
        existing = self.get_open_ai(turn_id=turn_id)
        if existing is not None:
            return existing
        started_at = when or utc_now()
        cursor = self._connection.execute(
            """
            INSERT INTO ai_sessions(
                session_id, turn_id, started_at, status, active_seconds, running_since
            )
            VALUES (?, ?, ?, 'running', 0, ?)
            """,
            (
                session_id,
                turn_id,
                to_iso(started_at),
                to_iso(started_at) if self.track_ai_time else None,
            ),
        )
        if self.track_ai_time:
            self._connection.execute(
                "INSERT INTO ai_activity_segments(ai_session_id, state, started_at) VALUES (?, 'running', ?)",
                (cursor.lastrowid, to_iso(started_at)),
            )
        self._connection.commit()
        ai_id = cursor.lastrowid
        if ai_id is None:
            raise RuntimeError("无法创建 Codex 会话记录")
        return AiSession(
            int(ai_id),
            session_id,
            turn_id,
            started_at,
            active_seconds=0.0,
            running_since=started_at if self.track_ai_time else None,
        )

    @staticmethod
    def _ai_session_from_row(row: sqlite3.Row) -> AiSession:
        started_at = from_iso(row["started_at"])
        if started_at is None:
            raise ValueError("AI 会话缺少开始时间")
        return AiSession(
            row["id"],
            row["session_id"],
            row["turn_id"],
            started_at,
            from_iso(row["ended_at"]),
            row["status"],
            bool(row["picker_skipped"]),
            float(row["active_seconds"] or 0.0),
            from_iso(row["running_since"]),
        )

    def get_open_ai(self, turn_id: str | None = None) -> AiSession | None:
        if turn_id:
            row = self._connection.execute(
                """
                SELECT * FROM ai_sessions
                WHERE turn_id = ? AND ended_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (turn_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM ai_sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self._ai_session_from_row(row)

    def list_open_ai(self) -> list[AiSession]:
        """Return all persisted AI sessions that have not ended."""

        rows = self._connection.execute(
            """
            SELECT * FROM ai_sessions
            WHERE ended_at IS NULL
            ORDER BY id DESC
            """
        ).fetchall()
        return [self._ai_session_from_row(row) for row in rows]

    def close_open_ai_sessions(
        self,
        status: str = "stale",
        when: datetime | None = None,
    ) -> int:
        """Close lifecycle rows left by an earlier WaitLAB process.

        Codex sessions are an event bridge, not durable work logs. Keeping an
        old ``running`` row active after a restart makes Cookie claim that
        Codex is still working even when the conversation has ended. The rows
        remain available for backward-compatible inspection, but they no
        longer participate in the current process' activity state.
        """

        open_sessions = self.list_open_ai()
        if not open_sessions:
            return 0
        ended_at = when or utc_now()
        with self._connection:
            for session in open_sessions:
                self._connection.execute(
                    "UPDATE ai_activity_segments SET ended_at = ? "
                    "WHERE ai_session_id = ? AND ended_at IS NULL",
                    (to_iso(ended_at), session.id),
                )
                self._connection.execute(
                    "UPDATE ai_sessions SET ended_at = ?, status = ?, running_since = NULL "
                    "WHERE id = ? AND ended_at IS NULL",
                    (to_iso(ended_at), status, session.id),
                )
        return len(open_sessions)

    def purge_ai_sessions(
        self,
        max_age_days: int = 30,
        max_rows: int = 500,
        now: datetime | None = None,
    ) -> int:
        """Prune old Codex lifecycle rows while retaining recent diagnostics."""

        age_days = max(1, int(max_age_days))
        keep_rows = max(1, int(max_rows))
        cutoff = (now or utc_now()) - timedelta(days=age_days)
        with self._connection:
            before = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM ai_sessions WHERE ended_at IS NOT NULL"
                ).fetchone()[0]
            )
            self._connection.execute(
                """
                DELETE FROM ai_sessions
                WHERE ended_at IS NOT NULL
                  AND (
                    ended_at < ?
                    OR id NOT IN (
                        SELECT id FROM ai_sessions
                        WHERE ended_at IS NOT NULL
                        ORDER BY ended_at DESC, id DESC
                        LIMIT ?
                    )
                  )
                """,
                (to_iso(cutoff), keep_rows),
            )
            after = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM ai_sessions WHERE ended_at IS NOT NULL"
                ).fetchone()[0]
            )
        return max(0, before - after)

    def get_ai_session(self, turn_id: str) -> AiSession | None:
        row = self._connection.execute(
            "SELECT * FROM ai_sessions WHERE turn_id = ? ORDER BY id DESC LIMIT 1",
            (turn_id,),
        ).fetchone()
        if row is None:
            return None
        return self._ai_session_from_row(row)

    def finish_ai_session(
        self,
        turn_id: str,
        status: str = "completed",
        when: datetime | None = None,
        fallback_latest: bool = True,
    ) -> AiSession | None:
        session = self.get_open_ai(turn_id=turn_id)
        if session is None and fallback_latest:
            session = self.get_open_ai()
        if session is None:
            return None
        ended_at = when or utc_now()
        if ended_at < session.started_at:
            ended_at = session.started_at
        active_seconds = session.active_seconds
        if self.track_ai_time:
            if session.running_since is not None and session.ended_at is None:
                active_seconds += max(
                    0.0,
                    (ended_at - session.running_since).total_seconds(),
                )
            self._connection.execute(
                "UPDATE ai_activity_segments SET ended_at = ? WHERE ai_session_id = ? AND ended_at IS NULL",
                (to_iso(ended_at), session.id),
            )
            self._connection.execute(
                """
                UPDATE ai_sessions
                SET ended_at = ?, status = ?, active_seconds = ?, running_since = NULL
                WHERE id = ?
                """,
                (to_iso(ended_at), status, active_seconds, session.id),
            )
        else:
            self._connection.execute(
                "UPDATE ai_sessions SET ended_at = ?, status = ?, running_since = NULL WHERE id = ?",
                (to_iso(ended_at), status, session.id),
            )
        self._connection.commit()
        session.ended_at = ended_at
        session.status = status
        if self.track_ai_time:
            session.active_seconds = active_seconds
        session.running_since = None
        return session

    def skip_ai_picker(self, turn_id: str) -> AiSession | None:
        session = self.get_open_ai(turn_id=turn_id)
        if session is None:
            return None
        self._connection.execute(
            "UPDATE ai_sessions SET picker_skipped = 1 WHERE id = ? AND ended_at IS NULL",
            (session.id,),
        )
        self._connection.commit()
        session.picker_skipped = True
        return session

    def set_ai_status(
        self,
        turn_id: str,
        status: str,
        fallback_latest: bool = True,
        when: datetime | None = None,
    ) -> AiSession | None:
        session = self.get_open_ai(turn_id=turn_id)
        if session is None and fallback_latest:
            session = self.get_open_ai()
        if session is None:
            return None
        changed_at = when or utc_now()
        active_seconds = session.active_seconds
        if self.track_ai_time and session.running_since is not None and session.ended_at is None:
            active_seconds += max(
                0.0,
                (changed_at - session.running_since).total_seconds(),
            )
        running_since = (
            changed_at
            if self.track_ai_time and status.casefold() in AI_RUNNING_STATUSES
            else None
        )
        if self.track_ai_time:
            self._connection.execute(
                "UPDATE ai_activity_segments SET ended_at = ? WHERE ai_session_id = ? AND ended_at IS NULL",
                (to_iso(changed_at), session.id),
            )
        if self.track_ai_time and running_since is not None:
            self._connection.execute(
                "INSERT INTO ai_activity_segments(ai_session_id, state, started_at) VALUES (?, 'running', ?)",
                (session.id, to_iso(changed_at)),
            )
        if self.track_ai_time:
            self._connection.execute(
                """
                UPDATE ai_sessions
                SET status = ?, active_seconds = ?, running_since = ?
                WHERE id = ? AND ended_at IS NULL
                """,
                (status, active_seconds, to_iso(running_since), session.id),
            )
        else:
            self._connection.execute(
                "UPDATE ai_sessions SET status = ?, running_since = NULL WHERE id = ? AND ended_at IS NULL",
                (status, session.id),
            )
        self._connection.commit()
        session.status = status
        if self.track_ai_time:
            session.active_seconds = active_seconds
        session.running_since = running_since
        return session

    @staticmethod
    def _today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
        """Return the current user's local day as UTC query bounds."""

        current = now or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_current = current.astimezone()
        local_start = local_current.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)

    def today_focus_seconds(self, now: datetime | None = None) -> float:
        return self.waiting_seconds("day", now)

    def today_completed_tasks(
        self,
        now: datetime | None = None,
    ) -> list[CompletedTaskSummary]:
        """Return today's completed tasks with their accumulated focus time."""

        day_start, day_end = self._today_window(now)
        rows = self._connection.execute(
            """
            SELECT task_id, task_title, task_kind, task_tag, started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE outcome = 'completed'
              AND ended_at >= ?
              AND ended_at < ?
            ORDER BY ended_at DESC
            """,
            (to_iso(day_start), to_iso(day_end)),
        ).fetchall()
        grouped: dict[tuple[int | None, str, TaskKind, str], CompletedTaskSummary] = {}
        for row in rows:
            started_at = from_iso(row["started_at"])
            ended_at = from_iso(row["ended_at"])
            if started_at is None or ended_at is None:
                continue
            duration = max(
                0.0,
                (ended_at - started_at).total_seconds()
                - float(row["paused_seconds"] or 0.0),
            )
            task_id = int(row["task_id"]) if row["task_id"] is not None else None
            title = str(row["task_title"])
            tag = self.normalize_tag(row["task_tag"])
            try:
                kind = TaskKind(str(row["task_kind"]))
            except ValueError:
                kind = TaskKind.DEFAULT
            key = (task_id, title, kind, tag)
            previous = grouped.get(key)
            if previous is None:
                grouped[key] = CompletedTaskSummary(
                    task_id=task_id,
                    title=title,
                    kind=kind,
                    total_seconds=duration,
                    completed_count=1,
                    last_completed_at=ended_at,
                    tag=tag,
                )
            else:
                grouped[key] = CompletedTaskSummary(
                    task_id=previous.task_id,
                    title=previous.title,
                    kind=previous.kind,
                    total_seconds=previous.total_seconds + duration,
                    completed_count=previous.completed_count + 1,
                    last_completed_at=max(previous.last_completed_at, ended_at),
                    tag=previous.tag,
                )
        return sorted(
            grouped.values(),
            key=lambda summary: summary.last_completed_at,
            reverse=True,
        )

    def completed_focus_records(
        self,
        task_id: int | None,
        title: str,
        kind: TaskKind,
        tag: str | None = None,
        now: datetime | None = None,
    ) -> list[CompletedFocusRecord]:
        """Return today's completed focus segments for one history row."""

        day_start, day_end = self._today_window(now)
        rows = self._connection.execute(
            """
            SELECT id, task_id, task_title, task_kind, task_tag,
                   started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE outcome = 'completed'
              AND ended_at >= ? AND ended_at < ?
              AND task_title = ? AND task_kind = ?
              AND (? IS NULL OR task_tag = ?)
              AND ((task_id IS NULL AND ? IS NULL) OR task_id = ?)
            ORDER BY ended_at DESC, id DESC
            """,
            (to_iso(day_start), to_iso(day_end), title, kind.value, tag, tag, task_id, task_id),
        ).fetchall()
        records: list[CompletedFocusRecord] = []
        for row in rows:
            started_at = from_iso(row["started_at"])
            ended_at = from_iso(row["ended_at"])
            if started_at is None or ended_at is None:
                continue
            duration = max(
                0.0,
                (ended_at - started_at).total_seconds()
                - float(row["paused_seconds"] or 0.0),
            )
            records.append(CompletedFocusRecord(
                id=int(row["id"]),
                task_id=int(row["task_id"]) if row["task_id"] is not None else None,
                title=str(row["task_title"]),
                kind=kind,
                tag=self.normalize_tag(row["task_tag"]),
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration,
            ))
        return records

    def get_completed_focus_record(self, session_id: int) -> CompletedFocusRecord | None:
        """Return one completed record for confirmation and undo actions."""

        row = self._connection.execute(
            """
            SELECT id, task_id, task_title, task_kind, task_tag,
                   started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE id = ? AND outcome = 'completed' AND ended_at IS NOT NULL
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        started_at = from_iso(row["started_at"])
        ended_at = from_iso(row["ended_at"])
        if started_at is None or ended_at is None:
            return None
        try:
            kind = TaskKind(str(row["task_kind"]))
        except ValueError:
            kind = TaskKind.DEFAULT
        duration = max(
            0.0,
            (ended_at - started_at).total_seconds()
            - float(row["paused_seconds"] or 0.0),
        )
        return CompletedFocusRecord(
            id=int(row["id"]),
            task_id=int(row["task_id"]) if row["task_id"] is not None else None,
            title=str(row["task_title"]),
            kind=kind,
            tag=self.normalize_tag(row["task_tag"]),
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
        )

    def archive_focus_session(self, session_id: int) -> bool:
        """Move one completed record to a local undo archive atomically."""

        deleted_at = to_iso(utc_now())
        try:
            with self._connection:
                row = self._connection.execute(
                    """
                    SELECT id, task_id, task_title, task_kind, started_at, ended_at,
                           paused_seconds, paused_at, last_heartbeat_at, outcome, task_tag
                    FROM focus_sessions
                    WHERE id = ? AND ended_at IS NOT NULL
                    """,
                    (session_id,),
                ).fetchone()
                if row is None:
                    return False
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO deleted_focus_sessions(
                        id, task_id, task_title, task_kind, started_at, ended_at,
                        paused_seconds, paused_at, last_heartbeat_at, outcome,
                        task_tag, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(row) + (deleted_at,),
                )
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO deleted_focus_segments(id, focus_session_id, started_at, ended_at)
                    SELECT id, focus_session_id, started_at, ended_at
                    FROM focus_segments WHERE focus_session_id = ?
                    """,
                    (session_id,),
                )
                self._connection.execute(
                    "DELETE FROM focus_sessions WHERE id = ?",
                    (session_id,),
                )
            return True
        except Exception:
            self._connection.rollback()
            raise

    def restore_archived_focus_session(self, session_id: int) -> bool:
        """Restore one recently archived completed record and its segments."""

        try:
            with self._connection:
                row = self._connection.execute(
                    "SELECT * FROM deleted_focus_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return False
                existing = self._connection.execute(
                    "SELECT 1 FROM focus_sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if existing is not None:
                    return False
                self._connection.execute(
                    """
                    INSERT INTO focus_sessions(
                        id, task_id, task_title, task_kind, started_at, ended_at,
                        paused_seconds, paused_at, last_heartbeat_at, outcome, task_tag
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(row[key] for key in (
                        "id", "task_id", "task_title", "task_kind", "started_at", "ended_at",
                        "paused_seconds", "paused_at", "last_heartbeat_at", "outcome", "task_tag",
                    )),
                )
                self._connection.execute(
                    """
                    INSERT INTO focus_segments(id, focus_session_id, started_at, ended_at)
                    SELECT id, focus_session_id, started_at, ended_at
                    FROM deleted_focus_segments WHERE focus_session_id = ?
                    """,
                    (session_id,),
                )
                self._connection.execute(
                    "DELETE FROM deleted_focus_segments WHERE focus_session_id = ?",
                    (session_id,),
                )
                self._connection.execute(
                    "DELETE FROM deleted_focus_sessions WHERE id = ?",
                    (session_id,),
                )
            return True
        except Exception:
            self._connection.rollback()
            raise

    def purge_archived_focus_sessions(self, max_age_days: int = 7) -> int:
        """Remove expired undo archives and return the number removed.

        Deleted history is recoverable only for a short window.  Purging the
        archive keeps long-running installations from growing indefinitely;
        segment rows are removed atomically with their parent sessions.
        """

        days = max(1, int(max_age_days))
        cutoff = to_iso(utc_now() - timedelta(days=days))
        try:
            with self._connection:
                rows = self._connection.execute(
                    "SELECT id FROM deleted_focus_sessions WHERE deleted_at < ?",
                    (cutoff,),
                ).fetchall()
                if not rows:
                    return 0
                ids = [int(row[0]) for row in rows]
                placeholders = ",".join("?" for _ in ids)
                self._connection.execute(
                    f"DELETE FROM deleted_focus_segments WHERE focus_session_id IN ({placeholders})",
                    ids,
                )
                self._connection.execute(
                    f"DELETE FROM deleted_focus_sessions WHERE id IN ({placeholders})",
                    ids,
                )
                return len(ids)
        except Exception:
            self._connection.rollback()
            raise

    def delete_focus_session(self, session_id: int) -> None:
        """Delete one completed/abandoned focus record from local history."""

        self._connection.execute(
            "DELETE FROM focus_sessions WHERE id = ? AND ended_at IS NOT NULL",
            (session_id,),
        )
        self._connection.commit()

    @staticmethod
    def _period_window(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
        current = (now or datetime.now().astimezone()).astimezone()
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "day":
            end = start + timedelta(days=1)
        elif period == "week":
            start = start - timedelta(days=start.weekday())
            end = start + timedelta(days=7)
        elif period == "month":
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            start = start.replace(day=1)
            end = next_month
        else:
            raise ValueError(f"unknown period: {period}")
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    @staticmethod
    def _overlap_seconds(
        started_at: datetime,
        ended_at: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        overlap_start = max(started_at, window_start)
        overlap_end = min(ended_at, window_end)
        return max(0.0, (overlap_end - overlap_start).total_seconds())

    def codex_active_seconds(self, period: str, now: datetime | None = None) -> float:
        """Return active Codex time in a local day/week/month window."""

        window_start, window_end = self._period_window(period, now)
        current = now or utc_now()
        segment_rows = self._connection.execute(
            "SELECT started_at, ended_at FROM ai_activity_segments WHERE state = 'running'"
        ).fetchall()
        total = 0.0
        for row in segment_rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current
            if started is not None and ended is not None:
                total += self._overlap_seconds(started, ended, window_start, window_end)

        # Legacy installations have no segment history; retain a conservative
        # proportional fallback until they naturally accumulate new records.
        rows = self._connection.execute(
            """
            SELECT started_at, ended_at, active_seconds, running_since
            FROM ai_sessions AS a
            WHERE NOT EXISTS (
                SELECT 1 FROM ai_activity_segments AS s WHERE s.ai_session_id = a.id
            )
            """
        ).fetchall()
        for row in rows:
            started = from_iso(row["started_at"])
            if started is None:
                continue
            ended = from_iso(row["ended_at"]) or current
            overlap = self._overlap_seconds(started, ended, window_start, window_end)
            wall = max(0.0, (ended - started).total_seconds())
            if overlap <= 0 or wall <= 0:
                continue
            active = float(row["active_seconds"] or 0.0)
            if row["ended_at"] is None and row["running_since"]:
                running_since = from_iso(row["running_since"])
                if running_since is not None:
                    active += max(0.0, (current - running_since).total_seconds())
            total += active * min(1.0, overlap / wall)
        return total

    def waiting_seconds(self, period: str, now: datetime | None = None) -> float:
        window_start, window_end = self._period_window(period, now)
        current = now or utc_now()
        segment_rows = self._connection.execute(
            """
            SELECT s.started_at, s.ended_at
            FROM focus_segments AS s
            JOIN focus_sessions AS f ON f.id = s.focus_session_id
            WHERE f.outcome IN ('completed', 'abandoned') OR f.ended_at IS NULL
            """
        ).fetchall()
        total = 0.0
        for row in segment_rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current
            if started is not None and ended is not None:
                total += self._overlap_seconds(started, ended, window_start, window_end)

        # Legacy installations have no focus segment history.
        rows = self._connection.execute(
            """
            SELECT f.started_at, f.ended_at, f.paused_seconds, f.paused_at
            FROM focus_sessions AS f
            WHERE NOT EXISTS (
                SELECT 1 FROM focus_segments AS s WHERE s.focus_session_id = f.id
            )
            """
        ).fetchall()
        for row in rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current
            if started is None or ended is None:
                continue
            wall = max(0.0, (ended - started).total_seconds())
            overlap = self._overlap_seconds(started, ended, window_start, window_end)
            paused = float(row["paused_seconds"] or 0.0)
            if row["ended_at"] is None and row["paused_at"]:
                paused += max(0.0, (current - (from_iso(row["paused_at"]) or current)).total_seconds())
            total += max(0.0, wall - paused) * (overlap / wall if wall else 0.0)
        return total

    def _tag_waiting_seconds_for_windows(
        self,
        windows: list[tuple[datetime, datetime]],
        current: datetime,
    ) -> list[dict[str, float]]:
        """Aggregate tag time for one or more UTC windows in one DB pass."""

        totals: list[dict[str, float]] = [{} for _ in windows]
        current_utc = current.astimezone(timezone.utc)
        segment_rows = self._connection.execute(
            """
            SELECT s.started_at, s.ended_at, f.task_tag
            FROM focus_segments AS s
            JOIN focus_sessions AS f ON f.id = s.focus_session_id
            WHERE f.outcome IN ('completed', 'abandoned') OR f.ended_at IS NULL
            """
        ).fetchall()
        for row in segment_rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current_utc
            if started is None or ended is None:
                continue
            tag = self.normalize_tag(row["task_tag"])
            for index, (window_start, window_end) in enumerate(windows):
                duration = self._overlap_seconds(
                    started,
                    ended,
                    window_start,
                    window_end,
                )
                if duration > 0:
                    totals[index][tag] = totals[index].get(tag, 0.0) + duration

        # Legacy fallback mirrors waiting_seconds above.  Old sessions do not
        # have pause segments, so their active time is distributed
        # proportionally over the overlapping calendar windows.
        rows = self._connection.execute(
            """
            SELECT f.started_at, f.ended_at, f.paused_seconds, f.paused_at, f.task_tag
            FROM focus_sessions AS f
            WHERE NOT EXISTS (
                SELECT 1 FROM focus_segments AS s WHERE s.focus_session_id = f.id
            )
            """
        ).fetchall()
        for row in rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current_utc
            if started is None or ended is None:
                continue
            wall = max(0.0, (ended - started).total_seconds())
            if wall <= 0:
                continue
            paused = float(row["paused_seconds"] or 0.0)
            if row["ended_at"] is None and row["paused_at"]:
                paused += max(
                    0.0,
                    (
                        current_utc
                        - (from_iso(row["paused_at"]) or current_utc)
                    ).total_seconds(),
                )
            active = max(0.0, wall - paused)
            if active <= 0:
                continue
            tag = self.normalize_tag(row["task_tag"])
            for index, (window_start, window_end) in enumerate(windows):
                overlap = self._overlap_seconds(
                    started,
                    ended,
                    window_start,
                    window_end,
                )
                if overlap > 0:
                    duration = active * (overlap / wall)
                    totals[index][tag] = totals[index].get(tag, 0.0) + duration
        return totals

    def tag_waiting_seconds(self, period: str, now: datetime | None = None) -> dict[str, float]:
        window_start, window_end = self._period_window(period, now)
        current = now or utc_now()
        totals = self._tag_waiting_seconds_for_windows(
            [(window_start, window_end)],
            current,
        )[0]
        return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))

    def tag_waiting_daily_series(
        self,
        period: str,
        now: datetime | None = None,
    ) -> list[TagTimeBucket]:
        """Return per-day tag totals for the current local week or month."""

        if period not in {"week", "month"}:
            raise ValueError("daily tag series only supports week or month")
        current = (now or datetime.now().astimezone()).astimezone()
        period_start, period_end = self._period_window(period, current)
        local_start = period_start.astimezone(current.tzinfo)
        local_end = period_end.astimezone(current.tzinfo)
        buckets: list[tuple[datetime, datetime]] = []
        cursor = local_start
        while cursor < local_end:
            next_cursor = min(cursor + timedelta(days=1), local_end)
            buckets.append((cursor, next_cursor))
            cursor = next_cursor
        windows = [
            (start.astimezone(timezone.utc), end.astimezone(timezone.utc))
            for start, end in buckets
        ]
        totals = self._tag_waiting_seconds_for_windows(windows, current)
        return [
            TagTimeBucket(start, end, dict(sorted(day.items())))
            for (start, end), day in zip(buckets, totals)
        ]

    def today_completed_titles(self, now: datetime | None = None) -> list[str]:
        """Backward-compatible title-only view of today's completed tasks."""

        return [summary.title for summary in self.today_completed_tasks(now)]

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
