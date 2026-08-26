from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .storage import Storage


class PopupMode(StrEnum):
    RAISE = "raise"
    QUIET = "quiet"
    TRAY_ONLY = "tray_only"


def _read_bool(storage: Storage, key: str, default: bool) -> bool:
    raw = storage.get_setting(key, "1" if default else "0").strip().casefold()
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class Preferences:
    popup_mode: PopupMode = PopupMode.RAISE
    completion_notifications: bool = True
    notification_sound: bool = True

    @classmethod
    def load(cls, storage: Storage) -> "Preferences":
        raw_mode = storage.get_setting("popup_mode", PopupMode.RAISE.value)
        try:
            popup_mode = PopupMode(raw_mode)
        except ValueError:
            popup_mode = PopupMode.RAISE
        return cls(
            popup_mode=popup_mode,
            completion_notifications=_read_bool(
                storage,
                "completion_notifications",
                True,
            ),
            notification_sound=_read_bool(storage, "notification_sound", True),
        )

    def save(self, storage: Storage) -> None:
        storage.set_setting("popup_mode", self.popup_mode.value)
        storage.set_setting(
            "completion_notifications",
            "1" if self.completion_notifications else "0",
        )
        storage.set_setting(
            "notification_sound",
            "1" if self.notification_sound else "0",
        )
