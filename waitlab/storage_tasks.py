"""Task, tag, and built-in task repository for the SQLite store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import sqlite3

from .models import (
    DefaultTaskEntry,
    DEFAULT_TAG,
    Task,
    TaskKind,
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

    def add_manual_task(self, title: str, tag: str = DEFAULT_TAG) -> Task:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("任务名称不能为空")
        next_order = self._connection.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tasks WHERE status = 'open'"
        ).fetchone()[0]
        clean_tag = self._normalize_tag(tag)
        if clean_tag not in self.available_tags():
            # A task imported from an older profile may carry a tag that is no
            # longer part of the built-in list. Keep it selectable and visible
            # in the tag manager instead of silently hiding it.
            self._save_available_tags_uncommitted(self.available_tags() + [clean_tag])
        cursor = self._connection.execute(
            "INSERT INTO tasks(title, status, sort_order, created_at, tag) VALUES (?, 'open', ?, ?, ?)",
            (clean_title, next_order, to_iso(utc_now()), clean_tag),
        )
        self._connection.commit()
        task_id = cursor.lastrowid
        if task_id is None:
            raise RuntimeError("无法创建任务")
        return Task(int(task_id), clean_title, TaskKind.MANUAL, next_order, clean_tag)

    def list_manual_tasks(self) -> list[Task]:
        rows = self._connection.execute(
            "SELECT id, title, sort_order, tag FROM tasks WHERE status = 'open' ORDER BY sort_order, id"
        ).fetchall()
        return [Task(row["id"], row["title"], TaskKind.MANUAL, row["sort_order"], row["tag"] or DEFAULT_TAG) for row in rows]

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
        entries = self.default_task_entries()
        entries = [
            DefaultTaskEntry(entry.title, entry.enabled, new if entry.tag == old else entry.tag)
            for entry in entries
        ]
        with self._connection:
            self._save_available_tags_uncommitted(renamed)
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
        entries = self.default_task_entries()
        entries = [
            DefaultTaskEntry(
                entry.title,
                entry.enabled,
                DEFAULT_TAG if entry.tag in clean_tags else entry.tag,
            )
            for entry in entries
        ]
        with self._connection:
            self._save_available_tags_uncommitted(remaining)
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
            "SELECT id, title, sort_order, tag FROM tasks WHERE id = ? AND status = 'open'",
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
        )
        self._connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._connection.commit()
        return deleted

    def suggested_tasks(self, limit: int = 3) -> list[Task]:
        manual_tasks = self.list_manual_tasks()
        if manual_tasks:
            return manual_tasks[:limit]
        entries = [entry for entry in self.default_task_entries() if entry.enabled]
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
            cleaned.append({"title": title, "enabled": bool(entry.enabled), "tag": self._normalize_tag(entry.tag)})
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
