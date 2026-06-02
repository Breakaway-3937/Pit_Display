"""
Control Screen — operator panel.

Layout:
  Top bar  : app title + active team selector (color reflects active team)
  Left col : screen cards — each has a name (click to configure) + power toggle
  Right col: settings panel for the selected screen

Only the control screen boots. Other screens are opened/closed via power toggles.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QComboBox, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal

from app.config import config, SCREENS, MODES
from app.teams import all_teams
from app.widgets.toggle_switch import ToggleSwitch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _label(text: str, obj_name: str = "", align=Qt.AlignmentFlag.AlignLeft) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(align)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


def _panel(obj_name: str = "panel") -> QFrame:
    f = QFrame()
    f.setObjectName(obj_name)
    f.setFrameShape(QFrame.Shape.StyledPanel)
    return f


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    return line


SCREEN_LABELS = {
    "presentation_a": "Presentation A",
    "presentation_b": "Presentation B",
    "project":        "Project",
    "control":        "Control",
}


# ── Mode button ──────────────────────────────────────────────────────────────

MODE_LABELS = {
    "standard": "Standard",
    "judges":   "Judges",
    "lunch":    "Lunch",
}


class ModeButton(QPushButton):
    """
    One of the three mode selector buttons in the top bar.
    Active state: solid fill with team primary color.
    Inactive state: dark/muted with hover feedback.
    Recolors automatically when the team changes.
    """

    def __init__(self, mode: str, team_color: str):
        super().__init__(MODE_LABELS.get(mode, mode.title()))
        self.mode = mode
        self._team_color = team_color
        self._active = False
        self.setMinimumWidth(110)
        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    def set_active(self, active: bool):
        self._active = active
        self._refresh()

    def update_color(self, hex_color: str):
        self._team_color = hex_color
        self._refresh()

    def _refresh(self):
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self._team_color};
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: 700;
                    letter-spacing: 0.3px;
                    padding: 0 20px;
                }}
                QPushButton:pressed {{
                    opacity: 0.85;
                }}
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #222222;
                    color: #666666;
                    border: 1px solid #333333;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: 500;
                    padding: 0 20px;
                }
                QPushButton:hover {
                    background-color: #2e2e2e;
                    color: #aaaaaa;
                    border-color: #444444;
                }
                QPushButton:pressed {
                    background-color: #1a1a1a;
                }
            """)


# ── Screen card (sidebar row) ─────────────────────────────────────────────────

class ScreenCard(QFrame):
    """
    One row in the left sidebar.
    Left side: clickable name that selects the settings panel.
    Right side: power ToggleSwitch that shows/hides the managed window.
    Control screen gets no power toggle (it's always on).
    """

    selected = pyqtSignal(str)       # screen_id — user clicked the name area
    power_toggled = pyqtSignal(str, bool)  # screen_id, on

    def __init__(self, screen_id: str, team_color: str):
        super().__init__()
        self.screen_id = screen_id
        self._active = False
        self._base_style = ""
        self._active_style = ""
        self._build(team_color)

    def _build(self, team_color: str):
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(0)

        # Name button (left — fills remaining space)
        self._name_btn = QPushButton(SCREEN_LABELS.get(self.screen_id, self.screen_id))
        self._name_btn.setFlat(True)
        self._name_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_btn.clicked.connect(lambda: self.selected.emit(self.screen_id))
        row.addWidget(self._name_btn)

        # Power toggle (right) — hidden for the control screen itself
        if self.screen_id != "control":
            self._toggle = ToggleSwitch(color_on=team_color)
            self._toggle.setChecked(False)
            self._toggle.toggled.connect(
                lambda on: self.power_toggled.emit(self.screen_id, on)
            )
            row.addWidget(self._toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        else:
            always_on = _label("Always On", "stat_label")
            always_on.setStyleSheet("color: #44cc66; font-size: 11px;")
            row.addWidget(always_on, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._toggle = None

        self._refresh_style()

    def set_active(self, active: bool):
        self._active = active
        self._refresh_style()

    def update_team_color(self, hex_color: str):
        if self._toggle:
            self._toggle.set_color_on(hex_color)
        self._refresh_style()

    def _refresh_style(self):
        border_color = "#888888" if not self._active else "#current_team"
        # Will be overridden by set_team_color; default to visible highlight
        name_style_active = """
            QPushButton {
                background-color: #1e1e1e;
                color: #ffffff;
                border: none;
                border-left: 3px solid %(color)s;
                border-radius: 0;
                text-align: left;
                padding-left: 16px;
                font-size: 14px;
                font-weight: 600;
            }
        """
        name_style_inactive = """
            QPushButton {
                background-color: transparent;
                color: #888888;
                border: none;
                border-left: 3px solid transparent;
                border-radius: 0;
                text-align: left;
                padding-left: 16px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #1a1a1a;
                color: #cccccc;
            }
        """
        # Store color for apply later
        self._active_style = name_style_active
        if self._active:
            self._name_btn.setStyleSheet(name_style_inactive)  # reset first
            # Applied properly via apply_team_color
        else:
            self._name_btn.setStyleSheet(name_style_inactive)

    def apply_team_color(self, hex_color: str):
        if self._active:
            self._name_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: #1e1e1e;
                    color: #ffffff;
                    border: none;
                    border-left: 3px solid {hex_color};
                    border-radius: 0;
                    text-align: left;
                    padding-left: 16px;
                    font-size: 14px;
                    font-weight: 600;
                }}
            """)
        else:
            self._name_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #888888;
                    border: none;
                    border-left: 3px solid transparent;
                    border-radius: 0;
                    text-align: left;
                    padding-left: 16px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background-color: #1a1a1a;
                    color: #cccccc;
                }
            """)
        if self._toggle:
            self._toggle.set_color_on(hex_color)


