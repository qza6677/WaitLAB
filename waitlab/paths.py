from __future__ import annotations

import os
from pathlib import Path


def data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "WaitLAB"
    return Path.home() / ".waitlab"


def database_path() -> Path:
    return data_directory() / "waitlab.db"

