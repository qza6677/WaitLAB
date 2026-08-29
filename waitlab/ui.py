from __future__ import annotations

import math
import random
import time
import hashlib
from html import escape
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTime,
    QTimer,
    QSize,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .autostart import is_autostart_enabled, set_autostart
from .connection import HookConnectionInfo, HookConnectionMonitor, HookConnectionState
from .cookie import (
    CookieAssets,
    CookieContext,
    CookieState,
    CookieStateMachine,
    coerce_cookie_state,
)
from .desktop_activity import DesktopActivityEvent, DesktopEventKind, DesktopTurnSnapshot
from .models import (
    DEFAULT_TAG,
    CompletedTaskSummary,
    CompletedFocusRecord,
    DefaultTaskEntry,
    ServiceUpdate,
    TagTimeBucket,
    Task,
)
from .preferences import PopupMode, Preferences
from .service import WaitLabService
from .storage import DEFAULT_TASKS
from .task_filters import filter_and_sort_tasks
from . import __version__
from .updates import (
    ReleaseInfo,
    cleanup_download_directory,
    describe_update_error,
    launch_installer,
)
from .update_manager import UpdateManager
from .windowing import apply_native_topmost


COLORS = {
    "ink": "#203133",
    "muted": "#748183",
    "cream": "#FFF9EF",
    "mint": "#63B89C",
    "mint_dark": "#367D69",
    "peach": "#F3A77C",
    "yellow": "#F6C85F",
    "line": "#E9E2D7",
    "white": "#FFFFFF",
}

DESKTOP_SOURCE_GRACE_SECONDS = 12.0


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def app_icon(size: int = 64) -> QIcon:
    """Use Cookie's transparent idle sprite as the application identity."""

    assets = CookieAssets()
    path = assets.path_for(CookieState.IDLE, size)
    if path is not None:
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            return QIcon(pixmap)
    # Keep a tiny vector fallback for source checkouts with missing assets.
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS["mint"]))
    painter.drawRoundedRect(QRectF(4, 4, size - 8, size - 8), 18, 18)
    painter.setBrush(QColor(COLORS["cream"]))
    painter.drawEllipse(QRectF(14, 17, size - 28, size - 25))
    painter.setBrush(QColor(COLORS["ink"]))
    painter.drawEllipse(QRectF(24, 30, 5, 7))
    painter.drawEllipse(QRectF(size - 29, 30, 5, 7))
    painter.end()
    return QIcon(pixmap)


class PetFace(QWidget):
    clicked = Signal()
    context_requested = Signal(QPoint)
    drag_started = Signal(QPoint)
    drag_moved = Signal(QPoint)
    drag_finished = Signal()
    state_changed = Signal(object)

    def __init__(self, size: int = 58, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._display_size = max(48, min(160, int(size)))
        self.setFixedSize(self._display_size, self._display_size)
        self.mode = "idle"
        self.cookie_state = CookieState.IDLE
        self.assets = CookieAssets()
        self._cookie_pixmap = QPixmap()
        self._previous_cookie_pixmap = QPixmap()
        self._transition_progress = 1.0
        self.phase = 0.0
        self._press_position: QPoint | None = None
        self._dragging = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(90)
        self._transition = QVariantAnimation(self)
        self._transition.setDuration(180)
        self._transition.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._transition.valueChanged.connect(self._on_transition_value)
        self._transition.finished.connect(self._finish_transition)
        self.set_state(CookieState.IDLE)

    def set_size(self, size: int) -> None:
        next_size = max(48, min(160, int(size)))
        if next_size == self._display_size:
            return
        self._display_size = next_size
        self.setFixedSize(next_size, next_size)
        # A larger widget may switch from the 96px source to the 256px
        # source.  Reload the current state even though the enum value did
        # not change.
        self._transition.stop()
        self._previous_cookie_pixmap = QPixmap()
        path = self.assets.path_for(self.cookie_state, next_size)
        self._cookie_pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        self.update()

    def set_mode(self, mode: str) -> None:
        self.set_state(coerce_cookie_state(mode))

    def set_state(self, state: CookieState | str) -> None:
        next_state = coerce_cookie_state(state)
        if next_state == self.cookie_state and not self._cookie_pixmap.isNull():
            return
        previous_pixmap = self._cookie_pixmap
        self.cookie_state = next_state
        self.mode = self.cookie_state.value
        path = self.assets.path_for(self.cookie_state, self._display_size)
        next_pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        self._transition.stop()
        if not previous_pixmap.isNull() and not next_pixmap.isNull():
            self._previous_cookie_pixmap = previous_pixmap
            self._transition_progress = 0.0
            self._cookie_pixmap = next_pixmap
            self._transition.setStartValue(0.0)
            self._transition.setEndValue(1.0)
            self._transition.start()
        else:
            self._previous_cookie_pixmap = QPixmap()
            self._transition_progress = 1.0
            self._cookie_pixmap = next_pixmap
        self.state_changed.emit(self.cookie_state)
        self.update()

    def _on_transition_value(self, value) -> None:
        self._transition_progress = float(value)
        self.update()

    def _finish_transition(self) -> None:
        self._previous_cookie_pixmap = QPixmap()
        self._transition_progress = 1.0
        self.update()

    def _animate(self) -> None:
        self.phase += 0.16
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.globalPosition().toPoint()
            self._dragging = False
            self.drag_started.emit(self._press_position)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if (current - self._press_position).manhattanLength() >= 4:
                self._dragging = True
            if self._dragging:
                self.drag_moved.emit(current)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press_position is not None:
            self.drag_finished.emit()
            if not self._dragging:
                self.clicked.emit()
            self._press_position = None
            self._dragging = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        if not self._cookie_pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            bob = math.sin(self.phase) * 1.0 if self.cookie_state in {
                CookieState.WAITING,
                CookieState.ATTENTION,
                CookieState.ERROR,
            } else 0.0
            if not self._previous_cookie_pixmap.isNull() and self._transition_progress < 1.0:
                self._draw_cookie_pixmap(
                    painter,
                    self._previous_cookie_pixmap,
                    1.0 - self._transition_progress,
                    bob,
                )
            self._draw_cookie_pixmap(painter, self._cookie_pixmap, self._transition_progress, bob)
            painter.end()
            return

        # Keep a small vector fallback so a missing asset never makes the
        # desktop pet disappear (for example during a development checkout).
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width(), self.height()) / 74.0
        painter.scale(scale, scale)
        bob = math.sin(self.phase) * 2 if self.mode in {"waiting", "attention", "blocked", "error"} else 0
        painter.translate(0, bob)

        accent = {
            "idle": QColor(COLORS["mint"]),
            "waiting": QColor(COLORS["yellow"]),
            "focus": QColor(COLORS["mint"]),
            "working": QColor(COLORS["mint"]),
            "done": QColor(COLORS["peach"]),
            "ai-complete": QColor(COLORS["peach"]),
            "paused": QColor("#B7A6D9"),
            "attention": QColor(COLORS["peach"]),
            "blocked": QColor("#D97862"),
            "error": QColor("#D97862"),
        }.get(self.mode, QColor(COLORS["mint"]))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent.lighter(145))
        painter.drawEllipse(QRectF(4, 7, 66, 61))
        painter.setBrush(accent)
        left_ear = QPainterPath()
        left_ear.moveTo(14, 23)
        left_ear.lineTo(18, 5)
        left_ear.lineTo(31, 20)
        left_ear.closeSubpath()
        right_ear = QPainterPath()
        right_ear.moveTo(43, 20)
        right_ear.lineTo(56, 5)
        right_ear.lineTo(60, 24)
        right_ear.closeSubpath()
        painter.drawPath(left_ear)
        painter.drawPath(right_ear)

        painter.setBrush(QColor(COLORS["cream"]))
        painter.drawEllipse(QRectF(13, 19, 48, 43))
        painter.setBrush(QColor(COLORS["ink"]))
        eye_height = 2 if self.mode in {"done", "ai-complete"} else 6
        painter.drawRoundedRect(QRectF(27, 35, 4, eye_height), 2, 2)
        painter.drawRoundedRect(QRectF(44, 35, 4, eye_height), 2, 2)
        painter.setPen(QPen(QColor(COLORS["ink"]), 2))
        if self.mode in {"done", "ai-complete"}:
            painter.drawArc(QRectF(32, 38, 12, 10), 200 * 16, 140 * 16)
        else:
            painter.drawArc(QRectF(34, 42, 8, 5), 200 * 16, 140 * 16)

    def _draw_cookie_pixmap(
        self,
        painter: QPainter,
        pixmap: QPixmap,
        opacity: float,
        bob: float,
    ) -> None:
        if opacity <= 0.0:
            return
        target = self.rect().adjusted(1, 1, -1, -1)
        target.translate(0, round(bob))
        painter.save()
        painter.setOpacity(opacity)
        painter.drawPixmap(target, pixmap)
        painter.restore()


class PresentationMode(StrEnum):
    ICON = "icon"
    PICKER = "picker"
    PLAYER = "player"
    COMPACT_PLAYER = "compact_player"
    NOTICE = "notice"


def choose_presentation_mode(
    has_focus: bool,
    picker_open: bool,
    page_hidden: bool = False,
    notice_open: bool = False,
    focus_paused: bool = False,
) -> PresentationMode:
    if has_focus and not (picker_open and focus_paused):
        return (
            PresentationMode.COMPACT_PLAYER
            if page_hidden
            else PresentationMode.PLAYER
        )
    if page_hidden:
        return PresentationMode.ICON
    if picker_open:
        return PresentationMode.PICKER
    if notice_open:
        return PresentationMode.NOTICE
    return PresentationMode.ICON


TAG_TONES: tuple[tuple[str, str, str], ...] = (
    ("purple", "#664C8F", "#F0E7FA"),
    ("blue", "#2D6792", "#E4F1FB"),
    ("teal", "#2E7567", "#E3F5EE"),
    ("orange", "#9A5B25", "#FFF0DD"),
    ("yellow", "#80641A", "#FFF6D8"),
    ("red", "#9B4F50", "#FDE8E7"),
    ("slate", "#53636C", "#EDF1F3"),
)
TAG_TONE_BY_NAME = {
    DEFAULT_TAG: "slate",
    "写作": "purple",
    "论文写作": "purple",
    "阅读": "blue",
    "文献阅读": "blue",
    "编码": "teal",
    "Vibe coding": "teal",
    "整理": "yellow",
    "工作/项目": "orange",
}


def tag_tone(tag: str) -> str:
    """Return a stable visual tone for a user-defined tag."""

    clean_tag = str(tag).strip()
    known = TAG_TONE_BY_NAME.get(clean_tag)
    if known is not None:
        return known
    digest = hashlib.sha1(clean_tag.encode("utf-8")).digest()
    return TAG_TONES[digest[0] % len(TAG_TONES)][0]


def tag_tone_colors(tone: str) -> tuple[str, str]:
    for name, foreground, background in TAG_TONES:
        if name == tone:
            return foreground, background
    return TAG_TONES[-1][1:]


def _tag_chip_stylesheet() -> str:
    return f"""
    QPushButton#tagChip {{
        min-height: 22px; max-height: 26px; padding: 3px 8px;
        border-radius: 12px; font-size: 10px; font-weight: 650;
    }}
    QPushButton#tagChip:checked {{
        border: 2px solid {COLORS['ink']}; padding: 2px 7px;
    }}
    QPushButton#tagChip[tone="purple"] {{ color: #664C8F; background: #F0E7FA; border: 1px solid #D8C4EA; }}
    QPushButton#tagChip[tone="blue"] {{ color: #2D6792; background: #E4F1FB; border: 1px solid #C4DFEF; }}
    QPushButton#tagChip[tone="teal"] {{ color: #2E7567; background: #E3F5EE; border: 1px solid #BFE4D6; }}
    QPushButton#tagChip[tone="orange"] {{ color: #9A5B25; background: #FFF0DD; border: 1px solid #F0D0A8; }}
    QPushButton#tagChip[tone="yellow"] {{ color: #80641A; background: #FFF6D8; border: 1px solid #EBD98E; }}
    QPushButton#tagChip[tone="red"] {{ color: #9B4F50; background: #FDE8E7; border: 1px solid #F0C6C4; }}
    QPushButton#tagChip[tone="slate"] {{ color: #53636C; background: #EDF1F3; border: 1px solid #D4DEE2; }}
    QPushButton#tagChip:hover {{ background: #FFFFFF; }}
    QWidget#quickTaskTag {{ min-height: 30px; }}
    QPushButton#tagChip[compact="true"] {{
        min-height: 22px; max-height: 24px; padding: 2px 7px;
        border-radius: 11px; font-size: 9px;
    }}
    QPushButton#tagChip[compact="true"]:checked {{
        border-width: 1px; padding: 2px 7px;
    }}
    """


