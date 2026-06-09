"""
Theme system. Generates QSS and applies it to individual windows.

apply_theme(window, "dark")  → clears window stylesheet (falls back to app-level dark)
apply_theme(window, "light") → injects light QSS onto the window
"""

from pathlib import Path


def _dark_qss() -> str:
    path = Path(__file__).parent.parent / "assets" / "styles.qss"
    return path.read_text() if path.exists() else ""


def _light_qss() -> str:
    return """
    QWidget {
        background-color: #f2f2f7;
        color: #1c1c1e;
        font-family: "Roboto";
        font-size: 13px;
    }

    QMainWindow { background-color: #f2f2f7; }

    QFrame#panel {
        background-color: #ffffff;
        border: 1px solid #d1d1d6;
        border-radius: 6px;
    }

    QFrame#panel_accent {
        background-color: #ffffff;
        border: 1px solid #aeaeb2;
        border-radius: 6px;
    }

    QLabel#section_header {
        color: #636366;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.5px;
    }

    QLabel#screen_title {
        color: #1c1c1e;
        font-size: 22px;
        font-weight: 700;
    }

    QLabel#match_label {
        color: #1c1c1e;
        font-size: 36px;
        font-weight: 800;
    }

    QLabel#score_red   { color: #d70015; font-size: 48px; font-weight: 900; }
    QLabel#score_blue  { color: #0040dd; font-size: 48px; font-weight: 900; }
    QLabel#rank_number { color: #9c6e00; font-size: 72px; font-weight: 900; }
    QLabel#team_number { color: #1c1c1e; font-size: 18px; font-weight: 700; }
    QLabel#stat_value  { color: #1c1c1e; font-size: 20px; font-weight: 600; }
    QLabel#stat_label  { color: #8e8e93; font-size: 11px; }
    QLabel#status_ok   { color: #1a7f37; font-weight: 600; }
    QLabel#status_warn { color: #9a6700; font-weight: 600; }
    QLabel#status_error{ color: #d70015; font-weight: 600; }

    QTableWidget {
        background-color: #ffffff;
        border: none;
        gridline-color: #e5e5ea;
        selection-background-color: #007aff;
        selection-color: #ffffff;
    }

    QTableWidget::item { padding: 6px 10px; border: none; }
    QTableWidget::item:alternate { background-color: #f9f9fb; }

    QHeaderView::section {
        background-color: #f2f2f7;
        color: #636366;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1px;
        padding: 8px 10px;
        border: none;
        border-bottom: 1px solid #d1d1d6;
    }

    QPushButton {
        background-color: #e5e5ea;
        color: #1c1c1e;
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: 500;
    }
    QPushButton:hover  { background-color: #d1d1d6; }
    QPushButton:pressed{ background-color: #c7c7cc; }

    QPushButton#btn_primary {
        background-color: #007aff;
        color: #ffffff;
        border: none;
        font-weight: 700;
    }
    QPushButton#btn_primary:hover  { background-color: #0071eb; }
    QPushButton#btn_primary:pressed{ background-color: #0062cc; }

    QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #d1d1d6; }

    QScrollBar:vertical { background: #f2f2f7; width: 8px; border: none; }
    QScrollBar::handle:vertical {
        background: #c7c7cc; border-radius: 4px; min-height: 20px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

    QComboBox {
        background-color: #ffffff;
        border: 1px solid #d1d1d6;
        border-radius: 4px;
        padding: 6px 10px;
        color: #1c1c1e;
    }
    QComboBox QAbstractItemView {
        background-color: #ffffff;
        border: 1px solid #d1d1d6;
        selection-background-color: #007aff;
        selection-color: #ffffff;
    }

    QTabWidget::pane { border: 1px solid #d1d1d6; background-color: #ffffff; }
    QTabBar::tab {
        background-color: #f2f2f7;
        color: #8e8e93;
        padding: 10px 20px;
        border: none;
        border-bottom: 2px solid transparent;
    }
    QTabBar::tab:selected { color: #1c1c1e; border-bottom: 2px solid #007aff; }
    QTabBar::tab:hover    { color: #3a3a3c; background-color: #e5e5ea; }

    QProgressBar {
        background-color: #e5e5ea;
        border: none;
        border-radius: 3px;
        height: 6px;
        color: transparent;
    }
    QProgressBar::chunk { background-color: #007aff; border-radius: 3px; }

    QLineEdit {
        background-color: #ffffff;
        border: 1px solid #d1d1d6;
        border-radius: 4px;
        padding: 6px 10px;
        color: #1c1c1e;
    }
    QLineEdit:focus { border-color: #007aff; }
    """


def apply_theme(window, theme: str) -> None:
    """Apply 'dark' or 'light' stylesheet to a single window."""
    if theme == "light":
        window.setStyleSheet(_light_qss())
    else:
        window.setStyleSheet("")  # clears override; app-level dark stylesheet takes over
