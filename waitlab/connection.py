from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .hook_installer import HOOK_EVENTS, MARKER, default_hooks_path
from .models import from_iso, to_iso, utc_now


class SettingsStore(Protocol):
    """Small persistence port required by :class:`HookConnectionMonitor`."""

    def get_setting(self, key: str, default: str = "") -> str: ...

    def set_setting(self, key: str, value: str) -> None: ...


class HookConnectionState(StrEnum):
    NOT_INSTALLED = "not_installed"
    PENDING = "pending"
    CONNECTED = "connected"
    LISTENER_FAILED = "listener_failed"


@dataclass(frozen=True, slots=True)
class HookConnectionInfo:
    state: HookConnectionState
    hooks_path: Path
    configured_events: tuple[str, ...]
    expected_events: tuple[str, ...]
    last_event_name: str | None = None
    last_event_at: datetime | None = None
    listener_error: str | None = None

    @property
    def label(self) -> str:
        return {
            HookConnectionState.NOT_INSTALLED: "Hook · 未安装",
            HookConnectionState.PENDING: "Hook · 待验证",
            HookConnectionState.CONNECTED: "Hook · 已连接",
            HookConnectionState.LISTENER_FAILED: "Hook · 监听失败",
        }[self.state]

    @property
    def detail(self) -> str:
        if self.state is HookConnectionState.LISTENER_FAILED:
            return f"本机事件监听启动失败：{self.listener_error or '未知错误'}"
        if self.state is HookConnectionState.NOT_INSTALLED:
            return "没有找到 WaitLAB Hook 配置。请先运行 install_hooks.ps1。"
        if len(self.configured_events) < len(self.expected_events):
            missing = [event for event in self.expected_events if event not in self.configured_events]
            return "Hook 配置不完整，缺少：" + "、".join(missing)
        if self.state is HookConnectionState.PENDING:
            return (
                "配置已就绪，但尚未收到真实事件。/hooks 仅存在于独立 Codex CLI，"
                "Windows 桌面端没有这个审核入口。"
            )
        return "已收到真实 Hook 事件，WaitLAB 与 Codex 的本机连接正常。"


def _contains_waitlab_handler(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    handlers = group.get("hooks")
    if not isinstance(handlers, list):
        return False
    for handler in handlers:
        if not isinstance(handler, dict):
            continue
        for key in ("command", "commandWindows"):
            command = handler.get(key)
            if isinstance(command, str) and MARKER in command:
                return True
    return False


def configured_waitlab_events(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ()
    hooks = config.get("hooks") if isinstance(config, dict) else None
    if not isinstance(hooks, dict):
        return ()
    return tuple(
        event_name
        for event_name in HOOK_EVENTS
        if any(_contains_waitlab_handler(group) for group in hooks.get(event_name, []))
    )


class HookConnectionMonitor:
    def __init__(self, settings: SettingsStore, hooks_path: Path | None = None) -> None:
        self.settings = settings
        self.hooks_path = hooks_path or default_hooks_path()
        self.listener_error: str | None = None

    def set_listener_error(self, error: str | None) -> None:
        self.listener_error = error or None

    def record_event(self, event_name: str, when: datetime | None = None) -> None:
        self.settings.set_setting("last_hook_event_name", event_name)
        self.settings.set_setting("last_hook_event_at", to_iso(when or utc_now()) or "")

    def inspect(self) -> HookConnectionInfo:
        configured = configured_waitlab_events(self.hooks_path)
        last_name = self.settings.get_setting("last_hook_event_name", "") or None
        last_at = from_iso(self.settings.get_setting("last_hook_event_at", ""))

        if self.listener_error:
            state = HookConnectionState.LISTENER_FAILED
        elif not configured:
            state = HookConnectionState.NOT_INSTALLED
        elif len(configured) < len(HOOK_EVENTS) or last_at is None:
            state = HookConnectionState.PENDING
        else:
            state = HookConnectionState.CONNECTED

        return HookConnectionInfo(
            state=state,
            hooks_path=self.hooks_path,
            configured_events=configured,
            expected_events=HOOK_EVENTS,
            last_event_name=last_name,
            last_event_at=last_at,
            listener_error=self.listener_error,
        )
