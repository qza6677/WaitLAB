"""Small, dependency-light UI primitives shared by the desktop views.

This module intentionally contains no application state or storage access.  It
is kept separate from :mod:`waitlab.ui` so dialogs and the main window can
reuse presentation helpers without importing the whole window coordinator.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from .cookie import CookieAssets, CookieState
from .models import DEFAULT_TAG


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
    "\u5199\u4f5c": "purple",
    "\u8bba\u6587\u5199\u4f5c": "purple",
    "\u9605\u8bfb": "blue",
    "\u6587\u732e\u9605\u8bfb": "blue",
    "\u7f16\u7801": "teal",
    "Vibe coding": "teal",
    "\u6574\u7406": "yellow",
    "\u5de5\u4f5c/\u9879\u76ee": "orange",
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


def tag_chip_stylesheet() -> str:
    """Return the shared Qt stylesheet for tag chips."""

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
    QWidget#quickTaskTag {{ min-height: 26px; }}
    QPushButton#tagChip[compact="true"] {{
        min-height: 22px; max-height: 24px; padding: 2px 7px;
        border-radius: 11px; font-size: 9px;
    }}
    QPushButton#tagChip[compact="true"]:checked {{
        border-width: 1px; padding: 2px 7px;
    }}
    """


def format_duration(seconds: float) -> str:
    """Format elapsed seconds as a compact timer label."""

    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def chart_duration(seconds: float) -> str:
    """Format a compact chart axis/tooltip duration."""

    value = max(0.0, float(seconds))
    if value >= 3600:
        return f"{value / 3600:.1f} \u5c0f\u65f6"
    if value >= 60:
        return f"{value / 60:.0f} \u5206\u949f"
    return f"{value:.0f} \u79d2"


def chart_color(tag: str, *, soft: bool = False) -> QColor:
    """Return the foreground or soft background color for a tag."""

    foreground, background = tag_tone_colors(tag_tone(tag))
    return QColor(background if soft else foreground)


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


class PresentationMode(StrEnum):
    """Top-level visual state used by the desktop pet window."""

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
    """Choose the visible pet layout from independent UI state flags."""

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
