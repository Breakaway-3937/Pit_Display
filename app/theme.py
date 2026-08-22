"""
Theme system. Both stylesheets are rendered from one QSS template using the
palette bundles in app/brand.py — change a token there and light/dark stay in
sync automatically.

dark_qss()  → the app-wide default stylesheet (set on QApplication in main)
apply_theme(window, "dark")  → clears window stylesheet (falls back to app-level dark)
apply_theme(window, "light") → injects the light QSS onto the window

Both themes are Breakaway-brand-accurate: red on white is the brand default;
white on carbon for the dark reverse. Carbon (not red) carries body copy.
"""

from app import brand


def _qss(p: dict) -> str:
    """Render the shared QSS template with a brand palette bundle."""
    return f"""
    /* ── Global ─────────────────────────────────────────────────────────── */
    /* No font rule here on purpose: a universal QSS font overrides every
       programmatic setFont() (e.g. the impact board's responsive type).
       The app default font is set via QApplication.setFont() in main(). */
    QWidget {{
        background-color: {p["bg"]};
        color: {p["ink"]};
    }}
    QMainWindow {{ background-color: {p["bg"]}; }}

    /* Labels are transparent so they never paint the page background over a
       card (RoundedFrame etc. use a lighter surface fill). */
    QLabel {{ background: transparent; }}

    QToolTip {{
        background-color: {p["surface"]}; color: {p["ink"]};
        border: 1px solid {p["line"]}; padding: 4px 8px;
    }}

    /* ── Type roles ─────────────────────────────────────────────────────── */
    /* Eyebrow — Chakra Petch, tracked caps, red */
    QLabel#section_header {{
        color: {brand.RED};
        font-family: "{brand.FONT_DISPLAY}";
        font-size: 12px; font-weight: 600; letter-spacing: 2px;
    }}
    QLabel#screen_title {{
        color: {p["title"]};
        font-family: "{brand.FONT_DISPLAY}";
        font-size: 22px; font-weight: 700;
    }}
    QLabel#stat_value {{
        color: {p["ink"]};
        font-family: "{brand.FONT_DISPLAY}";
        font-size: 20px; font-weight: 600;
    }}
    QLabel#stat_label {{ color: {p["muted"]}; font-size: 12px; }}

    /* ── Tables ─────────────────────────────────────────────────────────── */
    QTableWidget {{
        background-color: {p["surface"]}; border: none;
        gridline-color: {p["line"]};
        selection-background-color: {brand.RED}; selection-color: {brand.WHITE};
    }}
    QTableWidget::item {{ padding: 6px 10px; border: none; }}
    QTableWidget::item:alternate {{ background-color: {p["surface2"]}; }}
    QHeaderView::section {{
        background-color: {p["surface2"]}; color: {p["muted"]};
        font-family: "{brand.FONT_DISPLAY}"; font-size: 11px; font-weight: 600;
        letter-spacing: 1px; padding: 8px 10px;
        border: none; border-bottom: 1px solid {p["line"]};
    }}

    /* ── Buttons (brand system §7) ──────────────────────────────────────── */
    QPushButton {{
        background-color: {p["surface2"]}; color: {p["ink"]};
        border: 1px solid {p["line"]}; border-radius: {brand.R_BTN}px;
        padding: 8px 16px; font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p["control_hover"]}; border-color: {p["hover_border"]};
    }}
    QPushButton:pressed  {{ background-color: {p["control_pressed"]}; }}
    QPushButton:disabled {{ color: {p["faint"]}; }}

    QPushButton#btn_primary {{
        background-color: {brand.RED}; color: {brand.WHITE}; border: none;
        font-family: "{brand.FONT_DISPLAY}"; font-weight: 600;
    }}
    QPushButton#btn_primary:hover   {{ background-color: {brand.RED_HOVER}; }}
    QPushButton#btn_primary:pressed {{ background-color: {brand.EMBER}; }}

    QPushButton#btn_danger {{
        background-color: transparent; color: {brand.RED};
        border: 1px solid {p["line"]}; font-weight: 600;
    }}
    QPushButton#btn_danger:hover {{
        background-color: {brand.RED}; color: {brand.WHITE};
        border-color: {brand.RED};
    }}

    /* ── Separators ─────────────────────────────────────────────────────── */
    QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {p["line"]}; }}

    /* ── ScrollBars ─────────────────────────────────────────────────────── */
    QScrollBar:vertical {{ background: {p["bg"]}; width: 8px; border: none; }}
    QScrollBar::handle:vertical {{
        background: {p["scroll_handle"]}; border-radius: 4px; min-height: 20px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p["scroll_handle_hover"]}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: {p["bg"]}; height: 8px; border: none; }}
    QScrollBar::handle:horizontal {{
        background: {p["scroll_handle"]}; border-radius: 4px; min-width: 20px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ── ComboBox ───────────────────────────────────────────────────────── */
    QComboBox {{
        background-color: {p["input_bg"]}; border: 1px solid {p["input_border"]};
        border-radius: {brand.R_BTN}px; padding: 7px 12px; color: {p["ink"]};
        font-family: "{brand.FONT_DISPLAY}"; font-weight: 500;
    }}
    QComboBox:hover {{ border-color: {p["hover_border"]}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background-color: {p["surface"]}; border: 1px solid {p["line"]};
        selection-background-color: {brand.RED}; selection-color: {brand.WHITE};
        outline: none;
    }}

    /* ── Inputs ─────────────────────────────────────────────────────────── */
    QLineEdit, QTextEdit {{
        background-color: {p["input_bg"]}; border: 1px solid {p["input_border"]};
        border-radius: {brand.R_BTN}px; padding: 7px 11px; color: {p["ink"]};
        selection-background-color: {brand.RED}; selection-color: {brand.WHITE};
    }}
    QLineEdit:focus, QTextEdit:focus {{ border-color: {brand.RED}; }}

    /* In-cell editors reuse the QLineEdit rule above, but a table row is only
       ~30px tall — 7px of vertical padding plus a 10px radius leaves almost no
       room, so the text you are typing gets clipped even though it renders
       correctly once committed. Give editors inside item views their own tight
       geometry. */
    QAbstractItemView QLineEdit,
    QAbstractItemView QComboBox,
    QAbstractItemView QSpinBox {{
        padding: 0px 5px;
        border-radius: 3px;
        border: 1px solid {brand.RED};
        margin: 0px;
        min-height: 0px;
    }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border: 1px solid {p["input_border"]};
        border-radius: 4px; background: {p["input_bg"]};
    }}
    QCheckBox::indicator:checked {{
        background: {brand.RED}; border-color: {brand.RED};
    }}

    /* ── Sliders (LED brightness/speed, volume, EQ bands) ───────────────── */
    QSlider::groove:horizontal {{
        height: 4px; background: {p["line"]}; border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {brand.RED}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {p["surface"]}; border: 2px solid {brand.RED};
        width: 13px; height: 13px; margin: -6px 0; border-radius: 8px;
    }}
    QSlider::handle:horizontal:hover {{ background: {brand.RED}; }}

    QSlider::groove:vertical {{
        width: 4px; background: {p["line"]}; border-radius: 2px;
    }}
    /* Vertical sliders run bottom-up, so add-page is the filled half. EQ bands
       are bipolar (cut and boost), so the fill stays neutral — red here would
       imply "more is better", which is wrong for a cut. */
    QSlider::add-page:vertical {{
        background: {p["muted"]}; border-radius: 2px;
    }}
    QSlider::handle:vertical {{
        background: {p["surface"]}; border: 2px solid {brand.RED};
        width: 13px; height: 13px; margin: 0 -6px; border-radius: 8px;
    }}
    QSlider::handle:vertical:hover {{ background: {brand.RED}; }}
    QSlider:disabled::handle:horizontal, QSlider:disabled::handle:vertical {{
        border-color: {p["faint"]};
    }}

    /* ── Lists (music library + queue) ───────────────────────────────────── */
    QListWidget {{
        background-color: {p["surface"]}; border: 1px solid {p["line"]};
        border-radius: {brand.R_BTN}px; outline: none; padding: 4px;
    }}
    QListWidget::item {{
        padding: 7px 9px; border-radius: 6px; color: {p["ink"]};
    }}
    QListWidget::item:hover {{ background: {p["surface2"]}; }}
    QListWidget::item:selected {{
        background: {brand.RED}; color: {brand.WHITE};
    }}

    QProgressBar {{
        background: {p["line"]}; border: none; border-radius: 2px;
        height: 4px; text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {brand.RED}; border-radius: 2px; }}

    /* ── Dialogs ────────────────────────────────────────────────────────── */
    QDialog, QMessageBox {{ background-color: {p["surface"]}; }}
    """


def dark_qss() -> str:
    return _qss(brand.DARK)


def light_qss() -> str:
    return _qss(brand.LIGHT)


def apply_theme(window, theme: str) -> None:
    """Apply 'dark' or 'light' stylesheet to a single window."""
    if theme == "light":
        window.setStyleSheet(light_qss())
    else:
        window.setStyleSheet("")  # clears override; app-level dark stylesheet takes over
