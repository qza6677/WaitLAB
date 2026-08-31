"""AI session lifecycle repository for the SQLite store."""

from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3

from .models import AiSession, from_iso, to_iso, utc_now
from .storage_defaults import AI_RUNNING_STATUSES


class AiRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        track_ai_time: bool,
    ) -> None:
        self._connection = connection
        self._track_ai_time = bool(track_ai_time)

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
        with self._connection:
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
                    to_iso(started_at) if self._track_ai_time else None,
                ),
            )
            if self._track_ai_time:
                self._connection.execute(
                    "INSERT INTO ai_activity_segments(ai_session_id, state, started_at) VALUES (?, 'running', ?)",
                    (cursor.lastrowid, to_iso(started_at)),
                )
        ai_id = cursor.lastrowid
        if ai_id is None:
            raise RuntimeError("无法创建 Codex 会话记录")
        return AiSession(
            int(ai_id),
            session_id,
            turn_id,
            started_at,
            active_seconds=0.0,
            running_since=started_at if self._track_ai_time else None,
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
        with self._connection:
            if self._track_ai_time:
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
        session.ended_at = ended_at
        session.status = status
        if self._track_ai_time:
            session.active_seconds = active_seconds
        session.running_since = None
        return session

    def skip_ai_picker(self, turn_id: str) -> AiSession | None:
        session = self.get_open_ai(turn_id=turn_id)
        if session is None:
            return None
        with self._connection:
            self._connection.execute(
                "UPDATE ai_sessions SET picker_skipped = 1 WHERE id = ? AND ended_at IS NULL",
                (session.id,),
            )
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
        if self._track_ai_time and session.running_since is not None and session.ended_at is None:
            active_seconds += max(
                0.0,
                (changed_at - session.running_since).total_seconds(),
            )
        running_since = (
            changed_at
            if self._track_ai_time and status.casefold() in AI_RUNNING_STATUSES
            else None
        )
        with self._connection:
            if self._track_ai_time:
                self._connection.execute(
                    "UPDATE ai_activity_segments SET ended_at = ? WHERE ai_session_id = ? AND ended_at IS NULL",
                    (to_iso(changed_at), session.id),
                )
            if self._track_ai_time and running_since is not None:
                self._connection.execute(
                    "INSERT INTO ai_activity_segments(ai_session_id, state, started_at) VALUES (?, 'running', ?)",
                    (session.id, to_iso(changed_at)),
                )
            if self._track_ai_time:
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
        session.status = status
        if self._track_ai_time:
            session.active_seconds = active_seconds
        session.running_since = running_since
        return session
