from __future__ import annotations

from datetime import datetime

from .models import FocusOutcome, FocusSession, ServiceUpdate, Task, TaskKind, utc_now
from .storage import Storage


class WaitLabService:
    """Pure application logic shared by the GUI, hooks, and tests."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.focus: FocusSession | None = storage.get_open_focus()
        self.has_recovered_focus = self.focus is not None
        if self.focus is not None and not self.focus.is_paused:
            self.storage.recover_open_focus(self.focus)
        self.last_ai_completion_seconds: float | None = None
        self.last_ai_terminal_status: str | None = None

    def suggested_tasks(self) -> list[Task]:
        return self.storage.suggested_tasks(limit=3)

    def on_ai_started(
        self,
        session_id: str,
        turn_id: str,
        when: datetime | None = None,
    ) -> ServiceUpdate:
        session = self.storage.start_ai_session(session_id, turn_id, when=when)
        return ServiceUpdate(
            show_task_picker=self.focus is None and not session.picker_skipped,
            message="Codex 正在工作，选一个微任务吧",
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
        finished = self.storage.finish_ai_session(
            turn_id,
            status=status,
            when=when,
            fallback_latest=fallback_latest,
        )
        if finished is not None:
            self.last_ai_completion_seconds = finished.elapsed_seconds()
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
        updated = self.storage.set_ai_status(
            session.turn_id,
            "needs_attention",
            fallback_latest=fallback_latest,
        )
        return ServiceUpdate(
            ai_needs_attention=updated is not None,
            message="Codex 正在等待批准，微任务继续计时",
        )

    def on_ai_resumed(
        self,
        turn_id: str,
        fallback_latest: bool = True,
    ) -> ServiceUpdate:
        session = self.storage.get_open_ai(turn_id=turn_id)
        if session is None and fallback_latest:
            session = self.storage.get_open_ai()
        if session is None or session.status != "needs_attention":
            return ServiceUpdate()
        self.storage.set_ai_status(
            session.turn_id,
            "running",
            fallback_latest=fallback_latest,
        )
        return ServiceUpdate(
            ai_resumed=True,
            message="已批准，Codex 继续工作",
        )

    def start_focus(self, task: Task, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is not None:
            raise RuntimeError("已有正在进行的微任务")
        self.focus = self.storage.start_focus(task, when=when)
        return ServiceUpdate(focus_changed=True, message=f"开始：{task.title}")

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
        return ServiceUpdate(focus_changed=True, message=message)

    def resume_focus(
        self,
        when: datetime | None = None,
        message: str = "继续微任务",
    ) -> ServiceUpdate:
        if self.focus is None or self.focus.paused_at is None:
            return ServiceUpdate()
        current = when or utc_now()
        self.focus.paused_seconds += max(
            0.0,
            (current - self.focus.paused_at).total_seconds(),
        )
        self.focus.paused_at = None
        self.focus.last_heartbeat_at = current
        self.storage.save_focus_pause(self.focus)
        return ServiceUpdate(focus_changed=True, message=message)

    def heartbeat(self, when: datetime | None = None) -> None:
        if self.focus is not None and not self.focus.is_paused:
            self.storage.heartbeat_focus(self.focus, when=when)

    def complete_focus(self, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is None:
            return ServiceUpdate()
        completed = self.focus
        self.storage.end_focus(completed, FocusOutcome.COMPLETED, when=when)
        if completed.task.kind is TaskKind.MANUAL and completed.task.id is not None:
            self.storage.complete_manual_task(completed.task.id, when=when)
        else:
            self.storage.advance_default_task(completed.task.title)
        self.focus = None
        return ServiceUpdate(focus_changed=True, message="微任务完成，做得漂亮")

    def abandon_focus(self, when: datetime | None = None) -> ServiceUpdate:
        if self.focus is None:
            return ServiceUpdate()
        abandoned = self.focus
        self.storage.end_focus(abandoned, FocusOutcome.ABANDONED, when=when)
        if abandoned.task.kind is TaskKind.DEFAULT:
            self.storage.advance_default_task(abandoned.task.title)
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
