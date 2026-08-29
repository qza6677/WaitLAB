"""Cookie desktop-pet asset mapping and runtime path discovery."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CookieState(StrEnum):
    IDLE = "idle"
    WAITING = "waiting"
    WORKING = "working"
    PAUSED = "paused"
    ATTENTION = "attention"
    AI_COMPLETE = "ai-complete"
    ERROR = "error"
    TASK_COMPLETE = "task-complete"
    CURIOUS = "curious"
    OFFLINE = "offline"
    UPDATE_AVAILABLE = "update-available"
    UPDATING = "updating"


COOKIE_STATE_FILES: dict[CookieState, str] = {
    CookieState.IDLE: "01-idle.png",
    CookieState.WAITING: "02-waiting.png",
    CookieState.WORKING: "03-working.png",
    CookieState.PAUSED: "04-paused.png",
    CookieState.ATTENTION: "05-attention.png",
    CookieState.AI_COMPLETE: "06-ai-complete.png",
    CookieState.ERROR: "07-error.png",
    CookieState.TASK_COMPLETE: "08-task-complete.png",
    CookieState.CURIOUS: "09-curious.png",
    CookieState.OFFLINE: "10-offline.png",
    CookieState.UPDATE_AVAILABLE: "11-update-available.png",
    CookieState.UPDATING: "12-updating.png",
}

COOKIE_STATE_LABELS: dict[CookieState, str] = {
    CookieState.IDLE: "待机",
    CookieState.WAITING: "等待 AI",
    CookieState.WORKING: "微任务进行中",
    CookieState.PAUSED: "微任务已暂停",
    CookieState.ATTENTION: "需要注意",
    CookieState.AI_COMPLETE: "AI 已完成",
    CookieState.ERROR: "发生错误",
    CookieState.TASK_COMPLETE: "微任务已完成",
    CookieState.CURIOUS: "等待确认",
    CookieState.OFFLINE: "连接中断",
    CookieState.UPDATE_AVAILABLE: "有可用更新",
    CookieState.UPDATING: "正在更新",
}


MODE_TO_COOKIE_STATE: dict[str, CookieState] = {
    "idle": CookieState.IDLE,
    "waiting": CookieState.WAITING,
    "focus": CookieState.WORKING,
    "paused": CookieState.PAUSED,
    "attention": CookieState.ATTENTION,
    "done": CookieState.AI_COMPLETE,
    "blocked": CookieState.ERROR,
}


@dataclass(frozen=True)
class CookieContext:
    """Observable application conditions used to choose Cookie's expression."""

    focus_active: bool = False
    focus_paused: bool = False
    ai_active: bool = False
    ai_needs_attention: bool = False
    completion_visible: bool = False
    task_completion_visible: bool = False
    terminal_error: bool = False


def resolve_cookie_state(context: CookieContext) -> CookieState:
    """Resolve a UI state with explicit precedence for overlapping events."""

    if context.ai_needs_attention:
        return CookieState.ATTENTION
    # Completing a micro-task is an explicit user action and should be
    # visible even when the Codex turn has not finished yet.
    if context.task_completion_visible and not context.focus_active:
        return CookieState.TASK_COMPLETE
    if context.focus_active:
        if context.terminal_error:
            return CookieState.ERROR
        if context.completion_visible:
            return CookieState.AI_COMPLETE
        if context.focus_paused:
            return CookieState.PAUSED
        return CookieState.WORKING
    if context.ai_active:
        return CookieState.WAITING
    if context.completion_visible:
        return CookieState.ERROR if context.terminal_error else CookieState.AI_COMPLETE
    return CookieState.IDLE


class CookieStateMachine:
    """Small deterministic state machine for the desktop pet presentation."""

    def __init__(self, initial: CookieState = CookieState.IDLE) -> None:
        self.state = initial

    def transition(self, context: CookieContext) -> CookieState:
        self.state = resolve_cookie_state(context)
        return self.state


def coerce_cookie_state(value: CookieState | str) -> CookieState:
    if isinstance(value, CookieState):
        return value
    try:
        return CookieState(value)
    except ValueError:
        return MODE_TO_COOKIE_STATE.get(str(value), CookieState.IDLE)


def _candidate_asset_dirs() -> list[Path]:
    candidates: list[Path] = []
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        candidates.append(Path(bundled_root) / "resources" / "Cookie" / "processed" / "sprites-96")

    project_root = Path(__file__).resolve().parents[1]
    candidates.append(project_root / "resources" / "Cookie" / "processed" / "sprites-96")
    return candidates


def default_cookie_asset_dir() -> Path | None:
    for candidate in _candidate_asset_dirs():
        if candidate.is_dir():
            return candidate
    return None


class CookieAssets:
    """Resolve Cookie state names to bundled or source-tree PNG files."""

    def __init__(self, asset_dir: Path | str | None = None) -> None:
        self.asset_dir = Path(asset_dir) if asset_dir is not None else default_cookie_asset_dir()

    def path_for(self, state: CookieState | str, display_size: int | None = None) -> Path | None:
        if self.asset_dir is None:
            return None
        state_value = coerce_cookie_state(state)
        asset_dir = self.asset_dir
        if display_size is not None and display_size > 96:
            high_res_dir = self.asset_dir.parent / "sprites-256"
            if high_res_dir.is_dir():
                asset_dir = high_res_dir
        path = asset_dir / COOKIE_STATE_FILES[state_value]
        if path.is_file():
            return path
        if state_value is not CookieState.IDLE:
            fallback = asset_dir / COOKIE_STATE_FILES[CookieState.IDLE]
            if fallback.is_file():
                return fallback
        return None

    def available(self) -> bool:
        return self.path_for(CookieState.IDLE) is not None

