from datetime import datetime, timezone

from waitlab.stats_cache import StatsCache


class FakeStorage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def codex_active_seconds(self, period: str, now=None) -> float:
        self.calls.append(("codex", period))
        return 12.0

    def waiting_seconds(self, period: str, now=None) -> float:
        self.calls.append(("waiting", period))
        return 34.0

    def tag_waiting_seconds(self, period: str, now=None) -> dict[str, float]:
        self.calls.append(("tags", period))
        return {"论文写作": 34.0}


def test_stats_cache_reuses_short_lived_period_snapshot() -> None:
    storage = FakeStorage()
    cache = StatsCache(storage)  # type: ignore[arg-type]
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)

    first = cache.get("day", now)
    second = cache.get("day", now)

    assert first == second
    assert storage.calls == [("codex", "day"), ("waiting", "day"), ("tags", "day")]


def test_stats_cache_can_be_invalidated() -> None:
    storage = FakeStorage()
    cache = StatsCache(storage)  # type: ignore[arg-type]
    cache.get("week")
    cache.invalidate()
    cache.get("week")

    assert storage.calls.count(("codex", "week")) == 2
