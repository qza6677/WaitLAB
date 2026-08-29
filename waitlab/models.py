from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class TaskKind(StrEnum):
    MANUAL = "manual"
    DEFAULT = "default"


DEFAULT_TAG = "未分类"


class FocusOutcome(StrEnum):
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class Task:
    id: int | None
    title: str
    kind: TaskKind
    sort_order: int = 0
    tag: str = DEFAULT_TAG


@dataclass(frozen=True, slots=True)
class DefaultTaskEntry:
    title: str
    enabled: bool = True
    tag: str = DEFAULT_TAG


@dataclass(slots=True)
class FocusSession:
    id: int
    task: Task
    started_at: datetime
    paused_seconds: float = 0.0
    paused_at: datetime | None = None
    last_heartbeat_at: datetime | None = None

    @property
    def is_paused(self) -> bool:
        return self.paused_at is not None

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        endpoint = self.paused_at or now or utc_now()
        elapsed = (endpoint - self.started_at).total_seconds() - self.paused_seconds
        return max(0.0, elapsed)


@dataclass(frozen=True, slots=True)
class CompletedTaskSummary:
    """A compact daily roll-up used by the picker history list."""

    task_id: int | None
    title: str
    kind: TaskKind
    total_seconds: float
    completed_count: int
    last_completed_at: datetime
    tag: str = DEFAULT_TAG


@dataclass(frozen=True, slots=True)
class CompletedFocusRecord:
    """One completed Waiting Task focus segment shown in the expandable history."""

    id: int
    task_id: int | None
    title: str
    kind: TaskKind
    tag: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class TagTimeBucket:
    """Waiting Task time accumulated by tag within one local calendar day."""

    start: datetime
    end: datetime
    tag_seconds: dict[str, float]


@dataclass(slots=True)
class AiSession:
    id: int
    session_id: str
    turn_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str = "running"
    picker_skipped: bool = False
    active_seconds: float = 0.0
    running_since: datetime | None = None

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        endpoint = self.ended_at or now or utc_now()
        return max(0.0, (endpoint - self.started_at).total_seconds())

    def active_elapsed_seconds(self, now: datetime | None = None) -> float:
        """Return only the time spent in an explicitly running state."""

        total = max(0.0, float(self.active_seconds))
        if self.running_since is not None and self.ended_at is None:
            endpoint = now or utc_now()
            total += max(0.0, (endpoint - self.running_since).total_seconds())
        return total


@dataclass(frozen=True, slots=True)
class ServiceUpdate:
    show_task_picker: bool = False
    ai_completed: bool = False
    ai_blocked: bool = False
    ai_needs_attention: bool = False
    ai_resumed: bool = False
    focus_changed: bool = False
    message: str | None = None
    # Lifecycle identity is useful to the UI for de-duplicating completion
    # reminders.  It deliberately carries no duration information: Codex is
    # an event source, while Waiting Task is the only timed activity.
    ai_turn_id: str | None = None
    ai_status: str | None = None
