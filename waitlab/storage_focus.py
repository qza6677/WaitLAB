"""Focus session and history repository for the SQLite store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import sqlite3

from .models import (
    DefaultTaskEntry,
    DEFAULT_TAG,
    FocusOutcome,
    FocusSession,
    Task,
    TaskKind,
    from_iso,
    to_iso,
    utc_now,
)


class FocusRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        complete_manual_task_uncommitted: Callable[[int, datetime], None],
        default_task_entries: Callable[[], list[DefaultTaskEntry]],
        set_default_task_entries_uncommitted: Callable[[list[DefaultTaskEntry]], None],
        normalize_tag: Callable[[str | None], str],
    ) -> None:
        self._connection = connection
        self._complete_manual_task = complete_manual_task_uncommitted
        self._default_task_entries = default_task_entries
        self._set_default_task_entries = set_default_task_entries_uncommitted
        self._normalize_tag = normalize_tag

    def start_focus(self, task: Task, when: datetime | None = None) -> FocusSession:
        if self.get_running_focus() is not None:
            raise RuntimeError("已有正在进行的微任务")
        started_at = when or utc_now()
        with self._connection:
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
                    self._normalize_tag(task.tag),
                    to_iso(started_at),
                    to_iso(started_at),
                ),
            )
            self._connection.execute(
                "INSERT INTO focus_segments(focus_session_id, started_at) VALUES (?, ?)",
                (cursor.lastrowid, to_iso(started_at)),
            )
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
        with self._connection:
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

    def heartbeat_focus(
        self,
        session: FocusSession,
        when: datetime | None = None,
    ) -> None:
        if session.paused_at is not None:
            return
        heartbeat_at = when or utc_now()
        session.last_heartbeat_at = heartbeat_at
        with self._connection:
            self._connection.execute(
                "UPDATE focus_sessions SET last_heartbeat_at = ? WHERE id = ? AND ended_at IS NULL",
                (to_iso(heartbeat_at), session.id),
            )

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
        with self._connection:
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
                    self._complete_manual_task(session.task.id, ended_at)
                elif (
                    outcome is FocusOutcome.COMPLETED
                    and session.task.kind is TaskKind.DEFAULT
                ):
                    entries = self._default_task_entries()
                    enabled_titles = [entry.title for entry in entries if entry.enabled]
                    if enabled_titles:
                        selected = session.task.title if session.task.title in enabled_titles else enabled_titles[0]
                        selected_entry = next(entry for entry in entries if entry.title == selected)
                        entries.remove(selected_entry)
                        entries.append(selected_entry)
                        self._set_default_task_entries(entries)
        except Exception:
            self._connection.rollback()
            raise

    def update_completed_focus_end_time(
        self,
        session_id: int,
        ended_at: datetime,
    ) -> bool:
        """Shorten one completed focus record and keep its segments consistent.

        Editing is intentionally limited to moving the endpoint backwards. A
        later endpoint would invent activity that was never observed. Segment
        rows are truncated/deleted in the same transaction and the pause
        total is recomputed from the surviving active intervals.
        """

        new_end = ended_at.astimezone()
        now = utc_now()
        if new_end > now:
            raise ValueError("结束时间不能晚于当前时间")

        row = self._connection.execute(
            """
            SELECT id, task_id, started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE id = ? AND outcome = 'completed' AND ended_at IS NOT NULL
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        started_at = from_iso(row["started_at"])
        previous_end = from_iso(row["ended_at"])
        if started_at is None or previous_end is None:
            return False
        if new_end < started_at:
            raise ValueError("结束时间不能早于开始时间")
        if new_end > previous_end:
            raise ValueError("结束时间只能向前调整")

        new_end_iso = to_iso(new_end)
        previous_end_iso = to_iso(previous_end)
        segments = self._connection.execute(
            """
            SELECT id, started_at, ended_at
            FROM focus_segments
            WHERE focus_session_id = ?
            ORDER BY started_at, id
            """,
            (session_id,),
        ).fetchall()
        wall_seconds = max(0.0, (new_end - started_at).total_seconds())
        active_seconds = 0.0
        segment_updates: list[tuple[str, int]] = []
        segment_deletes: list[int] = []
        for segment in segments:
            segment_start = from_iso(segment["started_at"])
            if segment_start is None or segment_start >= new_end:
                segment_deletes.append(int(segment["id"]))
                continue
            segment_end = from_iso(segment["ended_at"]) or previous_end
            bounded_end = min(segment_end, new_end)
            if bounded_end <= segment_start:
                segment_deletes.append(int(segment["id"]))
                continue
            active_seconds += (bounded_end - segment_start).total_seconds()
            if segment["ended_at"] is None or bounded_end != segment_end:
                segment_updates.append((to_iso(bounded_end) or new_end_iso or "", int(segment["id"])))

        paused_seconds = max(0.0, wall_seconds - min(wall_seconds, active_seconds))
        if not segments:
            # Legacy records may not have focus_segments. Preserve their pause
            # estimate while ensuring it cannot exceed the corrected wall time.
            paused_seconds = min(
                wall_seconds,
                max(0.0, float(row["paused_seconds"] or 0.0)),
            )

        try:
            with self._connection:
                for segment_id in segment_deletes:
                    self._connection.execute(
                        "DELETE FROM focus_segments WHERE id = ? AND focus_session_id = ?",
                        (segment_id, session_id),
                    )
                for updated_end_iso, segment_id in segment_updates:
                    self._connection.execute(
                        "UPDATE focus_segments SET ended_at = ? WHERE id = ? AND focus_session_id = ?",
                        (updated_end_iso, segment_id, session_id),
                    )
                self._connection.execute(
                    """
                    UPDATE focus_sessions
                    SET ended_at = ?, paused_seconds = ?, paused_at = NULL
                    WHERE id = ? AND outcome = 'completed'
                    """,
                    (new_end_iso, paused_seconds, session_id),
                )
                task_id = row["task_id"]
                if task_id is not None:
                    self._connection.execute(
                        """
                        UPDATE tasks
                        SET completed_at = ?
                        WHERE id = ? AND status = 'completed' AND completed_at = ?
                        """,
                        (new_end_iso, int(task_id), previous_end_iso),
                    )
            return True
        except Exception:
            self._connection.rollback()
            raise

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

    def clear_focus_history(self) -> int:
        """Permanently remove terminal Waiting Task history.
    
        Open sessions are deliberately excluded so a running or paused task
        survives a history clear.  The short-lived single-record undo archive
        is cleared as well because it is part of the same local history.
        Return the number of focus session records removed from both stores.
        """
    
        try:
            with self._connection:
                rows = self._connection.execute(
                    """
                    SELECT id FROM focus_sessions
                    WHERE ended_at IS NOT NULL
                      AND outcome IN ('completed', 'abandoned')
                    """
                ).fetchall()
                archived_rows = self._connection.execute(
                    "SELECT id FROM deleted_focus_sessions"
                ).fetchall()
                ids = [int(row[0]) for row in rows]
                removed_count = len(ids) + len(archived_rows)
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    self._connection.execute(
                        f"DELETE FROM focus_segments WHERE focus_session_id IN ({placeholders})",
                        ids,
                    )
                    self._connection.execute(
                        f"DELETE FROM focus_sessions WHERE id IN ({placeholders})",
                        ids,
                    )
                self._connection.execute("DELETE FROM deleted_focus_segments")
                self._connection.execute("DELETE FROM deleted_focus_sessions")
            return removed_count
        except Exception:
            self._connection.rollback()
            raise
