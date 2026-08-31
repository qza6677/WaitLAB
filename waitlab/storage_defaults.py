"""Built-in task/tag defaults and compatibility migration constants."""
from __future__ import annotations

from .models import DEFAULT_TAG

# Kept only so upgrades can recognize and migrate the old research-focused
# built-ins. These values are never inserted into a fresh database.
LEGACY_DEFAULT_TASKS: tuple[str, ...] = (
    "精读并标记一段论文",
    "补写一条实验记录",
    "检查并完善一个图注",
    "整理一条参考文献",
    "写下当前研究的三个下一步",
    "清理一个代码 TODO",
    "修改一段论文表述",
)

DEFAULT_TASKS: tuple[str, ...] = (
    "处理一个五分钟待办",
    "整理一条笔记",
    "阅读几页内容并记下要点",
    "清理一个代码 TODO",
    "回复一条重要消息",
    "整理一个文件夹",
    "写下当前事情的下一步",
)

LEGACY_DEFAULT_TAGS: tuple[str, ...] = ("论文写作", "文献阅读", "Vibe coding", DEFAULT_TAG)
DEFAULT_TAGS: tuple[str, ...] = ("写作", "阅读", "编码", "整理", "工作/项目", DEFAULT_TAG)
DEFAULT_CONTENT_VERSION = "2"

LEGACY_DEFAULT_TASK_TAGS: dict[str, str] = {
    LEGACY_DEFAULT_TASKS[0]: "文献阅读",
    LEGACY_DEFAULT_TASKS[1]: "论文写作",
    LEGACY_DEFAULT_TASKS[2]: "论文写作",
    LEGACY_DEFAULT_TASKS[3]: "文献阅读",
    LEGACY_DEFAULT_TASKS[4]: "论文写作",
    LEGACY_DEFAULT_TASKS[5]: "Vibe coding",
    LEGACY_DEFAULT_TASKS[6]: "论文写作",
}
DEFAULT_TASK_TAGS: dict[str, str] = {
    DEFAULT_TASKS[0]: "工作/项目",
    DEFAULT_TASKS[1]: "整理",
    DEFAULT_TASKS[2]: "阅读",
    DEFAULT_TASKS[3]: "编码",
    DEFAULT_TASKS[4]: "工作/项目",
    DEFAULT_TASKS[5]: "整理",
    DEFAULT_TASKS[6]: "工作/项目",
}

AI_RUNNING_STATUSES = {"running", "inprogress"}


