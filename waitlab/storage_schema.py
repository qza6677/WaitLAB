"""SQLite schema bootstrap for WaitLAB.

Schema creation is kept separate from the Storage facade. Upgrade-specific
backfills still live in Storage until they are migrated behind repositories.
"""

from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
                ,tag TEXT NOT NULL DEFAULT '\u672a\u5206\u7c7b'
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
                task_tag TEXT NOT NULL DEFAULT '\u672a\u5206\u7c7b',
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
                task_tag TEXT NOT NULL DEFAULT '\u672a\u5206\u7c7b',
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


def create_base_schema(connection: sqlite3.Connection) -> None:
    """Create tables and baseline indexes without changing existing data."""

    connection.executescript(SCHEMA_SQL)

