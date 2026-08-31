"""Read-only statistics repository for SQLite-backed activity data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import sqlite3

from .models import (
    CompletedFocusRecord,
    CompletedTaskSummary,
    TagTimeBucket,
    TaskKind,
    from_iso,
    to_iso,
    utc_now,
)


class StatsRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        normalize_tag: Callable[[str | None], str],
    ) -> None:
        self._connection = connection
        self._normalize_tag = normalize_tag

    @staticmethod
    def _today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
        """Return the current user's local day as UTC query bounds."""

        current = now or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_current = current.astimezone()
        local_start = local_current.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)

    def today_focus_seconds(self, now: datetime | None = None) -> float:
        return self.waiting_seconds("day", now)

    def today_completed_tasks(
        self,
        now: datetime | None = None,
    ) -> list[CompletedTaskSummary]:
        """Return today's completed tasks with their accumulated focus time."""

        day_start, day_end = self._today_window(now)
        rows = self._connection.execute(
            """
            SELECT task_id, task_title, task_kind, task_tag, started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE outcome = 'completed'
              AND ended_at >= ?
              AND ended_at < ?
            ORDER BY ended_at DESC
            """,
            (to_iso(day_start), to_iso(day_end)),
        ).fetchall()
        grouped: dict[tuple[int | None, str, TaskKind, str], CompletedTaskSummary] = {}
        for row in rows:
            started_at = from_iso(row["started_at"])
            ended_at = from_iso(row["ended_at"])
            if started_at is None or ended_at is None:
                continue
            duration = max(
                0.0,
                (ended_at - started_at).total_seconds()
                - float(row["paused_seconds"] or 0.0),
            )
            task_id = int(row["task_id"]) if row["task_id"] is not None else None
            title = str(row["task_title"])
            tag = self._normalize_tag(row["task_tag"])
            try:
                kind = TaskKind(str(row["task_kind"]))
            except ValueError:
                kind = TaskKind.DEFAULT
            key = (task_id, title, kind, tag)
            previous = grouped.get(key)
            if previous is None:
                grouped[key] = CompletedTaskSummary(
                    task_id=task_id,
                    title=title,
                    kind=kind,
                    total_seconds=duration,
                    completed_count=1,
                    last_completed_at=ended_at,
                    tag=tag,
                )
            else:
                grouped[key] = CompletedTaskSummary(
                    task_id=previous.task_id,
                    title=previous.title,
                    kind=previous.kind,
                    total_seconds=previous.total_seconds + duration,
                    completed_count=previous.completed_count + 1,
                    last_completed_at=max(previous.last_completed_at, ended_at),
                    tag=previous.tag,
                )
        return sorted(
            grouped.values(),
            key=lambda summary: summary.last_completed_at,
            reverse=True,
        )

    def completed_focus_records(
        self,
        task_id: int | None,
        title: str,
        kind: TaskKind,
        tag: str | None = None,
        now: datetime | None = None,
    ) -> list[CompletedFocusRecord]:
        """Return today's completed focus segments for one history row."""

        day_start, day_end = self._today_window(now)
        rows = self._connection.execute(
            """
            SELECT id, task_id, task_title, task_kind, task_tag,
                   started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE outcome = 'completed'
              AND ended_at >= ? AND ended_at < ?
              AND task_title = ? AND task_kind = ?
              AND (? IS NULL OR task_tag = ?)
              AND ((task_id IS NULL AND ? IS NULL) OR task_id = ?)
            ORDER BY ended_at DESC, id DESC
            """,
            (to_iso(day_start), to_iso(day_end), title, kind.value, tag, tag, task_id, task_id),
        ).fetchall()
        records: list[CompletedFocusRecord] = []
        for row in rows:
            started_at = from_iso(row["started_at"])
            ended_at = from_iso(row["ended_at"])
            if started_at is None or ended_at is None:
                continue
            duration = max(
                0.0,
                (ended_at - started_at).total_seconds()
                - float(row["paused_seconds"] or 0.0),
            )
            records.append(CompletedFocusRecord(
                id=int(row["id"]),
                task_id=int(row["task_id"]) if row["task_id"] is not None else None,
                title=str(row["task_title"]),
                kind=kind,
                tag=self._normalize_tag(row["task_tag"]),
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration,
            ))
        return records

    def get_completed_focus_record(self, session_id: int) -> CompletedFocusRecord | None:
        """Return one completed record for confirmation and undo actions."""

        row = self._connection.execute(
            """
            SELECT id, task_id, task_title, task_kind, task_tag,
                   started_at, ended_at, paused_seconds
            FROM focus_sessions
            WHERE id = ? AND outcome = 'completed' AND ended_at IS NOT NULL
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        started_at = from_iso(row["started_at"])
        ended_at = from_iso(row["ended_at"])
        if started_at is None or ended_at is None:
            return None
        try:
            kind = TaskKind(str(row["task_kind"]))
        except ValueError:
            kind = TaskKind.DEFAULT
        duration = max(
            0.0,
            (ended_at - started_at).total_seconds()
            - float(row["paused_seconds"] or 0.0),
        )
        return CompletedFocusRecord(
            id=int(row["id"]),
            task_id=int(row["task_id"]) if row["task_id"] is not None else None,
            title=str(row["task_title"]),
            kind=kind,
            tag=self._normalize_tag(row["task_tag"]),
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
        )

    @staticmethod
    def _period_window(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
        current = (now or datetime.now().astimezone()).astimezone()
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "day":
            end = start + timedelta(days=1)
        elif period == "week":
            start = start - timedelta(days=start.weekday())
            end = start + timedelta(days=7)
        elif period == "month":
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            start = start.replace(day=1)
            end = next_month
        else:
            raise ValueError(f"unknown period: {period}")
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    @staticmethod
    def _overlap_seconds(
        started_at: datetime,
        ended_at: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        overlap_start = max(started_at, window_start)
        overlap_end = min(ended_at, window_end)
        return max(0.0, (overlap_end - overlap_start).total_seconds())

    def codex_active_seconds(self, period: str, now: datetime | None = None) -> float:
        """Return active Codex time in a local day/week/month window."""

        window_start, window_end = self._period_window(period, now)
        current = now or utc_now()
        segment_rows = self._connection.execute(
            """
            SELECT started_at, ended_at
            FROM ai_activity_segments
            WHERE state = 'running'
              AND started_at < ?
              AND (ended_at IS NULL OR ended_at > ?)
            """,
            (to_iso(window_end), to_iso(window_start)),
        ).fetchall()
        total = 0.0
        for row in segment_rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current
            if started is not None and ended is not None:
                total += self._overlap_seconds(started, ended, window_start, window_end)

        # Legacy installations have no segment history; retain a conservative
        # proportional fallback until they naturally accumulate new records.
        rows = self._connection.execute(
            """
            SELECT started_at, ended_at, active_seconds, running_since
            FROM ai_sessions AS a
            WHERE NOT EXISTS (
                SELECT 1 FROM ai_activity_segments AS s WHERE s.ai_session_id = a.id
            )
              AND a.started_at < ?
              AND (a.ended_at IS NULL OR a.ended_at > ?)
            """,
            (to_iso(window_end), to_iso(window_start)),
        ).fetchall()
        for row in rows:
            started = from_iso(row["started_at"])
            if started is None:
                continue
            ended = from_iso(row["ended_at"]) or current
            overlap = self._overlap_seconds(started, ended, window_start, window_end)
            wall = max(0.0, (ended - started).total_seconds())
            if overlap <= 0 or wall <= 0:
                continue
            active = float(row["active_seconds"] or 0.0)
            if row["ended_at"] is None and row["running_since"]:
                running_since = from_iso(row["running_since"])
                if running_since is not None:
                    active += max(0.0, (current - running_since).total_seconds())
            total += active * min(1.0, overlap / wall)
        return total

    def waiting_seconds(self, period: str, now: datetime | None = None) -> float:
        window_start, window_end = self._period_window(period, now)
        current = now or utc_now()
        segment_rows = self._connection.execute(
            """
            SELECT s.started_at, s.ended_at
            FROM focus_segments AS s
            JOIN focus_sessions AS f ON f.id = s.focus_session_id
            WHERE (f.outcome IN ('completed', 'abandoned') OR f.ended_at IS NULL)
              AND s.started_at < ?
              AND (s.ended_at IS NULL OR s.ended_at > ?)
            """,
            (to_iso(window_end), to_iso(window_start)),
        ).fetchall()
        total = 0.0
        for row in segment_rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current
            if started is not None and ended is not None:
                total += self._overlap_seconds(started, ended, window_start, window_end)

        # Legacy installations have no focus segment history.
        rows = self._connection.execute(
            """
            SELECT f.started_at, f.ended_at, f.paused_seconds, f.paused_at
            FROM focus_sessions AS f
            WHERE NOT EXISTS (
                SELECT 1 FROM focus_segments AS s WHERE s.focus_session_id = f.id
            )
              AND f.started_at < ?
              AND (f.ended_at IS NULL OR f.ended_at > ?)
            """,
            (to_iso(window_end), to_iso(window_start)),
        ).fetchall()
        for row in rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current
            if started is None or ended is None:
                continue
            wall = max(0.0, (ended - started).total_seconds())
            overlap = self._overlap_seconds(started, ended, window_start, window_end)
            paused = float(row["paused_seconds"] or 0.0)
            if row["ended_at"] is None and row["paused_at"]:
                paused += max(0.0, (current - (from_iso(row["paused_at"]) or current)).total_seconds())
            total += max(0.0, wall - paused) * (overlap / wall if wall else 0.0)
        return total

    def _tag_waiting_seconds_for_windows(
        self,
        windows: list[tuple[datetime, datetime]],
        current: datetime,
    ) -> list[dict[str, float]]:
        """Aggregate tag time for one or more UTC windows in one DB pass."""

        totals: list[dict[str, float]] = [{} for _ in windows]
        if not windows:
            return totals
        current_utc = current.astimezone(timezone.utc)
        span_start = min(window[0] for window in windows)
        span_end = max(window[1] for window in windows)
        segment_rows = self._connection.execute(
            """
            SELECT s.started_at, s.ended_at, f.task_tag
            FROM focus_segments AS s
            JOIN focus_sessions AS f ON f.id = s.focus_session_id
            WHERE (f.outcome IN ('completed', 'abandoned') OR f.ended_at IS NULL)
              AND s.started_at < ?
              AND (s.ended_at IS NULL OR s.ended_at > ?)
            """,
            (to_iso(span_end), to_iso(span_start)),
        ).fetchall()
        for row in segment_rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current_utc
            if started is None or ended is None:
                continue
            tag = self._normalize_tag(row["task_tag"])
            for index, (window_start, window_end) in enumerate(windows):
                duration = self._overlap_seconds(
                    started,
                    ended,
                    window_start,
                    window_end,
                )
                if duration > 0:
                    totals[index][tag] = totals[index].get(tag, 0.0) + duration

        # Legacy fallback mirrors waiting_seconds above.  Old sessions do not
        # have pause segments, so their active time is distributed
        # proportionally over the overlapping calendar windows.
        rows = self._connection.execute(
            """
            SELECT f.started_at, f.ended_at, f.paused_seconds, f.paused_at, f.task_tag
            FROM focus_sessions AS f
            WHERE NOT EXISTS (
                SELECT 1 FROM focus_segments AS s WHERE s.focus_session_id = f.id
            )
              AND f.started_at < ?
              AND (f.ended_at IS NULL OR f.ended_at > ?)
            """,
            (to_iso(span_end), to_iso(span_start)),
        ).fetchall()
        for row in rows:
            started = from_iso(row["started_at"])
            ended = from_iso(row["ended_at"]) or current_utc
            if started is None or ended is None:
                continue
            wall = max(0.0, (ended - started).total_seconds())
            if wall <= 0:
                continue
            paused = float(row["paused_seconds"] or 0.0)
            if row["ended_at"] is None and row["paused_at"]:
                paused += max(
                    0.0,
                    (
                        current_utc
                        - (from_iso(row["paused_at"]) or current_utc)
                    ).total_seconds(),
                )
            active = max(0.0, wall - paused)
            if active <= 0:
                continue
            tag = self._normalize_tag(row["task_tag"])
            for index, (window_start, window_end) in enumerate(windows):
                overlap = self._overlap_seconds(
                    started,
                    ended,
                    window_start,
                    window_end,
                )
                if overlap > 0:
                    duration = active * (overlap / wall)
                    totals[index][tag] = totals[index].get(tag, 0.0) + duration
        return totals

    def tag_waiting_seconds(self, period: str, now: datetime | None = None) -> dict[str, float]:
        window_start, window_end = self._period_window(period, now)
        current = now or utc_now()
        totals = self._tag_waiting_seconds_for_windows(
            [(window_start, window_end)],
            current,
        )[0]
        return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))

    def tag_waiting_daily_series(
        self,
        period: str,
        now: datetime | None = None,
    ) -> list[TagTimeBucket]:
        """Return per-day tag totals for the current local week or month."""

        if period not in {"week", "month"}:
            raise ValueError("daily tag series only supports week or month")
        current = (now or datetime.now().astimezone()).astimezone()
        period_start, period_end = self._period_window(period, current)
        local_start = period_start.astimezone(current.tzinfo)
        local_end = period_end.astimezone(current.tzinfo)
        buckets: list[tuple[datetime, datetime]] = []
        cursor = local_start
        while cursor < local_end:
            next_cursor = min(cursor + timedelta(days=1), local_end)
            buckets.append((cursor, next_cursor))
            cursor = next_cursor
        windows = [
            (start.astimezone(timezone.utc), end.astimezone(timezone.utc))
            for start, end in buckets
        ]
        totals = self._tag_waiting_seconds_for_windows(windows, current)
        return [
            TagTimeBucket(start, end, dict(sorted(day.items())))
            for (start, end), day in zip(buckets, totals)
        ]

    def today_completed_titles(self, now: datetime | None = None) -> list[str]:
        """Backward-compatible title-only view of today's completed tasks."""

        return [summary.title for summary in self.today_completed_tasks(now)]
