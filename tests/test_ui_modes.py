from waitlab.ui import PresentationMode, choose_presentation_mode


def test_idle_uses_icon_mode():
    assert choose_presentation_mode(False, False) is PresentationMode.ICON


def test_open_picker_uses_picker_mode_without_focus():
    assert choose_presentation_mode(False, True) is PresentationMode.PICKER


def test_focus_always_uses_player_mode():
    assert choose_presentation_mode(True, False) is PresentationMode.PLAYER
    assert choose_presentation_mode(True, True) is PresentationMode.PLAYER


def test_hidden_page_keeps_active_task_in_compact_player():
    assert (
        choose_presentation_mode(True, False, page_hidden=True)
        is PresentationMode.COMPACT_PLAYER
    )
    assert choose_presentation_mode(False, True, page_hidden=True) is PresentationMode.ICON


def test_notice_opens_a_bubble_without_requiring_a_task():
    assert choose_presentation_mode(False, False, notice_open=True) is PresentationMode.NOTICE
    assert choose_presentation_mode(True, False, notice_open=True) is PresentationMode.PLAYER
