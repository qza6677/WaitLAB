from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .models import utc_now


class DesktopEventKind(StrEnum):
    STARTED = "started"
    NEEDS_ATTENTION = "needs_attention"
    RESUMED = "resumed"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class DesktopActivityEvent:
    kind: DesktopEventKind
    thread_id: str
    turn_id: str
    status: str
    started_at: datetime
    occurred_at: datetime
    # ``True`` means this was observed during the reader's first poll rather
    # than caused by a new status transition.  The UI can use that distinction
    # to avoid replaying an old inProgress row as a fresh prompt.
    initial: bool = False


@dataclass(frozen=True, slots=True)
class DesktopTurnSnapshot:
    """Latest lifecycle metadata for one Codex turn.

    ``last_activity_at`` comes from item timestamps only; item contents are
    intentionally never read.
    """

    thread_id: str
    turn_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    last_activity_at: datetime | None


@dataclass(frozen=True, slots=True)
class _TurnRow:
    thread_id: str
    turn_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    last_activity_at: datetime | None


def default_thread_history_path() -> Path:
    return Path.home() / ".codex" / "thread_history_1.sqlite"


def _from_epoch(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    timestamp = float(value)
    while timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _normal_status(status: str) -> str:
    return "".join(character for character in status.casefold() if character.isalnum())


RUNNING_STATUSES = {"inprogress", "running"}
ATTENTION_STATUSES = {
    "needsattention",
    "needsinput",
    "needsapproval",
    "waitingforinput",
    "waitingforapproval",
}
COMPLETED_STATUSES = {"completed", "succeeded", "success"}
BLOCKED_STATUSES = {"failed", "interrupted", "cancelled", "canceled", "blocked"}
TERMINAL_STATUSES = COMPLETED_STATUSES | BLOCKED_STATUSES


class DesktopActivityReader:
    """Read only Codex turn lifecycle fields from the desktop app's local database.

    The query intentionally excludes messages, titles, paths, error payloads, and item JSON.
    """

    def __init__(
        self,
        database: str | Path | None = None,
        now: Callable[[], datetime] = utc_now,
        lookback: timedelta = timedelta(days=30),
        max_rows: int = 512,
    ) -> None:
        self.database = Path(database) if database is not None else default_thread_history_path()
        self._now = now
        self._known_statuses: dict[tuple[str, str], str] = {}
        self._initialized = False
        self.available = False
        self.error: str | None = None
        self.last_poll_at: datetime | None = None
        self._snapshot: dict[tuple[str, str], DesktopTurnSnapshot] = {}
        self.lookback = lookback
        self.max_rows = max(32, int(max_rows))
        self._last_row_id = 0

    def poll(self) -> list[DesktopActivityEvent]:
        try:
            rows = self._read_rows()
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.available = False
            self.error = f"{type(exc).__name__}: {exc}"
            self.last_poll_at = self._now()
            return []

        self.available = True
        self.error = None
        self.last_poll_at = self._now()
        self._snapshot = {
            (row.thread_id, row.turn_id): DesktopTurnSnapshot(
                thread_id=row.thread_id,
                turn_id=row.turn_id,
                status=row.status,
                started_at=row.started_at,
                completed_at=row.completed_at,
                last_activity_at=row.last_activity_at,
            )
            for row in rows
        }
        if not self._initialized:
            self._initialized = True
            self._known_statuses = self._tracked_statuses(rows)
            return [
                self._event_for(
                    row,
                    DesktopEventKind.STARTED,
                    self.last_poll_at or row.started_at,
                    initial=True,
                )
                for row in rows
                if _normal_status(row.status) in RUNNING_STATUSES
            ]

        events: list[DesktopActivityEvent] = []
        for row in rows:
            key = (row.thread_id, row.turn_id)
            previous = self._known_statuses.get(key)
            if previous == row.status:
                continue
            kind = self._event_kind(row.status, previous)
            if kind is None:
                continue
            occurred_at = (
                row.completed_at
                if kind in {DesktopEventKind.COMPLETED, DesktopEventKind.BLOCKED}
                else self.last_poll_at
                if kind in {DesktopEventKind.NEEDS_ATTENTION, DesktopEventKind.RESUMED}
                else row.started_at
            )
            events.append(self._event_for(row, kind, occurred_at or self.last_poll_at))

        self._known_statuses = self._tracked_statuses(rows)
        events.sort(key=lambda event: event.occurred_at)
        return events

    def _tracked_statuses(self, rows: list[_TurnRow]) -> dict[tuple[str, str], str]:
        """Keep a bounded cache of statuses needed for transition detection.

        Recent terminal rows must remain cached long enough to avoid replaying
        the same completion on every poll.  Active rows are prioritised, and
        the bounded cache prevents the compatibility OR-clause in ``_read_rows``
        from growing with months of Codex history.
        """

        tracked: dict[tuple[str, str], str] = {}
        for row in rows:
            if _normal_status(row.status) not in TERMINAL_STATUSES:
                tracked[(row.thread_id, row.turn_id)] = row.status
                if len(tracked) >= self.max_rows:
                    return tracked
        for row in rows:
            if len(tracked) >= self.max_rows:
                break
            key = (row.thread_id, row.turn_id)
            tracked.setdefault(key, row.status)
        return tracked

    def status_snapshot(self) -> tuple[DesktopTurnSnapshot, ...]:
        """Return the latest read-only lifecycle snapshot for reconciliation."""

        return tuple(self._snapshot.values())

    def _read_rows(self) -> list[_TurnRow]:
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        uri = f"file:{self.database.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.25) as connection:
            connection.execute("PRAGMA query_only=ON")
            latest_row_id = int(
                connection.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM thread_turns"
                ).fetchone()[0]
            )
            known_keys = list(self._known_statuses)
            key_clause = ""
            key_params: list[object] = []
            if known_keys:
                key_clause = " OR " + " OR ".join(
                    "(t.thread_id = ? AND t.turn_id = ?)" for _ in known_keys
                )
                for thread_id, turn_id in known_keys:
                    key_params.extend((thread_id, turn_id))

            # The first poll seeds a bounded recent snapshot. Subsequent polls
            # are incremental: new rowids plus currently tracked lifecycle
            # rows. This avoids rescanning every recent terminal turn.
            if not self._initialized:
                cutoff_epoch = (self._now() - self.lookback).timestamp()
                where_clause = (
                    "(t.started_at >= ? OR lower(t.status) IN "
                    "('inprogress', 'running', 'needsattention', 'needsinput', "
                    "'needsapproval', 'waitingforinput', 'waitingforapproval')"
                    f"{key_clause})"
                )
                query_params: list[object] = [cutoff_epoch, *key_params, self.max_rows]
            else:
                where_clause = (
                    "(t.rowid > ? OR lower(t.status) IN "
                    "('inprogress', 'running', 'needsattention', 'needsinput', "
                    "'needsapproval', 'waitingforinput', 'waitingforapproval')"
                    f"{key_clause})"
                )
                query_params = [self._last_row_id, *key_params, self.max_rows]

            raw_rows = connection.execute(
                f"""
                SELECT rowid AS row_id, thread_id, turn_id, status, started_at, completed_at
                FROM thread_turns AS t
                WHERE {where_clause}
                ORDER BY t.started_at DESC, t.rowid DESC
                LIMIT ?
                """,
                query_params,
            ).fetchall()

            # Item contents are never read. We only ask for timestamps for
            # active rows, and only after the cheap lifecycle query found them.
            item_activity: dict[tuple[str, str], int | float] = {}
            active_keys = [
                (str(row[1]), str(row[2]))
                for row in raw_rows
                if _normal_status(str(row[3]))
                in RUNNING_STATUSES | ATTENTION_STATUSES
            ]
            if active_keys:
                item_clause = " OR ".join(
                    "(thread_id = ? AND turn_id = ?)" for _ in active_keys
                )
                item_params: list[object] = []
                for thread_id, turn_id in active_keys:
                    item_params.extend((thread_id, turn_id))
                try:
                    item_rows = connection.execute(
                        f"""
                        SELECT thread_id, turn_id, MAX(created_at_ms)
                        FROM thread_items
                        WHERE {item_clause}
                        GROUP BY thread_id, turn_id
                        """,
                        item_params,
                    ).fetchall()
                except sqlite3.OperationalError:
                    # Older Codex databases may not expose thread_items.
                    item_rows = []
                item_activity = {
                    (str(row[0]), str(row[1])): row[2]
                    for row in item_rows
                    if row[2] is not None
                }

        rows: list[_TurnRow] = []
        for raw_row in raw_rows:
            if len(raw_row) == 6:
                row_id, thread_id, turn_id, status, started_at, completed_at = raw_row
            else:
                thread_id, turn_id, status, started_at, completed_at = raw_row
                row_id = None
            if row_id is not None:
                self._last_row_id = max(self._last_row_id, int(row_id))
            parsed_started_at = _from_epoch(started_at)
            if parsed_started_at is None:
                continue
            key = (str(thread_id), str(turn_id))
            rows.append(
                _TurnRow(
                    thread_id=key[0],
                    turn_id=key[1],
                    status=str(status),
                    started_at=parsed_started_at,
                    completed_at=_from_epoch(completed_at),
                    last_activity_at=_from_epoch(item_activity.get(key)),
                )
            )
        # Advance the watermark to the database head, not merely the last row
        # in the bounded result set. This prevents a large initial history from
        # being selected repeatedly on every poll.
        self._last_row_id = max(self._last_row_id, latest_row_id)
        return rows

    @staticmethod
    def _event_kind(status: str, previous: str | None) -> DesktopEventKind | None:
        normalized = _normal_status(status)
        previous_normalized = _normal_status(previous or "")
        if normalized in RUNNING_STATUSES:
            if previous_normalized in ATTENTION_STATUSES:
                return DesktopEventKind.RESUMED
            return DesktopEventKind.STARTED
        if normalized in ATTENTION_STATUSES:
            return DesktopEventKind.NEEDS_ATTENTION
        if normalized in COMPLETED_STATUSES:
            return DesktopEventKind.COMPLETED
        if normalized in BLOCKED_STATUSES:
            return DesktopEventKind.BLOCKED
        return None

    @staticmethod
    def _event_for(
        row: _TurnRow,
        kind: DesktopEventKind,
        occurred_at: datetime,
        initial: bool = False,
    ) -> DesktopActivityEvent:
        return DesktopActivityEvent(
            kind=kind,
            thread_id=row.thread_id,
            turn_id=row.turn_id,
            status=row.status,
            started_at=row.started_at,
            occurred_at=occurred_at,
            initial=initial,
        )


