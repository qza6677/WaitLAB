from __future__ import annotations

from datetime import datetime

from .desktop_activity import (
    BLOCKED_STATUSES,
    COMPLETED_STATUSES,
    DesktopTurnSnapshot,
)
from .models import AiSession, FocusOutcome, FocusSession, ServiceUpdate, Task, utc_now
from .storage import Storage
from .stats_cache import StatsCache


def _normal_status(status: str) -> str:
    return "".join(character for character in status.casefold() if character.isalnum())


AI_RUNNING_STATUSES = {"inprogress", "running"}
AI_ATTENTION_STATUSES = {
    "needsattention",
    "needsinput",
    "needsapproval",
    "waitingforinput",
    "waitingforapproval",
}
AI_STALE_AFTER_SECONDS = 5 * 60
AI_MISSING_AFTER_SECONDS = 5 * 60
AI_INITIAL_PROMPT_GRACE_SECONDS = 5 * 60


class WaitLabService:
    """Pure application logic shared by the GUI, hooks, and tests."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        # Waiting Task is the only user-facing timer. Codex activity is kept
        # as a short-lived lifecycle signal and is intentionally excluded
        # from periodic statistics queries.
        self.stats_cache = StatsCache(storage, include_codex=False)
        # AI lifecycle rows belong to the process that observed them. Close
        # anything left by a previous process before the desktop reader starts
        # feeding us fresh transitions; otherwise an old ``inProgress`` row
        # can make Cookie look permanently busy after a restart.
        self.storage.close_open_ai_sessions(status="stale")
        self._started_turn_ids: set[str] = set()
        # One focus may run at a time, while any number of other focus
        # sessions can remain paused for quick task switching.  Older
        # versions only loaded the newest open session; loading all sessions
        # here lets a switched task survive a restart as well.
        open_focuses = storage.list_open_focuses()
        for session in open_focuses:
            if not session.is_paused:
                self.storage.recover_open_focus(session)
        open_focuses = storage.list_open_focuses()
        self.focus: FocusSession | None = open_focuses[-1] if open_focuses else None
        self._paused_focuses: dict[int, FocusSession] = {
            session.id: session
            for session in open_focuses[:-1]
            if session.is_paused
        }
        self.has_recovered_focus = bool(open_focuses)
        self.last_ai_completion_seconds: float | None = None
        self.last_ai_terminal_status: str | None = None

    def suggested_tasks(self) -> list[Task]:
        return self.storage.suggested_tasks(limit=3)

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

    def open_ai_sessions(self) -> list[AiSession]:
        return self.storage.list_open_ai()

    def running_ai_sessions(self) -> list[AiSession]:
        return [
            session
            for session in self.storage.list_open_ai()
            if _normal_status(session.status) in AI_RUNNING_STATUSES
        ]

    def attention_ai_sessions(self) -> list[AiSession]:
        return [
            session
            for session in self.storage.list_open_ai()
            if _normal_status(session.status) in AI_ATTENTION_STATUSES
        ]

    def on_ai_started(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
        show_task_picker: bool = True,
    ) -> ServiceUpdate:
        session = self.storage.start_ai_session(session_id, turn_id, when=when)
        is_new_turn = turn_id not in self._started_turn_ids
        self._started_turn_ids.add(turn_id)
        self.stats_cache.invalidate()
        return ServiceUpdate(
            show_task_picker=(
                show_task_picker
                and is_new_turn
                and not self.has_active_focus()
                and not session.picker_skipped
            ),
            message="Codex 对话开始，选一个 Waiting Task 吧",
            ai_turn_id=turn_id,
            ai_status="running",
        )

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
        open_session = self.storage.get_open_ai(turn_id=turn_id)
        known_session = self.storage.get_ai_session(turn_id)
        if open_session is None and known_session is not None:
            return ServiceUpdate()
        if open_session is None and create_if_missing:
            self.storage.start_ai_session(session_id or "codex", turn_id, when=started_at)
            self.stats_cache.invalidate()
        finished = self.storage.finish_ai_session(
            turn_id,
            status=status,
            when=when,
            fallback_latest=fallback_latest,
        )
        if finished is not None:
            self.stats_cache.invalidate()
            self.last_ai_completion_seconds = finished.active_elapsed_seconds()
            self.last_ai_terminal_status = status
        completed = finished is not None and status == "completed"
        blocked = finished is not None and status != "completed"
        return ServiceUpdate(
            ai_completed=completed,
            ai_blocked=blocked,
            message=(
                "Codex 已完成；当前微任务继续计时"
                if completed and self.focus is not None
                else "Codex 已完成"
                if completed
                else "Codex 已中断；当前微任务继续计时"
                if blocked and self.focus is not None
                else "Codex 已中断或运行失败"
                if blocked
                else None
            ),
            ai_turn_id=finished.turn_id if finished is not None else None,
            ai_status=status if finished is not None else None,
        )

    def on_ai_needs_attention(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
        fallback_latest: bool = True,
    ) -> ServiceUpdate:
        session = self.storage.get_open_ai(turn_id=turn_id)
        if session is None and fallback_latest:
            session = self.storage.get_open_ai()
        if session is None:
            session = self.storage.start_ai_session(session_id, turn_id, when=when)
            self.stats_cache.invalidate()
        updated = self.storage.set_ai_status(
            session.turn_id,
            "needs_attention",
            when=when,
            fallback_latest=fallback_latest,
        )
        if updated is not None:
            self.stats_cache.invalidate()
        return ServiceUpdate(
            ai_needs_attention=updated is not None,
            message="Codex 正在等待批准，微任务继续计时",
            ai_turn_id=updated.turn_id if updated is not None else None,
            ai_status="needs_attention" if updated is not None else None,
        )

    def on_ai_resumed(
        self,
        turn_id: str,
        fallback_latest: bool = True,
        when: datetime | None = None,
    ) -> ServiceUpdate:
        session = self.storage.get_open_ai(turn_id=turn_id)
        if session is None and fallback_latest:
            session = self.storage.get_open_ai()
        if session is None or session.status != "needs_attention":
            return ServiceUpdate()
        self.storage.set_ai_status(
            session.turn_id,
            "running",
            when=when,
            fallback_latest=fallback_latest,
        )
        self.stats_cache.invalidate()
        return ServiceUpdate(
            ai_resumed=True,
            message="已批准，Codex 继续工作",
            ai_turn_id=session.turn_id,
            ai_status="running",
        )

    def reconcile_desktop_sessions(
        self,
        snapshots: tuple[DesktopTurnSnapshot, ...],
        now: datetime | None = None,
        stale_after_seconds: float = AI_STALE_AFTER_SECONDS,
    ) -> ServiceUpdate:
        """Reconcile persisted sessions with the desktop lifecycle source.

        This closes terminal rows missed while WaitLAB was not running and
        stops counting a running row whose item activity has gone stale.  Once
        the desktop source is available, a non-manual session that disappears
        from its bounded snapshot is also closed after a grace period.  This
        prevents a stale ``running`` row from keeping the Cookie in the
        working state forever after Codex exits unexpectedly.
        """

        current = now or utc_now()
        by_turn = {snapshot.turn_id: snapshot for snapshot in snapshots}
        completed = False
        blocked = False
        stale = False
        terminal_turn_id: str | None = None
        for session in self.storage.list_open_ai():
            snapshot = by_turn.get(session.turn_id)
            if snapshot is None:
                # Manual fallback sessions do not have a corresponding row in
                # Codex's database and must remain under explicit user control.
                # Hook/desktop sessions, however, should eventually be present
                # in the authoritative snapshot; close an orphan after the
                # same five-minute safety window used for stale activity.
                if (
                    session.session_id != "manual"
                    and _normal_status(session.status) in AI_RUNNING_STATUSES
                    and (current - session.started_at).total_seconds()
                    > AI_MISSING_AFTER_SECONDS
                ):
                    if self.storage.finish_ai_session(
                        session.turn_id,
                        status="stale",
                        when=current,
                        fallback_latest=False,
                    ) is not None:
                        self.stats_cache.invalidate()
                        self.last_ai_terminal_status = "stale"
                        blocked = True
                        stale = True
                        terminal_turn_id = session.turn_id
                continue
            normalized = _normal_status(snapshot.status)
            if normalized in COMPLETED_STATUSES:
                ended_at = snapshot.completed_at or current
                finished = self.storage.finish_ai_session(
                    session.turn_id,
                    status="completed",
                    when=ended_at,
                    fallback_latest=False,
                )
                if finished is not None:
                    self.stats_cache.invalidate()
                    self.last_ai_completion_seconds = finished.active_elapsed_seconds(ended_at)
                    self.last_ai_terminal_status = "completed"
                    completed = True
                    terminal_turn_id = session.turn_id
                continue
            if normalized in BLOCKED_STATUSES:
                ended_at = snapshot.completed_at or current
                finished = self.storage.finish_ai_session(
                    session.turn_id,
                    status="blocked",
                    when=ended_at,
                    fallback_latest=False,
                )
                if finished is not None:
                    self.stats_cache.invalidate()
                    self.last_ai_completion_seconds = finished.active_elapsed_seconds(ended_at)
                    self.last_ai_terminal_status = "blocked"
                    blocked = True
                    terminal_turn_id = session.turn_id
                continue
            if normalized in AI_ATTENTION_STATUSES:
                if _normal_status(session.status) not in AI_ATTENTION_STATUSES:
                    updated = self.storage.set_ai_status(
                        session.turn_id,
                        "needs_attention",
                        when=snapshot.last_activity_at or current,
                        fallback_latest=False,
                    )
                    if updated is not None:
                        self.stats_cache.invalidate()
                continue
            if normalized in AI_RUNNING_STATUSES:
                # Some Codex database versions do not expose item timestamps.
                # In that case the turn start is the only freshness signal;
                # an ``inProgress`` row with no activity for the safety window
                # is treated as an orphan rather than running forever.
                last_activity = snapshot.last_activity_at or snapshot.started_at
                if (
                    (current - last_activity).total_seconds() > stale_after_seconds
                ):
                    if self.storage.finish_ai_session(
                        session.turn_id,
                        status="stale",
                        when=last_activity,
                        fallback_latest=False,
                    ) is not None:
                        self.stats_cache.invalidate()
                        self.last_ai_terminal_status = "stale"
                        blocked = True
                        stale = True
        if stale:
            message = "Codex 状态长时间没有更新，已停止计时（仅停止本轮状态跟踪，不影响 Waiting Task）；请重新发起任务。"
        elif blocked:
            message = "Codex 任务已中断，本轮状态跟踪已停止。"
        elif completed:
            message = "Codex 已完成，本轮状态跟踪已停止。"
        else:
            message = None
        return ServiceUpdate(
            ai_completed=completed,
            ai_blocked=blocked,
            message=message,
            ai_turn_id=terminal_turn_id,
            ai_status="completed" if completed else "blocked" if blocked else None,
        )

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
        self.focus = None
        return ServiceUpdate(focus_changed=True, message="微任务完成，做得漂亮")

    def abandon_focus(self, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is None:
            return ServiceUpdate()
        abandoned = self.focus
        self.storage.finish_focus_and_task(abandoned, FocusOutcome.ABANDONED, when=when)
        self.stats_cache.invalidate()
        self.focus = None
        return ServiceUpdate(focus_changed=True, message="任务已放回任务池")

    def manual_ai_started(self, when: datetime | None = None) -> ServiceUpdate:
        marker = f"manual-{int((when or utc_now()).timestamp() * 1000)}"
        return self.on_ai_started("manual", marker, when=when)

    def manual_ai_finished(self, when: datetime | None = None) -> ServiceUpdate:
        open_ai = self.storage.get_open_ai()
        if open_ai is None:
            return ServiceUpdate(message="当前没有正在等待的 Codex 任务")
        return self.on_ai_finished(open_ai.turn_id, when=when)

    def skip_current_ai_round(self) -> ServiceUpdate:
        open_ai = self.storage.get_open_ai()
        if open_ai is None:
            return ServiceUpdate(message="当前没有正在等待的 Codex 任务")
        self.storage.skip_ai_picker(open_ai.turn_id)
        return ServiceUpdate(message="本轮已跳过；新的 Codex 任务仍会提醒")

