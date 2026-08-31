"""Reusable Qt widgets for the desktop pet and task pickers.

These widgets are deliberately presentation-only. They emit user gestures and
render state supplied by their owners, but never call application services or
Storage directly.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QSize,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from .cookie import CookieAssets, CookieState, coerce_cookie_state
from .models import DEFAULT_TAG
from .ui_primitives import COLORS, tag_tone, tag_tone_colors


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
    ) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._selected = ""
        self._buttons: dict[str, QPushButton] = {}
        self._single_line = bool(single_line)
        self._compact = False
        # Keep the compact chips comfortably clickable at 100% scaling;
        # their wrapping and the surrounding picker spacing provide the
        # density improvement without collapsing the hit target.
        self._compact_chip_height = 22
        self._sync_height_pending = False
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
                f"min-height:18px; max-height:{self._compact_chip_height}px; padding:0px 5px; "
                f"border-radius:11px; font-size:9px; color:{foreground}; "
                f"background:{background}; border:1px solid {background};"
                "} QPushButton#tagChip:checked { border:1px solid #203B3A; }"
            )
            button.setFixedHeight(self._compact_chip_height)
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



