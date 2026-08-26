from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

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


@dataclass(frozen=True, slots=True)
class _TurnRow:
    thread_id: str
    turn_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None


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


class DesktopActivityReader:
    """Read only Codex turn lifecycle fields from the desktop app's local database.

    The query intentionally excludes messages, titles, paths, error payloads, and item JSON.
    """

    def __init__(
        self,
        database: str | Path | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = Path(database) if database is not None else default_thread_history_path()
        self._now = now
        self._known_statuses: dict[tuple[str, str], str] = {}
        self._initialized = False
        self.available = False
        self.error: str | None = None
        self.last_poll_at: datetime | None = None

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
        current = {(row.thread_id, row.turn_id): row.status for row in rows}

        if not self._initialized:
            self._initialized = True
            self._known_statuses = current
            return [
                self._event_for(row, DesktopEventKind.STARTED, row.started_at)
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

        self._known_statuses = current
        events.sort(key=lambda event: event.occurred_at)
        return events

    def _read_rows(self) -> list[_TurnRow]:
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        uri = f"file:{self.database.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.25) as connection:
            connection.execute("PRAGMA query_only=ON")
            raw_rows = connection.execute(
                """
                SELECT thread_id, turn_id, status, started_at, completed_at
                FROM thread_turns
                ORDER BY started_at ASC
                """
            ).fetchall()

        rows: list[_TurnRow] = []
        for thread_id, turn_id, status, started_at, completed_at in raw_rows:
            parsed_started_at = _from_epoch(started_at)
            if parsed_started_at is None:
                continue
            rows.append(
                _TurnRow(
                    thread_id=str(thread_id),
                    turn_id=str(turn_id),
                    status=str(status),
                    started_at=parsed_started_at,
                    completed_at=_from_epoch(completed_at),
                )
            )
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
    ) -> DesktopActivityEvent:
        return DesktopActivityEvent(
            kind=kind,
            thread_id=row.thread_id,
            turn_id=row.turn_id,
            status=row.status,
            started_at=row.started_at,
            occurred_at=occurred_at,
        )
