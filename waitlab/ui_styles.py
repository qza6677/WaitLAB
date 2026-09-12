"""Qt stylesheets for the main window and dialogs.

Styles live outside the window coordinator so visual changes do not require
loading business logic or database code.
"""

from __future__ import annotations

from .ui_primitives import COLORS, tag_chip_stylesheet


def window_stylesheet() -> str:
    return f"""
    QWidget {{
        color: {COLORS['ink']};
        font-family: 'Microsoft YaHei UI', 'Segoe UI';
    }}
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
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI', 'Segoe UI'; }}
    {tag_chip_stylesheet()}
    QLabel#stateTitle {{ font-size: 17px; font-weight: 700; }}
    QLabel#muted {{ color: {COLORS['muted']}; font-size: 12px; }}
    QLabel#sectionTitle {{ font-size: 14px; font-weight: 700; }}
    QLabel#eyebrow {{ color: {COLORS['mint_dark']}; font-size: 10px; font-weight: 700; }}
    QLabel#focusTitle {{ font-size: 15px; font-weight: 600; }}
    QLabel#timerLarge {{ color: {COLORS['ink']}; font-family: 'Cascadia Mono', 'Consolas'; font-size: 30px; font-weight: 650; }}
    QLabel#timerSmall {{ font-family: 'Cascadia Mono'; font-size: 16px; font-weight: 700; }}
    QLabel#timerCompact {{ color: {COLORS['muted']}; font-family: 'Cascadia Mono'; font-size: 13px; font-weight: 700; }}
    QLabel#compactTimer {{
        color: {COLORS['mint_dark']}; font-family: 'Cascadia Mono'; font-size: 17px;
        font-weight: 700; padding: 6px 12px;
    }}
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
    QFrame#aiCard {{ background: #EAF4EE; border-radius: 10px; }}
    QFrame#aiCard[attention="true"] {{ background: #FFF4D8; border: 1px solid #D5B65F; }}
    QFrame#focusCard {{
        background: transparent; border: none; border-radius: 0;
    }}
    QFrame#bubbleCard {{
        background: {COLORS['cream']}; border: 1px solid {COLORS['line']}; border-radius: 16px;
    }}
    QWidget#chromeWidget {{
        background: transparent; border-bottom: 1px solid {COLORS['line']};
        border-top-left-radius: 16px; border-top-right-radius: 16px;
    }}
    QLabel#chromeBrand {{ font-size: 14px; font-weight: 700; color: {COLORS['ink']}; }}
    QPushButton#chromeButton, QToolButton#chromeButton {{
        color: {COLORS['muted']}; background: transparent; border: none;
        border-radius: 7px; min-height: 28px; max-height: 28px; padding: 3px 7px;
        font-size: 11px;
    }}
    QPushButton#chromeButton:hover, QToolButton#chromeButton:hover {{
        color: {COLORS['ink']}; background: #EAF4EE;
    }}
    QPushButton#chromeButton:pressed, QToolButton#chromeButton:pressed {{
        background: #DDF1E9;
    }}
    QWidget#footerWidget {{
        background: transparent; border-top: 1px solid {COLORS['line']};
        border-bottom-left-radius: 16px; border-bottom-right-radius: 16px;
    }}
    QLabel#statusDot {{ color: {COLORS['mint_dark']}; font-size: 12px; }}
    QLabel#statusDot[state="attention"] {{ color: #9B5B31; }}
    QLabel#statusDot[state="active"] {{ color: {COLORS['mint_dark']}; }}
    QLabel#statusDot[state="idle"] {{ color: {COLORS['muted']}; }}
    QLabel#footerStatus {{ color: {COLORS['muted']}; font-size: 11px; }}
    QFrame#noticeCard {{
        background: #EAF4EE; border: 1px solid #BFDCC9; border-radius: 10px;
    }}
    QFrame#noticeCard[level="success"] {{ background: #EAF4EE; border-color: #B9DEC9; }}
    QFrame#noticeCard[level="warning"] {{ background: #FFF4D8; border-color: #D5B65F; }}
    QFrame#noticeCard[level="error"] {{ background: #FCECEC; border-color: #D89A9A; }}
    QLabel#noticeTitle {{ font-size: 12px; font-weight: 700; }}
    QLabel#noticeBody {{ color: {COLORS['muted']}; font-size: 12px; }}
    QLabel#validationError {{ color: #A33232; font-size: 12px; padding: 2px 4px; }}
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
    QLabel#completedTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#completedMeta {{ color: {COLORS['muted']}; font-size: 11px; }}
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
    QFrame#quickCompose {{
        background: {COLORS['white']}; border: 1px solid #7F8E86; border-radius: 10px;
    }}
    QFrame#pickerTaskRow {{
        background: transparent; border-bottom: 1px solid {COLORS['line']};
    }}
    QFrame#pickerTaskRow[compact="true"] {{ border-bottom-color: #E5EBE6; }}
    QLabel#pickerTaskTag {{
        color: {COLORS['muted']}; font-size: 11px; padding-left: 4px; padding-bottom: 3px;
    }}
    QLabel#pickerTaskTag[compact="true"] {{ font-size: 10px; padding-left: 4px; padding-bottom: 0px; }}
    QPushButton {{
        color: {COLORS['ink']}; background: {COLORS['white']};
        border: 1px solid #7F8E86; border-radius: 8px;
        padding: 7px 12px; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px;
    }}
    QPushButton:hover {{ border-color: {COLORS['mint_dark']}; background: #F0F8F3; }}
    QPushButton:pressed {{ background: #DDECE3; padding-top: 8px; padding-bottom: 6px; }}
    QPushButton:focus-visible, QToolButton:focus-visible, QLineEdit:focus, QScrollArea:focus {{
        outline: none; border: 2px solid #2F6FB0;
    }}
    QPushButton:disabled {{ color: #87938D; background: #EEF1EE; border-color: #C7D0CA; }}
    QPushButton#primaryButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; font-weight: 600;
    }}
    QPushButton#primaryButton:hover {{ background: #2F6E5D; }}
    QPushButton#secondaryButton {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
        font-weight: 600; padding: 6px 10px;
    }}
    QPushButton#secondaryButton:hover {{ background: #E1F3EA; border-color: {COLORS['mint']}; }}
    QPushButton#playerButton, QPushButton#playerPrimaryButton {{
        min-width: 84px; max-width: 84px; min-height: 44px; max-height: 44px;
        padding: 7px 8px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 10px;
    }}
    QPushButton#playerSwitchButton {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
        min-width: 84px; max-width: 84px; min-height: 44px; max-height: 44px;
        padding: 7px 8px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 11px;
    }}
    QPushButton#playerSwitchButton:disabled {{
        color: {COLORS['muted']}; background: #F5F2ED; border-color: {COLORS['line']};
    }}
    QPushButton#playerPrimaryButton {{
        color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; font-weight: 650;
    }}
    QToolButton#playerMoreButton {{
        color: {COLORS['muted']}; background: transparent; border: none;
        min-height: 16px; max-height: 18px; padding: 0px 4px; border-radius: 6px;
        font-family: 'Microsoft YaHei UI'; font-size: 11px;
    }}
    QToolButton#playerMoreButton:hover, QToolButton#playerMoreButton:pressed {{
        color: {COLORS['mint_dark']}; background: #EDF8F3;
    }}
    QPushButton#playerCloseButton {{
        background: transparent; border: 1px solid {COLORS['line']}; color: {COLORS['muted']};
        min-width: 84px; max-width: 84px; min-height: 44px; max-height: 44px;
        padding: 3px 5px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 10px; line-height: 1.0;
    }}
    QPushButton#playerCloseButton:hover {{ color: #A5533D; background: #FFE9DE; }}
    QPushButton#playerSuspendButton {{
        background: transparent; border: 1px solid {COLORS['line']}; color: {COLORS['muted']};
        min-width: 84px; max-width: 84px; min-height: 44px; max-height: 44px;
        padding: 3px 5px; border-radius: 9px;
        font-family: 'Microsoft YaHei UI'; font-size: 10px; line-height: 1.0;
    }}
    QPushButton#playerSuspendButton:hover {{ color: {COLORS['mint_dark']}; background: #EAF4EE; }}
    QPushButton#taskButton {{
        text-align: left; min-height: 32px; max-height: 32px; padding: 4px 4px;
        color: {COLORS['ink']}; background: transparent; border: none; border-radius: 7px;
        font-size: 12px; font-weight: 500;
    }}
    QPushButton#taskButton:hover {{ background: #EAF4EE; color: {COLORS['mint_dark']}; }}
    QPushButton#taskButton:pressed {{ background: #DDF1E9; }}
    QPushButton#taskButton[compact="true"] {{
        min-height: 30px; max-height: 30px; padding: 0 4px; font-size: 10px;
    }}
    QPushButton#taskButton[cycle="true"] {{
        min-height: 30px; max-height: 30px; padding: 0 5px;
        color: {COLORS['mint_dark']}; font-size: 10px; font-weight: 600;
        background: transparent; border: none; border-radius: 6px;
    }}
    QPushButton#taskButton[cycle="true"]:hover {{
        background: #EAF4EE; color: {COLORS['ink']};
    }}
    QPushButton#taskButton[cycle="true"]:pressed {{ background: #DDF1E9; }}
    QScrollArea#cycleChoicesScroll {{ background: transparent; border: none; }}
    QScrollArea#cycleChoicesScroll QScrollBar:horizontal {{
        height: 5px; margin: 0 4px; background: transparent;
    }}
    QScrollArea#cycleChoicesScroll QScrollBar::handle:horizontal {{
        background: #CBE8DA; border-radius: 2px; min-width: 20px;
    }}
    QPushButton#cycleLinkButton {{
        color: {COLORS['mint_dark']}; background: transparent; border: none;
        padding: 3px 4px; min-height: 28px; max-height: 28px;
        font-size: 10px; font-weight: 600;
    }}
    QPushButton#cycleLinkButton:hover {{
        color: {COLORS['ink']}; background: #EAF4EE; border-radius: 6px;
    }}
    QPushButton#pausedTaskButton {{
        text-align: left; min-height: 32px; max-height: 32px; padding: 4px 9px;
        background: {COLORS['white']}; font-size: 11px;
    }}
    QPushButton#pickerStartButton {{
        color: {COLORS['mint_dark']}; background: {COLORS['white']};
        border: 1px solid #7F8E86; border-radius: 8px; padding: 4px 8px;
        min-height: 30px; max-height: 30px; font-size: 11px;
    }}
    QPushButton#pickerStartButton:hover {{ background: #EAF4EE; border-color: {COLORS['mint_dark']}; }}
    QPushButton#pickerStartButton[compact="true"] {{
        min-width: 34px; max-width: 34px; min-height: 22px; max-height: 22px;
        padding: 0 4px; font-size: 10px;
    }}
    QPushButton#ghostButton {{
        background: transparent; min-height: 28px; max-height: 28px; padding: 3px 8px;
    }}
    QPushButton#connectionStatusButton {{
        background: #F0ECE5; border: none; border-radius: 8px;
        color: {COLORS['muted']}; padding: 4px 8px; font-size: 11px;
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
    QWidget {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI', 'Segoe UI'; }}
    QLabel {{ color: {COLORS['ink']}; font-family: 'Microsoft YaHei UI', 'Segoe UI'; }}
    {tag_chip_stylesheet()}
    QLabel#dialogTitle {{ font-size: 23px; font-weight: 750; }}
    QLabel#taskManagerTitle {{ font-size: 20px; font-weight: 750; }}
    QLabel#muted {{ color: {COLORS['muted']}; font-size: 12px; }}
    QLabel#fallback {{ color: {COLORS['ink']}; background: #EAF4EE; border-radius: 10px; padding: 10px; }}
    QLineEdit, QPlainTextEdit, QListWidget, QComboBox, QSpinBox, QDateEdit {{
        color: {COLORS['ink']}; background: white; border: 1px solid #7F8E86;
        border-radius: 8px; padding: 9px; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{ border: 2px solid #2F6FB0; padding: 8px; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled {{ color: #87938D; background: #EEF1EE; }}
    QPlainTextEdit {{ padding: 7px 9px; }}
    QListWidget#taskRows {{
        background: transparent; border: none; border-radius: 0; padding: 0;
    }}
    QListWidget#taskRows::item {{ padding: 0; border-bottom: 1px solid #F2EEE8; }}
    QCheckBox {{ color: {COLORS['ink']}; spacing: 8px; padding: 3px; }}
    QListWidget::item {{ padding: 9px; border-bottom: 1px solid #F2EEE8; }}
    QListWidget::item:selected {{ color: {COLORS['ink']}; background: #DDF1E9; border-radius: 7px; }}
    QWidget#taskRow, QWidget#fixedTaskRow {{ background: transparent; }}
    QLabel#taskRowTitle {{ color: {COLORS['ink']}; font-size: 14px; font-weight: 600; }}
    QFrame#tagColorSwatch {{ border-radius: 13px; border: 1px solid {COLORS['line']}; }}
    QFrame#tagColorPreview {{ border-radius: 7px; border: 1px solid {COLORS['line']}; }}
    QWidget#taskRowSeparator {{ background: {COLORS['line']}; }}
    QPushButton {{
        color: {COLORS['ink']}; background: white; border: 1px solid #7F8E86;
        border-radius: 8px; padding: 8px 13px; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px;
    }}
    QPushButton:hover {{ border-color: {COLORS['mint_dark']}; background: #F0F8F3; }}
    QPushButton:pressed {{ background: #DDECE3; }}
    QPushButton:focus-visible {{ border: 2px solid #2F6FB0; }}
    QPushButton#taskRowStartButton, QPushButton#taskRowDecisionButton {{
        min-height: 28px; max-height: 28px; padding: 0 7px; font-size: 10px;
    }}
    QPushButton#taskRowStartButton {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
    }}
    QPushButton#taskRowStartButton:hover, QPushButton#taskRowDecisionButton:hover {{
        background: #DDF1E9; border-color: {COLORS['mint']};
    }}
    QToolButton#taskRowMoreButton {{
        color: {COLORS['muted']}; background: transparent; border: 1px solid transparent;
        border-radius: 8px; min-width: 28px; max-width: 28px;
        min-height: 28px; max-height: 28px; padding: 0; font-size: 16px;
    }}
    QToolButton#taskRowMoreButton:hover, QToolButton#taskRowMoreButton:pressed {{
        color: {COLORS['mint_dark']}; background: #EDF8F3; border-color: #CBE8DA;
    }}
    QTabWidget#taskManagerTabs::pane {{ border: none; background: transparent; }}
    QTabBar#taskManagerTabsBar::tab {{
        color: {COLORS['muted']}; background: transparent; border: none;
        border-bottom: 2px solid transparent; padding: 7px 11px; margin-right: 2px;
    }}
    QTabBar#taskManagerTabsBar::tab:selected {{
        color: {COLORS['mint_dark']}; border-bottom-color: {COLORS['mint_dark']}; font-weight: 650;
    }}
    QTabBar#taskManagerTabsBar::tab:hover {{ color: {COLORS['ink']}; background: #F5FCF9; }}
    QPushButton#compactLinkButton {{
        color: {COLORS['mint_dark']}; background: transparent; border: none;
        border-radius: 7px; padding: 4px 6px; font-size: 10px;
    }}
    QPushButton#compactLinkButton:hover {{ background: #E7F4EE; }}
    QMenu {{
        color: {COLORS['ink']}; background: white; border: 1px solid {COLORS['line']};
        border-radius: 8px; padding: 5px;
        font-family: 'Microsoft YaHei UI'; font-size: 11px;
    }}
    QMenu::item {{ padding: 7px 24px 7px 8px; border-radius: 6px; }}
    QMenu::item:selected {{ background: #E7F4EE; color: {COLORS['mint_dark']}; }}
    QFrame#taskTagPopup {{
        color: {COLORS['ink']}; background: {COLORS['white']};
        border: 1px solid #7F8E86; border-radius: 8px;
    }}
    QListWidget#taskTagPopupList {{
        color: {COLORS['ink']}; background: transparent; border: none;
        border-radius: 0; padding: 0; font-size: 11px;
    }}
    QListWidget#taskTagPopupList::item {{ padding: 6px 8px; border-radius: 6px; }}
    QListWidget#taskTagPopupList::item:selected {{
        color: {COLORS['mint_dark']}; background: #E7F4EE;
    }}
    QPushButton#taskTagPopupManage {{
        color: {COLORS['mint_dark']}; background: transparent;
        border: none; border-top: 1px solid {COLORS['line']};
        border-radius: 0; padding: 7px 8px; text-align: left; font-size: 11px;
    }}
    QPushButton#taskTagPopupManage:hover {{ background: #E7F4EE; }}
    QStackedWidget#settingsPages {{ background: transparent; border: none; }}
    QWidget#settingsSidebar {{ background: #F4F0E9; border-radius: 12px; }}
    QPushButton#settingsNavButton {{
        color: {COLORS['ink']}; background: transparent; border: none;
        border-radius: 9px; padding: 9px 10px; min-height: 34px;
        text-align: left; font-size: 11px;
    }}
    QPushButton#settingsNavButton:hover {{ background: #EAF6F0; }}
    QPushButton#settingsNavButton:checked {{
        color: {COLORS['mint_dark']}; background: #DDF1E9; font-weight: 650;
    }}
    QPushButton#primaryButton {{ color: white; background: {COLORS['mint_dark']}; border-color: {COLORS['mint_dark']}; }}
    QPushButton#historyClearButton {{
        color: #A5533D; background: transparent; border-color: #EAB89E;
        padding: 6px 10px; font-weight: 650;
    }}
    QPushButton#historyClearButton:hover {{ color: #8F3F2C; background: #FFE9DE; }}
    """