class DesktopActivityWorker(QObject):
    """Poll the Codex history database away from the GUI thread."""

    poll_ready = Signal(object, object, object, object, object)
    stop_requested = Signal()

    def __init__(self, reader: DesktopActivityReader, interval_ms: int = 750) -> None:
        super().__init__()
        self.reader = reader
        self.interval_ms = max(250, int(interval_ms))
        # Idle Codex periods do not need sub-second polling. Return to the
        # fast interval as soon as an active/attention turn is observed.
        self.idle_interval_ms = max(self.interval_ms, self.interval_ms * 2)
        self._timer: QTimer | None = None
        self.stop_requested.connect(self.stop)

    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self.poll_once)
        self._timer.start()
        self.poll_once()

    def poll_once(self) -> None:
        events = self.reader.poll()
        snapshots = self.reader.status_snapshot()
        if self._timer is not None:
            active = any(
                _normal_status(snapshot.status) in RUNNING_STATUSES | ATTENTION_STATUSES
                for snapshot in snapshots
            )
            target_interval = self.interval_ms if active else self.idle_interval_ms
            if self._timer.interval() != target_interval:
                self._timer.setInterval(target_interval)
        self.poll_ready.emit(
            events,
            self.reader.available,
            self.reader.error,
            self.reader.database,
            snapshots,
        )

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        thread = self.thread()
        if thread is not None:
            thread.quit()
