from waitlab.ui import PresentationMode, choose_presentation_mode


def test_idle_uses_icon_mode():
    assert choose_presentation_mode(False, False) is PresentationMode.ICON


def test_open_picker_uses_picker_mode_without_focus():
    assert choose_presentation_mode(False, True) is PresentationMode.PICKER


def test_focus_always_uses_player_mode():
    assert choose_presentation_mode(True, False) is PresentationMode.PLAYER
    assert choose_presentation_mode(True, True) is PresentationMode.PLAYER