# ── Setting row ───────────────────────────────────────────────────────────────

class SettingRow(QWidget):
    def __init__(self, label: str, description: str, control: QWidget):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(_label(label, "stat_value"))
        if description:
            desc = _label(description, "stat_label")
            desc.setWordWrap(True)
            text_col.addWidget(desc)
        layout.addLayout(text_col, stretch=1)
        layout.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)


# ── Per-screen settings panel ─────────────────────────────────────────────────

class ScreenSettingsPanel(QWidget):
    def __init__(self, screen_id: str):
        super().__init__()
        self._screen_id = screen_id

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(4)

        outer.addWidget(_label(
            SCREEN_LABELS.get(screen_id, screen_id).upper(), "section_header"
        ))
        outer.addWidget(_label(
            f"Configure settings for the {SCREEN_LABELS.get(screen_id, screen_id)} screen.",
            "stat_label",
        ))
        outer.addSpacing(12)
        outer.addWidget(_divider())
        outer.addSpacing(12)

        # ── Appearance ────────────────────────────────────────────────────
        outer.addWidget(_label("Appearance", "screen_title"))
        outer.addSpacing(8)

        self._theme_toggle = ToggleSwitch(
            color_on=config.active_team.primary_color
        )
        current_theme = config.screen_theme(screen_id)
        self._theme_toggle.setChecked(current_theme == "light")
        self._theme_toggle.toggled.connect(self._on_theme_toggled)

        self._theme_label = _label(
            "Light" if current_theme == "light" else "Dark", "stat_value"
        )

        toggle_row_widget = QWidget()
        toggle_row_layout = QHBoxLayout(toggle_row_widget)
        toggle_row_layout.setContentsMargins(0, 0, 0, 0)
        toggle_row_layout.addWidget(self._theme_toggle)
        toggle_row_layout.addSpacing(10)
        toggle_row_layout.addWidget(self._theme_label)
        toggle_row_layout.addStretch()

        outer.addWidget(SettingRow(
            label="Dark / Light Mode",
            description="Switch this screen between dark and light display themes.",
            control=toggle_row_widget,
        ))

        outer.addWidget(_divider())
        outer.addSpacing(12)

        # Placeholder for future settings
        placeholder = _panel()
        ph_layout = QVBoxLayout(placeholder)
        ph_layout.setContentsMargins(16, 12, 16, 12)
        ph_layout.addWidget(_label(
            "More settings coming soon", "stat_label",
            Qt.AlignmentFlag.AlignCenter,
        ))
        outer.addWidget(placeholder)
        outer.addStretch()

        config.team_changed.connect(self._on_team_changed)

    def _on_theme_toggled(self, light: bool):
        theme = "light" if light else "dark"
        self._theme_label.setText("Light" if light else "Dark")
        config.set(self._screen_id, "theme", theme)

    def _on_team_changed(self, team):
        self._theme_toggle.set_color_on(team.primary_color)


# ── Control screen ────────────────────────────────────────────────────────────

