from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
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
    always_on_top: bool = True
    auto_check_updates: bool = True
    quiet_hours_enabled: bool = False
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"

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
            always_on_top=_read_bool(storage, "always_on_top", True),
            auto_check_updates=_read_bool(storage, "auto_check_updates", True),
            quiet_hours_enabled=_read_bool(storage, "quiet_hours_enabled", False),
            quiet_start=storage.get_setting("quiet_start", "22:00"),
            quiet_end=storage.get_setting("quiet_end", "08:00"),
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
        for key, enabled in (
            ("always_on_top", self.always_on_top),
            ("auto_check_updates", self.auto_check_updates),
            ("quiet_hours_enabled", self.quiet_hours_enabled),
        ):
            storage.set_setting(key, "1" if enabled else "0")
        storage.set_setting("quiet_start", self.quiet_start)
        storage.set_setting("quiet_end", self.quiet_end)

    def is_quiet_now(self, now: time | None = None) -> bool:
        if not self.quiet_hours_enabled:
            return False
        try:
            start = time.fromisoformat(self.quiet_start)
            end = time.fromisoformat(self.quiet_end)
        except ValueError:
            return False
        current = now or datetime.now().time()
        return start <= current < end if start <= end else current >= start or current < end
