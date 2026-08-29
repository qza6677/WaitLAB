from waitlab.ipc import HookEventServer


def test_ipc_rejects_non_object_json_values():
    assert HookEventServer.sanitize_payload([]) is None
    assert HookEventServer.sanitize_payload("text") is None
    assert HookEventServer.sanitize_payload(1) is None


def test_ipc_sanitizes_lifecycle_payload_and_rejects_bad_fields():
    payload = {
        "event": "Stop",
        "session_id": " session ",
        "turn_id": " turn ",
        "prompt": "敏感内容不应继续转发",
    }
    assert HookEventServer.sanitize_payload(payload) == {
        "event": "Stop",
        "session_id": "session",
        "turn_id": "turn",
    }
    assert HookEventServer.sanitize_payload({"event": "Unknown"}) is None
    assert HookEventServer.sanitize_payload({"event": "Stop", "turn_id": 12}) is None

