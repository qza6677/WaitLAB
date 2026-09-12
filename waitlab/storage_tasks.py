"""Task, tag, and built-in task repository for the SQLite store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import json
import re
import sqlite3

from .models import (
    DailyTask,
    DefaultTaskEntry,
    DEFAULT_TAG,
    Task,
    TaskPlanningEvent,
    TaskKind,
    RepeatRule,
    from_iso,
    local_date_key,
    to_iso,
    utc_now,
)
from .storage_defaults import (
    DEFAULT_TAGS,
    DEFAULT_TASKS,
    DEFAULT_TASK_TAGS,
    LEGACY_DEFAULT_TASKS,
    LEGACY_DEFAULT_TASK_TAGS,
)

_ALLOWED_TAG_TONES = frozenset(
    {"purple", "blue", "teal", "orange", "yellow", "red", "slate"}
)
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def _normalize_tag_color(value: str) -> str | None:
    clean = str(value).strip()
    if clean.lower() in _ALLOWED_TAG_TONES:
        return clean.lower()
    if _HEX_COLOR_PATTERN.fullmatch(clean):
        return clean.upper()
    return None


class TaskRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        get_setting: Callable[[str, str], str],
        set_setting_uncommitted: Callable[[str, str], None],
        normalize_tag: Callable[[str | None], str],
    ) -> None:
        self._connection = connection
        self._get_setting = get_setting
        self._set_setting_uncommitted = set_setting_uncommitted
        self._normalize_tag = normalize_tag

    @staticmethod
    def _is_legacy_default_entries(entries: list[DefaultTaskEntry]) -> bool:
        if len(entries) != len(LEGACY_DEFAULT_TASKS):
            return False
        if {entry.title for entry in entries} != set(LEGACY_DEFAULT_TASKS):
            return False
        return all(
            entry.enabled and entry.tag == LEGACY_DEFAULT_TASK_TAGS[entry.title]
            for entry in entries
        )

    @staticmethod
    def _map_legacy_default_entries(
        entries: list[DefaultTaskEntry],
    ) -> list[DefaultTaskEntry]:
        title_map = dict(zip(LEGACY_DEFAULT_TASKS, DEFAULT_TASKS))
        return [
            DefaultTaskEntry(
                title_map[entry.title],
                True,
                DEFAULT_TASK_TAGS[title_map[entry.title]],
            )
            for entry in entries
        ]

    @staticmethod
    def _normalize_planned_date(value: str | datetime | None) -> str:
        if value is None:
            return local_date_key()
        if isinstance(value, datetime):
            return local_date_key(value)
        clean = str(value).strip()
        try:
            datetime.fromisoformat(clean)
        except ValueError as exc:
            raise ValueError("任务日期格式无效") from exc
        return clean[:10]

    @staticmethod
    def _normalize_priority(value: int | str | None) -> int:
        try:
            priority = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("任务优先级无效") from exc
        if priority < 0 or priority > 3:
            raise ValueError("任务优先级无效")
        return priority

    @staticmethod
    def _normalize_due_date(value: str | datetime | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        if isinstance(value, datetime):
            return local_date_key(value)
        clean = str(value).strip()
        try:
            return date.fromisoformat(clean[:10]).isoformat()
        except ValueError as exc:
            raise ValueError("截止日期格式无效") from exc

    @staticmethod
    def _normalize_repeat_rule(value: str | None) -> str:
        clean = str(value or "").strip().lower()
        allowed = {rule.value for rule in RepeatRule}
        return clean if clean in allowed else RepeatRule.ROTATION.value

    @staticmethod
    def _normalize_repeat_weekday(value: int | str | None) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            weekday = int(value)
        except (TypeError, ValueError):
            return None
        return weekday if 0 <= weekday <= 6 else None

    @classmethod
    def _is_default_task_due(
        cls,
        entry: DefaultTaskEntry,
        planned_date: str | datetime | None = None,
    ) -> bool:
        rule = cls._normalize_repeat_rule(entry.repeat_rule)
        if rule in {RepeatRule.ROTATION.value, RepeatRule.DAILY.value}:
            return True
        if isinstance(planned_date, datetime):
            target = planned_date.astimezone().date()
        else:
            raw = str(planned_date or local_date_key()).strip()[:10]
            try:
                target = date.fromisoformat(raw)
            except ValueError:
                target = date.today()
        if rule == RepeatRule.WEEKDAYS.value:
            return target.weekday() < 5
        if rule == RepeatRule.WEEKLY.value:
            weekday = cls._normalize_repeat_weekday(entry.repeat_weekday)
            return target.weekday() == (weekday if weekday is not None else 0)
        return True

    @staticmethod
    def _daily_task_from_row(row: sqlite3.Row) -> DailyTask:
        planned = row["planned_date"] or local_date_key()
        initial = row["initial_planned_date"] or planned
        return DailyTask(
            id=int(row["id"]),
            title=row["title"],
            tag=row["tag"] or DEFAULT_TAG,
            planned_date=planned,
            initial_planned_date=initial,
            status=row["status"] or "open",
            sort_order=int(row["sort_order"] or 0),
            completed_at=from_iso(row["completed_at"]),
            carried_from_date=row["carried_from_date"],
            rollover_count=int(row["rollover_count"] or 0),
            priority=int(row["priority"] or 0),
            due_date=row["due_date"],
        )

    def _record_planning_event_uncommitted(
        self,
        task_id: int,
        from_date: str,
        to_date: str,
        event_type: str,
    ) -> None:
        if from_date == to_date:
            return
        self._connection.execute(
            "INSERT INTO task_planning_events(task_id, from_date, to_date, event_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, from_date, to_date, event_type, to_iso(utc_now())),
        )

    def list_task_planning_events(self, task_id: int) -> list[TaskPlanningEvent]:
        rows = self._connection.execute(
            "SELECT id, task_id, from_date, to_date, event_type, created_at FROM task_planning_events WHERE task_id = ? ORDER BY created_at, id",
            (task_id,),
        ).fetchall()
        return [
            TaskPlanningEvent(
                id=int(row["id"]),
                task_id=int(row["task_id"]),
                from_date=row["from_date"],
                to_date=row["to_date"],
                event_type=row["event_type"],
                created_at=from_iso(row["created_at"]) or utc_now(),
            )
            for row in rows
        ]

    def add_manual_task(
        self,
        title: str,
        tag: str = DEFAULT_TAG,
        planned_date: str | datetime | None = None,
        *,
        priority: int = 0,
        due_date: str | datetime | None = None,
    ) -> Task:
        return self.add_manual_tasks(
            [title],
            tag,
            planned_date,
            priority=priority,
            due_date=due_date,
        )[0]

    def add_manual_tasks(
        self,
        titles: list[str],
        tag: str = DEFAULT_TAG,
        planned_date: str | datetime | None = None,
        *,
        priority: int = 0,
        due_date: str | datetime | None = None,
    ) -> list[Task]:
        clean_titles = [" ".join(str(title).strip().split()) for title in titles]
        if not clean_titles or any(not title for title in clean_titles):
            raise ValueError("任务名称不能为空")
        day = self._normalize_planned_date(planned_date)
        clean_priority = self._normalize_priority(priority)
        clean_due_date = self._normalize_due_date(due_date)
        clean_tag = self._normalize_tag(tag)
        available_tags = self.available_tags()
        if clean_tag not in available_tags:
            # A task imported from an older profile may carry a tag that is no
            # longer part of the built-in list. Keep it selectable and visible
            # in the tag manager instead of silently hiding it.
            available_tags.append(clean_tag)
        with self._connection:
            if clean_tag not in self.available_tags():
                self._save_available_tags_uncommitted(available_tags)
            next_order = self._connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open' AND planned_date = ?",
                (day,),
            ).fetchone()[0]
            created: list[Task] = []
            for clean_title in clean_titles:
                cursor = self._connection.execute(
                    "INSERT INTO tasks(title, status, sort_order, created_at, tag, planned_date, initial_planned_date, priority, due_date) VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        clean_title,
                        next_order,
                        to_iso(utc_now()),
                        clean_tag,
                        day,
                        day,
                        clean_priority,
                        clean_due_date,
                    ),
                )
                task_id = cursor.lastrowid
                if task_id is None:
                    raise RuntimeError("无法创建任务")
                created.append(
                    Task(
                        int(task_id),
                        clean_title,
                        TaskKind.MANUAL,
                        next_order,
                        clean_tag,
                        clean_priority,
                        clean_due_date,
                        day,
                    )
                )
                next_order += 1
        return created

    def list_manual_tasks(self) -> list[Task]:
        rows = self._connection.execute(
            "SELECT id, title, sort_order, tag, priority, due_date, planned_date FROM tasks WHERE status = 'open' ORDER BY sort_order, id"
        ).fetchall()
        return [
            Task(
                row["id"],
                row["title"],
                TaskKind.MANUAL,
                row["sort_order"],
                row["tag"] or DEFAULT_TAG,
                int(row["priority"] or 0),
                row["due_date"],
                row["planned_date"],
            )
            for row in rows
        ]

    def list_daily_tasks(
        self,
        planned_date: str | datetime | None = None,
        *,
        include_completed: bool = True,
    ) -> list[DailyTask]:
        day = self._normalize_planned_date(planned_date)
        status_clause = "" if include_completed else " AND status = 'open'"
        rows = self._connection.execute(
            f"SELECT id, title, status, sort_order, completed_at, tag, planned_date, initial_planned_date, carried_from_date, rollover_count, priority, due_date FROM tasks WHERE planned_date = ? AND status != 'deleted'{status_clause} ORDER BY CASE WHEN status = 'completed' THEN 1 ELSE 0 END, sort_order, id",
            (day,),
        ).fetchall()
        return [self._daily_task_from_row(row) for row in rows]

    def list_overdue_tasks(self, planned_date: str | datetime | None = None) -> list[DailyTask]:
        day = self._normalize_planned_date(planned_date)
        rows = self._connection.execute(
            "SELECT id, title, status, sort_order, completed_at, tag, planned_date, initial_planned_date, carried_from_date, rollover_count, priority, due_date FROM tasks WHERE status = 'open' AND planned_date < ? ORDER BY planned_date DESC, sort_order, id",
            (day,),
        ).fetchall()
        return [self._daily_task_from_row(row) for row in rows]

    def get_daily_task(self, task_id: int) -> DailyTask | None:
        row = self._connection.execute(
            "SELECT id, title, status, sort_order, completed_at, tag, planned_date, initial_planned_date, carried_from_date, rollover_count, priority, due_date FROM tasks WHERE id = ? AND status != 'deleted'",
            (task_id,),
        ).fetchone()
        return self._daily_task_from_row(row) if row is not None else None

    def update_manual_task(
        self,
        task_id: int,
        title: str,
        tag: str,
        *,
        priority: int = 0,
        due_date: str | datetime | None = None,
    ) -> Task | None:
        clean_title = " ".join(str(title).strip().split())
        if not clean_title:
            raise ValueError("任务名称不能为空")
        clean_tag = self._normalize_tag(tag)
        clean_priority = self._normalize_priority(priority)
        clean_due_date = self._normalize_due_date(due_date)
        if clean_tag not in self.available_tags():
            self._save_available_tags_uncommitted(self.available_tags() + [clean_tag])
        with self._connection:
            row = self._connection.execute(
                "SELECT sort_order, planned_date FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE tasks SET title = ?, tag = ?, priority = ?, due_date = ? WHERE id = ?",
                (clean_title, clean_tag, clean_priority, clean_due_date, task_id),
            )
            # Keep an active focus snapshot in sync while preserving history.
            self._connection.execute(
                "UPDATE focus_sessions SET task_title = ?, task_tag = ? WHERE task_id = ? AND ended_at IS NULL",
                (clean_title, clean_tag, task_id),
            )
        return Task(
            int(task_id),
            clean_title,
            TaskKind.MANUAL,
            int(row["sort_order"]),
            clean_tag,
            clean_priority,
            clean_due_date,
            row["planned_date"],
        )

    def set_manual_task_completed(
        self,
        task_id: int,
        completed: bool,
        when: datetime | None = None,
    ) -> bool:
        timestamp = to_iso(when or utc_now()) if completed else None
        status = "completed" if completed else "open"
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                (status, timestamp, task_id),
            )
        return cursor.rowcount > 0

    def carry_manual_task(
        self,
        task_id: int,
        planned_date: str | datetime | None = None,
    ) -> bool:
        target = self._normalize_planned_date(planned_date)
        with self._connection:
            row = self._connection.execute(
                "SELECT planned_date, status FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None or row["status"] != "open":
                return False
            if row["planned_date"] == target:
                return True
            next_order = self._connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open' AND planned_date = ?",
                (target,),
            ).fetchone()[0]
            self._connection.execute(
                "UPDATE tasks SET planned_date = ?, carried_from_date = ?, rollover_count = rollover_count + 1, sort_order = ? WHERE id = ?",
                (target, row["planned_date"], next_order, task_id),
            )
            self._record_planning_event_uncommitted(
                task_id,
                row["planned_date"],
                target,
                "carry",
            )
        return True

    def carry_manual_tasks(
        self,
        task_ids: list[int],
        planned_date: str | datetime | None = None,
    ) -> int:
        target = self._normalize_planned_date(planned_date)
        count = 0
        with self._connection:
            next_order = self._connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open' AND planned_date = ?",
                (target,),
            ).fetchone()[0]
            for task_id in dict.fromkeys(int(value) for value in task_ids):
                row = self._connection.execute(
                    "SELECT planned_date, status FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None or row["status"] != "open":
                    continue
                if row["planned_date"] == target:
                    continue
                self._connection.execute(
                    "UPDATE tasks SET planned_date = ?, carried_from_date = ?, rollover_count = rollover_count + 1, sort_order = ? WHERE id = ?",
                    (target, row["planned_date"], next_order, task_id),
                )
                self._record_planning_event_uncommitted(
                    task_id,
                    row["planned_date"],
                    target,
                    "carry",
                )
                next_order += 1
                count += 1
        return count

    def reschedule_manual_task(
        self,
        task_id: int,
        planned_date: str | datetime,
    ) -> bool:
        target = self._normalize_planned_date(planned_date)
        with self._connection:
            row = self._connection.execute(
                "SELECT planned_date, status FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return False
            if row["planned_date"] == target:
                return True
            next_order = self._connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open' AND planned_date = ?",
                (target,),
            ).fetchone()[0]
            self._connection.execute(
                "UPDATE tasks SET planned_date = ?, carried_from_date = ?, rollover_count = rollover_count + 1, sort_order = CASE WHEN status = 'open' THEN ? ELSE sort_order END WHERE id = ?",
                (target, row["planned_date"], next_order, task_id),
            )
            self._record_planning_event_uncommitted(
                task_id,
                row["planned_date"],
                target,
                "reschedule",
            )
        return True

    def reorder_manual_tasks(self, task_ids: list[int], planned_date: str | datetime | None = None) -> None:
        day = self._normalize_planned_date(planned_date)
        with self._connection:
            for order, task_id in enumerate(dict.fromkeys(int(value) for value in task_ids)):
                self._connection.execute(
                    "UPDATE tasks SET sort_order = ? WHERE id = ? AND planned_date = ? AND status = 'open'",
                    (order, task_id, day),
                )

    def available_tags(self) -> list[str]:
        raw = self._get_setting("task_tags", "")
        tags: list[str] = []
        if raw:
            try:
                stored = json.loads(raw)
            except json.JSONDecodeError:
                stored = []
            if isinstance(stored, list):
                tags = [self._normalize_tag(value) for value in stored if str(value).strip()]
        if not tags:
            tags = list(DEFAULT_TAGS)
        elif DEFAULT_TAG not in tags:
            # The fallback tag is always available so deleting a custom tag
            # never leaves existing tasks without a valid destination.
            tags.append(DEFAULT_TAG)
        return list(dict.fromkeys(tags))

    def tag_colors(self) -> dict[str, str]:
        """Return persisted visual tones keyed by tag name."""

        raw = self._get_setting("task_tag_colors", "")
        try:
            stored = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            stored = {}
        if not isinstance(stored, dict):
            return {}
        colors: dict[str, str] = {}
        for tag, value in stored.items():
            if not str(tag).strip():
                continue
            normalized = _normalize_tag_color(str(value))
            if normalized is not None:
                colors[self._normalize_tag(tag)] = normalized
        return colors

    def _save_tag_colors_uncommitted(self, colors: dict[str, str]) -> None:
        self._set_setting_uncommitted(
            "task_tag_colors",
            json.dumps(
                {
                    self._normalize_tag(tag): normalized
                    for tag, tone in colors.items()
                    if (normalized := _normalize_tag_color(str(tone))) is not None
                },
                ensure_ascii=False,
            ),
        )

    def set_tag_color(self, tag: str, tone: str) -> None:
        clean_tag = self._normalize_tag(tag)
        if clean_tag not in self.available_tags():
            raise ValueError("标签不存在")
        clean_color = _normalize_tag_color(str(tone))
        if clean_color is None:
            raise ValueError("标签颜色无效，请使用色轮或六位 HEX 颜色")
        colors = self.tag_colors()
        colors[clean_tag] = clean_color
        self._save_tag_colors_uncommitted(colors)
        self._connection.commit()

    def _save_available_tags_uncommitted(self, tags: list[str]) -> None:
        cleaned: list[str] = []
        for value in tags:
            tag = self._normalize_tag(value)
            if tag not in cleaned:
                cleaned.append(tag)
        if DEFAULT_TAG not in cleaned:
            cleaned.append(DEFAULT_TAG)
        self._set_setting_uncommitted(
            "task_tags",
            json.dumps(cleaned, ensure_ascii=False),
        )

    def add_tag(self, tag: str) -> str:
        clean_tag = self._normalize_tag(tag)
        if not str(tag or "").strip():
            raise ValueError("标签名称不能为空")
        tags = self.available_tags()
        if clean_tag in tags:
            raise ValueError("标签已存在")
        tags.append(clean_tag)
        self._save_available_tags_uncommitted(tags)
        self._connection.commit()
        return clean_tag

    def rename_tag(self, old_tag: str, new_tag: str) -> str:
        old = self._normalize_tag(old_tag)
        if not str(new_tag or "").strip():
            raise ValueError("标签名称不能为空")
        new = self._normalize_tag(new_tag)
        if old == DEFAULT_TAG:
            raise ValueError("未分类是系统保底标签，不能重命名")
        tags = self.available_tags()
        if old not in tags:
            raise ValueError("要修改的标签不存在")
        if new in tags and new != old:
            raise ValueError("标签已存在")
        if old == new:
            return new
        renamed = [new if tag == old else tag for tag in tags]
        colors = self.tag_colors()
        if old in colors:
            colors[new] = colors.pop(old)
        entries = self.default_task_entries()
        entries = [
            DefaultTaskEntry(
                entry.title,
                entry.enabled,
                new if entry.tag == old else entry.tag,
                entry.repeat_rule,
                entry.repeat_weekday,
            )
            for entry in entries
        ]
        with self._connection:
            self._save_available_tags_uncommitted(renamed)
            self._save_tag_colors_uncommitted(colors)
            self._connection.execute(
                "UPDATE tasks SET tag = ? WHERE tag = ?",
                (new, old),
            )
            self._connection.execute(
                "UPDATE focus_sessions SET task_tag = ? WHERE task_tag = ?",
                (new, old),
            )
            self._set_default_task_entries_uncommitted(entries)
        return new

    def delete_tag(self, tag: str) -> None:
        clean_tag = self._normalize_tag(tag)
        if clean_tag == DEFAULT_TAG:
            raise ValueError("未分类是系统保底标签，不能删除")
        self.delete_tags([clean_tag])

    def delete_tags(self, tags: list[str]) -> int:
        """Delete several tags atomically and reassign their data to fallback."""
    
        available = set(self.available_tags())
        clean_tags: list[str] = []
        for value in tags:
            clean_tag = self._normalize_tag(value)
            if clean_tag == DEFAULT_TAG:
                raise ValueError("未分类是系统保底标签，不能删除")
            if clean_tag in available and clean_tag not in clean_tags:
                clean_tags.append(clean_tag)
        if not clean_tags:
            return 0
    
        remaining = [value for value in self.available_tags() if value not in clean_tags]
        colors = {
            tag: tone for tag, tone in self.tag_colors().items() if tag not in clean_tags
        }
        entries = self.default_task_entries()
        entries = [
            DefaultTaskEntry(
                entry.title,
                entry.enabled,
                DEFAULT_TAG if entry.tag in clean_tags else entry.tag,
                entry.repeat_rule,
                entry.repeat_weekday,
            )
            for entry in entries
        ]
        with self._connection:
            self._save_available_tags_uncommitted(remaining)
            self._save_tag_colors_uncommitted(colors)
            for clean_tag in clean_tags:
                self._connection.execute(
                    "UPDATE tasks SET tag = ? WHERE tag = ?",
                    (DEFAULT_TAG, clean_tag),
                )
                self._connection.execute(
                    "UPDATE focus_sessions SET task_tag = ? WHERE task_tag = ?",
                    (DEFAULT_TAG, clean_tag),
                )
            self._set_default_task_entries_uncommitted(entries)
        return len(clean_tags)

    def tag_usage_counts(self) -> dict[str, int]:
        """Return current task counts for the tag management view."""
    
        counts = {tag: 0 for tag in self.available_tags()}
        rows = self._connection.execute(
            "SELECT tag, COUNT(*) AS count FROM tasks WHERE status = 'open' GROUP BY tag"
        ).fetchall()
        for row in rows:
            tag = self._normalize_tag(row["tag"])
            counts[tag] = counts.get(tag, 0) + int(row["count"])
        for entry in self.default_task_entries():
            if entry.enabled:
                counts[entry.tag] = counts.get(entry.tag, 0) + 1
        return counts

    def complete_manual_task(self, task_id: int, when: datetime | None = None) -> None:
        self._connection.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'open'",
            (to_iso(when or utc_now()), task_id),
        )
        self._connection.commit()

    def _complete_manual_task_uncommitted(self, task_id: int, when: datetime) -> None:
        self._connection.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'open'",
            (to_iso(when), task_id),
        )

    def delete_manual_task(self, task_id: int) -> Task | None:
        row = self._connection.execute(
            "SELECT id, title, sort_order, tag, priority, due_date, planned_date FROM tasks WHERE id = ? AND status = 'open'",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        deleted = Task(
            int(row["id"]),
            row["title"],
            TaskKind.MANUAL,
            int(row["sort_order"]),
            row["tag"] or DEFAULT_TAG,
            int(row["priority"] or 0),
            row["due_date"],
            row["planned_date"],
        )
        # Keep the row and its planning history so an immediate undo restores
        # the same task identity.  Active-task queries already use status =
        # 'open', while daily views explicitly exclude this tombstone state.
        self._connection.execute(
            "UPDATE tasks SET status = 'deleted' WHERE id = ? AND status = 'open'",
            (task_id,),
        )
        self._connection.commit()
        return deleted

    def restore_manual_task(self, task_id: int) -> Task | None:
        """Restore a soft-deleted task without changing its primary key."""

        row = self._connection.execute(
            "SELECT id, title, sort_order, tag, priority, due_date, planned_date FROM tasks WHERE id = ? AND status = 'deleted'",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        self._connection.execute(
            "UPDATE tasks SET status = 'open', completed_at = NULL WHERE id = ? AND status = 'deleted'",
            (task_id,),
        )
        self._connection.commit()
        return Task(
            int(row["id"]),
            row["title"],
            TaskKind.MANUAL,
            int(row["sort_order"]),
            row["tag"] or DEFAULT_TAG,
            int(row["priority"] or 0),
            row["due_date"],
            row["planned_date"],
        )

    def suggested_tasks(self, limit: int = 3) -> list[Task]:
        manual_tasks = [
            daily_task.as_task()
            for daily_task in self.list_daily_tasks(
                local_date_key(),
                include_completed=False,
            )
        ]
        if manual_tasks:
            return manual_tasks[:limit]
        entries = self.due_default_task_entries(local_date_key())
        return [
            Task(None, entry.title, TaskKind.DEFAULT, offset, entry.tag)
            for offset, entry in enumerate(entries[:limit])
        ]

    def advance_default_task(self, selected_title: str | None = None) -> None:
        entries = self.default_task_entries()
        enabled_titles = [entry.title for entry in entries if entry.enabled]
        if not enabled_titles:
            return
        selected = selected_title if selected_title in enabled_titles else enabled_titles[0]
        selected_entry = next(entry for entry in entries if entry.title == selected)
        if self._normalize_repeat_rule(selected_entry.repeat_rule) != RepeatRule.ROTATION.value:
            return
        entries.remove(selected_entry)
        entries.append(selected_entry)
        self.set_default_task_entries(entries)

    def default_task_entries(self) -> list[DefaultTaskEntry]:
        raw = self._get_setting("default_tasks_v2", "")
        if raw:
            entries = self._parse_default_task_entries(raw)
            if entries:
                return entries
        return [DefaultTaskEntry(title, True, DEFAULT_TASK_TAGS.get(title, DEFAULT_TAG)) for title in self._default_task_order()]

    def merge_default_task_entries(
        self,
        defaults: list[DefaultTaskEntry],
    ) -> list[DefaultTaskEntry]:
        """Append missing built-in entries while preserving user settings."""

        current = self.default_task_entries()
        known = {entry.title for entry in current}
        merged = current + [entry for entry in defaults if entry.title not in known]
        self.set_default_task_entries(merged)
        return merged

    def due_default_task_entries(
        self,
        planned_date: str | datetime | None = None,
    ) -> list[DefaultTaskEntry]:
        """Return enabled fixed tasks scheduled for the requested day."""

        return [
            entry
            for entry in self.default_task_entries()
            if entry.enabled and self._is_default_task_due(entry, planned_date)
        ]

    def _parse_default_task_entries(self, raw: str) -> list[DefaultTaskEntry]:
        try:
            stored = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(stored, list):
            return []
        entries: list[DefaultTaskEntry] = []
        seen: set[str] = set()
        for item in stored:
            if not isinstance(item, dict):
                continue
            title = " ".join(str(item.get("title") or "").strip().split())
            if not title or title in seen:
                continue
            seen.add(title)
            entries.append(DefaultTaskEntry(
                title,
                bool(item.get("enabled", True)),
                self._normalize_tag(item.get("tag") or DEFAULT_TASK_TAGS.get(title)),
                self._normalize_repeat_rule(item.get("repeat_rule")),
                self._normalize_repeat_weekday(item.get("repeat_weekday")),
            ))
        return entries

    def set_default_task_entries(self, entries: list[DefaultTaskEntry]) -> None:
        self._set_default_task_entries_uncommitted(entries)
        self._connection.commit()

    def _set_default_task_entries_uncommitted(self, entries: list[DefaultTaskEntry]) -> None:
        cleaned: list[dict[str, object]] = []
        seen: set[str] = set()
        for entry in entries:
            title = " ".join(entry.title.strip().split())
            if not title or title in seen:
                continue
            seen.add(title)
            cleaned.append(
                {
                    "title": title,
                    "enabled": bool(entry.enabled),
                    "tag": self._normalize_tag(entry.tag),
                    "repeat_rule": self._normalize_repeat_rule(entry.repeat_rule),
                    "repeat_weekday": self._normalize_repeat_weekday(entry.repeat_weekday),
                }
            )
        self._set_setting_uncommitted("default_tasks_v2", json.dumps(cleaned, ensure_ascii=False))

    def _default_task_order(self) -> list[str]:
        raw = self._get_setting("default_task_order", "")
        try:
            stored = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            stored = []
        valid = [title for title in stored if title in DEFAULT_TASKS]
        for title in DEFAULT_TASKS:
            if title not in valid:
                valid.append(title)
        return valid
