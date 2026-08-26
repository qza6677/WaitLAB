from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import (
    AiSession,
    DefaultTaskEntry,
    FocusOutcome,
    FocusSession,
    Task,
    TaskKind,
    from_iso,
    to_iso,
    utc_now,
)


DEFAULT_TASKS: tuple[str, ...] = (
    "精读并标记一段论文",
    "补写一条实验记录",
    "检查并完善一个图注",
    "整理一条参考文献",
    "写下当前研究的三个下一步",
    "清理一个代码 TODO",
    "修改一段论文表述",
)


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
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
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS ai_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                picker_skipped INTEGER NOT NULL DEFAULT 0
            );

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
        ai_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(ai_sessions)").fetchall()
        }
        if "picker_skipped" not in ai_columns:
            self._connection.execute(
                "ALTER TABLE ai_sessions ADD COLUMN picker_skipped INTEGER NOT NULL DEFAULT 0"
            )
        self._connection.commit()

    def add_manual_task(self, title: str) -> Task:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("任务名称不能为空")
        next_order = self._connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open'"
        ).fetchone()[0]
        cursor = self._connection.execute(
            "INSERT INTO tasks(title, status, sort_order, created_at) VALUES (?, 'open', ?, ?)",
            (clean_title, next_order, to_iso(utc_now())),
        )
        self._connection.commit()
        return Task(cursor.lastrowid, clean_title, TaskKind.MANUAL, next_order)

    def list_manual_tasks(self) -> list[Task]:
        rows = self._connection.execute(
            "SELECT id, title, sort_order FROM tasks WHERE status = 'open' ORDER BY sort_order, id"
        ).fetchall()
        return [Task(row["id"], row["title"], TaskKind.MANUAL, row["sort_order"]) for row in rows]

    def complete_manual_task(self, task_id: int, when: datetime | None = None) -> None:
        self._connection.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'open'",
            (to_iso(when or utc_now()), task_id),
        )
        self._connection.commit()

    def delete_manual_task(self, task_id: int) -> None:
        self._connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._connection.commit()

    def suggested_tasks(self, limit: int = 3) -> list[Task]:
        manual_tasks = self.list_manual_tasks()
        if manual_tasks:
            return manual_tasks[:limit]
        order = [entry.title for entry in self.default_task_entries() if entry.enabled]
        return [
            Task(None, title, TaskKind.DEFAULT, offset)
            for offset, title in enumerate(order[:limit])
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
            try:
                stored = json.loads(raw)
            except json.JSONDecodeError:
                stored = None
            if isinstance(stored, list):
                entries: list[DefaultTaskEntry] = []
                seen: set[str] = set()
                for item in stored:
                    if not isinstance(item, dict):
                        continue
                    title = " ".join(str(item.get("title") or "").strip().split())
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    entries.append(DefaultTaskEntry(title, bool(item.get("enabled", True))))
                return entries
        return [DefaultTaskEntry(title, True) for title in self._default_task_order()]

    def set_default_task_entries(self, entries: list[DefaultTaskEntry]) -> None:
        cleaned: list[dict[str, object]] = []
        seen: set[str] = set()
        for entry in entries:
            title = " ".join(entry.title.strip().split())
            if not title or title in seen:
                continue
            seen.add(title)
            cleaned.append({"title": title, "enabled": bool(entry.enabled)})
        self.set_setting("default_tasks_v2", json.dumps(cleaned, ensure_ascii=False))

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
        if self.get_open_focus() is not None:
            raise RuntimeError("已有正在进行的微任务")
        started_at = when or utc_now()
        cursor = self._connection.execute(
            """
            INSERT INTO focus_sessions(
                task_id, task_title, task_kind, started_at, last_heartbeat_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.title,
                task.kind.value,
                to_iso(started_at),
                to_iso(started_at),
            ),
        )
        self._connection.commit()
        return FocusSession(
            cursor.lastrowid,
            task,
            started_at,
            last_heartbeat_at=started_at,
        )

    def get_open_focus(self) -> FocusSession | None:
        row = self._connection.execute(
            "SELECT * FROM focus_sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        task_id = row["task_id"]
        task = Task(task_id, row["task_title"], TaskKind(row["task_kind"]))
        return FocusSession(
            id=row["id"],
            task=task,
            started_at=from_iso(row["started_at"]),
            paused_seconds=float(row["paused_seconds"]),
            paused_at=from_iso(row["paused_at"]),
            last_heartbeat_at=from_iso(row["last_heartbeat_at"]),
        )

    def save_focus_pause(self, session: FocusSession) -> None:
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
            """
            UPDATE focus_sessions
            SET ended_at = ?, paused_seconds = ?, paused_at = NULL, outcome = ?
            WHERE id = ?
            """,
            (to_iso(ended_at), paused_seconds, outcome.value, session.id),
        )
        self._connection.commit()

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
            INSERT INTO ai_sessions(session_id, turn_id, started_at, status)
            VALUES (?, ?, ?, 'running')
            """,
            (session_id, turn_id, to_iso(started_at)),
        )
        self._connection.commit()
        return AiSession(cursor.lastrowid, session_id, turn_id, started_at)

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
        return AiSession(
            row["id"],
            row["session_id"],
            row["turn_id"],
            from_iso(row["started_at"]),
            from_iso(row["ended_at"]),
            row["status"],
            bool(row["picker_skipped"]),
        )

    def get_ai_session(self, turn_id: str) -> AiSession | None:
        row = self._connection.execute(
            "SELECT * FROM ai_sessions WHERE turn_id = ? ORDER BY id DESC LIMIT 1",
            (turn_id,),
        ).fetchone()
        if row is None:
            return None
        return AiSession(
            row["id"],
            row["session_id"],
            row["turn_id"],
            from_iso(row["started_at"]),
            from_iso(row["ended_at"]),
            row["status"],
            bool(row["picker_skipped"]),
        )

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
        self._connection.execute(
            "UPDATE ai_sessions SET ended_at = ?, status = ? WHERE id = ?",
            (to_iso(ended_at), status, session.id),
        )
        self._connection.commit()
        session.ended_at = ended_at
        session.status = status
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
    ) -> AiSession | None:
        session = self.get_open_ai(turn_id=turn_id)
        if session is None and fallback_latest:
            session = self.get_open_ai()
        if session is None:
            return None
        self._connection.execute(
            "UPDATE ai_sessions SET status = ? WHERE id = ? AND ended_at IS NULL",
            (status, session.id),
        )
        self._connection.commit()
        session.status = status
        return session

    def today_focus_seconds(self, now: datetime | None = None) -> float:
        current = now or utc_now()
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self._connection.execute(
            """
            SELECT started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE ended_at IS NOT NULL AND started_at >= ?
            """,
            (to_iso(day_start),),
        ).fetchall()
        total = 0.0
        for row in rows:
            total += max(
                0.0,
                (from_iso(row["ended_at"]) - from_iso(row["started_at"])).total_seconds()
                - float(row["paused_seconds"]),
            )
        return total

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._connection.execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._connection.commit()
