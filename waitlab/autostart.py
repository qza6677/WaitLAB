from __future__ import annotations

import sys
from pathlib import Path


APP_NAME = "WaitLAB"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command(
    executable: str | Path | None = None,
    script: str | Path | None = None,
    frozen: bool | None = None,
) -> str:
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    executable_path = Path(executable) if executable is not None else Path(sys.executable)
    if is_frozen:
        return f'"{executable_path.resolve()}"'

    pythonw = executable_path.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = executable_path
    script_path = (
        Path(script)
        if script is not None
        else Path(__file__).resolve().parents[1] / "run_waitlab.py"
    )
    return f'"{pythonw.resolve()}" "{script_path.resolve()}"'


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _value_type = winreg.QueryValueEx(key, APP_NAME)
    except FileNotFoundError:
        return False
    return bool(str(value).strip())


def set_autostart(enabled: bool, command: str | None = None) -> None:
    if sys.platform != "win32":
        if enabled:
            raise OSError("开机启动目前仅支持 Windows")
        return
    import winreg

    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(
                key,
                APP_NAME,
                0,
                winreg.REG_SZ,
                command or autostart_command(),
            )
        return

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        return
