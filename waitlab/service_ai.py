"""AI lifecycle and desktop reconciliation coordinator."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime

from .desktop_activity import BLOCKED_STATUSES, COMPLETED_STATUSES, DesktopTurnSnapshot
from .models import AiSession, ServiceUpdate, utc_now
from .service_policy import (
    AI_ATTENTION_STATUSES,
    AI_MISSING_AFTER_SECONDS,
    AI_RUNNING_STATUSES,
    AI_STALE_AFTER_SECONDS,
    MAX_REMEMBERED_TURNS,
    normal_status as _normal_status,
)
from .stats_cache import StatsCache
from .storage import Storage


class AiCoordinator:
    def __init__(
        self,
        storage: Storage,
        stats_cache: StatsCache,
        has_active_focus: Callable[[], bool],
        has_focus: Callable[[], bool],
    ) -> None:
        self.storage = storage
        self.stats_cache = stats_cache
        self._has_active_focus = has_active_focus
        self._has_focus = has_focus
        self._started_turn_ids: OrderedDict[str, None] = OrderedDict()
        self.last_ai_completion_seconds: float | None = None
        self.last_ai_terminal_status: str | None = None

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
        self._started_turn_ids[turn_id] = None
        if len(self._started_turn_ids) > MAX_REMEMBERED_TURNS:
            self._started_turn_ids.popitem(last=False)
        self.stats_cache.invalidate()
        return ServiceUpdate(
            show_task_picker=(
                show_task_picker
                and is_new_turn
                and not self._has_active_focus()
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
                if completed and self._has_focus()
                else "Codex 已完成"
                if completed
                else "Codex 已中断；当前微任务继续计时"
                if blocked and self._has_focus()
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
                # Without an item timestamp there is no reliable freshness
                # signal: a long-running tool call can legitimately leave the
                # lifecycle row unchanged for several minutes. Keep the
                # explicit running status and let the missing-row grace path
                # handle sessions that disappear from the source entirely.
                last_activity = snapshot.last_activity_at
                if last_activity is None:
                    continue
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
