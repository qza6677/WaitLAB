"""Reusable Qt widgets for the desktop pet and task pickers.

These widgets are deliberately presentation-only. They emit user gestures and
render state supplied by their owners, but never call application services or
Storage directly.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QSize,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLayout,
    QLayoutItem,
    QLabel,
    QMenu,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
    QVBoxLayout,
)

from .cookie import CookieAssets, CookieState, coerce_cookie_state
from .models import DEFAULT_TAG, DefaultTaskEntry
from .ui_primitives import COLORS, tag_palette_for_tag, tag_tone


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
        self._animated_states = {
            CookieState.WAITING,
            CookieState.ATTENTION,
            CookieState.ERROR,
        }
        self._transition = QVariantAnimation(self)
        self._transition.setDuration(180)
        self._transition.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._transition.valueChanged.connect(self._on_transition_value)
        self._transition.finished.connect(self._finish_transition)
        self.set_state(CookieState.IDLE)

    def _sync_animation_timer(self) -> None:
        """Animate only states that visibly use the bobbing phase."""

        should_run = self.cookie_state in self._animated_states
        if should_run and not self._timer.isActive():
            self._timer.start(90)
        elif not should_run and self._timer.isActive():
            self._timer.stop()

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
        self._sync_animation_timer()
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


class FlowLayout(QLayout):
    """A small wrapping layout for tag chips."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._wrap = True
        self.setSpacing(6)

    def set_wrap(self, wrap: bool) -> None:
        next_wrap = bool(wrap)
        if next_wrap == self._wrap:
            return
        self._wrap = next_wrap
        self.invalidate()

    def wraps(self) -> bool:
        return self._wrap

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
        if self._wrap:
            size = QSize(0, 0)
            for item in self._items:
                size = size.expandedTo(item.minimumSize())
        else:
            width = sum(item.sizeHint().width() for item in self._items)
            if self._items:
                width += self.spacing() * (len(self._items) - 1)
            height = max((item.sizeHint().height() for item in self._items), default=0)
            size = QSize(width, height)
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
            if self._wrap and next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + spacing
                next_x = x + item_size.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, item_size.width(), item_size.height()))
            x = next_x
            line_height = max(line_height, item_size.height())
        return y + line_height - rect.y() + margins.bottom()


TASK_ROW_HEIGHT = 40
TASK_ROW_CONTROL_HEIGHT = 30


def _two_line_elided_text(text: str, font_metrics: QFontMetrics, width: int) -> str:
    """Keep a task title readable in two lines at narrow widths."""

    clean = " ".join(str(text).split())
    available = max(1, int(width))
    if font_metrics.horizontalAdvance(clean) <= available:
        return clean
    low, high = 1, len(clean)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if font_metrics.horizontalAdvance(clean[:middle]) <= available:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    if best <= 0:
        return font_metrics.elidedText(clean, Qt.TextElideMode.ElideRight, available)
    first = clean[:best].rstrip()
    remainder = clean[best:].lstrip()
    second = font_metrics.elidedText(remainder, Qt.TextElideMode.ElideRight, available)
    return f"{first}\n{second}"


