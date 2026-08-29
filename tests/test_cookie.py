from pathlib import Path

from waitlab.cookie import (
    COOKIE_STATE_FILES,
    CookieAssets,
    CookieContext,
    CookieState,
    CookieStateMachine,
    coerce_cookie_state,
    resolve_cookie_state,
)


def test_mode_names_map_to_cookie_states():
    assert coerce_cookie_state("focus") is CookieState.WORKING
    assert coerce_cookie_state("done") is CookieState.AI_COMPLETE
    assert coerce_cookie_state("blocked") is CookieState.ERROR
    assert coerce_cookie_state("unknown-mode") is CookieState.IDLE


def test_cookie_assets_resolve_states_and_fallback(tmp_path: Path):
    asset_dir = tmp_path / "sprites-96"
    asset_dir.mkdir()
    (asset_dir / COOKIE_STATE_FILES[CookieState.IDLE]).write_bytes(b"idle")
    assets = CookieAssets(asset_dir)

    assert assets.available()
    assert assets.path_for(CookieState.IDLE) == asset_dir / "01-idle.png"
    assert assets.path_for(CookieState.WORKING) == asset_dir / "01-idle.png"


def test_cookie_assets_use_high_resolution_source_for_large_cookie(tmp_path: Path):
    asset_dir = tmp_path / "sprites-96"
    high_res_dir = tmp_path / "sprites-256"
    asset_dir.mkdir()
    high_res_dir.mkdir()
    (asset_dir / COOKIE_STATE_FILES[CookieState.IDLE]).write_bytes(b"96")
    (high_res_dir / COOKIE_STATE_FILES[CookieState.IDLE]).write_bytes(b"256")
    assets = CookieAssets(asset_dir)

    assert assets.path_for(CookieState.IDLE, 88) == asset_dir / "01-idle.png"
    assert assets.path_for(CookieState.IDLE, 120) == high_res_dir / "01-idle.png"


def test_cookie_assets_return_none_when_directory_is_missing(tmp_path: Path):
    assets = CookieAssets(tmp_path / "missing")

    assert not assets.available()
    assert assets.path_for(CookieState.IDLE) is None


def test_cookie_state_precedence_keeps_attention_visible():
    context = CookieContext(
        focus_active=True,
        focus_paused=True,
        ai_active=True,
        ai_needs_attention=True,
        completion_visible=True,
        terminal_error=True,
    )

    assert resolve_cookie_state(context) is CookieState.ATTENTION


def test_cookie_state_machine_resolves_focus_and_completion_states():
    machine = CookieStateMachine()

    assert machine.transition(CookieContext(focus_active=True)) is CookieState.WORKING
    assert machine.transition(CookieContext(focus_active=True, focus_paused=True)) is CookieState.PAUSED
    # Once a micro-task is selected, Cookie reflects that user focus even
    # while the Codex turn is still running in the background.
    assert machine.transition(CookieContext(focus_active=True, ai_active=True)) is CookieState.WORKING
    assert machine.transition(
        CookieContext(focus_active=True, focus_paused=True, ai_active=True)
    ) is CookieState.PAUSED
    assert machine.transition(CookieContext(ai_active=True)) is CookieState.WAITING
    assert machine.transition(CookieContext(completion_visible=True)) is CookieState.AI_COMPLETE
    assert machine.state is CookieState.AI_COMPLETE
    assert machine.transition(CookieContext(task_completion_visible=True)) is CookieState.TASK_COMPLETE
    assert machine.transition(
        CookieContext(task_completion_visible=True, ai_active=True)
    ) is CookieState.TASK_COMPLETE
    assert machine.state is CookieState.TASK_COMPLETE


def test_cookie_state_machine_marks_terminal_error():
    assert resolve_cookie_state(
        CookieContext(focus_active=True, terminal_error=True)
    ) is CookieState.ERROR
    assert resolve_cookie_state(
        CookieContext(completion_visible=True, terminal_error=True)
    ) is CookieState.ERROR
