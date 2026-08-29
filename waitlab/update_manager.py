"""Thread-safe orchestration for update checks and downloads.

The Qt window owns presentation; this small coordinator owns concurrency so a
double click cannot start overlapping network requests or downloads.  Workers
are cancellable at the coordinator boundary: network helpers may still be
inside a blocking request, but their callbacks are suppressed during shutdown.
"""

from __future__ import annotations

from threading import Event, Lock, Thread, current_thread
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
        self._shutdown_event = Event()
        self._threads: set[Thread] = set()
        self._threads_lock = Lock()

    @property
    def check_busy(self) -> bool:
        return self._check_lock.locked()

    @property
    def download_busy(self) -> bool:
        return self._download_lock.locked()

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_event.is_set()

    def _register_thread(self, thread: Thread) -> bool:
        with self._threads_lock:
            if self._shutdown_event.is_set():
                return False
            self._threads.add(thread)
        try:
            thread.start()
        except BaseException:
            with self._threads_lock:
                self._threads.discard(thread)
            raise
        return True

    def _unregister_current_thread(self) -> None:
        with self._threads_lock:
            self._threads.discard(current_thread())

    def check(self, callback: UpdateCallback) -> bool:
        if self._shutdown_event.is_set() or not self._check_lock.acquire(blocking=False):
            return False

        def worker() -> None:
            try:
                try:
                    result: UpdateResult = fetch_latest_release(self.current_version)
                except BaseException as exc:  # forward to the Qt signal owner
                    result = exc
                if not self._shutdown_event.is_set():
                    callback(result)
            finally:
                self._check_lock.release()
                self._unregister_current_thread()

        thread = Thread(target=worker, daemon=True, name="waitlab-update-check")
        if not self._register_thread(thread):
            self._check_lock.release()
            return False
        return True

    def download(self, release: ReleaseInfo, callback: UpdateCallback) -> bool:
        if self._shutdown_event.is_set() or not self._download_lock.acquire(blocking=False):
            return False

        def worker() -> None:
            try:
                try:
                    result: UpdateResult = download_verified_installer(release)
                except BaseException as exc:  # forward to the Qt signal owner
                    result = exc
                if not self._shutdown_event.is_set():
                    callback(result)
            finally:
                self._download_lock.release()
                self._unregister_current_thread()

        thread = Thread(target=worker, daemon=True, name="waitlab-update-download")
        if not self._register_thread(thread):
            self._download_lock.release()
            return False
        return True

    def shutdown(self, join_timeout: float = 1.0) -> None:
        """Stop accepting work and wait briefly for in-flight workers.

        The HTTP helpers do not currently accept a cancellation token, so a
        request that is blocked in the OS may outlive this bounded wait. Its
        callback is still prevented from touching a closing Qt window.
        """

        self._shutdown_event.set()
        current = current_thread()
        with self._threads_lock:
            threads = tuple(self._threads)
        timeout = max(0.0, float(join_timeout))
        for thread in threads:
            if thread is not current:
                thread.join(timeout=timeout)