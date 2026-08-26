from __future__ import annotations

import json
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket


HOOK_PORT = int(os.environ.get("WAITLAB_HOOK_PORT", "38641"))
ALLOWED_EVENTS = {"UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop"}


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
            datagram = self.socket.receiveDatagram()
            try:
                payload = json.loads(bytes(datagram.data()).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if payload.get("event") in ALLOWED_EVENTS:
                self.event_received.emit(payload)
