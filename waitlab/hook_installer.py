from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


HOOK_EVENTS = ("UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop")
MARKER = "--waitlab-hook"


def default_hooks_path() -> Path:
    return Path.home() / ".codex" / "hooks.json"


def bridge_path() -> Path:
    return Path(__file__).resolve().parents[1] / "hook_bridge.py"


def hook_command(python_executable: str | Path, bridge: str | Path) -> str:
    return subprocess.list2cmdline([str(python_executable), str(bridge), MARKER])


def _is_waitlab_handler(handler: dict[str, Any]) -> bool:
    commands = (handler.get("command"), handler.get("commandWindows"))
    return any(isinstance(command, str) and MARKER in command for command in commands)


def _remove_waitlab_handlers(config: dict[str, Any]) -> int:
    removed = 0
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return removed
    for event_name in list(hooks):
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        remaining_groups = []
        for group in groups:
            if not isinstance(group, dict):
                remaining_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                remaining_groups.append(group)
                continue
            kept_handlers = []
            for handler in handlers:
                if isinstance(handler, dict) and _is_waitlab_handler(handler):
                    removed += 1
                else:
                    kept_handlers.append(handler)
            if kept_handlers:
                copied = dict(group)
                copied["hooks"] = kept_handlers
                remaining_groups.append(copied)
        if remaining_groups:
            hooks[event_name] = remaining_groups
        else:
            hooks.pop(event_name, None)
    return removed


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"description": "User lifecycle hooks.", "hooks": {}}
    raw = path.read_text(encoding="utf-8-sig")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("hooks.json 顶层必须是 JSON 对象")
    if "hooks" not in parsed:
        parsed["hooks"] = {}
    if not isinstance(parsed["hooks"], dict):
        raise ValueError("hooks.json 中的 hooks 必须是 JSON 对象")
    return parsed


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = path.with_name(f"hooks.waitlab-backup-{timestamp}.json")
    shutil.copy2(path, destination)
    return destination


def _atomic_write(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.waitlab.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def install_hooks(
    path: Path | None = None,
    python_executable: str | Path | None = None,
    bridge: str | Path | None = None,
    create_backup: bool = True,
) -> tuple[Path, Path | None]:
    target = path or default_hooks_path()
    bridge_file = Path(bridge or bridge_path()).resolve()
    if not bridge_file.exists():
        raise FileNotFoundError(f"找不到 hook bridge：{bridge_file}")
    config = _load_config(target)
    _remove_waitlab_handlers(config)
    command = hook_command(python_executable or sys.executable, bridge_file)
    for event_name in HOOK_EVENTS:
        config["hooks"].setdefault(event_name, []).append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "commandWindows": command,
                        "timeout": 3,
                    }
                ]
            }
        )
    backup = _backup(target) if create_backup else None
    _atomic_write(target, config)
    return target, backup


def uninstall_hooks(
    path: Path | None = None,
    create_backup: bool = True,
) -> tuple[Path, int, Path | None]:
    target = path or default_hooks_path()
    if not target.exists():
        return target, 0, None
    config = _load_config(target)
    removed = _remove_waitlab_handlers(config)
    backup = _backup(target) if create_backup and removed else None
    if removed:
        _atomic_write(target, config)
    return target, removed, backup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安装或移除 WaitLAB Codex hooks")
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--path", type=Path, help="覆盖 hooks.json 路径（用于测试或项目级安装）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "install":
        target, backup = install_hooks(path=args.path)
        print(f"WaitLAB hooks 已安装：{target}")
        if backup:
            print(f"原配置备份：{backup}")
        print("请在 Codex 中打开 /hooks，审核并信任新增的四个 hooks。")
        return 0
    target, removed, backup = uninstall_hooks(path=args.path)
    print(f"已从 {target} 移除 {removed} 个 WaitLAB hook。")
    if backup:
        print(f"原配置备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
