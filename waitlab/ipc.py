from __future__ import annotations

import json
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket


HOOK_PORT = int(os.environ.get("WAITLAB_HOOK_PORT", "38641"))
ALLOWED_EVENTS = {"UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop"}
MAX_DATAGRAM_BYTES = 16 * 1024
MAX_ID_LENGTH = 256
HOOK_TOKEN = os.environ.get("WAITLAB_HOOK_TOKEN", "").strip()


class HookEventServer(QObject):
    event_received = Signal(dict)
    bind_failed = Signal(str)

    def __init__(self, port: int = HOOK_PORT, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.socket = QUdpSocket(self)
        self.is_bound = self.socket.bind(
            QHostAddress.SpecialAddress.LocalHost,
            port,
            QUdpSocket.BindFlag.DontShareAddress,
        )
        self.error_string = "" if self.is_bound else self.socket.errorString()
        if self.is_bound:
            self.socket.readyRead.connect(self._read_pending)

    def _read_pending(self) -> None:
        while self.socket.hasPendingDatagrams():
            if self.socket.pendingDatagramSize() > MAX_DATAGRAM_BYTES:
                self.socket.receiveDatagram()
                continue
            datagram = self.socket.receiveDatagram()
            try:
                # PySide6's QByteArray is bytes-compatible at runtime, but
                # its generated stub omits that protocol on some wheels.
                payload = json.loads(bytes(datagram.data()).decode("utf-8"))  # type: ignore[arg-type]
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            sanitized = self.sanitize_payload(payload)
            if sanitized is None:
                continue
            self.event_received.emit(sanitized)

    @staticmethod
    def sanitize_payload(payload: object) -> dict | None:
        if not isinstance(payload, dict):
            return None
        event = payload.get("event")
        if not isinstance(event, str) or event not in ALLOWED_EVENTS:
            return None
        session_id = payload.get("session_id")
        turn_id = payload.get("turn_id")
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id.strip() or len(session_id) > MAX_ID_LENGTH
        ):
            return None
        if turn_id is not None and (
            not isinstance(turn_id, str) or not turn_id.strip() or len(turn_id) > MAX_ID_LENGTH
        ):
            return None
        if HOOK_TOKEN and payload.get("token") != HOOK_TOKEN:
            return None
        return {
            "event": event,
            "session_id": session_id.strip() if isinstance(session_id, str) else None,
            "turn_id": turn_id.strip() if isinstance(turn_id, str) else None,
        }