class TagPickerButton(QPushButton):
    """A compact colored tag chip that opens an inline selection menu."""

    tag_changed = Signal(str)
    manage_tags_requested = Signal()
    popup_requested = Signal(object)

    def __init__(
        self,
        tags: list[str] | tuple[str, ...] = (),
        selected: str | None = None,
        parent: QWidget | None = None,
        *,
        tone_map: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("taskTagButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(TASK_ROW_CONTROL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._tags: list[str] = []
        self._selected = ""
        self._tone_map = dict(tone_map or {})
        # Keep the menu as a compatibility surface for integrations and tests
        # that inspect or trigger its actions.  The visible interaction uses a
        # dialog-owned popup instead: a native QMenu is a separate top-level
        # window and can fall behind the task dialog on Windows.
        self._menu = QMenu(self)
        self.clicked.connect(self._request_popup)
        self.set_tags(tags, selected)

    def menu(self) -> QMenu:  # noqa: N802 - compatibility API
        """Return the legacy action menu without using it as a native popup."""

        return self._menu

    def tags(self) -> list[str]:
        return list(self._tags)

    def currentText(self) -> str:  # noqa: N802 - QComboBox-compatible API
        return self._selected

    def setCurrentText(self, text: str) -> None:  # noqa: N802 - compatibility API
        self._select(str(text).strip(), emit=True)

    def set_tone_map(self, tone_map: dict[str, str] | None = None) -> None:
        self._tone_map = dict(tone_map or {})
        self._rebuild_menu()
        self._apply_current_style()

    def set_tags(
        self,
        tags: list[str] | tuple[str, ...],
        selected: str | None = None,
    ) -> None:
        values: list[str] = []
        for value in tags:
            clean_value = str(value).strip()
            if clean_value and clean_value not in values:
                values.append(clean_value)
        if not values:
            values = [DEFAULT_TAG]
        target = str(selected or self._selected or values[0]).strip()
        if target not in values:
            target = DEFAULT_TAG if DEFAULT_TAG in values else values[0]
        self._tags = values
        self._selected = target or values[0]
        self._rebuild_menu()
        self._apply_current_style()

    @staticmethod
    def _swatch_icon(color: str) -> QIcon:
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(2, 2, 10, 10))
        painter.end()
        return QIcon(pixmap)

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        for tag in self._tags:
            accent, _foreground, _background, _border = tag_palette_for_tag(
                tag,
                self._tone_map,
            )
            action = self._menu.addAction(self._swatch_icon(accent), tag)
            action.setData(tag)
            action.setCheckable(True)
            action.setChecked(tag == self._selected)
            action.triggered.connect(
                lambda _checked=False, value=tag: self._select(value, emit=True)
            )
        self._menu.addSeparator()
        manage_action = self._menu.addAction("管理标签…")
        manage_action.triggered.connect(lambda: self.manage_tags_requested.emit())

    def _select(self, tag: str, *, emit: bool) -> None:
        if tag not in self._tags:
            return
        changed = tag != self._selected
        self._selected = tag
        for action in self._menu.actions():
            value = action.data()
            if isinstance(value, str):
                action.setChecked(value == tag)
        self._apply_current_style()
        if emit and changed:
            self.tag_changed.emit(tag)

    def _request_popup(self) -> None:
        self.popup_requested.emit(self)

    def _apply_current_style(self) -> None:
        accent, foreground, background, border = tag_palette_for_tag(
            self._selected,
            self._tone_map,
        )
        text_width = self.fontMetrics().horizontalAdvance(self._selected)
        self.setFixedWidth(max(68, min(112, text_width + 34)))
        self.setText(self._selected)
        self.setAccessibleName(f"任务标签：{self._selected}")
        self.setToolTip(f"当前标签：{self._selected}；点击修改")
        self.setStyleSheet(
            "QPushButton#taskTagButton {"
            f"color:{foreground}; background:{background}; border:1px solid {border};"
            "border-radius:9px; padding:0 18px;"
            "font-size:10px; font-weight:650; text-align:center;"
            "}"
            "QPushButton#taskTagButton:hover { background:#FFFFFF; }"
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        accent, _foreground, _background, _border = tag_palette_for_tag(
            self._selected,
            self._tone_map,
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        center_y = self.height() / 2
        arrow = QPainterPath()
        arrow.moveTo(self.width() - 13, center_y - 2)
        arrow.lineTo(self.width() - 5, center_y - 2)
        arrow.lineTo(self.width() - 9, center_y + 3)
        arrow.closeSubpath()
        painter.drawPath(arrow)
        painter.end()


class TagPickerPopup(QFrame):
    """A dialog-owned tag picker that cannot fall behind its task window."""

    tag_selected = Signal(str)
    manage_tags_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("taskTagPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._button: TagPickerButton | None = None
        self._event_filter_installed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("taskTagPopupList")
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.list_widget.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_widget.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.list_widget.itemClicked.connect(self._select_item)
        self.list_widget.itemActivated.connect(self._select_item)
        layout.addWidget(self.list_widget)

        self.manage_button = QPushButton("管理标签…", self)
        self.manage_button.setObjectName("taskTagPopupManage")
        self.manage_button.clicked.connect(self._manage_tags)
        layout.addWidget(self.manage_button)
        self.hide()

    def open_for(self, button: TagPickerButton) -> None:
        """Open below or above ``button`` while staying inside the dialog."""

        if self.isVisible() and self._button is button:
            self.hide()
            return
        self._button = button
        self._populate(button)
        self.adjustSize()
        host = self.parentWidget()
        if host is None:
            self.hide()
            return

        button_top = host.mapFromGlobal(button.mapToGlobal(QPoint(0, 0)))
        margin = 8
        width = self.width()
        height = self.height()
        max_x = max(margin, host.width() - margin - width)
        x = min(max(margin, button_top.x()), max_x)
        below_y = button_top.y() + button.height() + 4
        above_y = button_top.y() - height - 4
        max_y = max(margin, host.height() - margin - height)
        if below_y <= max_y:
            y = below_y
        elif above_y >= margin:
            y = above_y
        else:
            y = min(max(margin, below_y), max_y)
        self.setGeometry(x, y, width, height)
        self.show()
        self.raise_()
        self._install_event_filter()
        self.list_widget.setFocus(Qt.FocusReason.PopupFocusReason)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._remove_event_filter()
        self._button = None
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if not self.isVisible():
            return super().eventFilter(watched, event)
        event_type = event.type()
        if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.hide()
            return True
        if event_type == QEvent.Type.MouseButtonPress:
            watched_widget = watched if isinstance(watched, QWidget) else None
            if watched_widget is not None and self.isAncestorOf(watched_widget):
                return False
            if watched_widget is self._button:
                return False
            self.hide()
        elif event_type in {QEvent.Type.Hide, QEvent.Type.Close}:
            host = self.parentWidget()
            if watched is host:
                self.hide()
        return super().eventFilter(watched, event)

    def _populate(self, button: TagPickerButton) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        max_text_width = 0
        selected_row = -1
        for row, tag in enumerate(button.tags()):
            accent, _foreground, _background, _border = tag_palette_for_tag(
                tag,
                button._tone_map,
            )
            item = QListWidgetItem(TagPickerButton._swatch_icon(accent), tag)
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.list_widget.addItem(item)
            if tag == button.currentText():
                selected_row = row
            max_text_width = max(
                max_text_width,
                self.fontMetrics().horizontalAdvance(tag),
            )
        if selected_row >= 0:
            self.list_widget.setCurrentRow(selected_row)
        self.list_widget.blockSignals(False)

        row_height = max(28, self.list_widget.sizeHintForRow(0))
        visible_rows = min(8, max(1, self.list_widget.count()))
        self.list_widget.setFixedHeight(row_height * visible_rows + 2)
        self.setFixedWidth(max(132, min(260, max_text_width + 58)))

    def _select_item(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(value, str):
            return
        self.hide()
        self.tag_selected.emit(value)

    def _manage_tags(self) -> None:
        self.hide()
        self.manage_tags_requested.emit()

    def _install_event_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._event_filter_installed:
            app.installEventFilter(self)
            self._event_filter_installed = True

    def _remove_event_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self._event_filter_installed:
            app.removeEventFilter(self)
            self._event_filter_installed = False


class TaskRowWidget(QWidget):
    """A compact actionable task row with a two-line title fallback."""

    action_requested = Signal(str)
    checked_changed = Signal(bool)
    tag_changed = Signal(str)
    manage_tags_requested = Signal()
    tag_popup_requested = Signal(object)

    def __init__(
        self,
        title: str,
        tag: str,
        tone: str,
        *,
        completed: bool = False,
        meta: str = "",
        priority: int = 0,
        overdue: bool = False,
        has_history: bool = False,
        carry_label: str = "\u4eca\u5929",
        tags: list[str] | tuple[str, ...] = (),
        tone_map: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("taskRow")
        self.setFixedHeight(TASK_ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 5, 4, 5)
        layout.setSpacing(5)

        self.checkbox = QCheckBox(self)
        self.checkbox.setFixedWidth(26)
        self.checkbox.setChecked(bool(completed))
        self.checkbox.setToolTip(
            "\u6807\u8bb0\u5b8c\u6210" if not completed else "\u53d6\u6d88\u5b8c\u6210"
        )
        self.checkbox.toggled.connect(self._on_checked)
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        # Kept for compatibility with integrations that still inspect this
        # attribute; priority is no longer part of the task workflow.
        self.priority_label = None

        self._full_title = str(title)
        self.title_label = QLabel(self._full_title, self)
        self.title_label.setObjectName("taskRowTitle")
        self.title_label.setToolTip(title)
        self.title_label.setWordWrap(False)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.title_label.setMaximumHeight(
            self.title_label.fontMetrics().lineSpacing() * 2 + 2
        )
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        picker_tones = dict(tone_map or {})
        picker_tones.setdefault(tag, tone)
        available_tags = list(tags) if tags else [tag]
        self.tag_label = TagPickerButton(
            available_tags,
            tag,
            self,
            tone_map=picker_tones,
        )
        self.tag_label.tag_changed.connect(self.tag_changed.emit)
        self.tag_label.manage_tags_requested.connect(self.manage_tags_requested.emit)
        self.tag_label.popup_requested.connect(self.tag_popup_requested.emit)
        layout.addWidget(self.tag_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.meta_label: QLabel | None = None
        if meta:
            self.meta_label = QLabel(meta, self)
            self.meta_label.setObjectName("muted")
            self.meta_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.meta_label.setWordWrap(False)
            self.meta_label.setMaximumWidth(76)
            self.meta_label.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed,
            )
            self.meta_label.setToolTip(meta)
            layout.addWidget(self.meta_label, 0, Qt.AlignmentFlag.AlignVCenter)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(5)

        self.start_button = QPushButton("\u5f00\u59cb", self)
        self.start_button.setObjectName("taskRowStartButton")
        self.start_button.setToolTip("\u5f00\u59cb\u8fd9\u9879\u4efb\u52a1\u7684\u8ba1\u65f6")
        self.start_button.clicked.connect(lambda: self.action_requested.emit("start"))
        self.start_button.setVisible(not completed and not overdue)
        self.start_button.setFixedSize(48, TASK_ROW_CONTROL_HEIGHT)
        actions.addWidget(self.start_button)

        self.complete_button = QPushButton("\u5b8c\u6210", self)
        self.complete_button.setObjectName("taskRowDecisionButton")
        self.complete_button.setToolTip(
            "\u5c06\u8fd9\u9879\u8fc7\u5f80\u4efb\u52a1\u6807\u8bb0\u4e3a\u5df2\u5b8c\u6210"
        )
        self.complete_button.clicked.connect(lambda: self.action_requested.emit("complete"))
        # Overdue tasks use the same completion checkbox as today's tasks;
        # this keeps the row compact while preserving an explicit status action.
        self.complete_button.setVisible(False)
        self.complete_button.setFixedSize(48, TASK_ROW_CONTROL_HEIGHT)
        actions.addWidget(self.complete_button)

        self.carry_button = QPushButton("\u5ef6\u7eed", self)
        self.carry_button.setObjectName("taskRowDecisionButton")
        self.carry_button.setToolTip(f"\u5ef6\u7eed\u5230{carry_label}")
        self.carry_button.clicked.connect(lambda: self.action_requested.emit("carry"))
        self.carry_button.setVisible(overdue)
        self.carry_button.setFixedSize(48, TASK_ROW_CONTROL_HEIGHT)
        actions.addWidget(self.carry_button)

        # Keep the old button attributes for compatibility. Secondary actions
        # are now kept behind one compact, consistently sized menu button.
        self.edit_button = QPushButton("\u7f16\u8f91", self)
        self.edit_button.setObjectName("taskRowActionButton")
        self.edit_button.clicked.connect(lambda: self.action_requested.emit("edit"))
        self.edit_button.hide()

        self.history_button = QPushButton("\u8f68\u8ff9", self)
        self.history_button.setObjectName("taskRowActionButton")
        self.history_button.setToolTip("\u67e5\u770b\u8fd9\u9879\u4efb\u52a1\u7684\u65e5\u671f\u53d8\u66f4\u8f68\u8ff9")
        self.history_button.clicked.connect(lambda: self.action_requested.emit("history"))
        self.history_button.setVisible(has_history)
        self.history_button.hide()

        self.delete_button = QPushButton("\u5220\u9664", self)
        self.delete_button.setObjectName("taskRowActionButton")
        self.delete_button.clicked.connect(lambda: self.action_requested.emit("delete"))
        self.delete_button.hide()

        self.more_button = QToolButton(self)
        self.more_button.setObjectName("taskRowMoreButton")
        self.more_button.setText("\u2026")
        self.more_button.setToolTip("\u7f16\u8f91\u3001\u67e5\u770b\u8f68\u8ff9\u6216\u5220\u9664")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setFixedSize(TASK_ROW_CONTROL_HEIGHT, TASK_ROW_CONTROL_HEIGHT)
        self.more_menu = QMenu(self.more_button)
        edit_action = self.more_menu.addAction("\u7f16\u8f91")
        edit_action.triggered.connect(lambda: self.action_requested.emit("edit"))
        if has_history:
            history_action = self.more_menu.addAction("\u67e5\u770b\u8f68\u8ff9")
            history_action.triggered.connect(lambda: self.action_requested.emit("history"))
        self.more_menu.addSeparator()
        delete_action = self.more_menu.addAction("\u5220\u9664")
        delete_action.triggered.connect(lambda: self.action_requested.emit("delete"))
        self.more_button.setMenu(self.more_menu)
        actions.addWidget(self.more_button)
        layout.addLayout(actions, 0)

        self._apply_completed_style(bool(completed))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep long titles readable without pushing task actions away."""

        super().resizeEvent(event)
        available = max(1, self.title_label.contentsRect().width())
        self.title_label.setText(
            _two_line_elided_text(self._full_title, self.title_label.fontMetrics(), available)
        )

    def _on_checked(self, checked: bool) -> None:
        self.checkbox.setToolTip(
            "\u53d6\u6d88\u5b8c\u6210" if checked else "\u6807\u8bb0\u5b8c\u6210"
        )
        self._apply_completed_style(checked)
        self.checked_changed.emit(bool(checked))

    def _apply_completed_style(self, completed: bool) -> None:
        font = self.title_label.font()
        font.setStrikeOut(bool(completed))
        self.title_label.setFont(font)


class FixedTaskRowWidget(QWidget):
    """Compact editor row for one fixed task entry."""

    action_requested = Signal(str)
    enabled_changed = Signal(bool)
    tag_changed = Signal(str)
    manage_tags_requested = Signal()
    tag_popup_requested = Signal(object)

    def __init__(
        self,
        entry: DefaultTaskEntry,
        tone: str,
        parent: QWidget | None = None,
        *,
        tags: list[str] | tuple[str, ...] = (),
        tone_map: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("fixedTaskRow")
        self.setFixedHeight(TASK_ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 5, 4, 5)
        layout.setSpacing(5)

        self.enabled_checkbox = QCheckBox(self)
        self.enabled_checkbox.setChecked(entry.enabled)
        self.enabled_checkbox.setToolTip(
            "\u505c\u7528\u56fa\u5b9a\u4efb\u52a1"
            if entry.enabled
            else "\u542f\u7528\u56fa\u5b9a\u4efb\u52a1"
        )
        self.enabled_checkbox.toggled.connect(self._on_enabled_changed)
        self.enabled_checkbox.setFixedWidth(26)
        layout.addWidget(self.enabled_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(entry.title, self)
        self.title_label.setObjectName("taskRowTitle")
        self.title_label.setToolTip(entry.title)
        self.title_label.setWordWrap(False)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._full_title = entry.title
        self.title_label.setMaximumHeight(
            self.title_label.fontMetrics().lineSpacing() * 2 + 2
        )
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        picker_tones = dict(tone_map or {})
        picker_tones.setdefault(entry.tag, tone)
        available_tags = list(tags) if tags else [entry.tag]
        self.tag_label = TagPickerButton(
            available_tags,
            entry.tag,
            self,
            tone_map=picker_tones,
        )
        self.tag_label.tag_changed.connect(self.tag_changed.emit)
        self.tag_label.manage_tags_requested.connect(self.manage_tags_requested.emit)
        self.tag_label.popup_requested.connect(self.tag_popup_requested.emit)
        layout.addWidget(self.tag_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.schedule_label = QLabel(entry.schedule_label, self)
        self.schedule_label.setObjectName("muted")
        self.schedule_label.setToolTip(entry.schedule_label)
        self.schedule_label.setWordWrap(False)
        self.schedule_label.setMaximumWidth(62)
        self.schedule_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.schedule_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.edit_button = QPushButton("\u7f16\u8f91", self)
        self.edit_button.setObjectName("taskRowActionButton")
        self.edit_button.clicked.connect(lambda: self.action_requested.emit("edit"))
        self.edit_button.hide()

        self.delete_button = QPushButton("\u5220\u9664", self)
        self.delete_button.setObjectName("taskRowActionButton")
        self.delete_button.clicked.connect(lambda: self.action_requested.emit("delete"))
        self.delete_button.hide()

        self.more_button = QToolButton(self)
        self.more_button.setObjectName("taskRowMoreButton")
        self.more_button.setText("\u2026")
        self.more_button.setToolTip("\u7f16\u8f91\u6216\u5220\u9664\u56fa\u5b9a\u4efb\u52a1")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setFixedSize(TASK_ROW_CONTROL_HEIGHT, TASK_ROW_CONTROL_HEIGHT)
        self.more_menu = QMenu(self.more_button)
        edit_action = self.more_menu.addAction("\u7f16\u8f91")
        edit_action.triggered.connect(lambda: self.action_requested.emit("edit"))
        self.more_menu.addSeparator()
        delete_action = self.more_menu.addAction("\u5220\u9664")
        delete_action.triggered.connect(lambda: self.action_requested.emit("delete"))
        self.more_button.setMenu(self.more_menu)
        layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        available = max(1, self.title_label.contentsRect().width())
        self.title_label.setText(
            _two_line_elided_text(self._full_title, self.title_label.fontMetrics(), available)
        )

    def _on_enabled_changed(self, enabled: bool) -> None:
        self.enabled_checkbox.setToolTip(
            "\u505c\u7528\u56fa\u5b9a\u4efb\u52a1"
            if enabled
            else "\u542f\u7528\u56fa\u5b9a\u4efb\u52a1"
        )
        self.enabled_changed.emit(bool(enabled))


class ColorWheelWidget(QWidget):
    """A compact HSV color wheel used by the tag color editor."""

    color_changed = Signal(QColor)

    def __init__(self, color: QColor | str = "#367D69", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        if not self._color.isValid():
            self._color = QColor("#367D69")
        self._wheel_image = QImage()
        self.setMinimumSize(192, 192)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(224, 224)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor | str, *, emit: bool = False) -> None:
        next_color = QColor(color)
        if not next_color.isValid():
            return
        changed = next_color.name().lower() != self._color.name().lower()
        self._color = next_color
        self.update()
        if emit and changed:
            self.color_changed.emit(self.color())

    def _wheel_geometry(self) -> tuple[QPoint, int]:
        side = min(self.width(), self.height())
        radius = max(1, side // 2 - 8)
        return QPoint(self.width() // 2, self.height() // 2), radius

    def _build_wheel(self, side: int) -> QImage:
        image = QImage(side, side, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        center = side / 2.0
        radius = center - 1.0
        for y in range(side):
            dy = y - center
            for x in range(side):
                dx = x - center
                distance = (dx * dx + dy * dy) ** 0.5
                if distance > radius:
                    continue
                saturation = min(1.0, distance / radius)
                hue = (0.5 - math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                image.setPixelColor(x, y, QColor.fromHsvF(hue, saturation, 1.0))
        return image

    def _pick(self, position: QPoint) -> None:
        center, radius = self._wheel_geometry()
        dx = position.x() - center.x()
        dy = position.y() - center.y()
        distance = (dx * dx + dy * dy) ** 0.5
        if distance > radius:
            return
        hue = (0.5 - math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        saturation = min(1.0, distance / radius)
        value = self._color.valueF()
        next_color = QColor.fromHsvF(hue, saturation, value if value > 0 else 1.0)
        if next_color.name().lower() == self._color.name().lower():
            return
        self._color = next_color
        self.update()
        self.color_changed.emit(self.color())

    def resizeEvent(self, event) -> None:  # noqa: N802
        side = min(self.width(), self.height())
        if self._wheel_image.size() != QSize(side, side):
            self._wheel_image = self._build_wheel(side)
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._wheel_image.isNull():
            side = min(self.width(), self.height())
            self._wheel_image = self._build_wheel(side)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        origin = QPoint((self.width() - side) // 2, (self.height() - side) // 2)
        painter.drawImage(origin, self._wheel_image)
        center, radius = self._wheel_geometry()
        hue = self._color.hsvHueF()
        if hue < 0:
            hue = 0.0
        saturation = self._color.hsvSaturationF()
        angle = (0.5 - hue) * 2 * math.pi
        marker = QPoint(
            round(center.x() + math.cos(angle) * saturation * radius),
            round(center.y() + math.sin(angle) * saturation * radius),
        )
        painter.setPen(QPen(Qt.GlobalColor.white, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(marker, 7, 7)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(marker, 7, 7)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)


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
        *,
        single_line: bool = False,
        tone_map: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._selected = ""
        self._buttons: dict[str, QPushButton] = {}
        self._single_line = bool(single_line)
        self._tone_map = dict(tone_map or {})
        self._compact = False
        # Keep the compact chips comfortably clickable at 100% scaling;
        # their wrapping and the surrounding picker spacing provide the
        # density improvement without collapsing the hit target.
        self._compact_chip_height = 22
        self._sync_height_pending = False
        self._sync_height_timer = QTimer(self)
        self._sync_height_timer.setSingleShot(True)
        self._sync_height_timer.timeout.connect(self._run_scheduled_sync_height)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._layout = FlowLayout(self)
        self._layout.set_wrap(not self._single_line)
        self._layout.setContentsMargins(0, 1, 0, 1)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed if self._single_line else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.setMinimumHeight(36)
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
            self._layout.setSpacing(2 if self._compact else 6)
            self._layout.setContentsMargins(0, 1, 0, 1)
            self.setMinimumHeight(26 if self._compact else 36)
            if self._single_line:
                self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        density_changed = mode_changed
        for button in self._buttons.values():
            should_reapply = mode_changed or (
                self._compact and button.height() != self._compact_chip_height
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
                button.setFixedHeight(self._compact_chip_height)
        if mode_changed or density_changed:
            self._resize_single_line_content()
            self.updateGeometry()
            self._schedule_sync_height()

    def tags(self) -> list[str]:
        return list(self._tags)

    def set_tone_map(self, tone_map: dict[str, str] | None = None) -> None:
        self._tone_map = dict(tone_map or {})
        for tag, button in self._buttons.items():
            button.setProperty("tone", self._tone_for(tag))
            self._apply_chip_density(button)
            button.style().unpolish(button)
            button.style().polish(button)

    def _tone_for(self, tag: str) -> str:
        return self._tone_map.get(tag, tag_tone(tag))

    def _palette_for(self, tag: str) -> tuple[str, str, str, str]:
        return tag_palette_for_tag(tag, self._tone_map)

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
            button.setProperty("tone", self._tone_for(tag))
            button.setProperty("compact", "true" if self._compact else "false")
            self._apply_chip_density(button)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"\u9009\u62e9\u6807\u7b7e\uff1a{tag}")
            button.clicked.connect(lambda _checked, value=tag: self._select(value, emit=True))
            self._button_group.addButton(button)
            self._layout.addWidget(button)
            self._buttons[tag] = button
        self._select(target, emit=False)
        self._resize_single_line_content()
        self.updateGeometry()
        self._schedule_sync_height()

    def _resize_single_line_content(self) -> None:
        """Keep the scrollable single-line content as wide as its chips."""

        if not self._single_line:
            return
        content = self._layout.minimumSize()
        self.setMinimumWidth(max(1, content.width()))
        self.resize(
            max(1, content.width()),
            max(self.minimumHeight(), content.height()),
        )

    def _apply_chip_density(self, button: QPushButton) -> None:
        accent, _foreground, background, border = self._palette_for(button.text())
        if self._compact:
            compact_font = button.font()
            compact_font.setPointSize(9)
            button.setFont(compact_font)
            # The parent card stylesheet supplies the normal chip rules.  A
            # widget-local rule is used for the compact home variant because
            # Qt does not reliably re-polish dynamic property selectors on a
            # Python-defined QWidget after the parent stylesheet is applied.
            button.setStyleSheet(
                "QPushButton#tagChip {"
                f"min-height:18px; max-height:{self._compact_chip_height}px; padding:0px 5px; "
                f"border-radius:11px; font-size:9px; color:{accent}; "
                f"background:{background}; border:1px solid {border};"
                f"}} QPushButton#tagChip:checked {{ border:1px solid {accent}; }}"
            )
            button.setFixedHeight(self._compact_chip_height)
        else:
            button.setStyleSheet(
                "QPushButton#tagChip {"
                f"color:{accent}; background:{background}; border:1px solid {border};"
                "padding:3px 8px; border-radius:12px; font-size:10px; font-weight:650;"
                f"}} QPushButton#tagChip:checked {{ border:2px solid {accent}; padding:2px 7px; }}"
            )
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
        self._sync_height_timer.start(0)

    def _run_scheduled_sync_height(self) -> None:
        self._sync_height_pending = False
        # Button polish can finish after set_tags() returns.  Recalculate the
        # natural width on the queued pass so a scroll area never keeps the
        # stale width from the briefly empty layout.
        self._resize_single_line_content()
        self.sync_height()

    def sync_height(self) -> None:
        if self.width() <= 0:
            return
        required = max(26 if self._compact else 36, self._layout.heightForWidth(self.width()))
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
