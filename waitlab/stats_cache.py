"""Short-lived Waiting Task statistics snapshots shared by desktop views.

The underlying storage queries are intentionally kept in ``Storage``. This
module only avoids running the same day/week/month queries repeatedly while a
window is repainting; callers can invalidate the cache immediately after a
state-changing operation. ``codex_seconds`` remains a compatibility field for
older callers, but the application disables that query.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from .storage import Storage


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    codex_seconds: float
    waiting_seconds: float
    tag_seconds: dict[str, float]


class StatsCache:
    """Cache one coherent snapshot per period for a short configurable TTL."""

    def __init__(
        self,
        storage: Storage,
        ttl_seconds: float = 1.0,
        *,
        include_codex: bool = True,
    ) -> None:
        self.storage = storage
        self.ttl_seconds = max(0.1, float(ttl_seconds))
        # Kept as an opt-in compatibility switch for older integrations. The
        # WaitLAB application passes ``False`` because Codex is an event
        # source, not a metric to aggregate.
        self.include_codex = bool(include_codex)
        self._cache: dict[str, tuple[float, int, StatsSnapshot]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    def get(self, period: str, now: datetime | None = None) -> StatsSnapshot:
        current = time.monotonic()
        bucket = int((now or datetime.now().astimezone()).timestamp())
        cached = self._cache.get(period)
        if (
            cached is not None
            and cached[1] == bucket
            and current - cached[0] < self.ttl_seconds
        ):
            return cached[2]
        query_now = now or datetime.now().astimezone()
        codex_seconds = (
            self.storage.codex_active_seconds(period, query_now)
            if self.include_codex
            else 0.0
        )
        snapshot = StatsSnapshot(
            codex_seconds=codex_seconds,
            waiting_seconds=self.storage.waiting_seconds(period, query_now),
            tag_seconds=dict(self.storage.tag_waiting_seconds(period, query_now)),
        )
        self._cache[period] = (current, bucket, snapshot)
        return snapshot
