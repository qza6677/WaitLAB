from pathlib import Path

from waitlab.autostart import autostart_command
from waitlab.preferences import PopupMode, Preferences
from waitlab.storage import Storage


def test_preferences_round_trip(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    expected = Preferences(
        popup_mode=PopupMode.TRAY_ONLY,
        completion_notifications=False,
        notification_sound=False,
    )

    expected.save(storage)

    assert Preferences.load(storage) == expected
    storage.close()


def test_invalid_popup_mode_falls_back_to_raise(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    storage.set_setting("popup_mode", "unknown")

    assert Preferences.load(storage).popup_mode is PopupMode.RAISE
    storage.close()


def test_in_app_and_system_notifications_are_independent(tmp_path):
    storage = Storage(tmp_path / "waitlab.db")
    expected = Preferences(in_app_notifications=False, completion_notifications=True)
    expected.save(storage)

    loaded = Preferences.load(storage)

    assert loaded.in_app_notifications is False
    assert loaded.completion_notifications is True
    storage.close()


def test_source_autostart_command_quotes_pythonw_and_script(tmp_path):
    python = tmp_path / "Python Folder" / "python.exe"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    pythonw = python.with_name("pythonw.exe")
    pythonw.write_text("", encoding="utf-8")
    script = tmp_path / "Wait LAB" / "run_waitlab.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")

    command = autostart_command(python, script, frozen=False)

    assert command == f'"{pythonw.resolve()}" "{script.resolve()}"'


def test_packaged_autostart_command_only_uses_executable(tmp_path):
    executable = Path(tmp_path) / "WaitLAB.exe"
    executable.write_text("", encoding="utf-8")

    assert autostart_command(executable, frozen=True) == f'"{executable.resolve()}"'
