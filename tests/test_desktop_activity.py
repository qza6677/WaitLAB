import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from waitlab.desktop_activity import DesktopActivityReader, DesktopEventKind


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)


def create_history(path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            CREATE TABLE thread_turns (
                thread_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                completed_at INTEGER,
                duration_ms INTEGER
            )
            """
        )
        connection.commit()


def add_turn(path, thread_id, turn_id, status, started_at, completed_at=None) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO thread_turns VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, turn_id, status, started_at, completed_at, None),
        )
        connection.commit()


def set_status(path, turn_id, status, completed_at=None) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE thread_turns SET status = ?, completed_at = ? WHERE turn_id = ?",
            (status, completed_at, turn_id),
        )
        connection.commit()


def test_initial_poll_emits_only_currently_running_turns(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    add_turn(path, "thread-old", "turn-old", "completed", 100, 120)
    add_turn(path, "thread-live", "turn-live", "inProgress", 200)

    reader = DesktopActivityReader(path, now=lambda: NOW)
    events = reader.poll()

    assert reader.available is True
    assert [(event.kind, event.turn_id) for event in events] == [
        (DesktopEventKind.STARTED, "turn-live")
    ]
    assert events[0].initial is True


def test_new_turn_transitions_from_running_to_completed_once(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    reader = DesktopActivityReader(path, now=lambda: NOW)
    assert reader.poll() == []
    add_turn(path, "thread-1", "turn-1", "inProgress", 1_787_700_000)
    started = reader.poll()
    set_status(path, "turn-1", "completed", 1_787_700_005)
    completed = reader.poll()

    assert [event.kind for event in started] == [DesktopEventKind.STARTED]
    assert [event.kind for event in completed] == [DesktopEventKind.COMPLETED]
    assert reader.poll() == []
    assert reader._known_statuses == {("thread-1", "turn-1"): "completed"}


def test_terminal_turns_are_not_kept_in_poll_tracking_cache(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    reader = DesktopActivityReader(path, now=lambda: NOW, max_rows=32)

    for index in range(80):
        add_turn(
            path,
            f"thread-{index}",
            f"turn-{index}",
            "completed",
            1_787_700_000 + index,
            1_787_700_001 + index,
        )

    assert reader.poll() == []
    assert len(reader._known_statuses) <= 32


def test_poll_reads_a_bounded_recent_window_for_large_history(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    rows = [
        (
            f"thread-{index}",
            f"turn-{index}",
            "completed",
            int(NOW.timestamp()) - index,
            int(NOW.timestamp()) - index + 1,
            None,
        )
        for index in range(2000)
    ]
    with closing(sqlite3.connect(path)) as connection:
        connection.executemany("INSERT INTO thread_turns VALUES (?, ?, ?, ?, ?, ?)", rows)
        connection.commit()

    reader = DesktopActivityReader(path, now=lambda: NOW, max_rows=64)
    assert reader.poll() == []
    assert len(reader.status_snapshot()) <= 64
    assert reader._last_row_id == 2000


def test_fast_completed_turn_is_reported_without_replaying_history(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    add_turn(path, "thread-old", "turn-old", "completed", 100, 120)
    reader = DesktopActivityReader(path, now=lambda: NOW)
    assert reader.poll() == []

    add_turn(path, "thread-new", "turn-new", "completed", 200, 201)
    events = reader.poll()

    assert [(event.kind, event.turn_id) for event in events] == [
        (DesktopEventKind.COMPLETED, "turn-new")
    ]


def test_attention_resume_and_interruption_are_mapped(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    add_turn(path, "thread-1", "turn-1", "inProgress", 1_787_700_000)
    reader = DesktopActivityReader(path, now=lambda: NOW)
    reader.poll()

    set_status(path, "turn-1", "waitingForApproval")
    attention = reader.poll()
    set_status(path, "turn-1", "inProgress")
    resumed = reader.poll()
    set_status(path, "turn-1", "interrupted", 1_787_700_010)
    blocked = reader.poll()

    assert [event.kind for event in attention] == [DesktopEventKind.NEEDS_ATTENTION]
    assert [event.kind for event in resumed] == [DesktopEventKind.RESUMED]
    assert [event.kind for event in blocked] == [DesktopEventKind.BLOCKED]


def test_missing_or_incompatible_database_degrades_without_crashing(tmp_path):
    missing = DesktopActivityReader(tmp_path / "missing.sqlite", now=lambda: NOW)
    assert missing.poll() == []
    assert missing.available is False
    assert "FileNotFoundError" in (missing.error or "")

    incompatible_path = tmp_path / "incompatible.sqlite"
    sqlite3.connect(incompatible_path).close()
    incompatible = DesktopActivityReader(incompatible_path, now=lambda: NOW)
    assert incompatible.poll() == []
    assert incompatible.available is False
    assert "OperationalError" in (incompatible.error or "")


def test_status_snapshot_reads_item_timestamp_without_item_contents(tmp_path):
    path = tmp_path / "thread_history_1.sqlite"
    create_history(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, created_at_ms INTEGER, item_json TEXT)"
        )
        connection.execute(
            "INSERT INTO thread_turns VALUES (?, ?, ?, ?, ?, ?)",
            ("thread-1", "turn-1", "inProgress", 1_787_700_000, None, None),
        )
        connection.execute(
            "INSERT INTO thread_items VALUES (?, ?, ?, ?)",
            ("thread-1", "turn-1", 1_787_700_123_000, "secret content"),
        )
        connection.commit()

    reader = DesktopActivityReader(path, now=lambda: NOW)
    reader.poll()
    snapshot = reader.status_snapshot()[0]

    assert snapshot.turn_id == "turn-1"
    assert snapshot.last_activity_at is not None
    assert snapshot.last_activity_at.timestamp() == 1_787_700_123
