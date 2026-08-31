from __future__ import annotations

import random as random
from datetime import datetime

from .desktop_activity import (
    BLOCKED_STATUSES as BLOCKED_STATUSES,
    COMPLETED_STATUSES as COMPLETED_STATUSES,
    DesktopTurnSnapshot,
)
from .models import (
    AiSession,
    CompletedFocusRecord,
    CompletedTaskSummary,
    DEFAULT_TAG,
    DefaultTaskEntry,
    FocusSession,
    ServiceUpdate,
    TagTimeBucket,
    Task,
    TaskKind,
)
from .preferences import Preferences
from .storage import Storage
from .stats_cache import StatsCache
from .service_focus import FocusCoordinator
from .service_ai import AiCoordinator
from .service_policy import (
    AI_ATTENTION_STATUSES as AI_ATTENTION_STATUSES,
    AI_INITIAL_PROMPT_GRACE_SECONDS as AI_INITIAL_PROMPT_GRACE_SECONDS,
    AI_MISSING_AFTER_SECONDS as AI_MISSING_AFTER_SECONDS,
    AI_RUNNING_STATUSES as AI_RUNNING_STATUSES,
    AI_STALE_AFTER_SECONDS,
    MAX_REMEMBERED_TURNS as MAX_REMEMBERED_TURNS,
    STATS_CACHE_TTL_SECONDS,
    normal_status as _normal_status,  # noqa: F401 - compatibility export
)