class FlowLayout(QLayout):
    """A small wrapping layout for tag chips."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(6)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            item_size = item.sizeHint()
            next_x = x + item_size.width() + (spacing if line_height else 0)
            if next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + spacing
                next_x = x + item_size.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, item_size.width(), item_size.height()))
            x = next_x
            line_height = max(line_height, item_size.height())
        return y + line_height - rect.y() + margins.bottom()


class TagChipBar(QWidget):
    """A stable, popup-free single-selection group of colored tag chips."""

    currentTextChanged = Signal(str)
    tag_selected = Signal(str)
    geometry_changed = Signal()

    def __init__(
        self,
        tags: list[str] | tuple[str, ...] = (),
        selected: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._selected = ""
        self._buttons: dict[str, QPushButton] = {}
        self._compact = False
        self._sync_height_pending = False
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._layout = FlowLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(38)
        self.set_tags(tags, selected)

    def set_compact(self, compact: bool = True) -> None:
        """Use a denser chip layout for narrow home-page controls."""

        next_compact = bool(compact)
        mode_changed = next_compact != self._compact
        # This method is also called after the parent card is polished.  The
        # old implementation always reset the baseline minimum height here,
        # which changed a wrapped chip bar from (for example) 54px back to
        # 30px and emitted geometry_changed again.  Only change that baseline
        # when the mode changes; repeated calls are otherwise idempotent.
        self._compact = next_compact
        if mode_changed:
            self._layout.setSpacing(4 if self._compact else 6)
            self.setMinimumHeight(30 if self._compact else 38)
        density_changed = mode_changed
        for button in self._buttons.values():
            should_reapply = mode_changed or (
                self._compact and button.height() != 24
            ) or (not self._compact and bool(button.styleSheet()))
            if not should_reapply:
                continue
            density_changed = True
            self._apply_chip_density(button)
            button.style().unpolish(button)
            button.style().polish(button)
            # Re-apply the fixed geometry after the parent stylesheet is
            # polished; Qt styles may restore their default button metric.
            if self._compact:
                button.setFixedHeight(24)
        if mode_changed or density_changed:
            self.updateGeometry()
            self._schedule_sync_height()

    def tags(self) -> list[str]:
        return list(self._tags)

    def currentText(self) -> str:  # noqa: N802 - QComboBox-compatible API
        return self._selected

    def setCurrentText(self, text: str) -> None:  # noqa: N802 - compatibility API
        clean_text = str(text).strip()
        if clean_text in self._buttons:
            self._select(clean_text, emit=True)

    def set_tags(
        self,
        tags: list[str] | tuple[str, ...],
        selected: str | None = None,
    ) -> None:
        next_tags: list[str] = []
        for value in tags:
            clean_value = str(value).strip()
            if clean_value and clean_value not in next_tags:
                next_tags.append(clean_value)
        if not next_tags:
            next_tags = [DEFAULT_TAG]
        target = selected if selected in next_tags else self._selected
        if target not in next_tags:
            target = next_tags[0]
        if next_tags == self._tags:
            self._select(target, emit=False)
            return

        while self._layout.count():
            layout_item = self._layout.takeAt(0)
            if layout_item is None:
                break
            button = layout_item.widget()
            if button is not None:
                self._button_group.removeButton(button)
                button.deleteLater()
        self._buttons.clear()
        self._tags = next_tags
        for tag in self._tags:
            button = QPushButton(tag, self)
            button.setObjectName("tagChip")
            button.setProperty("tone", tag_tone(tag))
            button.setProperty("compact", "true" if self._compact else "false")
            self._apply_chip_density(button)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"选择标签：{tag}")
            button.clicked.connect(lambda _checked, value=tag: self._select(value, emit=True))
            self._button_group.addButton(button)
            self._layout.addWidget(button)
            self._buttons[tag] = button
        self._select(target, emit=False)
        self.updateGeometry()
        self._schedule_sync_height()

    def _apply_chip_density(self, button: QPushButton) -> None:
        if self._compact:
            foreground, background = tag_tone_colors(tag_tone(button.text()))
            compact_font = button.font()
            compact_font.setPointSize(9)
            button.setFont(compact_font)
            # The parent card stylesheet supplies the normal chip rules.  A
            # widget-local rule is used for the compact home variant because
            # Qt does not reliably re-polish dynamic property selectors on a
            # Python-defined QWidget after the parent stylesheet is applied.
            button.setStyleSheet(
                "QPushButton#tagChip {"
                f"min-height:20px; max-height:24px; padding:2px 6px; "
                f"border-radius:11px; font-size:9px; color:{foreground}; "
                f"background:{background}; border:1px solid {background};"
                "} QPushButton#tagChip:checked { border:1px solid #203B3A; }"
            )
            button.setFixedHeight(24)
        else:
            button.setStyleSheet("")
            button.setMinimumHeight(0)
            button.setMaximumHeight(16777215)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_sync_height()

    def _schedule_sync_height(self) -> None:
        """Coalesce queued height recalculations into one GUI callback."""

        if self._sync_height_pending:
            return
        self._sync_height_pending = True
        QTimer.singleShot(0, self._run_scheduled_sync_height)

    def _run_scheduled_sync_height(self) -> None:
        self._sync_height_pending = False
        self.sync_height()

    def sync_height(self) -> None:
        if self.width() <= 0:
            return
        required = max(30 if self._compact else 38, self._layout.heightForWidth(self.width()))
        if self.minimumHeight() != required:
            self.setMinimumHeight(required)
            self.updateGeometry()
            self.geometry_changed.emit()

    def _select(self, tag: str, *, emit: bool) -> None:
        if tag not in self._buttons:
            return
        changed = self._selected != tag
        self._selected = tag
        button = self._buttons[tag]
        if not button.isChecked():
            button.setChecked(True)
        if emit and changed:
            self.currentTextChanged.emit(tag)
            self.tag_selected.emit(tag)


class TagManagerDialog(QDialog):
    """Manage the shared labels used by manual and fixed Waiting Tasks."""

    tags_changed = Signal()

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Waiting Task · 标签管理")
        self.setMinimumSize(420, 380)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(_dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("任务标签")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        subtitle = QLabel("标签会同步应用到手动任务、固定循环任务和历史记录。按住 Ctrl 可多选并批量删除；相关记录会归入“未分类”。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.tag_list.itemSelectionChanged.connect(self._fill_selected_tag)
        layout.addWidget(self.tag_list, 1)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("输入新标签，或选择标签后输入新名称…")
        self.tag_input.returnPressed.connect(self._add_tag)
        layout.addWidget(self.tag_input)

        actions = QHBoxLayout()
        add_button = QPushButton("新增")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_tag)
        rename_button = QPushButton("修改选中")
        rename_button.clicked.connect(self._rename_tag)
        delete_button = QPushButton("删除选中")
        delete_button.clicked.connect(self._delete_tag)
        actions.addWidget(add_button)
        actions.addWidget(rename_button)
        actions.addWidget(delete_button)
        actions.addStretch()
        close_button = QPushButton("完成")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        selected = self._selected_tag()
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        usage = self.service.storage.tag_usage_counts()
        for tag in self.service.storage.available_tags():
            tone = tag_tone(tag)
            foreground, background = tag_tone_colors(tone)
            item = QListWidgetItem(f"●  {tag}  ·  {usage.get(tag, 0)} 个任务")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setForeground(QColor(foreground))
            item.setBackground(QColor(background))
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)
        if selected:
            for index in range(self.tag_list.count()):
                item = self.tag_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == selected:
                    self.tag_list.setCurrentItem(item)
                    break

    def _selected_tag(self) -> str | None:
        item = self.tag_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else item.text().split("  ·  ", 1)[0].strip()

    def _fill_selected_tag(self) -> None:
        selected = self._selected_tag()
        if selected is not None:
            self.tag_input.setText(selected)

    def _show_tag_error(self, error: ValueError) -> None:
        QMessageBox.warning(self, "标签操作失败", str(error))

    def _add_tag(self) -> None:
        try:
            self.service.storage.add_tag(self.tag_input.text())
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.tag_input.clear()
        self.refresh()
        self.tags_changed.emit()

    def _rename_tag(self) -> None:
        if len(self.tag_list.selectedItems()) > 1:
            self._show_tag_error(ValueError("修改标签时只能选择一个标签"))
            return
        old_tag = self._selected_tag()
        if old_tag is None:
            return
        try:
            self.service.storage.rename_tag(old_tag, self.tag_input.text())
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.refresh()
        self.tags_changed.emit()

    def _delete_tag(self) -> None:
        tags = self._selected_tags()
        if not tags:
            return
        if DEFAULT_TAG in tags:
            self._show_tag_error(ValueError("未分类是系统保底标签，不能删除；请取消对它的选择"))
            return
        label = f"标签“{tags[0]}”" if len(tags) == 1 else f"{len(tags)} 个标签"
        answer = QMessageBox.question(
            self,
            "删除标签？",
            f"删除{label}后，使用它的任务和历史记录会归入“{DEFAULT_TAG}”。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.storage.delete_tags(tags)
        except ValueError as error:
            self._show_tag_error(error)
            return
        self.tag_input.clear()
        self.refresh()
        self.tags_changed.emit()

    def _selected_tags(self) -> list[str]:
        tags: list[str] = []
        for item in self.tag_list.selectedItems():
            value = item.data(Qt.ItemDataRole.UserRole)
            tag = str(value) if value else item.text().split("  ·  ", 1)[0].strip()
            if tag not in tags:
                tags.append(tag)
        return tags


class TaskManagerDialog(QDialog):
    tasks_changed = Signal()
    task_started = Signal(object)

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._deleted_task: Task | None = None
        self.setWindowTitle("WaitLAB · Waiting Task")
        self.setMinimumSize(600, 720)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(_dialog_stylesheet())

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(0)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setObjectName("taskManagerScroll")
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page = QWidget()
        page.setObjectName("taskManagerPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        title = QLabel("Waiting Task")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("统一维护手动任务和固定循环任务；有手动任务时优先使用手动任务。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("例如：核对图 3 的统计标注")
        self.input.returnPressed.connect(self._add_task)
        self.manual_tag = TagChipBar(self.service.storage.available_tags())
        self.manual_tag.setToolTip("为新任务选择标签")
        add_button = QPushButton("添加")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_task)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(add_button)
        layout.addLayout(input_row)
        manual_tag_row = QHBoxLayout()
        manual_tag_label = QLabel("标签")
        manual_tag_label.setObjectName("muted")
        manual_tag_row.addWidget(manual_tag_label)
        manual_tag_row.addWidget(self.manual_tag, 1)
        layout.addLayout(manual_tag_row)

        manual_header = QHBoxLayout()
        manual_title = QLabel("我的任务")
        manual_title.setObjectName("sectionTitle")
        manual_header.addWidget(manual_title)
        manual_header.addStretch()
        manage_tags = QPushButton("管理标签")
        manage_tags.setObjectName("ghostButton")
        manage_tags.setToolTip("新增、修改或删除任务标签")
        manage_tags.clicked.connect(self._open_tag_manager)
        manual_header.addWidget(manage_tags)
        layout.addLayout(manual_header)

        filter_row = QHBoxLayout()
        self.task_search = QLineEdit()
        self.task_search.setPlaceholderText("搜索任务名称…")
        self.task_search.textChanged.connect(self.refresh)
        self.task_tag_filter = TagChipBar(
            ["全部标签", *self.service.storage.available_tags()],
            "全部标签",
        )
        self.task_tag_filter.currentTextChanged.connect(lambda _text: self.refresh())
        self.task_sort = QComboBox()
        self.task_sort.addItems(["自定义顺序", "名称 A-Z", "标签"])
        self.task_sort.currentTextChanged.connect(lambda _text: self.refresh())
        filter_row.addWidget(self.task_search, 1)
        filter_row.addWidget(self.task_sort)
        layout.addLayout(filter_row)
        tag_filter_row = QHBoxLayout()
        tag_filter_label = QLabel("筛选标签")
        tag_filter_label.setObjectName("muted")
        tag_filter_row.addWidget(tag_filter_label)
        tag_filter_row.addWidget(self.task_tag_filter, 1)
        layout.addLayout(tag_filter_row)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._start_selected())
        layout.addWidget(self.list_widget, 1)

        action_row = QHBoxLayout()
        start_button = QPushButton("开始所选")
        start_button.setObjectName("primaryButton")
        start_button.clicked.connect(self._start_selected)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self._delete_selected)
        self.undo_delete_button = QPushButton("撤销删除")
        self.undo_delete_button.setVisible(False)
        self.undo_delete_button.clicked.connect(self._undo_delete)
        action_row.addWidget(start_button)
        action_row.addWidget(delete_button)
        action_row.addWidget(self.undo_delete_button)
        action_row.addStretch()
        stats_button = QPushButton("统计")
        stats_button.clicked.connect(self._open_stats)
        action_row.addWidget(stats_button)
        layout.addLayout(action_row)

        fixed_header = QHBoxLayout()
        fallback_title = QLabel("固定循环任务")
        fallback_title.setObjectName("sectionTitle")
        fixed_help = QLabel("勾选启用；顺序就是轮播顺序")
        fixed_help.setObjectName("muted")
        fixed_header.addWidget(fallback_title)
        fixed_header.addStretch()
        fixed_header.addWidget(fixed_help)
        layout.addLayout(fixed_header)

        self.fixed_list = QListWidget()
        self.fixed_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fixed_list.itemDoubleClicked.connect(lambda _item: self._rename_selected_fixed())
        self.fixed_list.itemChanged.connect(lambda _item: self._persist_fixed())
        layout.addWidget(self.fixed_list, 1)

        fixed_controls = QHBoxLayout()
        fixed_add = QPushButton("添加固定")
        fixed_add.clicked.connect(self._add_fixed_task)
        fixed_rename = QPushButton("重命名")
        fixed_rename.clicked.connect(self._rename_selected_fixed)
        fixed_delete = QPushButton("删除")
        fixed_delete.clicked.connect(self._delete_selected_fixed)
        fixed_up = QPushButton("上移")
        fixed_up.clicked.connect(lambda: self._move_selected_fixed(-1))
        fixed_down = QPushButton("下移")
        fixed_down.clicked.connect(lambda: self._move_selected_fixed(1))
        self.fixed_tag = TagChipBar(self.service.storage.available_tags())
        self.fixed_tag.setToolTip("先选标签，再点击应用标签")
        fixed_apply_tag = QPushButton("应用标签")
        fixed_apply_tag.clicked.connect(self._apply_fixed_tag)
        fixed_reset = QPushButton("恢复默认")
        fixed_reset.clicked.connect(self._reset_defaults)
        for button in (fixed_add, fixed_rename, fixed_delete, fixed_up, fixed_down):
            fixed_controls.addWidget(button)
        fixed_controls.addStretch()
        fixed_controls.addWidget(fixed_reset)
        layout.addLayout(fixed_controls)
        fixed_tag_row = QHBoxLayout()
        fixed_tag_label = QLabel("固定任务标签")
        fixed_tag_label.setObjectName("muted")
        fixed_tag_row.addWidget(fixed_tag_label)
        fixed_tag_row.addWidget(self.fixed_tag, 1)
        fixed_tag_row.addWidget(fixed_apply_tag)
        layout.addLayout(fixed_tag_row)

        fallback_title = QLabel("无手动任务时的轮播预览")
        fallback_title.setObjectName("muted")
        self.fallback = QLabel()
        self.fallback.setWordWrap(True)
        self.fallback.setObjectName("fallback")
        layout.addWidget(fallback_title)
        layout.addWidget(self.fallback)
        self.content_scroll.setWidget(page)
        outer_layout.addWidget(self.content_scroll)
        self.refresh()

    def refresh(self) -> None:
        entries = self.service.storage.default_task_entries()
        enabled = [f"{entry.title}（{entry.tag}）" for entry in entries if entry.enabled]
        disabled_count = sum(not entry.enabled for entry in entries)
        if enabled:
            suffix = f"（另有 {disabled_count} 项已停用）" if disabled_count else ""
            self.fallback.setText("  ·  ".join(enabled) + suffix)
        else:
            self.fallback.setText("固定任务已全部停用，可在上方重新启用。")
        self.list_widget.clear()
        query = self.task_search.text().strip()
        selected_tag = self.task_tag_filter.currentText()
        tasks = filter_and_sort_tasks(
            self.service.storage.list_manual_tasks(),
            query=query,
            tag=selected_tag,
            sort_mode=self.task_sort.currentText(),
        )
        self._fill_fixed_tasks(entries)
        if not tasks:
            message = (
                "没有匹配的手动任务"
                if query or selected_tag != "全部标签"
                else "还没有手动任务，将使用固定滚动任务"
            )
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(placeholder)
            return
        for task in tasks:
            foreground, background = tag_tone_colors(tag_tone(task.tag))
            item = QListWidgetItem(f"{task.title}  ·  ● {task.tag}")
            item.setData(Qt.ItemDataRole.UserRole, task)
            item.setForeground(QColor(foreground))
            item.setBackground(QColor(background))
            self.list_widget.addItem(item)

    def _add_task(self) -> None:
        try:
            self.service.storage.add_manual_task(self.input.text(), self.manual_tag.currentText())
        except ValueError:
            self.input.setFocus()
            return
        self.input.clear()
        self.refresh()
        self.tasks_changed.emit()

    def _open_tag_manager(self) -> None:
        dialog = TagManagerDialog(self.service, self)
        dialog.tags_changed.connect(self._refresh_tag_controls)
        dialog.exec()

    def _refresh_tag_controls(self) -> None:
        tags = self.service.storage.available_tags()
        self.service.stats_cache.invalidate()
        self.manual_tag.set_tags(tags, self.manual_tag.currentText())
        self.fixed_tag.set_tags(tags, self.fixed_tag.currentText())
        selected_filter = self.task_tag_filter.currentText()
        self.task_tag_filter.set_tags(
            ["全部标签", *tags],
            selected_filter if selected_filter in tags else "全部标签",
        )
        self.refresh()
        self.tasks_changed.emit()

    def _selected_task(self) -> Task | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _start_selected(self) -> None:
        task = self._selected_task()
        if task is not None:
            self.task_started.emit(task)

    def _delete_selected(self) -> None:
        task = self._selected_task()
        if task is not None and task.id is not None:
            open_focuses = self.service.open_focuses()
            if any(
                focus.task.kind is task.kind
                and (
                    focus.task.id == task.id
                    if task.id is not None
                    else focus.task.title == task.title
                )
                for focus in open_focuses
            ):
                QMessageBox.information(self, "无法删除", "当前正在计时的任务不能删除，请先完成、暂停或取消它。")
                return
            deleted = self.service.storage.delete_manual_task(task.id)
            if deleted is None:
                return
            self._deleted_task = deleted
            self.undo_delete_button.setVisible(True)
            self.refresh()
            self.tasks_changed.emit()

    def _undo_delete(self) -> None:
        if self._deleted_task is None:
            return
        self.service.storage.add_manual_task(
            self._deleted_task.title,
            self._deleted_task.tag,
        )
        self._deleted_task = None
        self.undo_delete_button.setVisible(False)
        self.refresh()
        self.tasks_changed.emit()

    def _fill_fixed_tasks(self, entries: list[DefaultTaskEntry]) -> None:
        self.fixed_list.blockSignals(True)
        self.fixed_list.clear()
        for entry in entries:
            foreground, background = tag_tone_colors(tag_tone(entry.tag))
            item = QListWidgetItem(f"{entry.title}  ·  ● {entry.tag}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setForeground(QColor(foreground))
            item.setBackground(QColor(background))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked)
            self.fixed_list.addItem(item)
        self.fixed_list.blockSignals(False)

    @staticmethod
    def _fixed_item_values(item: QListWidgetItem) -> tuple[str, str]:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, DefaultTaskEntry):
            return entry.title, entry.tag
        return item.text().split("  ·  ", 1)[0].strip(), DEFAULT_TAG

    def _persist_fixed(self) -> None:
        entries: list[DefaultTaskEntry] = []
        seen: set[str] = set()
        for index in range(self.fixed_list.count()):
            item = self.fixed_list.item(index)
            title, tag = self._fixed_item_values(item)
            title = " ".join(title.split())
            if not title or title in seen:
                continue
            seen.add(title)
            entries.append(DefaultTaskEntry(title, item.checkState() == Qt.CheckState.Checked, tag))
        self.service.storage.set_default_task_entries(entries)
        self.refresh()
        self.tasks_changed.emit()

    def _add_fixed_task(self) -> None:
        title, accepted = QInputDialog.getText(self, "添加固定任务", "任务名称：")
        clean_title = " ".join(title.strip().split())
        if not accepted or not clean_title or self._has_fixed_title(clean_title):
            return
        entry = DefaultTaskEntry(clean_title, True, self.fixed_tag.currentText())
        foreground, background = tag_tone_colors(tag_tone(entry.tag))
        item = QListWidgetItem(f"{entry.title}  ·  ● {entry.tag}")
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setForeground(QColor(foreground))
        item.setBackground(QColor(background))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.fixed_list.addItem(item)
        self._persist_fixed()

    def _rename_selected_fixed(self) -> None:
        item = self.fixed_list.currentItem()
        if item is None:
            return
        old_title, tag = self._fixed_item_values(item)
        title, accepted = QInputDialog.getText(self, "重命名固定任务", "任务名称：", text=old_title)
        clean_title = " ".join(title.strip().split())
        if not accepted or not clean_title or self._has_fixed_title(clean_title, item):
            return
        item.setData(Qt.ItemDataRole.UserRole, DefaultTaskEntry(clean_title, item.checkState() == Qt.CheckState.Checked, tag))
        item.setText(f"{clean_title}  ·  ● {tag}")
        self._persist_fixed()

    def _delete_selected_fixed(self) -> None:
        row = self.fixed_list.currentRow()
        if row >= 0:
            self.fixed_list.takeItem(row)
            self._persist_fixed()

    def _move_selected_fixed(self, offset: int) -> None:
        row = self.fixed_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.fixed_list.count():
            return
        item = self.fixed_list.takeItem(row)
        self.fixed_list.insertItem(target, item)
        self.fixed_list.setCurrentRow(target)
        self._persist_fixed()

    def _apply_fixed_tag(self) -> None:
        item = self.fixed_list.currentItem()
        if item is None:
            return
        title, _ = self._fixed_item_values(item)
        entry = DefaultTaskEntry(title, item.checkState() == Qt.CheckState.Checked, self.fixed_tag.currentText())
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setText(f"{title}  ·  ● {entry.tag}")
        foreground, background = tag_tone_colors(tag_tone(entry.tag))
        item.setForeground(QColor(foreground))
        item.setBackground(QColor(background))
        self._persist_fixed()

    def _reset_defaults(self) -> None:
        self._fill_fixed_tasks([DefaultTaskEntry(title, True, DEFAULT_TAG) for title in DEFAULT_TASKS])
        self._persist_fixed()

    def _has_fixed_title(self, title: str, except_item: QListWidgetItem | None = None) -> bool:
        return any(
            self.fixed_list.item(index) is not except_item
            and self._fixed_item_values(self.fixed_list.item(index))[0] == title
            for index in range(self.fixed_list.count())
        )

    def _open_stats(self) -> None:
        dialog = StatisticsDialog(self.service, self)
        dialog.exec()


def _chart_duration(seconds: float) -> str:
    """Format a compact chart axis/tooltip duration."""

    value = max(0.0, float(seconds))
    if value >= 3600:
        return f"{value / 3600:.1f} 小时"
    if value >= 60:
        return f"{value / 60:.0f} 分钟"
    return f"{value:.0f} 秒"


def _chart_color(tag: str, *, soft: bool = False) -> QColor:
    foreground, background = tag_tone_colors(tag_tone(tag))
    return QColor(background if soft else foreground)


class TagDonutChart(QWidget):
    """Small interactive donut chart for today's tag allocation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, float] = {}
        self._total = 0.0
        self._hovered_tag: str | None = None
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName("今日标签时间环状图")
        self.setAccessibleDescription("展示今天各标签 Waiting Task 的专注时间分布")

    def sizeHint(self) -> QSize:
        return QSize(280, 240)

    def set_values(self, values: dict[str, float]) -> None:
        self._values = {
            str(tag): max(0.0, float(seconds))
            for tag, seconds in values.items()
            if float(seconds) > 0
        }
        self._values = dict(
            sorted(self._values.items(), key=lambda item: (-item[1], item[0]))
        )
        self._total = sum(self._values.values())
        self._hovered_tag = None
        self.setToolTip("")
        self.update()

    def _chart_rect(self) -> QRectF:
        side = max(0.0, float(min(self.width(), self.height()) - 30))
        return QRectF(
            (self.width() - side) / 2,
            (self.height() - side) / 2,
            side,
            side,
        )

    def _tag_at(self, point: QPointF) -> str | None:
        if self._total <= 0:
            return None
        rect = self._chart_rect()
        center = rect.center()
        dx = point.x() - center.x()
        dy = point.y() - center.y()
        radius = math.hypot(dx, dy)
        outer = rect.width() / 2
        inner = outer * 0.53
        if radius < inner or radius > outer:
            return None
        angle = math.degrees(math.atan2(-dy, dx)) % 360
        relative = (90 - angle) % 360
        cursor = 0.0
        items = list(self._values.items())
        for index, (tag, seconds) in enumerate(items):
            span = 360 * seconds / self._total
            if relative < cursor + span or index == len(items) - 1:
                return tag
            cursor += span
        return None

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._total <= 0:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "今天暂无等待时间")
            painter.end()
            return

        rect = self._chart_rect()
        start = 90.0
        painter.setPen(Qt.PenStyle.NoPen)
        for tag, seconds in self._values.items():
            span = -360 * seconds / self._total
            color = _chart_color(tag)
            if tag == self._hovered_tag:
                color = color.lighter(118)
            painter.setBrush(color)
            painter.drawPie(rect, int(start * 16), int(span * 16))
            start += span

        outer = rect.width() / 2
        hole = outer * 0.53
        center = rect.center()
        hole_rect = QRectF(
            center.x() - hole,
            center.y() - hole,
            hole * 2,
            hole * 2,
        )
        painter.setBrush(QColor(COLORS["cream"]))
        painter.drawEllipse(hole_rect)
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSize(15)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["ink"]))
        painter.drawText(
            hole_rect.adjusted(-18, -12, 18, 12),
            Qt.AlignmentFlag.AlignCenter,
            format_duration(self._total),
        )
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        tag = self._tag_at(event.position())
        if tag != self._hovered_tag:
            self._hovered_tag = tag
            self.update()
        if tag is None:
            self.setToolTip("")
        else:
            seconds = self._values[tag]
            percentage = seconds / self._total * 100 if self._total else 0
            self.setToolTip(
                f"{tag}\n{_chart_duration(seconds)} · {percentage:.1f}%"
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hovered_tag = None
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]


