from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from waitlab.ui import app_icon  # noqa: E402


def main() -> int:
    QApplication.instance() or QApplication([])
    output = PROJECT_ROOT / "packaging" / "waitlab.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not app_icon(256).pixmap(256, 256).save(str(output), "ICO"):
        raise RuntimeError("Qt 无法导出 WaitLAB 图标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
