from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from waitlab.service import WaitLabService
from waitlab.storage import Storage
from waitlab.ui import PetWindow


def render() -> None:
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    output_dir = PROJECT_ROOT / "artifacts"
    output_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="waitlab-preview-") as temp_dir:
        storage = Storage(Path(temp_dir) / "preview.db")
        service = WaitLabService(storage)
        window = PetWindow(service)

        window.apply_update(service.on_ai_started("preview", "preview-turn"))
        window.show()
        app.processEvents()
        window.grab().save(str(output_dir / "waiting-picker.png"))

        task = service.storage.add_manual_task("核对图 3 的统计标注与图注")
        window.start_focus(task)
        app.processEvents()
        window.grab().save(str(output_dir / "focus-session.png"))

        window.close()
        storage.close()


if __name__ == "__main__":
    render()
