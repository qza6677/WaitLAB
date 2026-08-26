import json
import os
import socket
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bridge_forwards_only_sanitized_lifecycle_data():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(2)
        port = listener.getsockname()[1]
        hook_input = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "thread-secret",
            "turn_id": "turn-123",
            "prompt": "这段内容绝不能离开 hook 进程",
            "transcript_path": "C:/private/transcript.jsonl",
            "cwd": "C:/private/project",
        }
        environment = dict(os.environ)
        environment["WAITLAB_HOOK_PORT"] = str(port)
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hook_bridge.py"), "--waitlab-hook"],
            input=json.dumps(hook_input, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        data, _address = listener.recvfrom(4096)

    forwarded = json.loads(data.decode("utf-8"))
    assert result.stdout == '{"continue":true}'
    assert forwarded["event"] == "UserPromptSubmit"
    assert forwarded["session_id"] == "thread-secret"
    assert forwarded["turn_id"] == "turn-123"
    assert "prompt" not in forwarded
    assert "transcript_path" not in forwarded
    assert "cwd" not in forwarded


def test_bridge_forwards_post_tool_use_without_writing_a_hook_decision():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(2)
        port = listener.getsockname()[1]
        environment = dict(os.environ)
        environment["WAITLAB_HOOK_PORT"] = str(port)
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hook_bridge.py"), "--waitlab-hook"],
            input=json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "thread-1",
                    "turn_id": "turn-1",
                    "tool_input": {"secret": "discard me"},
                    "tool_output": "also discard me",
                }
            ),
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        data, _address = listener.recvfrom(4096)

    forwarded = json.loads(data.decode("utf-8"))
    assert result.stdout == ""
    assert forwarded["event"] == "PostToolUse"
    assert "tool_input" not in forwarded
    assert "tool_output" not in forwarded


def test_bridge_permission_request_is_observational_and_silent():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(2)
        port = listener.getsockname()[1]
        environment = dict(os.environ)
        environment["WAITLAB_HOOK_PORT"] = str(port)
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "hook_bridge.py"), "--waitlab-hook"],
            input=json.dumps(
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "thread-1",
                    "turn_id": "turn-1",
                    "tool_name": "exec_command",
                }
            ),
            text=True,
            capture_output=True,
            check=True,
            env=environment,
        )
        data, _address = listener.recvfrom(4096)

    assert result.stdout == ""
    assert json.loads(data.decode("utf-8"))["event"] == "PermissionRequest"


def test_bridge_ignores_unknown_hook_events():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "hook_bridge.py"), "--waitlab-hook"],
        input=json.dumps({"hook_event_name": "PreToolUse"}),
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout == '{"continue":true}'
