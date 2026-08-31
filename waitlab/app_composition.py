"""Application composition root for persistence-backed services.

Qt wiring still lives in :mod:`waitlab.app`, but construction of the domain
services is kept here so tests and future front ends can create the same
runtime without duplicating database setup knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .service import WaitLabService
from .storage import Storage


@dataclass(slots=True)
class ApplicationContext:
    """Long-lived application services and their persistence boundary."""

    storage: Storage
    service: WaitLabService

    @classmethod
    def open(
        cls,
        database: Path,
        *,
        track_ai_time: bool = False,
    ) -> "ApplicationContext":
        storage = Storage(database, track_ai_time=track_ai_time)
        try:
            service = WaitLabService(storage)
        except Exception:
            storage.close()
            raise
        return cls(storage=storage, service=service)

    def close(self) -> None:
        """Close the persistence boundary owned by this context."""

        self.storage.close()


def open_application_context(
    database: Path,
    *,
    track_ai_time: bool = False,
) -> ApplicationContext:
    """Create the standard WaitLAB service graph for an application process."""

    return ApplicationContext.open(database, track_ai_time=track_ai_time)