class DailyTagStackedChart(QWidget):
    """Stacked daily tag totals for the selected local week or month."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._period = "week"
        self._buckets: list[TagTimeBucket] = []
        self._hovered_index = -1
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName("按天标签时长堆叠柱状图")
        self.setAccessibleDescription("展示本周或本月每天各标签的 Waiting Task 专注时间")

    def sizeHint(self) -> QSize:
        return QSize(680, 290)

    def set_data(self, period: str, buckets: list[TagTimeBucket]) -> None:
        self._period = period
        self._buckets = list(buckets)
        self._hovered_index = -1
        self.setToolTip("")
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            54,
            16,
            max(0.0, float(self.width() - 72)),
            max(0.0, float(self.height() - 60)),
        )

    def _tags(self) -> list[str]:
        totals: dict[str, float] = {}
        for bucket in self._buckets:
            for tag, seconds in bucket.tag_seconds.items():
                totals[tag] = totals.get(tag, 0.0) + seconds
        return [
            tag
            for tag, _seconds in sorted(
                totals.items(), key=lambda item: (-item[1], item[0])
            )
        ]

    @staticmethod
    def _bucket_total(bucket: TagTimeBucket) -> float:
        return sum(bucket.tag_seconds.values())

    def _tooltip_for(self, index: int) -> str:
        bucket = self._buckets[index]
        date_label = bucket.start.strftime("%Y-%m-%d")
        lines = [f"{date_label} · {_chart_duration(self._bucket_total(bucket))}"]
        for tag, seconds in sorted(
            bucket.tag_seconds.items(), key=lambda item: (-item[1], item[0])
        ):
            if seconds > 0:
                lines.append(f"{tag}：{_chart_duration(seconds)}")
        return "\n".join(lines)

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        totals = [self._bucket_total(bucket) for bucket in self._buckets]
        maximum = max(totals, default=0.0)
        if not self._buckets or maximum <= 0:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "所选周期暂无等待时间",
            )
            painter.end()
            return

        painter.setFont(QFont(self.font()))
        tick_count = 4
        for tick in range(tick_count + 1):
            value = maximum * tick / tick_count
            y = plot.bottom() - plot.height() * tick / tick_count
            painter.setPen(QPen(QColor(COLORS["line"]), 1))
            painter.drawLine(plot.left(), y, plot.right(), y)
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(
                QRectF(0, y - 9, plot.left() - 8, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _chart_duration(value),
            )

        tags = self._tags()
        step = plot.width() / len(self._buckets)
        bar_width = max(4.0, min(36.0, step * 0.72))
        for index, bucket in enumerate(self._buckets):
            x = plot.left() + index * step + (step - bar_width) / 2
            bottom = plot.bottom()
            for tag in tags:
                seconds = bucket.tag_seconds.get(tag, 0.0)
                if seconds <= 0:
                    continue
                height = plot.height() * seconds / maximum
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_chart_color(tag))
                painter.drawRect(QRectF(x, bottom - height, bar_width, height))
                bottom -= height

            if index == self._hovered_index:
                painter.setPen(QPen(QColor(COLORS["ink"]), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(x, plot.top(), bar_width, plot.height()))

            if (
                self._period == "week"
                or index == 0
                or index == len(self._buckets) - 1
                or index % 5 == 0
            ):
                label = bucket.start.strftime("%m/%d")
                painter.setPen(QColor(COLORS["muted"]))
                painter.drawText(
                    QRectF(x - 14, plot.bottom() + 7, bar_width + 28, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._buckets:
            return
        plot = self._plot_rect()
        if not plot.contains(event.position()):
            index = -1
        else:
            step = plot.width() / len(self._buckets)
            index = int((event.position().x() - plot.left()) / step)
            if index < 0 or index >= len(self._buckets):
                index = -1
        if index != self._hovered_index:
            self._hovered_index = index
            self.update()
        self.setToolTip(self._tooltip_for(index) if index >= 0 else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._hovered_index = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]


class StatisticsDialog(QDialog):
    """Visual statistics view for today's allocation and daily trends."""

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("WaitLAB · 统计")
        self.setMinimumSize(720, 700)
        self.resize(780, 760)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(_dialog_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("时间统计")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Waiting Task 统计实际专注时间；Codex 只作为活动提醒来源，不记录运行时长。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        today_header = QHBoxLayout()
        today_title = QLabel("今日标签分布")
        today_title.setObjectName("sectionTitle")
        today_header.addWidget(today_title)
        today_header.addStretch(1)
        self.today_total_label = QLabel()
        self.today_total_label.setObjectName("statValue")
        today_header.addWidget(self.today_total_label)
        layout.addLayout(today_header)

        today_content = QHBoxLayout()
        today_content.setSpacing(18)
        self.today_donut = TagDonutChart()
        today_content.addWidget(self.today_donut, 1)
        self.today_legend = QLabel()
        self.today_legend.setObjectName("chartLegend")
        self.today_legend.setWordWrap(True)
        self.today_legend.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.today_legend.setMinimumWidth(230)
        self.today_legend.setAccessibleName("今日标签时间明细")
        today_content.addWidget(self.today_legend, 1)
        layout.addLayout(today_content)

        series_header = QHBoxLayout()
        series_title = QLabel("按天标签时长")
        series_title.setObjectName("sectionTitle")
        series_header.addWidget(series_title)
        series_header.addStretch(1)
        self.series_total_label = QLabel()
        self.series_total_label.setObjectName("statValue")
        series_header.addWidget(self.series_total_label)
        self.week_button = QPushButton("本周")
        self.week_button.setObjectName("periodButton")
        self.week_button.setCheckable(True)
        self.week_button.clicked.connect(lambda: self._set_period("week"))
        self.month_button = QPushButton("本月")
        self.month_button.setObjectName("periodButton")
        self.month_button.setCheckable(True)
        self.month_button.clicked.connect(lambda: self._set_period("month"))
        series_header.addWidget(self.week_button)
        series_header.addWidget(self.month_button)
        layout.addLayout(series_header)

        self.series_chart = DailyTagStackedChart()
        layout.addWidget(self.series_chart, 1)
        self.series_legend = QLabel()
        self.series_legend.setObjectName("chartLegend")
        self.series_legend.setWordWrap(True)
        self.series_legend.setAccessibleName("按天标签图例")
        layout.addWidget(self.series_legend)

        self._period = "week"
        self.week_button.setChecked(True)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def refresh(self) -> None:
        day_snapshot = self.service.stats_cache.get("day")
        self.today_total_label.setText(format_duration(day_snapshot.waiting_seconds))
        self.today_donut.set_values(day_snapshot.tag_seconds)
        self.today_legend.setText(self._legend_html(day_snapshot.tag_seconds))
        self._refresh_series()

    def _set_period(self, period: str) -> None:
        self._period = period
        self.week_button.setChecked(period == "week")
        self.month_button.setChecked(period == "month")
        self._refresh_series()

    def _refresh_series(self) -> None:
        buckets = self.service.storage.tag_waiting_daily_series(self._period)
        self.series_chart.set_data(self._period, buckets)
        totals: dict[str, float] = {}
        for bucket in buckets:
            for tag, seconds in bucket.tag_seconds.items():
                totals[tag] = totals.get(tag, 0.0) + seconds
        total_seconds = sum(totals.values())
        period_label = "本周" if self._period == "week" else "本月"
        self.series_total_label.setText(
            f"{period_label} {format_duration(total_seconds)}"
        )
        self.series_legend.setText(self._legend_html(totals))

    @staticmethod
    def _legend_html(values: dict[str, float]) -> str:
        positive = {
            tag: seconds for tag, seconds in values.items() if seconds > 0
        }
        if not positive:
            return "暂无标签记录"
        total = sum(positive.values())
        parts = []
        for tag, seconds in sorted(
            positive.items(), key=lambda item: (-item[1], item[0])
        ):
            foreground, _background = tag_tone_colors(tag_tone(tag))
            percentage = seconds / total * 100 if total else 0
            parts.append(
                f'<span style="color:{foreground};">●</span> '
                f"{escape(tag)}  {format_duration(seconds)} ({percentage:.1f}%)"
            )
        return "　".join(parts)


class SettingsDialog(QDialog):
    settings_changed = Signal()

    def __init__(self, service: WaitLabService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("WaitLAB · 设置")
        self.setMinimumSize(520, 520)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(_dialog_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)

        title = QLabel("日用设置")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("控制桌宠的提醒方式、置顶行为和日常提醒。任务统一在 Waiting Task 中维护。")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        behavior_title = QLabel("提醒与启动")
        behavior_title.setObjectName("sectionTitle")
        layout.addWidget(behavior_title)

        self.popup_mode = QComboBox()
        self.popup_mode.addItem("弹出并置顶", PopupMode.RAISE.value)
        self.popup_mode.addItem("静默显示，不主动置顶", PopupMode.QUIET.value)
        self.in_app_notifications = QCheckBox("Codex 输出、完成或中断时在 Cookie 气泡内提醒")
        self.notification_sound = QCheckBox("提醒时播放提示音")
        self.autostart = QCheckBox("登录 Windows 后自动启动 WaitLAB")
        self.always_on_top = QCheckBox("悬浮窗始终置顶（可随时拖动）")
        self.cookie_size = QSpinBox()
        self.cookie_size.setRange(48, 160)
        self.cookie_size.setSingleStep(8)
        self.cookie_size.setSuffix(" px")
        self.cookie_size.setToolTip("调整 Cookie 桌宠图标大小")
        self.auto_check_updates = QCheckBox("启动时检查 GitHub 新版本")
        self.quiet_hours = QCheckBox("静默时段不播放提示音")
        self.quiet_start = QTimeEdit()
        self.quiet_end = QTimeEdit()
        self.quiet_start.setDisplayFormat("HH:mm")
        self.quiet_end.setDisplayFormat("HH:mm")
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(self.quiet_hours)
        quiet_row.addStretch()
        quiet_row.addWidget(QLabel("从"))
        quiet_row.addWidget(self.quiet_start)
        quiet_row.addWidget(QLabel("到"))
        quiet_row.addWidget(self.quiet_end)
        layout.addWidget(QLabel("收到新的 Codex 指令时："))
        layout.addWidget(self.popup_mode)
        layout.addWidget(self.in_app_notifications)
        layout.addWidget(self.notification_sound)
        layout.addWidget(self.autostart)
        layout.addWidget(self.always_on_top)
        cookie_size_row = QHBoxLayout()
        cookie_size_row.addWidget(QLabel("Cookie 图标大小"))
        cookie_size_row.addStretch()
        cookie_size_row.addWidget(self.cookie_size)
        layout.addLayout(cookie_size_row)
        layout.addWidget(self.auto_check_updates)
        layout.addLayout(quiet_row)

        actions = QHBoxLayout()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("保存设置")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        actions.addStretch()
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        preferences = Preferences.load(self.service.storage)
        mode_index = self.popup_mode.findData(preferences.popup_mode.value)
        self.popup_mode.setCurrentIndex(max(0, mode_index))
        self.in_app_notifications.setChecked(preferences.in_app_notifications)
        self.notification_sound.setChecked(preferences.notification_sound)
        self.autostart.setChecked(is_autostart_enabled())
        self.always_on_top.setChecked(preferences.always_on_top)
        self.cookie_size.setValue(preferences.cookie_size)
        self.auto_check_updates.setChecked(preferences.auto_check_updates)
        self.quiet_hours.setChecked(preferences.quiet_hours_enabled)
        self.quiet_start.setTime(QTime.fromString(preferences.quiet_start, "HH:mm"))
        self.quiet_end.setTime(QTime.fromString(preferences.quiet_end, "HH:mm"))

    def _save(self) -> None:
        preferences = Preferences(
            popup_mode=PopupMode(str(self.popup_mode.currentData())),
            in_app_notifications=self.in_app_notifications.isChecked(),
            # Kept in Preferences for backwards-compatible config reads. Codex
            # lifecycle prompts are now rendered only inside the Cookie bubble.
            completion_notifications=False,
            notification_sound=self.notification_sound.isChecked(),
            always_on_top=self.always_on_top.isChecked(),
            auto_check_updates=self.auto_check_updates.isChecked(),
            quiet_hours_enabled=self.quiet_hours.isChecked(),
            quiet_start=self.quiet_start.time().toString("HH:mm"),
            quiet_end=self.quiet_end.time().toString("HH:mm"),
            cookie_size=self.cookie_size.value(),
        )
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as exc:
            QMessageBox.critical(self, "开机启动设置失败", str(exc))
            return
        preferences.save(self.service.storage)
        self.settings_changed.emit()
        self.accept()


class PetWindow(QWidget):
    quit_requested = Signal()
    update_check_finished = Signal(str)
    update_available = Signal(object, bool)
    update_downloaded = Signal(object)

    def __init__(self, service: WaitLabService) -> None:
        super().__init__()
        self.service = service
        self.update_manager = UpdateManager(__version__)
        self.tray: QSystemTrayIcon | None = None
        self.task_dialog: TaskManagerDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.task_picker_open = False
        self.page_hidden = False
        self.pet_hidden = False
        self._native_topmost_enabled = True
        self._next_native_topmost_sync = 0.0
        self._suggestion_signature: tuple[tuple[int | None, str, str, str], ...] | None = None
        self._suggestion_mode: str | None = None
        self._paused_signature: tuple[tuple[int, str, str, int], ...] | None = None
        self._completed_signature: tuple[tuple[int | None, str, float, int, str], ...] | None = None
        self.completion_banner_until = 0.0
        self._completion_queue: list[ServiceUpdate] = []
        self._completion_notified_turns: set[str] = set()
        self._active_completion_turn_id: str | None = None
        self.task_completion_banner_until = 0.0
        self._notice_until = 0.0
        self._notice_title = ""
        self._notice_body = ""
        self._notice_level = "info"
        self._deleted_history_record: CompletedFocusRecord | None = None
        self._deleted_history_until = 0.0
        self.last_message = "等待下一次 Codex 指令"
        self._drag_origin: QPoint | None = None
        self.hook_monitor = HookConnectionMonitor(service.storage)
        self._hook_info: HookConnectionInfo | None = None
        self._next_connection_check = 0.0
        self._desktop_source_available: bool | None = None
        self._desktop_source_error: str | None = None
        self._desktop_source_path: Path | None = None
        self._desktop_unavailable_since: float | None = None
        self._desktop_turn_ids: set[str] = set()
        self._desktop_unknown_notice_shown = False
        self._desktop_snapshots: tuple[DesktopTurnSnapshot, ...] = ()
        self._ai_attention = False
        self.presentation_mode: PresentationMode | None = None
        self.cookie_state_machine = CookieStateMachine()
        self._focus_full_title = ""
        self._state_full_title = ""
        self._fit_to_content_pending = False

        self.setWindowTitle("WaitLAB")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(82)
        self._build_ui()
        self.update_check_finished.connect(self._show_update_result)
        self.update_available.connect(self._offer_update)
        self.update_downloaded.connect(self._install_downloaded_update)
        self._apply_window_preferences()
        self._restore_position()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        # The worker thread handles Codex polling independently.  UI refresh
        # only needs to update labels/countdowns once per second; the Cookie
        # animation has its own timer, so a 250 ms full-widget refresh only
        # caused needless database reads and layout work.
        self.timer.start(1000)
        self.refresh()
        if Preferences.load(self.service.storage).auto_check_updates:
            QTimer.singleShot(3500, lambda: self.check_for_updates(silent=True))

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.card = QFrame()
        self.card.setObjectName("mainCard")
        self.card.setProperty("presentation", PresentationMode.ICON.value)
        self.card.setStyleSheet(_window_stylesheet())
        self._outer_shadow = QGraphicsDropShadowEffect(self)
        self._outer_shadow.setBlurRadius(28)
        self._outer_shadow.setOffset(0, 7)
        self._outer_shadow.setColor(QColor(40, 55, 50, 55))
        self.card.setGraphicsEffect(self._outer_shadow)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.pet = PetFace(88)
        self.pet.clicked.connect(self.toggle_picker)
        self.pet.context_requested.connect(self.show_pet_menu)
        self.pet.drag_started.connect(self._begin_pet_drag)
        self.pet.drag_moved.connect(self._move_pet_drag)
        self.pet.drag_finished.connect(self._finish_pet_drag)
        header.addWidget(self.pet)

        self.header_details = QWidget()
        titles = QVBoxLayout(self.header_details)
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self.state_label = QLabel("空闲")
        self.state_label.setObjectName("stateTitle")
        self.state_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.state_label.setMinimumWidth(0)
        self.state_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title_row.addWidget(self.state_label, 1)
        tasks_button = QPushButton("任务")
        tasks_button.setObjectName("ghostButton")
        tasks_button.setToolTip("管理手动任务和固定循环任务")
        tasks_button.clicked.connect(self.open_task_manager)
        settings_button = QPushButton("设置")
        settings_button.setObjectName("ghostButton")
        settings_button.setToolTip("调整提醒、置顶、静默时段和日用任务")
        settings_button.clicked.connect(self.open_settings)
        stats_button = QPushButton("统计")
        stats_button.setObjectName("ghostButton")
        stats_button.setToolTip("查看今天、本周和本月的 Waiting Task 时长")
        stats_button.clicked.connect(self.open_statistics)
        hide_button = QPushButton("−")
        hide_button.setObjectName("iconButton")
        hide_button.setFixedSize(28, 28)
        hide_button.setToolTip("隐藏页面，仅保留 Cookie")
        hide_button.clicked.connect(self.hide_page)
        title_row.addWidget(hide_button)
        pet_hide_button = QPushButton("×")
        pet_hide_button.setObjectName("iconButton")
        pet_hide_button.setFixedSize(28, 28)
        pet_hide_button.setToolTip("隐藏桌宠到系统托盘")
        pet_hide_button.clicked.connect(self.hide_pet)
        title_row.addWidget(pet_hide_button)
        titles.addLayout(title_row)
        self.message_label = QLabel(self.last_message)
        self.message_label.setObjectName("muted")
        self.message_label.setWordWrap(True)
        titles.addWidget(self.message_label)
        self.connection_status_button = QPushButton("Codex · 检查中")
        self.connection_status_button.setObjectName("connectionStatusButton")
        self.connection_status_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connection_status_button.clicked.connect(self.show_codex_connection_info)
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)
        meta_row.addWidget(self.connection_status_button, 0, Qt.AlignmentFlag.AlignLeft)
        meta_row.addStretch()
        meta_row.addWidget(tasks_button)
        meta_row.addWidget(stats_button)
        meta_row.addWidget(settings_button)
        titles.addLayout(meta_row)
        header.addWidget(self.header_details, 1)

        self.focus_card = QFrame()
        self.focus_card.setObjectName("focusCard")
        self.focus_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.focus_card.setMinimumWidth(350)
        focus_shadow = QGraphicsDropShadowEffect(self.focus_card)
        focus_shadow.setBlurRadius(18)
        focus_shadow.setOffset(0, 4)
        focus_shadow.setColor(QColor(40, 55, 50, 35))
        self.focus_card.setGraphicsEffect(focus_shadow)
        focus_layout = QVBoxLayout(self.focus_card)
        focus_layout.setContentsMargins(14, 12, 14, 12)
        focus_layout.setSpacing(10)
        focus_info = QHBoxLayout()
        focus_info.setContentsMargins(0, 0, 0, 0)
        focus_info.setSpacing(8)
        self.focus_title = QLabel()
        self.focus_title.setObjectName("focusTitle")
        self.focus_title.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.focus_title.setMinimumWidth(0)
        self.focus_title.setMinimumHeight(22)
        self.focus_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.focus_time = QLabel("00:00")
        self.focus_time.setObjectName("timerCompact")
        self.focus_time.setMinimumWidth(72)
        self.focus_time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        focus_info.addWidget(self.focus_title, 1)
        focus_info.addWidget(self.focus_time, 0)
        self.focus_hide_button = QPushButton("−")
        self.focus_hide_button.setObjectName("iconButton")
        self.focus_hide_button.setFixedSize(24, 24)
        self.focus_hide_button.setToolTip("隐藏页面，仅保留 Cookie")
        self.focus_hide_button.clicked.connect(self.hide_page)
        focus_info.addWidget(self.focus_hide_button, 0)
        focus_layout.addLayout(focus_info)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self.pause_button = QPushButton("Ⅱ\n暂停")
        self.pause_button.setObjectName("playerButton")
        self.pause_button.setToolTip("暂停或继续微任务")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.switch_button = QPushButton("↔\n切换")
        self.switch_button.setObjectName("playerSwitchButton")
        self.switch_button.setToolTip("暂停当前任务后切换到另一个任务")
        self.switch_button.clicked.connect(self.open_task_switcher)
        complete_button = QPushButton("✓\n完成")
        complete_button.setObjectName("playerPrimaryButton")
        complete_button.setToolTip("完成当前微任务")
        complete_button.clicked.connect(self.complete_focus)
        abandon_button = QPushButton("×\n取消")
        abandon_button.setObjectName("playerCloseButton")
        abandon_button.setToolTip("取消本次计时并放回任务池")
        abandon_button.clicked.connect(self.abandon_focus)
        for button in (self.pause_button, complete_button, abandon_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumSize(88, 44)
        self.switch_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.switch_button.setFixedWidth(72)
        controls.addWidget(self.pause_button, 1)
        controls.addWidget(self.switch_button, 0)
        controls.addWidget(complete_button, 1)
        controls.addWidget(abandon_button, 1)
        self.focus_controls = QWidget(self.focus_card)
        self.focus_controls.setLayout(controls)
        focus_layout.addWidget(self.focus_controls)
        header.addWidget(self.focus_card, 1)
        layout.addLayout(header)

        self.ai_card = QFrame()
        self.ai_card.setObjectName("aiCard")
        self.ai_card.setProperty("attention", False)
        self.ai_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        ai_layout = QHBoxLayout(self.ai_card)
        ai_layout.setContentsMargins(13, 10, 13, 10)
        self.ai_status_label = QLabel("Codex 对话进行中")
        self.ai_status_label.setObjectName("cardLabel")
        ai_layout.addWidget(self.ai_status_label)
        ai_layout.addStretch()
        layout.addWidget(self.ai_card)

        self.picker = QFrame()
        self.picker.setObjectName("picker")
        self.picker.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        picker_layout = QVBoxLayout(self.picker)
        picker_layout.setContentsMargins(2, 4, 2, 2)
        picker_layout.setSpacing(8)
        picker_header = QHBoxLayout()
        self.picker_title = QLabel("选一个等待任务")
        self.picker_title.setObjectName("sectionTitle")
        self.picker_source = QLabel()
        self.picker_source.setObjectName("sourcePill")
        picker_header.addWidget(self.picker_title)
        picker_header.addStretch()
        picker_header.addWidget(self.picker_source)
        picker_layout.addLayout(picker_header)
        self.home_stats_label = QLabel("今天 · Waiting Task 00:00")
        self.home_stats_label.setObjectName("muted")
        picker_layout.addWidget(self.home_stats_label)
        task_input_row = QHBoxLayout()
        self.quick_task_input = QLineEdit()
        self.quick_task_input.setPlaceholderText("新增一个具体任务…")
        self.quick_task_input.returnPressed.connect(self._add_quick_task)
        task_input_row.addWidget(self.quick_task_input)
        picker_layout.addLayout(task_input_row)

        add_row = QHBoxLayout()
        quick_tag_label = QLabel("标签")
        quick_tag_label.setObjectName("muted")
        self.quick_task_tag = TagChipBar(self.service.storage.available_tags())
        self.quick_task_tag.setObjectName("quickTaskTag")
        self.quick_task_tag.set_compact(True)
        self.quick_task_tag.setToolTip("为新任务选择标签")
        self.quick_task_tag.geometry_changed.connect(self._schedule_fit_to_content)
        add_quick = QPushButton("新增")
        add_quick.clicked.connect(self._add_quick_task)
        add_row.addWidget(quick_tag_label)
        add_row.addWidget(self.quick_task_tag, 1)
        add_row.addStretch()
        add_row.addWidget(add_quick)
        picker_layout.addLayout(add_row)
        self.random_task_button = QPushButton("随机开始一个固定任务")
        self.random_task_button.setObjectName("secondaryButton")
        self.random_task_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.random_task_button.clicked.connect(self._start_random_task)
        picker_layout.addWidget(self.random_task_button)
        self.suggestion_container = QWidget()
        self.suggestion_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self.suggestion_layout = QVBoxLayout(self.suggestion_container)
        self.suggestion_layout.setContentsMargins(0, 0, 0, 0)
        self.suggestion_layout.setSpacing(7)
        picker_layout.addWidget(self.suggestion_container)
        completed_header = QLabel("今日已完成")
        completed_header.setObjectName("muted")
        picker_layout.addWidget(completed_header)
        self.today_completed_list = QListWidget()
        self.today_completed_list.setObjectName("todayCompletedList")
        self.today_completed_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.today_completed_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.today_completed_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.today_completed_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.today_completed_list.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        self.today_completed_list.setMinimumHeight(0)
        picker_layout.addWidget(self.today_completed_list)
        self.today_completed_empty = QLabel("今日还没有完成微任务")
        self.today_completed_empty.setObjectName("muted")
        self.today_completed_empty.setWordWrap(True)
        picker_layout.addWidget(self.today_completed_empty)
        later_button = QPushButton("本轮跳过")
        later_button.setObjectName("linkButton")
        later_button.clicked.connect(self.skip_current_round)
        picker_layout.addWidget(later_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.picker)

        self.footer_widget = QWidget()
        footer = QHBoxLayout(self.footer_widget)
        footer.setContentsMargins(0, 0, 0, 0)
        start_ai = QPushButton("手动开始等待")
        start_ai.setObjectName("ghostButton")
        start_ai.clicked.connect(self.manual_ai_start)
        finish_ai = QPushButton("AI 已完成")
        finish_ai.setObjectName("ghostButton")
        finish_ai.clicked.connect(self.manual_ai_finish)
        self.today_label = QLabel("今日回收 0 分钟")
        self.today_label.setObjectName("muted")
        footer.addWidget(start_ai)
        footer.addWidget(finish_ai)
        footer.addStretch()
        footer.addWidget(self.today_label)
        layout.addWidget(self.footer_widget)

        # Keep Cookie visually separate from the operation surface.  The
        # transparent outer card hosts the pet and one rounded bubble; all
        # status, task, and control widgets remain inside that bubble.
        self.bubble_card = QFrame()
        self.bubble_card.setObjectName("bubbleCard")
        self.bubble_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        bubble_shadow = QGraphicsDropShadowEffect(self.bubble_card)
        bubble_shadow.setBlurRadius(22)
        bubble_shadow.setOffset(0, 6)
        bubble_shadow.setColor(QColor(40, 55, 50, 48))
        self.bubble_card.setGraphicsEffect(bubble_shadow)
        bubble_layout = QVBoxLayout(self.bubble_card)
        bubble_layout.setContentsMargins(13, 11, 13, 11)
        bubble_layout.setSpacing(8)
        self.notice_card = QFrame()
        self.notice_card.setObjectName("noticeCard")
        self.notice_card.setProperty("level", "info")
        notice_layout = QHBoxLayout(self.notice_card)
        notice_layout.setContentsMargins(10, 8, 8, 8)
        notice_layout.setSpacing(8)
        notice_text = QVBoxLayout()
        notice_text.setContentsMargins(0, 0, 0, 0)
        notice_text.setSpacing(1)
        self.notice_title_label = QLabel()
        self.notice_title_label.setObjectName("noticeTitle")
        self.notice_body_label = QLabel()
        self.notice_body_label.setObjectName("noticeBody")
        self.notice_body_label.setWordWrap(True)
        notice_text.addWidget(self.notice_title_label)
        notice_text.addWidget(self.notice_body_label)
        self.notice_action_row = QHBoxLayout()
        self.notice_action_row.setContentsMargins(0, 4, 0, 0)
        self.notice_action_row.setSpacing(6)
        self.notice_continue_button = QPushButton("继续微任务")
        self.notice_continue_button.setObjectName("noticeActionButton")
        self.notice_continue_button.clicked.connect(self._continue_after_ai)
        self.notice_pause_button = QPushButton("暂停")
        self.notice_pause_button.setObjectName("noticeActionButton")
        self.notice_pause_button.clicked.connect(self._pause_after_ai)
        self.notice_complete_button = QPushButton("完成")
        self.notice_complete_button.setObjectName("noticePrimaryActionButton")
        self.notice_complete_button.clicked.connect(self._complete_after_ai)
        self.notice_undo_button = QPushButton("撤销删除")
        self.notice_undo_button.setObjectName("noticeActionButton")
        self.notice_undo_button.clicked.connect(self._undo_deleted_history)
        self.notice_action_row.addWidget(self.notice_continue_button)
        self.notice_action_row.addWidget(self.notice_pause_button)
        self.notice_action_row.addWidget(self.notice_complete_button)
        self.notice_action_row.addWidget(self.notice_undo_button)
        self.notice_action_row.addStretch()
        notice_text.addLayout(self.notice_action_row)
        self.notice_action_row.setEnabled(False)
        notice_layout.addLayout(notice_text, 1)
        dismiss_notice = QPushButton("×")
        dismiss_notice.setObjectName("iconButton")
        dismiss_notice.setFixedSize(24, 24)
        dismiss_notice.setToolTip("关闭这条提示")
        dismiss_notice.clicked.connect(self.dismiss_notice)
        notice_layout.addWidget(dismiss_notice, 0, Qt.AlignmentFlag.AlignTop)
        self.notice_card.hide()
        self.notice_action_row.setEnabled(False)
        self.notice_continue_button.hide()
        self.notice_pause_button.hide()
        self.notice_complete_button.hide()
        self.notice_undo_button.hide()
        bubble_layout.addWidget(self.notice_card)
        self.compact_timer_label = QLabel("00:00")
        self.compact_timer_label.setObjectName("compactTimer")
        self.compact_timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.compact_timer_label.setToolTip("当前 Waiting Task 计时")
        self.compact_timer_label.hide()
        bubble_layout.addWidget(self.compact_timer_label, 0, Qt.AlignmentFlag.AlignCenter)
        header.removeWidget(self.header_details)
        header.removeWidget(self.focus_card)
        layout.removeWidget(self.ai_card)
        layout.removeWidget(self.picker)
        layout.removeWidget(self.footer_widget)
        for widget in (
            self.header_details,
            self.focus_card,
            self.ai_card,
            self.picker,
            self.footer_widget,
        ):
            widget.setParent(self.bubble_card)
            bubble_layout.addWidget(widget)
        header.addWidget(self.bubble_card, 1)

    def set_tray(self, tray: QSystemTrayIcon) -> None:
        self.tray = tray

    def _notice_is_visible(self) -> bool:
        return bool(self._notice_body) and time.monotonic() < self._notice_until

    def show_notice(
        self,
        title: str,
        body: str,
        *,
        level: str = "info",
        duration: float = 5.0,
        sound: bool = False,
    ) -> None:
        """Show a short-lived notification inside Cookie's operation bubble."""

        self._notice_title = str(title).strip()
        self._notice_body = str(body).strip()
        self._notice_level = level if level in {"info", "success", "warning", "error"} else "info"
        self.notice_title_label.setText(self._notice_title)
        self.notice_body_label.setText(self._notice_body)
        self._hide_notice_actions()
        self.notice_card.setProperty("level", self._notice_level)
        self.notice_card.style().unpolish(self.notice_card)
        self.notice_card.style().polish(self.notice_card)
        self._notice_until = time.monotonic() + max(0.0, float(duration))
        self.notice_card.setVisible(bool(self._notice_body))
        if sound:
            self._play_notification_sound(Preferences.load(self.service.storage))
        self.refresh()

    def _enqueue_completion_reminder(self, update: ServiceUpdate) -> None:
        """Queue one completion notice per Codex turn.

        Desktop polling can deliver several terminal transitions in a single
        tick. Keeping a small FIFO prevents the latest turn from overwriting
        an earlier reminder and makes each output actionable.
        """

        turn_id = update.ai_turn_id
        if turn_id is not None:
            if turn_id in self._completion_notified_turns:
                return
            self._completion_notified_turns.add(turn_id)

        preferences = Preferences.load(self.service.storage)

        if not preferences.in_app_notifications:
            return
        self._completion_queue.append(update)
        self._show_next_completion_reminder()

    def _show_next_completion_reminder(self) -> None:
        if (
            self._active_completion_turn_id is not None
            or not self._completion_queue
            or self._notice_is_visible()
        ):
            return
        update = self._completion_queue.pop(0)
        self._active_completion_turn_id = update.ai_turn_id or f"anonymous-{time.time_ns()}"
        self.completion_banner_until = time.monotonic() + 12.0
        completed = update.ai_completed
        title = "Codex 已完成" if completed else "Codex 已中断"
        has_focus = self.service.focus is not None
        body = (
            "Codex 已输出。你可以继续当前微任务，也可以在这里暂停或完成它。"
            if completed and has_focus
            else "可以回到 Codex 查看结果。"
            if completed
            else "Codex 已中断。你可以继续当前微任务，也可以在这里暂停或完成它。"
            if has_focus
            else "请回到 Codex 查看中断或失败原因。"
        )
        self.show_notice(
            title,
            body,
            level="success" if completed else "warning",
            duration=12.0,
        )
        if has_focus:
            self._show_completion_actions()
            self.refresh()

    def _hide_notice_actions(self) -> None:
        self.notice_action_row.setEnabled(False)
        self.notice_continue_button.hide()
        self.notice_pause_button.hide()
        self.notice_complete_button.hide()
        self.notice_undo_button.hide()

    def _show_history_undo(self) -> None:
        if self._deleted_history_record is None:
            return
        self.notice_action_row.setEnabled(True)
        self.notice_undo_button.show()

    def _show_completion_actions(self) -> None:
        if self.service.focus is None:
            return
        self.notice_action_row.setEnabled(True)
        self.notice_continue_button.show()
        self.notice_pause_button.show()
        self.notice_complete_button.show()

    def _continue_after_ai(self) -> None:
        # The default is to keep the Waiting Task running.  Explicitly
        # dismissing the reminder makes the choice visible without changing
        # the focus clock.
        self.dismiss_notice()

    def _pause_after_ai(self) -> None:
        self.apply_update(
            self.service.pause_focus(message="Codex 已输出，微任务已暂停")
        )
        self.dismiss_notice()

    def _complete_after_ai(self) -> None:
        self._active_completion_turn_id = None
        self.complete_focus()

    def dismiss_notice(self) -> None:
        self._active_completion_turn_id = None
        self._notice_until = 0.0
        self._notice_title = ""
        self._notice_body = ""
        self._deleted_history_record = None
        self._deleted_history_until = 0.0
        self._hide_notice_actions()
        self.notice_card.hide()
        self._show_next_completion_reminder()
        self.refresh()

    def set_hook_listener_error(self, error: str) -> None:
        self.hook_monitor.set_listener_error(error)
        self._update_connection_status(force=True)

    def set_desktop_source_status(
        self,
        available: bool,
        error: str | None,
        database: Path,
    ) -> None:
        changed = (
            self._desktop_source_available != available
            or self._desktop_source_error != error
            or self._desktop_source_path != database
        )
        self._desktop_source_available = available
        self._desktop_source_error = error
        self._desktop_source_path = database
        if available:
            self._desktop_unavailable_since = None
            self._desktop_unknown_notice_shown = False
        elif self._desktop_unavailable_since is None:
            self._desktop_unavailable_since = time.monotonic()
        if changed:
            self._update_connection_status(force=True)

    def _desktop_source_is_unknown(self) -> bool:
        """Return whether a lost desktop source invalidates active rows."""

        return (
            self._desktop_unavailable_since is not None
            and time.monotonic() - self._desktop_unavailable_since
            >= DESKTOP_SOURCE_GRACE_SECONDS
            and bool(self._desktop_turn_ids)
        )

    def set_desktop_snapshots(
        self,
        snapshots: tuple[DesktopTurnSnapshot, ...],
    ) -> None:
        self._desktop_snapshots = snapshots

    def handle_hook_event(self, payload: dict) -> None:
        event_name = payload.get("event")
        if not isinstance(event_name, str):
            return
        self.hook_monitor.record_event(event_name)
        self._update_connection_status(force=True)
        session_id = str(payload.get("session_id") or "codex")
        turn_id = str(payload.get("turn_id") or f"unknown-{time.time_ns()}")
        if event_name == "UserPromptSubmit":
            update = self.service.on_ai_started(session_id, turn_id)
        elif event_name == "PermissionRequest":
            update = self.service.on_ai_needs_attention(session_id, turn_id)
        elif event_name == "PostToolUse":
            update = self.service.on_ai_resumed(turn_id)
        elif event_name == "Stop":
            update = self.service.on_ai_finished(turn_id)
        else:
            return
        self.apply_update(update)

    def handle_desktop_event(self, event: DesktopActivityEvent) -> None:
        self._desktop_turn_ids.add(event.turn_id)
        if event.kind is DesktopEventKind.STARTED:
            if (
                event.initial
                and (event.occurred_at - event.started_at).total_seconds()
                > 5 * 60
            ):
                # The row predates this WaitLAB process and is most likely a
                # stale database entry.  Do not create a new lifecycle row or
                # show a picker for it; the snapshot is still available for
                # diagnostics and future transitions.
                return
            update = self.service.on_ai_started(
                event.thread_id,
                event.turn_id,
                when=event.started_at,
                # A first-poll row may be an old stale record.  It still gets
                # tracked for reconciliation, but only a recent row should
                # replay the task picker as if the user just sent a prompt.
                show_task_picker=(
                    not event.initial
                    or (event.occurred_at - event.started_at).total_seconds()
                    <= 5 * 60
                ),
            )
        elif event.kind is DesktopEventKind.NEEDS_ATTENTION:
            update = self.service.on_ai_needs_attention(
                event.thread_id,
                event.turn_id,
                when=event.occurred_at,
                fallback_latest=False,
            )
        elif event.kind is DesktopEventKind.RESUMED:
            update = self.service.on_ai_resumed(
                event.turn_id,
                when=event.occurred_at,
                fallback_latest=False,
            )
        elif event.kind in {DesktopEventKind.COMPLETED, DesktopEventKind.BLOCKED}:
            update = self.service.on_ai_finished(
                event.turn_id,
                when=event.occurred_at,
                status=(
                    "completed"
                    if event.kind is DesktopEventKind.COMPLETED
                    else event.status
                ),
                fallback_latest=False,
                session_id=event.thread_id,
                started_at=event.started_at,
                create_if_missing=True,
            )
        else:
            return
        self.apply_update(update)

    def apply_update(self, update: ServiceUpdate) -> None:
        if update.message:
            self.last_message = update.message
        if update.show_task_picker:
            preferences = Preferences.load(self.service.storage)
            # ``tray_only`` was the old external-toast mode. Keep reading it
            # for compatibility, but present the picker in Cookie now that all
            # Codex prompts are in-app.
            popup_mode = (
                PopupMode.RAISE
                if preferences.popup_mode is PopupMode.TRAY_ONLY
                else preferences.popup_mode
            )
            self.page_hidden = False
            self.task_picker_open = True
            if self.task_picker_open:
                self._set_presentation_mode(PresentationMode.PICKER)
            if popup_mode is PopupMode.RAISE:
                self.pet_hidden = False
                self.show()
                self.raise_()
                self.activateWindow()
            elif popup_mode is PopupMode.QUIET:
                self.pet_hidden = False
                self.show()
        if update.ai_completed or update.ai_blocked:
            if self.service.focus is None:
                self.task_picker_open = False
            self._enqueue_completion_reminder(update)
        if update.ai_needs_attention:
            preferences = Preferences.load(self.service.storage)
            if (
                preferences.in_app_notifications
                and self._active_completion_turn_id is None
            ):
                self.show_notice(
                    "Codex 等待批准",
                    "请回到 Codex 处理权限请求；当前微任务仍在继续计时。",
                    level="warning",
                    duration=7.0,
                )
            if not preferences.is_quiet_now():
                self._play_notification_sound(preferences)
        self.refresh()

    @staticmethod
    def _play_notification_sound(preferences: Preferences) -> None:
        if preferences.notification_sound and not preferences.is_quiet_now():
            QApplication.beep()

    def refresh(self) -> None:
        now = time.monotonic()
        if (
            self._active_completion_turn_id is not None
            and now >= self._notice_until
        ):
            self._active_completion_turn_id = None
            self._hide_notice_actions()
        if (
            self._deleted_history_record is not None
            and now >= self._deleted_history_until
        ):
            self._deleted_history_record = None
            self._deleted_history_until = 0.0
            self._hide_notice_actions()
        if (
            self._active_completion_turn_id is None
            and self._completion_queue
            and not self._notice_is_visible()
        ):
            self._show_next_completion_reminder()
            return
        focus = self.service.focus
        running_ai = self.service.running_ai_sessions()
        attention_ai = self.service.attention_ai_sessions()
        desktop_source_unknown = self._desktop_source_is_unknown()
        unknown_running = (
            desktop_source_unknown
            and any(session.turn_id in self._desktop_turn_ids for session in running_ai)
        )
        unknown_attention = (
            desktop_source_unknown
            and any(session.turn_id in self._desktop_turn_ids for session in attention_ai)
        )
        source_state_unknown = unknown_running or unknown_attention
        if source_state_unknown and not self._desktop_unknown_notice_shown:
            self._desktop_unknown_notice_shown = True
            self.show_notice(
                "Codex 状态待确认",
                "暂时无法读取 Codex 状态，Cookie 不会继续判断它正在工作；连接恢复后会自动同步。",
                level="warning",
                duration=60.0,
            )
            return
        if desktop_source_unknown:
            running_ai = [
                session
                for session in running_ai
                if session.turn_id not in self._desktop_turn_ids
            ]
            attention_ai = [
                session
                for session in attention_ai
                if session.turn_id not in self._desktop_turn_ids
            ]
        primary_ai = (
            running_ai[0] if running_ai else attention_ai[0] if attention_ai else None
        )
        completed_visible = now < self.completion_banner_until
        task_completed_visible = time.monotonic() < self.task_completion_banner_until
        terminal_blocked = (
            completed_visible
            and self.service.last_ai_terminal_status not in {None, "completed"}
        )
        has_running_ai = bool(running_ai)
        needs_attention = bool(attention_ai)

        cookie_state = self.cookie_state_machine.transition(
            CookieContext(
                focus_active=focus is not None,
                focus_paused=focus is not None and focus.is_paused,
                ai_active=has_running_ai,
                ai_needs_attention=needs_attention,
                completion_visible=completed_visible,
                task_completion_visible=task_completed_visible,
                terminal_error=terminal_blocked,
            )
        )

        if source_state_unknown:
            state = "Codex 状态待确认"
        elif has_running_ai and needs_attention:
            state = "Codex 正在工作 · 另有任务等待批准"
        elif has_running_ai:
            state = "Codex 对话进行中"
        elif needs_attention:
            state = "Codex 等待批准"
        elif focus is not None:
            state = "微任务已暂停" if focus.is_paused else "正在回收等待时间"
        elif task_completed_visible:
            state = "微任务已完成"
        elif completed_visible:
            state = "Codex 已中断" if terminal_blocked else "Codex 已完成"
        else:
            state = "等待下一轮"

        self.pet.set_state(cookie_state)
        self._state_full_title = state
        self.state_label.setText(state)
        self.state_label.setToolTip(state)
        self._elide_state_title()
        self.message_label.setText(self.last_message)

        self.ai_card.setVisible(False)
        if source_state_unknown:
            self.ai_status_label.setText("Codex 连接中断，状态待确认")
        elif has_running_ai:
            assert primary_ai is not None
            status_text = "Codex 对话进行中"
            if len(running_ai) > 1:
                status_text += f" · {len(running_ai)} 个任务"
            if needs_attention:
                status_text += " · 另有任务等待批准"
            self.ai_status_label.setText(status_text)
        elif needs_attention and primary_ai is not None:
            self.ai_status_label.setText("Codex 等待你的操作")
        elif completed_visible:
            self.ai_status_label.setText(
                "Codex 已中断 · 微任务继续"
                if terminal_blocked and focus is not None
                else "Codex 已中断或运行失败"
                if terminal_blocked
                else "Codex 已完成 · 微任务继续"
                if focus is not None
                else "Codex 已完成"
            )
        highlighted = needs_attention or terminal_blocked
        if highlighted != self._ai_attention:
            self._ai_attention = highlighted
            self.ai_card.setProperty("attention", highlighted)
            self.ai_card.style().unpolish(self.ai_card)
            self.ai_card.style().polish(self.ai_card)

        if focus is not None:
            self._focus_full_title = focus.task.title
            self.focus_title.setText(self._focus_full_title)
            self.focus_title.setToolTip(focus.task.title)
            elapsed_text = format_duration(focus.elapsed_seconds())
            self.focus_time.setText(elapsed_text)
            self.compact_timer_label.setText(elapsed_text)
            self.compact_timer_label.setToolTip(f"{focus.task.title} · {elapsed_text}")
            self.pause_button.setText("▶\n继续" if focus.is_paused else "Ⅱ\n暂停")
            self.switch_button.setEnabled(True)
            self.switch_button.setToolTip(
                "选择另一个 Waiting Task"
                if focus.is_paused
                else "自动暂停当前任务，并选择另一个 Waiting Task"
            )

        else:
            self._focus_full_title = ""
            self.focus_title.clear()
            self.focus_title.setToolTip("")
            self.compact_timer_label.setText("00:00")
            self.compact_timer_label.setToolTip("当前 Waiting Task 计时")
            self.switch_button.setEnabled(False)

        picker_visible = (
            self.task_picker_open
            and not self.page_hidden
            and (focus is None or focus.is_paused)
        )
        if picker_visible:
            self.picker_title.setText(
                "切换 Waiting Task" if focus is not None else "选一个等待任务"
            )
            self._refresh_suggestions()
        notice_visible = self._notice_is_visible() and not self.page_hidden

        self._set_presentation_mode(
            choose_presentation_mode(
                focus is not None,
                picker_visible,
                page_hidden=self.page_hidden,
                notice_open=notice_visible,
                focus_paused=focus is not None and focus.is_paused,
            )
        )
        self.notice_card.setVisible(
            notice_visible and self.presentation_mode is not PresentationMode.ICON
        )
        # Keep the AI lifecycle visible alongside an active micro-task.  The
        # task timer is independent, so completion/attention feedback never
        # replaces or stops the focus bubble.
        self.ai_card.setVisible(
            self.presentation_mode is not PresentationMode.ICON
            and not self.page_hidden
            and (has_running_ai or needs_attention or unknown_running or unknown_attention or completed_visible)
        )
        if focus is not None:
            self._elide_focus_title()
            QTimer.singleShot(0, self._elide_focus_title)
        if self._state_full_title:
            QTimer.singleShot(0, self._elide_state_title)

        day_stats = self.service.stats_cache.get("day")
        minutes = int(day_stats.waiting_seconds // 60)
        self.today_label.setText(f"今日回收 {minutes} 分钟")
        self.home_stats_label.setText(
            "今天 · Waiting Task "
            f"{format_duration(day_stats.waiting_seconds)}"
        )
        self._update_connection_status()
        self.card.layout().invalidate()
        self.card.layout().activate()
        self._schedule_fit_to_content()
        if (
            self.isVisible()
            and self._native_topmost_enabled
            and time.monotonic() >= self._next_native_topmost_sync
        ):
            self._next_native_topmost_sync = time.monotonic() + 1.5
            self._apply_native_topmost()

    def _schedule_fit_to_content(self) -> None:
        """Coalesce layout fitting requests from refresh and chip geometry."""

        if self._fit_to_content_pending:
            return
        self._fit_to_content_pending = True
        QTimer.singleShot(0, self._run_scheduled_fit_to_content)

    def _run_scheduled_fit_to_content(self) -> None:
        self._fit_to_content_pending = False
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        # Presentation-mode stylesheet changes can restore Qt's default
        # button metrics; reapply the compact home selector after polishing.
        self.quick_task_tag.set_compact(True)
        self.quick_task_tag.sync_height()
        self.card.layout().invalidate()
        self.card.layout().activate()
        target_height = self.card.sizeHint().height() + 16
        if self.height() != target_height:
            self.setFixedHeight(target_height)

    def _elide_focus_title(self) -> None:
        if not self._focus_full_title:
            return
        width = self.focus_title.contentsRect().width()
        if width <= 0:
            return
        self.focus_title.setText(
            self.focus_title.fontMetrics().elidedText(
                self._focus_full_title,
                Qt.TextElideMode.ElideRight,
                width,
            )
        )

    def _elide_state_title(self) -> None:
        if not self._state_full_title:
            return
        width = self.state_label.contentsRect().width()
        if width <= 0:
            return
        self.state_label.setText(
            self.state_label.fontMetrics().elidedText(
                self._state_full_title,
                Qt.TextElideMode.ElideRight,
                width,
            )
        )

    def _set_presentation_mode(self, mode: PresentationMode) -> None:
        if self.presentation_mode is mode:
            return
        self.presentation_mode = mode
        is_picker = mode is PresentationMode.PICKER
        is_player = mode is PresentationMode.PLAYER
        is_compact_player = mode is PresentationMode.COMPACT_PLAYER
        is_notice = mode is PresentationMode.NOTICE
        self.bubble_card.setVisible(mode is not PresentationMode.ICON)
        self.header_details.setVisible(is_picker or is_notice)
        self.focus_card.setVisible(is_player)
        self.focus_controls.setVisible(is_player)
        self.focus_hide_button.setVisible(is_player)
        self.compact_timer_label.setVisible(is_compact_player)
        self.picker.setVisible(is_picker)
        self.notice_card.setVisible(is_notice or is_picker or is_player)
        self.ai_card.setVisible(False)
        self.footer_widget.setVisible(False)

        if mode is PresentationMode.ICON:
            width = self.pet.width() + 16
            margins = (4, 4, 4, 4)
            opacity = 0.88
        elif mode is PresentationMode.PLAYER:
            # The player sits beside the pet inside the same header row.  Its
            # window width therefore needs to reserve a full title row and
            # four equal-width controls; the old 340px minimum squeezed
            # long task names into the action buttons.
            # Include the pet, bubble margins, and the focus card's minimum
            # width in the top-level geometry; otherwise the rightmost
            # cancel button can be clipped at the window edge.
            width = max(570, self.pet.width() + 482)
            margins = (7, 6, 7, 6)
            opacity = 0.96
        elif mode is PresentationMode.COMPACT_PLAYER:
            # Keep only one small timer line in the existing bubble while the
            # expanded page is hidden.  The full player returns on click.
            self.focus_card.setMinimumWidth(0)
            width = max(220, self.pet.width() + 120)
            margins = (6, 5, 6, 5)
            opacity = 0.96
        elif mode is PresentationMode.NOTICE:
            width = max(360, self.pet.width() + 290)
            margins = (7, 6, 7, 6)
            opacity = 0.96
        else:
            width = max(430, self.pet.width() + 342)
            margins = (16, 14, 16, 14)
            opacity = 1.0

        self.card.layout().setContentsMargins(*margins)
        self.card.setProperty("presentation", mode.value)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        self.setWindowOpacity(opacity)
        if mode is PresentationMode.PLAYER:
            self.focus_card.setMinimumWidth(350)
        self.setFixedWidth(width)
        self._outer_shadow.setColor(
            QColor(40, 55, 50, 55 if mode is PresentationMode.ICON else 0)
        )
        QTimer.singleShot(0, self._apply_native_topmost)
        self.card.layout().invalidate()
        self.card.layout().activate()
        self._schedule_fit_to_content()
        QTimer.singleShot(0, self._keep_on_screen)

    def _update_connection_status(self, force: bool = False) -> None:
        current = time.monotonic()
        if not force and current < self._next_connection_check:
            return
        self._next_connection_check = current + 2.0
        info = self.hook_monitor.inspect()
        self._hook_info = info
        if self._desktop_source_available is True:
            label = "Codex · 已连接"
            detail = "桌面端本机状态源正常；不读取对话正文。"
            state = "connected"
        elif info.state is HookConnectionState.CONNECTED:
            label = "Codex · Hook 兜底"
            detail = "桌面状态源不可用，当前由可选 Hook 提供事件。"
            state = "fallback"
        elif self._desktop_source_available is None:
            label = "Codex · 检查中"
            detail = "正在检查桌面端本机状态源。"
            state = "checking"
        else:
            label = "Codex · 降级模式"
            detail = "自动状态源暂不可用；手动按钮和快捷键仍可使用。"
            state = "degraded"
        self.connection_status_button.setText(label)
        self.connection_status_button.setToolTip(detail)
        self.connection_status_button.setProperty("state", state)
        self.connection_status_button.style().unpolish(self.connection_status_button)
        self.connection_status_button.style().polish(self.connection_status_button)

    def show_codex_connection_info(self) -> None:
        self._update_connection_status(force=True)
        info = self._hook_info
        if info is None:
            return
        configured = "、".join(info.configured_events) or "无"
        last_event = "尚未收到"
        if info.last_event_at is not None:
            local_time = info.last_event_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            last_event = f"{info.last_event_name or '未知事件'} · {local_time}"
        desktop_state = "正常（主连接）" if self._desktop_source_available else "不可用"
        desktop_error = self._desktop_source_error or "无"
        source_path = self._desktop_source_path or "尚未检查"
        dialog = QMessageBox(self)
        dialog.setWindowTitle("WaitLAB · Codex 连接")
        dialog.setIcon(
            QMessageBox.Icon.Warning
            if self._desktop_source_available is False
            and info.state is not HookConnectionState.CONNECTED
            else QMessageBox.Icon.Information
        )
        dialog.setText(self.connection_status_button.text())
        dialog.setInformativeText(
            f"桌面状态源：{desktop_state}\n"
            f"状态库：{source_path}\n"
            f"读取范围：thread_id、turn_id、status、开始/结束时间\n"
            f"错误：{desktop_error}\n\n"
            f"可选 Hook：{info.label}\n"
            f"配置文件：{info.hooks_path}\n"
            f"已配置事件：{configured}\n"
            f"最近事件：{last_event}\n\n"
            "隐私说明：主连接不会查询消息、标题、工作目录、错误详情或 item JSON。\n"
            "Hook 只用于兼容和权限等待增强；Windows 桌面端不需要 /hooks。"
        )
        recalibrate = dialog.addButton("重新校准 Codex 状态", QMessageBox.ButtonRole.ActionRole)
        dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() is recalibrate:
            update = self.service.reconcile_desktop_sessions(
                self._desktop_snapshots,
            )
            self.apply_update(update)
            if not update.ai_completed and not update.ai_blocked:
                self.last_message = "Codex 状态已校准，未发现可关闭的陈旧会话"
                self.refresh()

    def _refresh_today_completed(self) -> None:
        summaries = self.service.storage.today_completed_tasks()
        signature = tuple(
            (
                summary.task_id,
                summary.title,
                round(summary.total_seconds, 3),
                summary.completed_count,
                summary.tag,
            )
            for summary in summaries
        )
        if signature != self._completed_signature:
            self._completed_signature = signature
            self.today_completed_list.clear()
            for summary in summaries:
                item = QListWidgetItem(self.today_completed_list)
                row = QFrame()
                row.setObjectName("completedRow")
                row_layout_parent = QVBoxLayout(row)
                row_layout_parent.setContentsMargins(0, 0, 0, 0)
                row_layout_parent.setSpacing(3)
                top_row = QFrame()
                row_layout = QHBoxLayout(top_row)
                row_layout.setContentsMargins(10, 8, 10, 2)
                row_layout.setSpacing(4)

                task_text = QVBoxLayout()
                task_text.setContentsMargins(0, 0, 0, 0)
                task_text.setSpacing(3)
                title = QLabel(summary.title)
                title.setObjectName("completedTitle")
                title.setToolTip(summary.title)
                title.setWordWrap(True)
                title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                completed_at = summary.last_completed_at.astimezone().strftime("%H:%M")
                meta_text = f"{summary.tag}  ·  完成 {summary.completed_count} 次 · 最近 {completed_at}"
                meta = QLabel(meta_text)
                meta.setObjectName("completedMeta")
                meta.setStyleSheet(
                    f"color: {tag_tone_colors(tag_tone(summary.tag))[0]};"
                )
                meta.setWordWrap(True)
                task_text.addWidget(title)
                task_text.addWidget(meta)
                row_layout.addLayout(task_text, 1)
                row_layout_parent.addWidget(top_row)

                bottom_row = QHBoxLayout()
                bottom_row.setContentsMargins(10, 0, 10, 8)
                bottom_row.setSpacing(7)
                duration = QLabel(f"总计 {format_duration(summary.total_seconds)}")
                duration.setObjectName("completedDuration")
                duration.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                bottom_row.addWidget(duration, 0)
                bottom_row.addStretch(1)
                continue_button = QPushButton("继续")
                continue_button.setObjectName("completedContinueButton")
                continue_button.setFixedWidth(54)
                continue_button.setToolTip("继续进行这个微任务")
                continue_button.clicked.connect(
                    lambda _checked=False, selected=summary: self._continue_completed_task(selected)
                )
                bottom_row.addWidget(continue_button, 0)
                details_button = QPushButton("展开")
                details_button.setObjectName("completedDetailsButton")
                details_button.setFixedWidth(54)
                bottom_row.addWidget(details_button, 0)
                row_layout_parent.addLayout(bottom_row)
                details = QListWidget()
                details.setObjectName("completedDetailsList")
                details.setVisible(False)
                for record in self.service.storage.completed_focus_records(
                    summary.task_id, summary.title, summary.kind, summary.tag
                ):
                    detail_item = QListWidgetItem(details)
                    detail_item.setSizeHint(QSize(0, 30))
                    detail_row = QFrame()
                    detail_layout = QHBoxLayout(detail_row)
                    detail_layout.setContentsMargins(6, 2, 4, 2)
                    started = record.started_at.astimezone().strftime("%H:%M")
                    ended = record.ended_at.astimezone().strftime("%H:%M")
                    detail_layout.addWidget(QLabel(f"{started}–{ended}  {format_duration(record.duration_seconds)}"), 1)
                    delete_record = QPushButton("删除")
                    delete_record.setObjectName("completedDeleteButton")
                    delete_record.setFixedWidth(48)
                    delete_record.clicked.connect(
                        lambda _checked=False, record_id=record.id: self._delete_completed_record(record_id)
                    )
                    detail_layout.addWidget(delete_record)
                    details.setItemWidget(detail_item, detail_row)
                details_height = max(0, details.count() * 30 + 8)
                details.setMinimumHeight(0)
                details.setMaximumHeight(max(1, details_height))
                details.setFixedHeight(max(1, details_height))
                row_layout_parent.addWidget(details)
                details_button.clicked.connect(
                    lambda _checked=False, widget=details, button=details_button, list_item=item: self._toggle_completed_details(widget, button, list_item)
                )
                self.today_completed_list.setItemWidget(item, row)
                item.setSizeHint(QSize(0, max(56, row.sizeHint().height())))

        has_completed = bool(summaries)
        self.today_completed_list.setVisible(has_completed)
        self.today_completed_empty.setVisible(not has_completed)
        self._update_completed_list_height()
        QTimer.singleShot(0, self._update_completed_list_height)

    def _update_completed_list_height(self) -> None:
        if not self.today_completed_list.isVisible() or self.today_completed_list.count() == 0:
            self.today_completed_list.setMinimumHeight(0)
            self.today_completed_list.setMaximumHeight(0)
            return
        total_height = sum(
            max(56, self.today_completed_list.item(index).sizeHint().height())
            for index in range(self.today_completed_list.count())
        )
        # Grow to the actual number of rows, while retaining a bounded scroll
        # area for unusually long histories so the desktop pet stays usable.
        target = min(430, max(56, total_height + 2))
        self.today_completed_list.setMinimumHeight(target)
        self.today_completed_list.setMaximumHeight(target)

    def _continue_completed_task(self, summary: CompletedTaskSummary) -> None:
        if self.service.has_active_focus():
            self.apply_update(
                self.service.pause_focus(message="褰撳墠浠诲姟宸叉殏鍋滐紝姝ｅ湪鍒囨崲浠诲姟")
            )
            self.start_focus(Task(summary.task_id, summary.title, summary.kind, 0, summary.tag))
            return
        if self.service.focus is not None and self.service.has_active_focus():
            self.apply_update(
                self.service.pause_focus(message="褰撳墠浠诲姟宸叉殏鍋滐紝姝ｅ湪鍒囨崲浠诲姟")
            )
            self.last_message = "请先完成或暂停当前微任务"
            self.refresh()
            return
        self.start_focus(Task(summary.task_id, summary.title, summary.kind, 0, summary.tag))

    def _toggle_completed_details(
        self,
        details: QWidget,
        button: QPushButton,
        item: QListWidgetItem,
    ) -> None:
        visible = not details.isVisible()
        details.setVisible(visible)
        button.setText("收起" if visible else "展开")
        row = self.today_completed_list.itemWidget(item)
        if row is not None:
            item.setSizeHint(QSize(0, row.sizeHint().height()))
        self._update_completed_list_height()
        self.today_completed_list.updateGeometry()
        self._schedule_fit_to_content()

    def _delete_completed_record(self, record_id: int) -> None:
        record = self.service.storage.get_completed_focus_record(record_id)
        if record is None:
            return
        answer = QMessageBox.question(
            self,
            "删除计时记录？",
            f"删除“{record.title}”在 {record.started_at.astimezone().strftime('%H:%M')}–"
            f"{record.ended_at.astimezone().strftime('%H:%M')} 的计时记录？\n"
            "删除后可在 8 秒内撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.service.storage.archive_focus_session(record_id):
            return
        self._deleted_history_record = record
        self._deleted_history_until = time.monotonic() + 8.0
        self.service.stats_cache.invalidate()
        self._completed_signature = None
        self._refresh_today_completed()
        self.show_notice(
            "计时记录已删除",
            f"{record.title} · {format_duration(record.duration_seconds)}",
            level="warning",
            duration=8.0,
        )
        self._show_history_undo()

    def _undo_deleted_history(self) -> None:
        record = self._deleted_history_record
        if record is None or time.monotonic() >= self._deleted_history_until:
            self._deleted_history_record = None
            self._deleted_history_until = 0.0
            self._hide_notice_actions()
            return
        if not self.service.storage.restore_archived_focus_session(record.id):
            self.last_message = "记录恢复失败，请检查本地数据库"
            self._deleted_history_record = None
            self._deleted_history_until = 0.0
            self._hide_notice_actions()
            self.refresh()
            return
        self._deleted_history_record = None
        self._deleted_history_until = 0.0
        self.service.stats_cache.invalidate()
        self._completed_signature = None
        self._refresh_today_completed()
        self.show_notice(
            "计时记录已恢复",
            f"已恢复：{record.title}",
            level="success",
            duration=4.0,
        )

    def _refresh_suggestions(self) -> None:
        manual = self.service.storage.list_manual_tasks()
        switching = self.service.focus is not None and self.service.focus.is_paused
        # Avoid parsing the fixed-cycle settings on every idle refresh when
        # manual tasks already determine the home picker's contents.  The
        # switcher still loads them alongside manual tasks.
        fixed_cycle = (
            self.service.fixed_cycle_tasks() if switching or not manual else []
        )
        paused = self.service.paused_focuses()
        paused_keys = {
            (session.task.id, session.task.kind, session.task.title)
            for session in paused
        }

        def available(candidates: list[Task]) -> list[Task]:
            return [
                task
                for task in candidates
                if (task.id, task.kind, task.title) not in paused_keys
            ]

        # The home picker keeps the original priority rule: manual tasks are
        # shown first, and fixed-cycle tasks are the fallback when no manual
        # task exists.  Once a task is paused for switching, both queues are
        # useful and must be visible so the user can move between them.
        if switching:
            groups = [
                ("我的具体任务", available(manual)),
                ("固定循环候选", available(fixed_cycle)),
            ]
            source_mode = "switcher"
        else:
            candidates = manual if manual else fixed_cycle
            groups = [
                ("我的具体任务" if manual else "固定循环候选", available(candidates))
            ]
            source_mode = "manual" if manual else "fixed"
        tasks = [task for _title, group in groups for task in group]
        signature = tuple((task.id, task.title, task.kind.value, task.tag) for task in tasks)
        paused_signature = tuple(
            (
                session.id,
                session.task.title,
                session.task.tag,
                int(session.elapsed_seconds()),
            )
            for session in paused
        )
        self._refresh_today_completed()
        self.random_task_button.setVisible(not manual and bool(tasks))
        self.picker_source.setText(
            "切换任务"
            if switching
            else "我的具体任务"
            if manual
            else "固定循环任务"
        )
        if (
            signature == self._suggestion_signature
            and paused_signature == self._paused_signature
            and source_mode == self._suggestion_mode
        ):
            return
        self._suggestion_signature = signature
        self._suggestion_mode = source_mode
        self._paused_signature = paused_signature
        while self.suggestion_layout.count():
            item = self.suggestion_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if paused:
            paused_title = QLabel("已暂停任务（点击继续）")
            paused_title.setObjectName("muted")
            self.suggestion_layout.addWidget(paused_title)
            for session in paused:
                button = QPushButton(
                    f"↻  {session.task.title}  ·  {format_duration(session.elapsed_seconds())}"
                )
                button.setObjectName("pausedTaskButton")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setToolTip("继续这个已暂停的 Waiting Task")
                button.clicked.connect(
                    lambda _checked=False, selected=session.task: self.start_focus(selected)
                )
                self.suggestion_layout.addWidget(button)
        if not tasks and not paused:
            empty = QLabel("暂无可用任务，请在 Waiting Task 中添加手动任务或启用固定任务。")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.suggestion_layout.addWidget(empty)
            configure = QPushButton("打开 Waiting Task")
            configure.clicked.connect(self.open_task_manager)
            self.suggestion_layout.addWidget(configure)
            return
        index = 1
        for section_title, section_tasks in groups:
            if not section_tasks:
                continue
            section = QLabel(section_title)
            section.setObjectName("muted")
            self.suggestion_layout.addWidget(section)
            for task in section_tasks:
                button = QPushButton(f"{index} · {task.title}  ·  {task.tag}")
                button.setObjectName("taskButton")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(
                    lambda _checked=False, selected=task: self.start_focus(selected)
                )
                self.suggestion_layout.addWidget(button)
                index += 1
        self.suggestion_layout.invalidate()
        self.suggestion_container.updateGeometry()
        self.picker.layout().invalidate()
        self.picker.updateGeometry()
        self.card.layout().invalidate()

    def _start_random_task(self) -> None:
        if self.service.storage.list_manual_tasks():
            self.last_message = "先完成或删除手动任务，再使用固定循环任务"
            self.refresh()
            return
        tasks = self.service.suggested_tasks()
        if not tasks:
            self.last_message = "暂无启用的固定循环任务"
            self.refresh()
            return
        # The service keeps the rotation order; choosing from the visible
        # candidates adds a lightweight random entry point without changing
        # persistence or completion semantics.
        self.start_focus(random.choice(tasks))

    def _add_quick_task(self) -> None:
        try:
            task = self.service.storage.add_manual_task(
                self.quick_task_input.text(),
                self.quick_task_tag.currentText(),
            )
        except ValueError:
            self.quick_task_input.setFocus()
            return
        self.quick_task_input.clear()
        self._suggestion_signature = None
        self.start_focus(task)

    def start_focus(self, task: Task) -> None:
        if self.service.has_active_focus():
            self.apply_update(
                self.service.pause_focus(message="褰撳墠浠诲姟宸叉殏鍋滐紝姝ｅ湪鍒囨崲浠诲姟")
            )
        if self.service.has_active_focus():
            self.last_message = "请先暂停当前微任务，再切换任务"
            self.refresh()
            return
        self.task_picker_open = False
        self._suggestion_signature = None
        self._paused_signature = None
        self.apply_update(self.service.start_focus(task))
        if self.task_dialog is not None:
            self.task_dialog.hide()

    def open_task_switcher(self) -> None:
        focus = self.service.focus
        if focus is None:
            self.task_picker_open = True
            self._suggestion_signature = None
            self._paused_signature = None
            self.refresh()
            return
        if not focus.is_paused:
            self.apply_update(
                self.service.pause_focus(message="当前任务已暂停，选择另一个任务继续")
            )
        self.task_picker_open = True
        self._suggestion_signature = None
        self._paused_signature = None
        self.last_message = "当前任务已暂停，选择另一个任务继续"
        self.refresh()

    def toggle_pause(self) -> None:
        self.apply_update(self.service.toggle_focus_pause())

    def complete_focus(self) -> None:
        completed_title = self.service.focus.task.title if self.service.focus is not None else "微任务"
        self._active_completion_turn_id = None
        self.apply_update(self.service.complete_focus())
        # Keep the completion expression in sync with the in-bubble success
        # notice so the user has enough time to see the feedback.
        self.task_completion_banner_until = time.monotonic() + 4.0
        self.task_picker_open = True
        self.show_notice(
            "微任务完成",
            f"已完成：{completed_title}",
            level="success",
            duration=4.0,
            sound=True,
        )
        self._suggestion_signature = None
        if self.task_dialog is not None:
            self.task_dialog.refresh()

    def abandon_focus(self) -> None:
        self.apply_update(self.service.abandon_focus())
        self._suggestion_signature = None

    def manual_ai_start(self) -> None:
        self.apply_update(self.service.manual_ai_started())

    def manual_ai_finish(self) -> None:
        self.apply_update(self.service.manual_ai_finished())

    def toggle_picker(self) -> None:
        if self.page_hidden:
            # Cookie is also the restore control: bring back the task picker
            # when idle, or the full player controls when a task is running.
            self.page_hidden = False
            if self.service.focus is None:
                self.task_picker_open = True
            self.refresh()
            self.raise_()
            self.activateWindow()
            self._apply_native_topmost()
            return
        if self.service.focus is None or self.service.focus.is_paused:
            self.task_picker_open = not self.task_picker_open
            self._suggestion_signature = None
            self._paused_signature = None
            self.refresh()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.service.focus is not None:
            if event.key() == Qt.Key.Key_Space:
                self.toggle_pause()
                event.accept()
                return
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self.complete_focus()
                event.accept()
                return
        elif self.task_picker_open and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_3:
            tasks = self.service.suggested_tasks()
            index = event.key() - Qt.Key.Key_1
            if index < len(tasks):
                self.start_focus(tasks[index])
                event.accept()
                return
        if event.key() == Qt.Key.Key_Escape:
            self.close_picker()
            event.accept()
            return
        super().keyPressEvent(event)

    def show_pet_menu(self, global_position: QPoint) -> None:
        menu = QMenu(self)
        if self.service.focus is not None:
            pause_label = "继续微任务" if self.service.focus.is_paused else "暂停微任务"
            pause_action = menu.addAction(pause_label)
            pause_action.triggered.connect(self.toggle_pause)
            if self.service.focus.is_paused:
                switch_action = menu.addAction("切换微任务")
                switch_action.triggered.connect(self.open_task_switcher)
            complete_action = menu.addAction("完成微任务")
            complete_action.triggered.connect(self.complete_focus)
            cancel_action = menu.addAction("取消并放回")
            cancel_action.triggered.connect(self.abandon_focus)
            menu.addSeparator()
        else:
            picker_action = menu.addAction("选择微任务")
            picker_action.triggered.connect(self.toggle_picker)
        tasks_action = menu.addAction("Waiting Task")
        tasks_action.triggered.connect(self.open_task_manager)
        stats_action = menu.addAction("统计")
        stats_action.triggered.connect(self.open_statistics)
        settings_action = menu.addAction("设置")
        settings_action.triggered.connect(self.open_settings)
        update_action = menu.addAction("检查更新")
        update_action.triggered.connect(self.check_for_updates)
        menu.addSeparator()
        hide_action = menu.addAction("隐藏到托盘")
        hide_action.triggered.connect(self.hide_pet)
        topmost_action = menu.addAction(
            "取消始终置顶" if self._native_topmost_enabled else "始终置顶"
        )
        topmost_action.triggered.connect(self.toggle_always_on_top)
        page_action = menu.addAction(
            "显示任务页面" if self.page_hidden else "隐藏页面（仅保留 Cookie）"
        )
        page_action.triggered.connect(self.show_page if self.page_hidden else self.hide_page)
        quit_action = menu.addAction("退出 WaitLAB")
        quit_action.triggered.connect(self.quit_requested)
        menu.exec(global_position)

    def close_picker(self) -> None:
        self.task_picker_open = False
        self.refresh()

    def skip_current_round(self) -> None:
        self.task_picker_open = False
        self.apply_update(self.service.skip_current_ai_round())

    def open_task_manager(self) -> None:
        if self.task_dialog is None:
            self.task_dialog = TaskManagerDialog(self.service, self)
            self.task_dialog.tasks_changed.connect(self._tasks_changed)
            self.task_dialog.task_started.connect(self.start_focus)
        self.task_dialog.refresh()
        self.task_dialog.show()
        self.task_dialog.raise_()
        self.task_dialog.activateWindow()

    def open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.service, self)
            self.settings_dialog.settings_changed.connect(self._settings_changed)
        self.settings_dialog.refresh()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def open_statistics(self) -> None:
        dialog = StatisticsDialog(self.service, self)
        dialog.exec()

    def show_recovery_prompt(self) -> None:
        focus = self.service.focus
        if focus is None or not self.service.has_recovered_focus:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("继续上次的微任务？")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(f"上次的微任务尚未结束：\n\n{focus.task.title}")
        dialog.setInformativeText(
            f"已记录 {format_duration(focus.elapsed_seconds())}。离线时间没有计入。"
        )
        continue_button = dialog.addButton("继续任务", QMessageBox.ButtonRole.AcceptRole)
        end_button = dialog.addButton("结束并放回", QMessageBox.ButtonRole.DestructiveRole)
        pause_button = dialog.addButton("保持暂停", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(continue_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is continue_button:
            self.apply_update(self.service.resume_focus(message="已继续上次的微任务"))
        elif clicked is end_button:
            self.apply_update(self.service.abandon_focus())
        elif clicked is pause_button:
            self.apply_update(ServiceUpdate(message="上次的微任务保持暂停"))

    def _tasks_changed(self) -> None:
        self._suggestion_signature = None
        self._refresh_quick_task_tags()
        self.refresh()

    def _refresh_quick_task_tags(self) -> None:
        """Keep the home-page quick-add selector in sync with tag management."""

        tags = self.service.storage.available_tags()
        selected = self.quick_task_tag.currentText()
        self.quick_task_tag.set_tags(tags, selected)

    def _settings_changed(self) -> None:
        self._suggestion_signature = None
        if self.task_dialog is not None:
            self.task_dialog.refresh()
        self.last_message = "设置已保存"
        self._apply_window_preferences()
        self.refresh()

    def _apply_window_preferences(self) -> None:
        preferences = Preferences.load(self.service.storage)
        old_cookie_size = self.pet.width()
        self.pet.set_size(preferences.cookie_size)
        if self.pet.width() != old_cookie_size:
            # Force geometry recalculation even when the presentation mode
            # itself did not change (for example while the player is open).
            self.presentation_mode = None
        self._native_topmost_enabled = preferences.always_on_top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, preferences.always_on_top)
        self.show()
        apply_native_topmost(self, preferences.always_on_top)
        QTimer.singleShot(0, lambda: apply_native_topmost(self, preferences.always_on_top))

    def toggle_always_on_top(self) -> None:
        enabled = not Preferences.load(self.service.storage).always_on_top
        self.service.storage.set_setting("always_on_top", "1" if enabled else "0")
        self._apply_window_preferences()
        self.refresh()

    def hide_page(self) -> None:
        """Collapse the page to the Cookie icon without stopping the app."""

        self.page_hidden = True
        self.task_picker_open = False
        self.refresh()
        self.show()
        self._apply_native_topmost()

    def show_page(self) -> None:
        """Restore the expanded page while keeping any active focus session."""

        self.page_hidden = False
        self.pet_hidden = False
        if self.service.focus is None:
            self.task_picker_open = True
        self.show()
        self.refresh()
        self.raise_()
        self.activateWindow()
        self._apply_native_topmost()

    def hide_pet(self) -> None:
        """Hide the whole desktop pet to the tray; timers keep running."""

        self.pet_hidden = True
        self.hide()

    def restore_from_tray(self, show_page: bool = False) -> None:
        self.pet_hidden = False
        if show_page:
            self.page_hidden = False
            if self.service.focus is None:
                self.task_picker_open = True
        self.show()
        self.refresh()
        self.raise_()
        self.activateWindow()
        self._apply_native_topmost()

    def _apply_native_topmost(self) -> None:
        apply_native_topmost(self, self._native_topmost_enabled)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Windows can reorder a tool window when another app is activated.
        # Reassert the native z-order after Qt has completed the show event.
        QTimer.singleShot(0, self._apply_native_topmost)

    def check_for_updates(self, silent: bool = False) -> None:
        def on_result(result: object) -> None:
            self._handle_update_check_result(result, silent)

        started = self.update_manager.check(on_result)
        if not started and not silent:
            self._show_update_result("更新检查已在进行中，请稍候")

    def _handle_update_check_result(self, result: object, silent: bool) -> None:
        if isinstance(result, BaseException):
            if not silent:
                self.update_check_finished.emit(describe_update_error(result))
        elif isinstance(result, ReleaseInfo):
            self.update_available.emit(result, silent)
        elif not silent:
            self.update_check_finished.emit("当前已是最新版本")

    def _offer_update(self, release: ReleaseInfo, silent: bool) -> None:
        if self.service.focus is not None:
            self._show_update_result(f"发现 {release.version}；当前任务结束后可一键更新")
            return
        if silent:
            self._show_update_result(f"发现 WaitLAB {release.version}，右键桌宠可开始更新")
            return
        answer = QMessageBox.question(self, "更新 WaitLAB", f"发现版本 {release.version}。现在下载、校验并安装吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._show_update_result("正在下载并校验更新…")
        started = self.update_manager.download(
            release,
            self._handle_update_download_result,
        )
        if not started:
            self._show_update_result("更新下载已在进行中，请稍候")

    def _handle_update_download_result(self, result: object) -> None:
        if isinstance(result, Path):
            self.update_downloaded.emit(result)
        elif isinstance(result, BaseException):
            self.update_check_finished.emit(describe_update_error(result))

    def _install_downloaded_update(self, installer: Path) -> None:
        if self.service.focus is not None:
            self._show_update_result("任务仍在进行，已取消本次安装")
            cleanup_download_directory(installer, delay_seconds=0.0)
            return
        launch_installer(installer)
        cleanup_download_directory(installer)
        self.quit_requested.emit()

    def _show_update_result(self, message: str) -> None:
        self.last_message = message
        preferences = Preferences.load(self.service.storage)
        if not preferences.is_quiet_now():
            self.show_notice("WaitLAB 更新", message, duration=4.5)
        self.refresh()

    def save_position(self) -> None:
        self.service.storage.set_setting("window_x", str(self.x()))
        self.service.storage.set_setting("window_y", str(self.y()))

    def _restore_position(self) -> None:
        x_value = self.service.storage.get_setting("window_x", "")
        y_value = self.service.storage.get_setting("window_y", "")
        saved_point = None
        try:
            if x_value and y_value:
                saved_point = QPoint(int(x_value), int(y_value))
        except (TypeError, ValueError):
            self.service.storage.set_setting("window_x", "")
            self.service.storage.set_setting("window_y", "")
        screen = QApplication.screenAt(saved_point) if saved_point is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            preferred_y = saved_point.y() if saved_point is not None else geometry.top() + 180
            maximum_y = max(geometry.top() + 10, geometry.bottom() - self.height() - 10)
            preferred_x = saved_point.x() if saved_point is not None else geometry.left() + 10
            maximum_x = max(geometry.left() + 10, geometry.right() - self.width() - 10)
            self.move(min(max(preferred_x, geometry.left() + 10), maximum_x), min(max(preferred_y, geometry.top() + 10), maximum_y))

    def _begin_pet_drag(self, global_position: QPoint) -> None:
        self._drag_origin = global_position - self.frameGeometry().topLeft()

    def _move_pet_drag(self, global_position: QPoint) -> None:
        if self._drag_origin is not None:
            self.move(global_position - self._drag_origin)

    def _finish_pet_drag(self) -> None:
        self._drag_origin = None
        self._snap_to_near_edge()
        self.save_position()

    def _snap_to_near_edge(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self._keep_on_screen()
        threshold = 28
        x = self.x()
        if abs(x - geometry.left()) <= threshold:
            self.move(geometry.left() + 10, self.y())
        elif abs((x + self.width()) - geometry.right()) <= threshold:
            self.move(geometry.right() - self.width() - 10, self.y())

    def _keep_on_screen(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = min(max(self.x(), geometry.left() + 10), max(geometry.left() + 10, geometry.right() - self.width() - 10))
        y = min(max(self.y(), geometry.top() + 10), max(geometry.top() + 10, geometry.bottom() - self.height() - 10))
        self.move(x, y)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.presentation_mode is PresentationMode.ICON:
            self.setWindowOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.presentation_mode is PresentationMode.ICON:
            self.setWindowOpacity(0.88)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 105:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None:
            self._drag_origin = None
            self._snap_to_near_edge()
            self.save_position()
        super().mouseReleaseEvent(event)


def create_tray(window: PetWindow) -> QSystemTrayIcon:
    tray = QSystemTrayIcon(app_icon(), window)
    tray.setToolTip("WaitLAB · 把等待变成可用进度")
    menu = QMenu()
    show_action = menu.addAction("显示 WaitLAB")
    show_action.triggered.connect(window.restore_from_tray)
    page_action = menu.addAction("显示任务页面")
    page_action.triggered.connect(lambda: window.restore_from_tray(show_page=True))
    settings_action = menu.addAction("设置")
    settings_action.triggered.connect(window.open_settings)
    menu.addSeparator()
    start_action = menu.addAction("手动开始等待")
    start_action.triggered.connect(window.manual_ai_start)
    finish_action = menu.addAction("AI 已完成")
    finish_action.triggered.connect(window.manual_ai_finish)
    pause_action = menu.addAction("暂停 / 继续微任务")
    pause_action.triggered.connect(window.toggle_pause)
    menu.addSeparator()
    quit_action = menu.addAction("退出")
    quit_action.triggered.connect(window.quit_requested)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.restore_from_tray()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()
    return tray


def _window_stylesheet() -> str:
    return f"""
    QFrame#mainCard {{
        background: {COLORS['cream']};
        border: 1px solid {COLORS['line']};
        border-radius: 22px;
    }}
    QFrame#mainCard[presentation="icon"] {{
        background: transparent;
        border: none;
        border-radius: 40px;
    }}
    QFrame#mainCard[presentation="player"], QFrame#mainCard[presentation="compact_player"], QFrame#mainCard[presentation="picker"] {{
        background: transparent;
        border: none;
        border-radius: 0;
    }}
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI'; }}
    {_tag_chip_stylesheet()}
    QLabel#stateTitle {{ font-size: 17px; font-weight: 700; }}
    QLabel#muted {{ color: {COLORS['muted']}; font-size: 11px; }}
    QLabel#sectionTitle {{ font-size: 14px; font-weight: 700; }}
    QLabel#eyebrow {{ color: {COLORS['mint_dark']}; font-size: 10px; font-weight: 700; }}
    QLabel#focusTitle {{ font-size: 14px; font-weight: 650; }}
    QLabel#timerLarge {{ font-family: 'Cascadia Mono'; font-size: 29px; font-weight: 700; }}
    QLabel#timerSmall {{ font-family: 'Cascadia Mono'; font-size: 16px; font-weight: 700; }}
    QLabel#timerCompact {{ color: {COLORS['muted']}; font-family: 'Cascadia Mono'; font-size: 13px; font-weight: 700; }}
    QLabel#compactTimer {{ color: {COLORS['muted']}; font-family: 'Cascadia Mono'; font-size: 11px; font-weight: 650; padding: 1px 4px; }}
    QLabel#cardLabel {{ font-size: 12px; font-weight: 650; }}
    QLabel#sourcePill {{
        color: {COLORS['mint_dark']}; background: #DDF1E9; border-radius: 8px;
        padding: 3px 8px; font-size: 10px; font-weight: 650;
    }}
    QFrame#aiCard {{ background: #FFF1D1; border-radius: 13px; }}
    QFrame#aiCard[attention="true"] {{ background: #FFE2D2; border: 1px solid {COLORS['peach']}; }}
    QFrame#focusCard {{
        background: {COLORS['white']}; border: 1px solid {COLORS['line']}; border-radius: 16px;
    }}
    QFrame#bubbleCard {{
        background: {COLORS['cream']}; border: 1px solid {COLORS['line']}; border-radius: 22px;
    }}
    QFrame#noticeCard {{
        background: #EEF8F3; border: 1px solid #CBE8DA; border-radius: 14px;
    }}
    QFrame#noticeCard[level="success"] {{ background: #E6F6EE; border-color: #B9DEC9; }}
    QFrame#noticeCard[level="warning"] {{ background: #FFF4DA; border-color: #F0D28A; }}
    QFrame#noticeCard[level="error"] {{ background: #FFE9DE; border-color: #EAB89E; }}
    QLabel#noticeTitle {{ font-size: 12px; font-weight: 700; }}
    QLabel#noticeBody {{ color: {COLORS['muted']}; font-size: 10px; }}
    QPushButton#noticeActionButton, QPushButton#noticePrimaryActionButton {{
        padding: 4px 8px; font-size: 10px; border-radius: 7px;
    }}
    QPushButton#noticePrimaryActionButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']};
    }}
    QListWidget#todayCompletedList {{
        background: transparent; border: none; padding: 0;
    }}
    QListWidget#todayCompletedList::item {{ padding: 0; border: none; }}
    QFrame#completedRow {{
        background: {COLORS['white']}; border: 1px solid {COLORS['line']}; border-radius: 10px;
    }}
    QListWidget#completedDetailsList {{
        background: #FBF8F2; border: none; border-top: 1px solid {COLORS['line']};
        padding: 2px 6px 4px 6px;
    }}
    QListWidget#completedDetailsList::item {{ padding: 0; border: none; }}
    QPushButton#completedDetailsButton, QPushButton#completedDeleteButton {{
        color: {COLORS['muted']}; background: transparent; border: 1px solid {COLORS['line']};
        padding: 3px 6px; font-size: 10px;
    }}
    QPushButton#completedDetailsButton:hover {{ color: {COLORS['mint_dark']}; background: #EDF8F3; }}
    QPushButton#completedDeleteButton:hover {{ color: #A5533D; background: #FFE9DE; }}
    QFrame#statRow {{
        background: {COLORS['white']}; border: 1px solid {COLORS['line']}; border-radius: 10px;
    }}
    QLabel#statValue {{ color: {COLORS['mint_dark']}; font-family: 'Cascadia Mono'; font-weight: 700; }}
    QLabel#chartLegend {{ color: {COLORS['muted']}; font-size: 11px; line-height: 1.35; }}
    QPushButton#periodButton {{ padding: 5px 11px; font-size: 10px; }}
    QPushButton#periodButton:checked {{
        color: {COLORS['mint_dark']}; background: #DDF1E9; border-color: #BFE4D6;
        font-weight: 700;
    }}
    QLabel#completedTitle {{ font-size: 11px; font-weight: 650; }}
    QLabel#completedMeta {{ color: {COLORS['muted']}; font-size: 9px; }}
    QLabel#completedDuration {{
        color: {COLORS['mint_dark']}; font-family: 'Cascadia Mono';
        font-size: 10px; font-weight: 700;
    }}
    QPushButton#completedContinueButton {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
        padding: 4px 7px; font-size: 10px; font-weight: 650;
    }}
    QPushButton#completedContinueButton:hover {{ background: #DDF1E9; border-color: {COLORS['mint']}; }}
    QFrame#picker {{ background: transparent; border: none; }}
    QPushButton {{
        color: {COLORS['ink']}; background: {COLORS['white']};
        border: 1px solid {COLORS['line']}; border-radius: 9px;
        padding: 7px 11px; font-family: 'Microsoft YaHei UI'; font-size: 11px;
    }}
    QPushButton:hover {{ border-color: {COLORS['mint']}; background: #F5FCF9; }}
    QPushButton#primaryButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; font-weight: 650;
    }}
    QPushButton#primaryButton:hover {{ background: #2F6E5D; }}
    QPushButton#secondaryButton {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
        font-weight: 650; padding: 7px 10px;
    }}
    QPushButton#secondaryButton:hover {{ background: #E1F3EA; border-color: {COLORS['mint']}; }}
    QPushButton#playerButton, QPushButton#playerPrimaryButton {{
        min-width: 82px; min-height: 44px; padding: 3px 5px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 10px; line-height: 1.0;
    }}
    QPushButton#playerSwitchButton {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
        min-width: 60px; max-width: 60px; min-height: 44px; max-height: 44px;
        padding: 3px 4px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 10px; line-height: 1.0;
    }}
    QPushButton#playerSwitchButton:disabled {{
        color: {COLORS['muted']}; background: #F5F2ED; border-color: {COLORS['line']};
    }}
    QPushButton#playerPrimaryButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; font-weight: 650;
    }}
    QPushButton#playerCloseButton {{
        background: transparent; border: 1px solid {COLORS['line']}; color: {COLORS['muted']};
        min-width: 82px; min-height: 44px; padding: 3px 5px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 10px; line-height: 1.0;
    }}
    QPushButton#playerCloseButton:hover {{ color: #A5533D; background: #FFE9DE; }}
    QPushButton#taskButton {{
        text-align: left; padding: 11px 13px; background: {COLORS['white']}; font-size: 12px;
    }}
    QPushButton#ghostButton {{ background: transparent; padding: 6px 8px; }}
    QPushButton#connectionStatusButton {{
        background: #F0ECE5; border: none; border-radius: 8px;
        color: {COLORS['muted']}; padding: 3px 7px; font-size: 9px;
    }}
    QPushButton#connectionStatusButton[state="connected"] {{ background: #DDF1E9; color: {COLORS['mint_dark']}; }}
    QPushButton#connectionStatusButton[state="fallback"] {{ background: #FFF1D1; color: #8A6217; }}
    QPushButton#connectionStatusButton[state="degraded"] {{ background: #FFE2D2; color: #9B4F30; }}
    QPushButton#iconButton {{ background: transparent; border: none; color: {COLORS['muted']}; font-size: 15px; padding: 0px; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }}
    QPushButton#iconButton:hover {{ color: {COLORS['ink']}; background: #F3F0EA; border-radius: 8px; }}
    QPushButton#linkButton {{ background: transparent; border: none; color: {COLORS['muted']}; padding-left: 2px; }}
    """


def _dialog_stylesheet() -> str:
    return f"""
    QDialog {{ background: {COLORS['cream']}; }}
    QScrollArea#taskManagerScroll {{ background: transparent; border: none; }}
    QWidget#taskManagerPage {{ background: transparent; }}
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI'; }}
    {_tag_chip_stylesheet()}
    QLabel#dialogTitle {{ font-size: 23px; font-weight: 750; }}
    QLabel#muted {{ color: {COLORS['muted']}; font-size: 12px; }}
    QLabel#fallback {{ color: {COLORS['ink']}; background: #FFF1D1; border-radius: 10px; padding: 10px; }}
    QLineEdit, QListWidget, QComboBox, QSpinBox {{
        color: {COLORS['ink']}; background: white; border: 1px solid {COLORS['line']};
        border-radius: 10px; padding: 9px; font-family: 'Microsoft YaHei UI';
    }}
    QCheckBox {{ color: {COLORS['ink']}; spacing: 8px; padding: 3px; }}
    QListWidget::item {{ padding: 9px; border-bottom: 1px solid #F2EEE8; }}
    QListWidget::item:selected {{ color: {COLORS['ink']}; background: #DDF1E9; border-radius: 7px; }}
    QPushButton {{
        color: {COLORS['ink']}; background: white; border: 1px solid {COLORS['line']};
        border-radius: 9px; padding: 8px 13px; font-family: 'Microsoft YaHei UI';
    }}
    QPushButton#primaryButton {{ color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; }}
    """
