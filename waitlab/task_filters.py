"""Pure task-pool filtering and sorting helpers used by the Qt dialog."""

from __future__ import annotations

from collections.abc import Iterable

from .models import Task


def filter_and_sort_tasks(
    tasks: Iterable[Task],
    query: str = "",
    tag: str = "全部标签",
    sort_mode: str = "自定义顺序",
) -> list[Task]:
    filtered = list(tasks)
    normalized_query = query.strip().casefold()
    if normalized_query:
        filtered = [
            task for task in filtered
            if normalized_query in task.title.casefold()
        ]
    if tag and tag != "全部标签":
        filtered = [task for task in filtered if task.tag == tag]
    if sort_mode == "名称 A-Z":
        filtered.sort(key=lambda task: task.title.casefold())
    elif sort_mode == "标签":
        filtered.sort(key=lambda task: (task.tag.casefold(), task.title.casefold()))
    return filtered
