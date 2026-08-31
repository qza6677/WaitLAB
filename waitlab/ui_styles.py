"""Qt stylesheets for the main window and dialogs.

Styles live outside the window coordinator so visual changes do not require
loading business logic or database code.
"""

from __future__ import annotations

from .ui_primitives import COLORS, tag_chip_stylesheet


def window_stylesheet() -> str:
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
    {tag_chip_stylesheet()}
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
        padding: 2px 7px; font-size: 10px; font-weight: 650;
    }}
    QScrollArea#quickTaskTagScroll {{ background: transparent; border: none; }}
    QScrollArea#quickTaskTagScroll QScrollBar:horizontal {{
        height: 8px; background: #F1ECE4; border: none; border-radius: 4px;
        margin: 0 2px;
    }}
    QScrollArea#quickTaskTagScroll QScrollBar::handle:horizontal {{
        min-width: 28px; background: #B7DCCC; border-radius: 4px;
    }}
    QScrollArea#quickTaskTagScroll QScrollBar::add-line:horizontal,
    QScrollArea#quickTaskTagScroll QScrollBar::sub-line:horizontal {{
        width: 0px; background: transparent;
    }}
    QScrollArea#quickTaskTagScroll QScrollBar::add-page:horizontal,
    QScrollArea#quickTaskTagScroll QScrollBar::sub-page:horizontal {{
        background: transparent;
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
        font-weight: 650; padding: 4px 9px;
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
    QPushButton#taskButton, QPushButton#pausedTaskButton {{
        text-align: left; min-height: 32px; max-height: 32px; padding: 4px 9px;
        background: {COLORS['white']}; font-size: 11px;
    }}
    QPushButton#ghostButton {{
        background: transparent; min-height: 28px; max-height: 28px; padding: 3px 8px;
    }}
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


def dialog_stylesheet() -> str:
    return f"""
    QDialog {{ background: {COLORS['cream']}; }}
    QScrollArea#taskManagerScroll {{ background: transparent; border: none; }}
    QWidget#taskManagerPage {{ background: transparent; }}
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI'; }}
    {tag_chip_stylesheet()}
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
    QPushButton#historyClearButton {{
        color: #A5533D; background: transparent; border-color: #EAB89E;
        padding: 6px 10px; font-weight: 650;
    }}
    QPushButton#historyClearButton:hover {{ color: #8F3F2C; background: #FFE9DE; }}
    """
