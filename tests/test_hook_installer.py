import json

from waitlab.hook_installer import install_hooks, uninstall_hooks


def test_installer_preserves_existing_hooks_and_is_idempotent(tmp_path):
    target = tmp_path / ".codex" / "hooks.json"
    target.parent.mkdir()
    target.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "python existing.py"}
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    bridge = tmp_path / "hook_bridge.py"
    bridge.write_text("pass", encoding="utf-8")

    install_hooks(target, "C:/Python/python.exe", bridge, create_backup=False)
    install_hooks(target, "C:/Python/python.exe", bridge, create_backup=False)
    config = json.loads(target.read_text(encoding="utf-8"))

    handlers_by_event = {
        event_name: [
            handler
            for group in config["hooks"][event_name]
            for handler in group["hooks"]
        ]
        for event_name in ("UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop")
    }
    stop_handlers = handlers_by_event["Stop"]
    assert any(handler.get("command") == "python existing.py" for handler in stop_handlers)
    for handlers in handlers_by_event.values():
        assert sum("--waitlab-hook" in handler.get("command", "") for handler in handlers) == 1


def test_uninstaller_only_removes_waitlab_handlers(tmp_path):
    target = tmp_path / "hooks.json"
    bridge = tmp_path / "hook_bridge.py"
    bridge.write_text("pass", encoding="utf-8")
    install_hooks(target, "python", bridge, create_backup=False)

    _path, removed, _backup = uninstall_hooks(target, create_backup=False)
    config = json.loads(target.read_text(encoding="utf-8"))

    assert removed == 4
    assert config["hooks"] == {}