class WaitLabService:
    """Pure application logic shared by the GUI, hooks, and tests."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        # Waiting Task is the only user-facing timer. Codex activity is kept
        # as a short-lived lifecycle signal and is intentionally excluded
        # from periodic statistics queries.
        # The visible clock updates every second, but aggregate statistics do
        # not need a database pass at that frequency. State-changing actions
        # invalidate this cache immediately; the short TTL keeps the home
        # summary fresh while avoiding repeated historical scans.
        self.stats_cache = StatsCache(
            storage,
            ttl_seconds=STATS_CACHE_TTL_SECONDS,
            include_codex=False,
        )
        # AI lifecycle rows belong to the process that observed them. Close
        # anything left by a previous process before the desktop reader starts
        # feeding us fresh transitions; otherwise an old ``inProgress`` row
        # can make Cookie look permanently busy after a restart.
        self.storage.close_open_ai_sessions(status="stale")
        self.focus_coordinator = FocusCoordinator(storage, self.stats_cache)
        self.has_recovered_focus = self.focus_coordinator.has_recovered_focus
        self.ai_coordinator = AiCoordinator(
            storage,
            self.stats_cache,
            self.has_active_focus,
            lambda: self.focus is not None,
        )
        self.last_ai_completion_seconds = None
        self.last_ai_terminal_status = None

    # ------------------------------------------------------------------
    # Application-facing queries and commands
    # ------------------------------------------------------------------
    # The GUI consumes these methods instead of reaching into Storage.  This
    # keeps persistence details behind the application boundary and gives us a
    # single place to add cache invalidation, validation, or telemetry later.

    def available_tags(self) -> list[str]:
        return self.storage.available_tags()

    def tag_usage_counts(self) -> dict[str, int]:
        return self.storage.tag_usage_counts()

    def add_tag(self, tag: str) -> str:
        return self.storage.add_tag(tag)

    def rename_tag(self, old_tag: str, new_tag: str) -> str:
        return self.storage.rename_tag(old_tag, new_tag)

    def delete_tags(self, tags: list[str]) -> int:
        return self.storage.delete_tags(tags)

    def default_task_entries(self) -> list[DefaultTaskEntry]:
        return self.storage.default_task_entries()

    def set_default_task_entries(self, entries: list[DefaultTaskEntry]) -> None:
        self.storage.set_default_task_entries(entries)

    def list_manual_tasks(self) -> list[Task]:
        return self.storage.list_manual_tasks()

    def add_manual_task(self, title: str, tag: str = DEFAULT_TAG) -> Task:
        return self.storage.add_manual_task(title, tag)

    def delete_manual_task(self, task_id: int) -> Task | None:
        return self.storage.delete_manual_task(task_id)

    def today_completed_tasks(
        self,
        now: datetime | None = None,
    ) -> list[CompletedTaskSummary]:
        return self.storage.today_completed_tasks(now)

    def completed_focus_records(
        self,
        task_id: int | None,
        title: str,
        kind: TaskKind,
        tag: str | None = None,
        now: datetime | None = None,
    ) -> list[CompletedFocusRecord]:
        return self.storage.completed_focus_records(task_id, title, kind, tag, now)

    def get_completed_focus_record(self, session_id: int) -> CompletedFocusRecord | None:
        return self.storage.get_completed_focus_record(session_id)

    def update_completed_focus_end_time(
        self,
        session_id: int,
        ended_at: datetime,
    ) -> bool:
        updated = self.storage.update_completed_focus_end_time(session_id, ended_at)
        if updated:
            self.stats_cache.invalidate()
        return updated

    def archive_focus_session(self, session_id: int) -> bool:
        return self.storage.archive_focus_session(session_id)

    def restore_archived_focus_session(self, session_id: int) -> bool:
        return self.storage.restore_archived_focus_session(session_id)

    def clear_focus_history(self) -> int:
        return self.storage.clear_focus_history()

    def tag_waiting_daily_series(
        self,
        period: str,
        now: datetime | None = None,
    ) -> list[TagTimeBucket]:
        return self.storage.tag_waiting_daily_series(period, now)

    def get_setting(self, key: str, default: str = "") -> str:
        return self.storage.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.storage.set_setting(key, value)

    def load_preferences(self) -> Preferences:
        return Preferences.load(self.storage)

    def save_preferences(self, preferences: Preferences) -> None:
        preferences.save(self.storage)

    @property
    def last_ai_completion_seconds(self) -> float | None:
        return self.ai_coordinator.last_ai_completion_seconds

    @last_ai_completion_seconds.setter
    def last_ai_completion_seconds(self, value: float | None) -> None:
        self.ai_coordinator.last_ai_completion_seconds = value

    @property
    def last_ai_terminal_status(self) -> str | None:
        return self.ai_coordinator.last_ai_terminal_status

    @last_ai_terminal_status.setter
    def last_ai_terminal_status(self, value: str | None) -> None:
        self.ai_coordinator.last_ai_terminal_status = value

    @property
    def focus(self) -> FocusSession | None:
        return self.focus_coordinator.focus

    @focus.setter
    def focus(self, value: FocusSession | None) -> None:
        self.focus_coordinator.focus = value

    @staticmethod
    def _read_focus_id(value: str) -> int | None:
        return FocusCoordinator._read_focus_id(value)


    def _persist_focus_selection(self, focus: FocusSession | None) -> None:
        self.focus_coordinator._persist_focus_selection(focus)


    def suggested_tasks(self) -> list[Task]:
        return self.focus_coordinator.suggested_tasks()


    def fixed_cycle_tasks(
        self,
        limit: int = 3,
        *,
        randomize: bool = False,
    ) -> list[Task]:
        return self.focus_coordinator.fixed_cycle_tasks(limit, randomize=randomize)


    def has_active_focus(self) -> bool:
        return self.focus_coordinator.has_active_focus()


    def paused_focuses(self) -> list[FocusSession]:
        return self.focus_coordinator.paused_focuses()


    def open_focuses(self) -> list[FocusSession]:
        return self.focus_coordinator.open_focuses()


    def _resume_focus_session(
        self,
        session: FocusSession,
        current: datetime,
    ) -> None:
        self.focus_coordinator._resume_focus_session(session, current)


    def open_ai_sessions(self) -> list[AiSession]:
        return self.ai_coordinator.open_ai_sessions()


    def running_ai_sessions(self) -> list[AiSession]:
        return self.ai_coordinator.running_ai_sessions()


    def attention_ai_sessions(self) -> list[AiSession]:
        return self.ai_coordinator.attention_ai_sessions()


    def on_ai_started(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
        show_task_picker: bool = True,
    ) -> ServiceUpdate:
        return self.ai_coordinator.on_ai_started(session_id, turn_id, when, show_task_picker)


    def on_ai_finished(
        self,
        turn_id: str,
        when: datetime | None = None,
        status: str = "completed",
        fallback_latest: bool = True,
        session_id: str | None = None,
        started_at: datetime | None = None,
        create_if_missing: bool = False,
    ) -> ServiceUpdate:
        return self.ai_coordinator.on_ai_finished(
            turn_id,
            when,
            status,
            fallback_latest,
            session_id,
            started_at,
            create_if_missing,
        )


    def on_ai_needs_attention(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
        fallback_latest: bool = True,
    ) -> ServiceUpdate:
        return self.ai_coordinator.on_ai_needs_attention(
            session_id,
            turn_id,
            when,
            fallback_latest,
        )


    def on_ai_resumed(
        self,
        turn_id: str,
        fallback_latest: bool = True,
        when: datetime | None = None,
    ) -> ServiceUpdate:
        return self.ai_coordinator.on_ai_resumed(turn_id, fallback_latest, when)


    def reconcile_desktop_sessions(
        self,
        snapshots: tuple[DesktopTurnSnapshot, ...],
        now: datetime | None = None,
        stale_after_seconds: float = AI_STALE_AFTER_SECONDS,
    ) -> ServiceUpdate:
        return self.ai_coordinator.reconcile_desktop_sessions(
            snapshots,
            now,
            stale_after_seconds,
        )


    def start_focus(self, task: Task, when: datetime | None = None) -> ServiceUpdate:
        return self.focus_coordinator.start_focus(task, when)


    def toggle_focus_pause(self, when: datetime | None = None) -> ServiceUpdate:
        return self.focus_coordinator.toggle_focus_pause(when)


    def pause_focus(
        self,
        when: datetime | None = None,
        message: str = "微任务已暂停",
    ) -> ServiceUpdate:
        return self.focus_coordinator.pause_focus(when, message)


    def resume_focus(
        self,
        when: datetime | None = None,
        message: str = "继续微任务",
    ) -> ServiceUpdate:
        return self.focus_coordinator.resume_focus(when, message)


    def heartbeat(self, when: datetime | None = None) -> None:
        self.focus_coordinator.heartbeat(when)


    def complete_focus(self, when: datetime | None = None) -> ServiceUpdate:
        return self.focus_coordinator.complete_focus(when)

    def abandon_focus(self, when: datetime | None = None) -> ServiceUpdate:
        return self.focus_coordinator.abandon_focus(when)

    def manual_ai_started(self, when: datetime | None = None) -> ServiceUpdate:
        return self.ai_coordinator.manual_ai_started(when)


    def manual_ai_finished(self, when: datetime | None = None) -> ServiceUpdate:
        return self.ai_coordinator.manual_ai_finished(when)


    def skip_current_ai_round(self) -> ServiceUpdate:
        return self.ai_coordinator.skip_current_ai_round()
