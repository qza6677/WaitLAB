"""Interactive statistics charts used by the desktop dialogs.

The chart widgets only consume view data and presentation helpers. They do not
read from Storage or mutate application state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .models import TagTimeBucket
from .ui_primitives import COLORS, chart_color, chart_duration, format_duration


@dataclass(frozen=True, slots=True)
class StackedChartSegment:
    """The painted area and data represented by one stacked chart segment."""

    rect: QRectF
    date: datetime
    tag: str
    seconds: float
    daily_total_seconds: float


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
        self.setAccessibleName("\u4eca\u65e5\u6807\u7b7e\u65f6\u95f4\u73af\u72b6\u56fe")
        self.setAccessibleDescription("\u5c55\u793a\u4eca\u5929\u5404\u6807\u7b7e Waiting Task \u7684\u4e13\u6ce8\u65f6\u95f4\u5206\u5e03")

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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "\u4eca\u5929\u6682\u65e0\u7b49\u5f85\u65f6\u95f4")
            painter.end()
            return

        rect = self._chart_rect()
        start = 90.0
        painter.setPen(Qt.PenStyle.NoPen)
        for tag, seconds in self._values.items():
            span = -360 * seconds / self._total
            color = chart_color(tag)
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
                f"{tag}\n{chart_duration(seconds)} \u00b7 {percentage:.1f}%"
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
        self._segments: list[StackedChartSegment] = []
        self._hovered_index = -1
        self._locked_index = -1
        self._cursor_position: QPointF | None = None
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName("\u6309\u5929\u6807\u7b7e\u65f6\u957f\u5806\u53e0\u67f1\u72b6\u56fe")
        self.setAccessibleDescription("\u5c55\u793a\u672c\u5468\u6216\u672c\u6708\u6bcf\u5929\u5404\u6807\u7b7e\u7684 Waiting Task \u4e13\u6ce8\u65f6\u95f4")

    def sizeHint(self) -> QSize:
        return QSize(680, 290)

    def set_data(self, period: str, buckets: list[TagTimeBucket]) -> None:
        self._period = period
        self._buckets = list(buckets)
        self._segments = []
        self._hovered_index = -1
        self._locked_index = -1
        self._cursor_position = None
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

    def _segment_at(self, point: QPointF) -> int:
        """Return the painted segment under ``point`` or ``-1``."""

        for index in range(len(self._segments) - 1, -1, -1):
            if self._segments[index].rect.contains(point):
                return index
        return -1

    def _tooltip_for(self, index: int) -> str:
        if index < 0 or index >= len(self._segments):
            return ""
        segment = self._segments[index]
        date_value = segment.date.astimezone() if segment.date.tzinfo else segment.date
        date_label = date_value.strftime("%Y-%m-%d")
        daily_percentage = (
            segment.seconds / segment.daily_total_seconds * 100
            if segment.daily_total_seconds > 0
            else 0.0
        )
        return "\n".join(
            (
                f"\u65e5\u671f\uff1a{date_label}",
                f"\u6807\u7b7e\uff1a{segment.tag}",
                f"\u65f6\u957f\uff1a{chart_duration(segment.seconds)}",
                f"\u5f53\u65e5\u5360\u6bd4\uff1a{daily_percentage:.1f}%",
            )
        )

    def _date_label_indices(self, step: float, count: int) -> list[int]:
        """Choose readable date labels without crowding narrow charts."""

        if count <= 0:
            return []
        if self._period == "week":
            preferred = list(range(count))
        else:
            stride = max(1, math.ceil(44.0 / max(step, 1.0)))
            preferred = list(range(0, count, stride))
            if count - 1 not in preferred:
                preferred.append(count - 1)

        selected: list[int] = []
        last_center: float | None = None
        for index in preferred:
            center = index * step + step / 2
            if last_center is None or center - last_center >= 40.0:
                selected.append(index)
                last_center = center
        return selected

    def _draw_detail(self, painter: QPainter, index: int) -> None:
        lines = self._tooltip_for(index).splitlines()
        font = QFont(self.font())
        font.setPointSize(max(9, font.pointSize()))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        padding_x = 10
        padding_y = 8
        line_height = metrics.height()
        box_width = max(metrics.horizontalAdvance(line) for line in lines) + padding_x * 2
        box_height = line_height * len(lines) + padding_y * 2

        plot = self._plot_rect()
        if self._cursor_position is None:
            x = plot.right() - box_width - 6
            y = plot.top() + 8
        else:
            x = self._cursor_position.x() + 12
            y = self._cursor_position.y() + 12
            if x + box_width > self.width() - 4:
                x = self._cursor_position.x() - box_width - 12
            if y + box_height > self.height() - 4:
                y = self._cursor_position.y() - box_height - 12
        x = max(4.0, min(x, self.width() - box_width - 4.0))
        y = max(4.0, min(y, self.height() - box_height - 4.0))

        painter.save()
        painter.setPen(QPen(QColor(COLORS["line"]), 1))
        painter.setBrush(QColor(COLORS["cream"]))
        painter.drawRoundedRect(QRectF(x, y, box_width, box_height), 8, 8)
        painter.setPen(QColor(COLORS["ink"]))
        for line_index, line in enumerate(lines):
            painter.drawText(
                QRectF(
                    x + padding_x,
                    y + padding_y + line_index * line_height,
                    box_width - padding_x * 2,
                    line_height,
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
        painter.restore()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._segments = []
        plot = self._plot_rect()
        totals = [self._bucket_total(bucket) for bucket in self._buckets]
        maximum = max(totals, default=0.0)
        if not self._buckets or maximum <= 0:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "\u6240\u9009\u5468\u671f\u6682\u65e0\u7b49\u5f85\u65f6\u95f4",
            )
            painter.end()
            return

        painter.setFont(QFont(self.font()))
        tick_count = 4
        for tick in range(tick_count + 1):
            value = maximum * tick / tick_count
            y = plot.bottom() - plot.height() * tick / tick_count
            painter.setPen(QPen(QColor(COLORS["line"]), 1))
            painter.drawLine(
                QPointF(plot.left(), y),
                QPointF(plot.right(), y),
            )
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(
                QRectF(0, y - 9, plot.left() - 8, 18),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                chart_duration(value),
            )

        tags = self._tags()
        step = plot.width() / len(self._buckets)
        bar_width = max(4.0, min(36.0, step * 0.72))
        for index, bucket in enumerate(self._buckets):
            x = plot.left() + index * step + (step - bar_width) / 2
            bottom = plot.bottom()
            daily_total = totals[index]
            for tag in tags:
                seconds = bucket.tag_seconds.get(tag, 0.0)
                if seconds <= 0:
                    continue
                height = plot.height() * seconds / maximum
                rect = QRectF(x, bottom - height, bar_width, height)
                self._segments.append(
                    StackedChartSegment(
                        rect=rect,
                        date=bucket.start,
                        tag=tag,
                        seconds=seconds,
                        daily_total_seconds=daily_total,
                    )
                )
                segment_index = len(self._segments) - 1
                painter.setPen(Qt.PenStyle.NoPen)
                color = chart_color(tag)
                is_active = segment_index == self._locked_index or (
                    self._locked_index < 0 and segment_index == self._hovered_index
                )
                if is_active:
                    color = color.lighter(118)
                painter.setBrush(color)
                painter.drawRect(rect)
                if is_active:
                    painter.setPen(QPen(QColor(COLORS["ink"]), 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect)
                bottom -= height

            if index in self._date_label_indices(step, len(self._buckets)):
                label = bucket.start.strftime("%m/%d")
                painter.setPen(QColor(COLORS["muted"]))
                painter.drawText(
                    QRectF(x - 14, plot.bottom() + 7, bar_width + 28, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
        active_index = (
            self._locked_index
            if self._locked_index >= 0
            else self._hovered_index
        )
        if 0 <= active_index < len(self._segments):
            self._draw_detail(painter, active_index)
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._cursor_position = event.position()
        index = self._segment_at(event.position())
        changed = index != self._hovered_index
        self._hovered_index = index
        active_index = self._locked_index if self._locked_index >= 0 else index
        self.setToolTip(self._tooltip_for(active_index))
        if changed or active_index >= 0:
            self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton:
            index = self._segment_at(event.position())
            self._cursor_position = event.position()
            if self._locked_index >= 0:
                if index < 0 or index == self._locked_index:
                    # A second click on the locked segment, or any blank
                    # chart area, releases the pinned detail.
                    self._locked_index = -1
                    self._hovered_index = -1
                    self.setToolTip("")
                else:
                    self._locked_index = index
                    self._hovered_index = index
                    self.setToolTip(self._tooltip_for(index))
            elif index >= 0:
                self._locked_index = index
                self._hovered_index = index
                self.setToolTip(self._tooltip_for(index))
            else:
                self._hovered_index = -1
                self.setToolTip("")
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802
        self._cursor_position = None
        if self._locked_index < 0:
            self._hovered_index = -1
            self.setToolTip("")
        else:
            self.setToolTip(self._tooltip_for(self._locked_index))
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]