class ControlScreen(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Control")
        self.setMinimumSize(1100, 680)
        self._screen_cards: dict[str, ScreenCard] = {}
        self._settings_panels: dict[str, ScreenSettingsPanel] = {}
        self._managed_windows: dict[str, QMainWindow] = {}
        self._mode_buttons: dict[str, ModeButton] = {}
        self._build_ui()
        config.team_changed.connect(self._on_team_changed)
        config.screen_setting_changed.connect(self._on_screen_setting_changed)

    def set_managed_windows(self, windows: dict[str, "QMainWindow"]) -> None:
        """Called from main.py to hand over the other 3 window references."""
        self._managed_windows = windows

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        main.addWidget(self._top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._left_sidebar(), stretch=0)
        body.addWidget(self._right_panel(), stretch=1)
        main.addLayout(body)

    def _top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(72)
        bar.setStyleSheet(
            "QFrame { background-color: #111111; border: none;"
            " border-bottom: 1px solid #2a2a2a; }"
        )

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        # Left: app title
        self._title_label = _label("PIT DISPLAY", "section_header")
        self._title_label.setStyleSheet(
            f"color: {config.active_team.primary_color}; font-size: 11px;"
            " font-weight: 700; letter-spacing: 1.5px;"
        )
        self._title_label.setFixedWidth(110)
        layout.addWidget(self._title_label)

        layout.addStretch()

        # Center: mode buttons
        team_color = config.active_team.primary_color
        for mode in MODES:
            btn = ModeButton(mode, team_color)
            btn.set_active(mode == config.mode)
            btn.clicked.connect(lambda checked, m=mode: self._on_mode_clicked(m))
            self._mode_buttons[mode] = btn
            layout.addWidget(btn)

        layout.addStretch()

        # Right: team selector
        layout.addWidget(_label("Team:", "stat_label"))
        layout.addSpacing(4)

        self._team_combo = QComboBox()
        self._team_combo.setMinimumWidth(190)
        self._populate_team_combo()
        self._team_combo.currentIndexChanged.connect(self._on_team_selected)
        layout.addWidget(self._team_combo)

        return bar

    def _populate_team_combo(self):
        self._team_combo.blockSignals(True)
        self._team_combo.clear()
        for i, team in enumerate(all_teams()):
            self._team_combo.addItem(f"Team {team.display_name}", userData=team.number)
            if team.number == config.active_team.number:
                self._team_combo.setCurrentIndex(i)
        self._team_combo.blockSignals(False)

    def _left_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(
            "QFrame { background-color: #111111; border: none;"
            " border-right: 1px solid #2a2a2a; }"
        )

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(0)

        hdr = _label("  SCREENS", "section_header")
        hdr.setContentsMargins(0, 0, 0, 8)
        layout.addWidget(hdr)

        team_color = config.active_team.primary_color
        for screen_id in SCREENS:
            card = ScreenCard(screen_id, team_color)
            card.selected.connect(self._select_screen)
            card.power_toggled.connect(self._on_power_toggled)
            self._screen_cards[screen_id] = card
            layout.addWidget(card)

        layout.addStretch()
        return sidebar

    def _right_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for screen_id in SCREENS:
            panel = ScreenSettingsPanel(screen_id)
            self._settings_panels[screen_id] = panel
            panel.setVisible(False)
            layout.addWidget(panel)

        layout.addStretch()

        self._select_screen(SCREENS[0])
        return container

    # ── Interaction ───────────────────────────────────────────────────────

    def _select_screen(self, screen_id: str):
        for sid, panel in self._settings_panels.items():
            panel.setVisible(sid == screen_id)
        for sid, card in self._screen_cards.items():
            card.set_active(sid == screen_id)
            card.apply_team_color(config.active_team.primary_color)

    def _on_power_toggled(self, screen_id: str, on: bool):
        window = self._managed_windows.get(screen_id)
        if window is None:
            return
        if on:
            window.show()
        else:
            window.hide()

    def _on_team_selected(self, index: int):
        team_number = self._team_combo.itemData(index)
        if team_number is not None:
            config.set_team(team_number)

    def _on_mode_clicked(self, mode: str):
        config.set_mode(mode)
        for m, btn in self._mode_buttons.items():
            btn.set_active(m == mode)

    def _on_team_changed(self, team):
        # Title bar accent
        self._title_label.setStyleSheet(
            f"color: {team.primary_color}; font-size: 11px;"
            " font-weight: 700; letter-spacing: 1.5px;"
        )
        # Mode buttons recolor
        for btn in self._mode_buttons.values():
            btn.update_color(team.primary_color)
        # Sidebar cards recolor
        for card in self._screen_cards.values():
            card.apply_team_color(team.primary_color)
        # Keep combo in sync if changed programmatically
        for i in range(self._team_combo.count()):
            if self._team_combo.itemData(i) == team.number:
                self._team_combo.blockSignals(True)
                self._team_combo.setCurrentIndex(i)
                self._team_combo.blockSignals(False)
                break

    def _on_screen_setting_changed(self, screen_id: str, key: str, value):
        if key != "theme":
            return
        window = self._managed_windows.get(screen_id)
        if window is None:
            return
        from app.theme import apply_theme
        apply_theme(window, value)
