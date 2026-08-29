from waitlab.models import Task, TaskKind
from waitlab.task_filters import filter_and_sort_tasks


def test_task_filter_and_sort_is_case_insensitive_and_tag_aware() -> None:
    tasks = [
        Task(1, "写 Discussion", TaskKind.MANUAL, tag="论文写作"),
        Task(2, "读方法论文", TaskKind.MANUAL, tag="文献阅读"),
        Task(3, "写摘要", TaskKind.MANUAL, tag="论文写作"),
    ]

    result = filter_and_sort_tasks(tasks, query="写", tag="论文写作", sort_mode="名称 A-Z")

    assert [task.title for task in result] == ["写 Discussion", "写摘要"]
