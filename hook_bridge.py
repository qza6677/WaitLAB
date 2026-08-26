"""Privacy-preserving Codex hook bridge for WaitLAB.

Codex sends a JSON object on stdin. This bridge intentionally forwards only the
lifecycle event and opaque session identifiers to the local WaitLAB UDP socket.
Prompt text, assistant messages, transcript paths, models, and cwd are discarded.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time


DEFAULT_PORT = 38641
ALLOWED_EVENTS = {"UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop"}
SILENT_EVENTS = {"PermissionRequest", "PostToolUse"}


def sanitized_event(raw: dict) -> dict | None:
    event_name = raw.get("hook_event_name") or raw.get("event")
    if event_name not in ALLOWED_EVENTS:
        return None
    return {
        "event": event_name,
        "session_id": str(raw.get("session_id") or "codex"),
        "turn_id": str(raw.get("turn_id") or ""),
        "timestamp": time.time(),
    }


def send_event(payload: dict, port: int | None = None) -> None:
    target_port = port or int(os.environ.get("WAITLAB_HOOK_PORT", DEFAULT_PORT))
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.sendto(encoded, ("127.0.0.1", target_port))


def _read_stdin_utf8() -> str:
    """Read hook JSON as UTF-8 regardless of the Windows console locale."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return sys.stdin.read()
    return stream.read().decode("utf-8", errors="replace")


def main() -> int:
    event_name = ""
    try:
        raw_text = _read_stdin_utf8()
        raw = json.loads(raw_text) if raw_text.strip() else {}
        payload = sanitized_event(raw)
        if payload is not None:
            event_name = payload["event"]
            send_event(payload)
    except Exception:
        # A focus helper must never block or fail a Codex turn.
        pass
    # PermissionRequest uses exit 0 with no output to preserve Codex's normal
    # approval flow. PostToolUse is also observational and needs no response.
    if event_name not in SILENT_EVENTS:
        sys.stdout.write('{"continue":true}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
