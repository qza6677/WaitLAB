from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from waitlab.service import WaitLabService  # noqa: E402
from waitlab.storage import Storage  # noqa: E402
from waitlab.ui import PetWindow  # noqa: E402


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    output_dir = PROJECT_ROOT / "artifacts"
    output_dir.mkdir(exist_ok=True)
    temp_dir = tempfile.TemporaryDirectory(prefix="waitlab-native-preview-")
    storage = Storage(Path(temp_dir.name) / "preview.db")
    service = WaitLabService(storage)
    window = PetWindow(service)
    window.apply_update(service.on_ai_started("preview", "preview-turn"))
    window.show()

    def capture_waiting() -> None:
        window.grab().save(str(output_dir / "waiting-picker-native.png"))
        task = service.storage.add_manual_task("整理一条待办的下一步")
        window.start_focus(task)

    def capture_focus() -> None:
        window.grab().save(str(output_dir / "focus-session-native.png"))
        window.apply_update(
            service.on_ai_needs_attention("preview", "preview-turn")
        )

    def capture_attention() -> None:
        window.grab().save(str(output_dir / "permission-attention-native.png"))
        window.open_settings()

    def capture_settings() -> None:
        if window.settings_dialog is not None:
            window.settings_dialog.grab().save(str(output_dir / "settings-native.png"))
            window.settings_dialog.close()
        window.close()
        storage.close()
        temp_dir.cleanup()
        app.quit()

    QTimer.singleShot(500, capture_waiting)
    QTimer.singleShot(1000, capture_focus)
    QTimer.singleShot(1500, capture_attention)
    QTimer.singleShot(2200, capture_settings)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
