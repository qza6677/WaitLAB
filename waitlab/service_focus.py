"""Focus state and user-facing focus commands for the application layer."""

from __future__ import annotations

import random
from datetime import datetime

from .models import FocusOutcome, FocusSession, ServiceUpdate, Task, TaskKind, utc_now
from .stats_cache import StatsCache
from .storage import Storage


class FocusCoordinator:
    def __init__(self, storage: Storage, stats_cache: StatsCache) -> None:
        self.storage = storage
        self.stats_cache = stats_cache
        selected_focus_id = self._read_focus_id(
            storage.get_setting("active_focus_id", "")
        )
        running_before_recovery = storage.get_running_focus()
        open_focuses = storage.list_open_focuses()
        for session in open_focuses:
            if not session.is_paused:
                self.storage.recover_open_focus(session)
        open_focuses = storage.list_open_focuses()
        selected = next(
            (session for session in open_focuses if session.id == selected_focus_id),
            None,
        )
        if selected is None and running_before_recovery is not None:
            selected = next(
                (session for session in open_focuses if session.id == running_before_recovery.id),
                None,
            )
        self.focus: FocusSession | None = selected or (
            open_focuses[-1] if open_focuses else None
        )
        self._paused_focuses: dict[int, FocusSession] = {
            session.id: session
            for session in open_focuses
            if self.focus is None or session.id != self.focus.id
        }
        self._persist_focus_selection(self.focus)
        self.has_recovered_focus = bool(open_focuses)

    @staticmethod
    def _read_focus_id(value: str) -> int | None:
        try:
            focus_id = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return focus_id if focus_id > 0 else None

    def _persist_focus_selection(self, focus: FocusSession | None) -> None:
        self.storage.set_setting("active_focus_id", str(focus.id) if focus is not None else "")

    def suggested_tasks(self) -> list[Task]:
        return self.storage.suggested_tasks(limit=3)

    def fixed_cycle_tasks(
        self,
        limit: int = 3,
        *,
        randomize: bool = False,
    ) -> list[Task]:
        """Return enabled fixed-cycle tasks without manual-task fallback.
    
        The home picker and task switcher can both offer this queue alongside
        manual tasks.  Keeping the default-task conversion here avoids
        duplicating it in the UI.
    
        ``randomize`` is deliberately opt-in.  The UI uses it when opening a
        picker and then caches that small sample for the lifetime of the page
        so a one-second clock update cannot reshuffle the visible choices.
        """
    
        entries = [entry for entry in self.storage.default_task_entries() if entry.enabled]
        if randomize and len(entries) > limit:
            entries = random.sample(entries, limit)
        else:
            entries = entries[:limit]
        return [
            Task(None, entry.title, TaskKind.DEFAULT, offset, entry.tag)
            for offset, entry in enumerate(entries)
        ]

    def has_active_focus(self) -> bool:
        """Return whether the selected focus session is counting time."""
    
        return self.focus is not None and not self.focus.is_paused

    def paused_focuses(self) -> list[FocusSession]:
        """Return paused sessions, including the currently selected one."""
    
        sessions = list(self._paused_focuses.values())
        if self.focus is not None and self.focus.is_paused:
            sessions.append(self.focus)
        return sorted(sessions, key=lambda session: session.id, reverse=True)

    def open_focuses(self) -> list[FocusSession]:
        """Return every unfinished focus session for task-safety checks."""
    
        sessions = list(self._paused_focuses.values())
        if self.focus is not None:
            sessions.append(self.focus)
        return sorted(sessions, key=lambda session: session.id, reverse=True)

    def _resume_focus_session(
        self,
        session: FocusSession,
        current: datetime,
    ) -> None:
        if session.paused_at is None:
            return
        session.paused_seconds += max(
            0.0,
            (current - session.paused_at).total_seconds(),
        )
        session.paused_at = None
        session.last_heartbeat_at = current
        self.storage.save_focus_pause(session)

    def start_focus(self, task: Task, when: datetime | None = None) -> ServiceUpdate:
        if self.has_active_focus():
            raise RuntimeError("已有正在进行的微任务；请先暂停后再切换")
        current = when or utc_now()
        existing = self.storage.get_open_focus_for_task(task)
        if existing is not None:
            if self.focus is not None and self.focus.id != existing.id:
                if self.focus.is_paused:
                    self._paused_focuses[self.focus.id] = self.focus
            self._paused_focuses.pop(existing.id, None)
            self._resume_focus_session(existing, current)
            self.focus = existing
            message = f"继续：{task.title}"
        else:
            if self.focus is not None and self.focus.is_paused:
                self._paused_focuses[self.focus.id] = self.focus
            self.focus = self.storage.start_focus(task, when=current)
            message = f"开始：{task.title}"
        self._persist_focus_selection(self.focus)
        self.stats_cache.invalidate()
        return ServiceUpdate(focus_changed=True, message=message)

    def toggle_focus_pause(self, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is None:
            return ServiceUpdate()
        if self.focus.paused_at is None:
            return self.pause_focus(when=when)
        return self.resume_focus(when=when)

    def pause_focus(
        self,
        when: datetime | None = None,
        message: str = "微任务已暂停",
    ) -> ServiceUpdate:
        if self.focus is None or self.focus.paused_at is not None:
            return ServiceUpdate()
        current = when or utc_now()
        self.focus.paused_at = current
        self.focus.last_heartbeat_at = current
        self._persist_focus_selection(self.focus)
        self.storage.save_focus_pause(self.focus)
        self.stats_cache.invalidate()
        return ServiceUpdate(focus_changed=True, message=message)

    def resume_focus(
        self,
        when: datetime | None = None,
        message: str = "继续微任务",
    ) -> ServiceUpdate:
        if self.focus is None or self.focus.paused_at is None:
            return ServiceUpdate()
        current = when or utc_now()
        self._resume_focus_session(self.focus, current)
        self._persist_focus_selection(self.focus)
        self._paused_focuses.pop(self.focus.id, None)
        self.stats_cache.invalidate()
        return ServiceUpdate(focus_changed=True, message=message)

    def heartbeat(self, when: datetime | None = None) -> None:
        if self.focus is not None and not self.focus.is_paused:
            self.storage.heartbeat_focus(self.focus, when=when)

    def complete_focus(self, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is None:
            return ServiceUpdate()
        completed = self.focus
        self.storage.finish_focus_and_task(completed, FocusOutcome.COMPLETED, when=when)
        self.stats_cache.invalidate()
        self._persist_focus_selection(None)
        self.focus = None
        return ServiceUpdate(focus_changed=True, message="微任务完成，做得漂亮")

    def abandon_focus(self, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is None:
            return ServiceUpdate()
        abandoned = self.focus
        self.storage.finish_focus_and_task(abandoned, FocusOutcome.ABANDONED, when=when)
        self.stats_cache.invalidate()
        self._persist_focus_selection(None)
        self.focus = None
        return ServiceUpdate(focus_changed=True, message="任务已放回任务池")
