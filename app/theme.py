"""
Theme system. Generates QSS and applies it to individual windows.

apply_theme(window, "dark")  → clears window stylesheet (falls back to app-level dark)
apply_theme(window, "light") → injects the light (white) QSS onto the window

Both themes are Breakaway-brand-accurate: red on white is the default combo;
white on carbon for the dark reverse. Tokens mirror app/brand.py.
"""

from pathlib import Path


def _dark_qss() -> str:
    path = Path(__file__).parent.parent / "assets" / "styles.qss"
    return path.read_text() if path.exists() else ""


def _light_qss() -> str:
    return """
    /* Breakaway 3937 — light (white) theme. Red on white is the brand default;
       Carbon carries body copy (never red text for long copy). */

    QWidget {
        background-color: #FAF9F8;      /* N50 */
        color: #181416;                 /* Carbon */
        font-family: "Roboto";
        font-size: 13px;
    }
    QMainWindow { background-color: #FAF9F8; }

    QLabel { background: transparent; }

    QToolTip {
        background-color: #FFFFFF; color: #181416;
        border: 1px solid #E5E2E1; padding: 4px 8px;
    }

    QFrame#panel {
        background-color: #FFFFFF;
        border: 1px solid #E5E2E1;      /* N200 */
        border-radius: 12px;
    }
    QFrame#panel_accent {
        background-color: #FFFFFF;
        border: 1px solid #C82027;
        border-radius: 12px;
    }

    QLabel#section_header {
        color: #C82027;
        font-family: "Chakra Petch";
        font-size: 12px; font-weight: 600; letter-spacing: 2px;
    }
    QLabel#screen_title {
        color: #181416; font-family: "Chakra Petch";
        font-size: 22px; font-weight: 700;
    }
    QLabel#match_label {
        color: #181416; font-family: "Chakra Petch";
        font-size: 36px; font-weight: 700;
    }
    QLabel#rank_number  { color: #C82027; font-family: "Chakra Petch"; font-size: 72px; font-weight: 700; }
    QLabel#team_number  { color: #C82027; font-family: "Chakra Petch"; font-size: 18px; font-weight: 700; }
    QLabel#stat_value   { color: #181416; font-family: "Chakra Petch"; font-size: 20px; font-weight: 600; }
    QLabel#stat_label   { color: #6A6462; font-size: 12px; }
    QLabel#status_ok    { color: #2E8B7F; font-weight: 600; }
    QLabel#status_warn  { color: #E08A1E; font-weight: 600; }
    QLabel#status_error { color: #C82027; font-weight: 600; }

    QTableWidget {
        background-color: #FFFFFF; border: none;
        gridline-color: #E5E2E1;
        selection-background-color: #C82027; selection-color: #FFFFFF;
    }
    QTableWidget::item { padding: 6px 10px; border: none; }
    QTableWidget::item:alternate { background-color: #F3F1F0; }
    QHeaderView::section {
        background-color: #F3F1F0; color: #6A6462;
        font-family: "Chakra Petch"; font-size: 11px; font-weight: 600;
        letter-spacing: 1px; padding: 8px 10px;
        border: none; border-bottom: 1px solid #E5E2E1;
    }

    QPushButton {
        background-color: #F3F1F0; color: #181416;
        border: 1px solid #E5E2E1; border-radius: 8px;
        padding: 8px 16px; font-weight: 500;
    }
    QPushButton:hover   { background-color: #E5E2E1; }
    QPushButton:pressed { background-color: #CFCBC9; }
    QPushButton:disabled { color: #A6A19E; }

    QPushButton#btn_primary {
        background-color: #C82027; color: #FFFFFF; border: none;
        font-family: "Chakra Petch"; font-weight: 600;
    }
    QPushButton#btn_primary:hover   { background-color: #B01C22; }
    QPushButton#btn_primary:pressed { background-color: #8E1519; }

    QPushButton#btn_secondary {
        background-color: transparent; color: #C82027;
        border: 1.5px solid #C82027; font-weight: 600;
    }
    QPushButton#btn_secondary:hover { background-color: #C82027; color: #FFFFFF; }

    QPushButton#btn_success {
        background-color: transparent; color: #2E8B7F;
        border: 1.5px solid #2E8B7F; font-weight: 600;
    }
    QPushButton#btn_success:hover { background-color: #2E8B7F; color: #FFFFFF; }

    QPushButton#btn_danger {
        background-color: transparent; color: #C82027;
        border: 1px solid #E5E2E1; font-weight: 600;
    }
    QPushButton#btn_danger:hover { background-color: #C82027; color: #FFFFFF; border-color: #C82027; }

    QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #E5E2E1; }

    QScrollBar:vertical { background: #FAF9F8; width: 8px; border: none; }
    QScrollBar::handle:vertical { background: #CFCBC9; border-radius: 4px; min-height: 20px; }
    QScrollBar::handle:vertical:hover { background: #A6A19E; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

    QComboBox {
        background-color: #FFFFFF; border: 1px solid #CFCBC9;
        border-radius: 8px; padding: 7px 12px; color: #181416;
        font-family: "Chakra Petch"; font-weight: 500;
    }
    QComboBox:hover { border-color: #A6A19E; }
    QComboBox::drop-down { border: none; width: 22px; }
    QComboBox QAbstractItemView {
        background-color: #FFFFFF; border: 1px solid #E5E2E1;
        selection-background-color: #C82027; selection-color: #FFFFFF; outline: none;
    }

    QTabWidget::pane { border: 1px solid #E5E2E1; background-color: #FFFFFF; }
    QTabBar::tab {
        background-color: #F3F1F0; color: #6A6462;
        font-family: "Chakra Petch"; font-weight: 500;
        padding: 10px 20px; border: none; border-bottom: 2px solid transparent;
    }
    QTabBar::tab:selected { color: #181416; border-bottom: 2px solid #C82027; }
    QTabBar::tab:hover    { color: #181416; background-color: #E5E2E1; }

    QProgressBar {
        background-color: #E5E2E1; border: none; border-radius: 3px;
        height: 6px; color: transparent;
    }
    QProgressBar::chunk { background-color: #C82027; border-radius: 3px; }

    QLineEdit, QTextEdit {
        background-color: #FFFFFF; border: 1px solid #CFCBC9;
        border-radius: 8px; padding: 7px 11px; color: #181416;
        selection-background-color: #C82027; selection-color: #FFFFFF;
    }
    QLineEdit:focus, QTextEdit:focus { border-color: #C82027; }

    QCheckBox { spacing: 8px; }
    QCheckBox::indicator {
        width: 16px; height: 16px; border: 1px solid #CFCBC9;
        border-radius: 4px; background: #FFFFFF;
    }
    QCheckBox::indicator:checked { background: #C82027; border-color: #C82027; }

    QDialog, QMessageBox { background-color: #FFFFFF; }
    """


def apply_theme(window, theme: str) -> None:
    """Apply 'dark' or 'light' stylesheet to a single window."""
    if theme == "light":
        window.setStyleSheet(_light_qss())
    else:
        window.setStyleSheet("")  # clears override; app-level dark stylesheet takes over
