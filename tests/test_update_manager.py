import threading
import time

from waitlab.update_manager import UpdateManager
from waitlab.updates import ReleaseInfo


def test_update_manager_deduplicates_checks(monkeypatch) -> None:
    started = threading.Event()
    release_gate = threading.Event()
    release = ReleaseInfo("0.5.7", "installer", "checksums", "page")

    def fake_fetch(_version: str):
        started.set()
        release_gate.wait(2)
        return release

    monkeypatch.setattr("waitlab.update_manager.fetch_latest_release", fake_fetch)
    manager = UpdateManager("0.5.6")
    results: list[object] = []
    assert manager.check(results.append) is True
    assert started.wait(1)
    assert manager.check(results.append) is False
    release_gate.set()
    for _ in range(20):
        if results:
            break
        time.sleep(0.05)
    assert results == [release]
    assert manager.check_busy is False

