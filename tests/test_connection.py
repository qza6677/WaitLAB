from waitlab.connection import HookConnectionMonitor, HookConnectionState
from waitlab.hook_installer import install_hooks
from waitlab.storage import Storage


def test_hook_connection_moves_from_missing_to_pending_to_connected(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    hooks_path = tmp_path / ".codex" / "hooks.json"
    bridge = tmp_path / "hook_bridge.py"
    bridge.write_text("pass", encoding="utf-8")
    monitor = HookConnectionMonitor(storage, hooks_path)

    assert monitor.inspect().state is HookConnectionState.NOT_INSTALLED

    install_hooks(
        hooks_path,
        python_executable="python",
        bridge=bridge,
        create_backup=False,
    )
    pending = monitor.inspect()
    assert pending.state is HookConnectionState.PENDING
    assert len(pending.configured_events) == 4

    monitor.record_event("UserPromptSubmit")
    connected = monitor.inspect()
    assert connected.state is HookConnectionState.CONNECTED
    assert connected.last_event_name == "UserPromptSubmit"
    storage.close()


def test_listener_failure_has_priority_over_configuration(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    monitor = HookConnectionMonitor(storage, tmp_path / "hooks.json")
    monitor.set_listener_error("端口已被占用")

    info = monitor.inspect()

    assert info.state is HookConnectionState.LISTENER_FAILED
    assert "端口已被占用" in info.detail
    storage.close()
