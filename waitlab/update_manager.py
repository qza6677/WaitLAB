"""Thread-safe orchestration for update checks and downloads.

The Qt window owns presentation; this small coordinator owns concurrency so a
double click cannot start overlapping network requests or downloads.
"""

from __future__ import annotations

from threading import Lock, Thread
from pathlib import Path
from typing import Callable

from .updates import (
    ReleaseInfo,
    download_verified_installer,
    fetch_latest_release,
)


UpdateResult = ReleaseInfo | Path | None | BaseException
UpdateCallback = Callable[[UpdateResult], None]


class UpdateManager:
    def __init__(self, current_version: str) -> None:
        self.current_version = current_version
        self._check_lock = Lock()
        self._download_lock = Lock()

    @property
    def check_busy(self) -> bool:
        return self._check_lock.locked()

    @property
    def download_busy(self) -> bool:
        return self._download_lock.locked()

    def check(self, callback: UpdateCallback) -> bool:
        if not self._check_lock.acquire(blocking=False):
            return False

        def worker() -> None:
            try:
                try:
                    result: UpdateResult = fetch_latest_release(self.current_version)
                except BaseException as exc:  # forward to the Qt signal owner
                    result = exc
                callback(result)
            finally:
                self._check_lock.release()

        Thread(target=worker, daemon=True, name="waitlab-update-check").start()
        return True

    def download(self, release: ReleaseInfo, callback: UpdateCallback) -> bool:
        if not self._download_lock.acquire(blocking=False):
            return False

        def worker() -> None:
            try:
                try:
                    result: UpdateResult = download_verified_installer(release)
                except BaseException as exc:  # forward to the Qt signal owner
                    result = exc
                callback(result)
            finally:
                self._download_lock.release()

        Thread(target=worker, daemon=True, name="waitlab-update-download").start()
        return True

