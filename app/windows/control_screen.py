"""
Control Screen — operator panel.

Layout:
  Top bar  : app title + active team selector (color reflects active team)
  Left col : screen cards — each has a name (click to configure) + power toggle
  Right col: settings panel for the selected screen

Only the control screen boots. Other screens are opened/closed via power toggles.
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QComboBox, QSizePolicy, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from app import brand
from app.cad_assets import cad_assets
from app.config import config, MODES, MODE_LABELS, SCREENS, SCREEN_LABELS
from app.judges_slides import judges_slides
from app.teams import all_teams
from app.theme import apply_theme
from app.widgets.brand_widgets import ChamferButton, ChamferFrame, eyebrow
from app.widgets.cad_upload_panel import CADSettingsPanel
from app.widgets.helpers import clear_layout, divider, label
from app.widgets.toggle_switch import ToggleSwitch


# ── Mode button ──────────────────────────────────────────────────────────────

class ModeButton(ChamferButton):
    """
    One of the three mode selector buttons in the top bar. Built on The Cut:
    active = red fill (team accent), inactive = ghost. Recolors with the team.
    """

    def __init__(self, mode: str, team_color: str):
        super().__init__(
            MODE_LABELS.get(mode, mode.title()),
            variant="ghost",
            accent=team_color,
            cut=brand.CUT_SMALL,
        )
        self.mode = mode
        self.setMinimumWidth(124)
        self.setFixedHeight(42)
        f = self.font()
        f.setPixelSize(15)
        self.setFont(f)


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
        self._team_color = team_color
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
            always_on = label("Always On", "stat_label")
            always_on.setStyleSheet(f"color: {brand.STATUS_ONLINE}; font-size: 11px;")
            row.addWidget(always_on, alignment=Qt.AlignmentFlag.AlignVCenter)
            self._toggle = None

        self._apply_style()

    def set_active(self, active: bool):
        self._active = active
        self._apply_style()

    def apply_team_color(self, hex_color: str):
        self._team_color = hex_color
        if self._toggle:
            self._toggle.set_color_on(hex_color)
        self._apply_style()

    def _apply_style(self):
        base = (
            "border: none; border-radius: 0; text-align: left; padding-left: 16px;"
            f' font-family: "{brand.FONT_DISPLAY}"; font-size: 15px;'
        )
        if self._active:
            qss = f"""
                QPushButton {{ {base}
                    background-color: {brand.CARBON_SURF}; color: {brand.WHITE};
                    border-left: 3px solid {self._team_color}; font-weight: 600;
                }}
            """
        else:
            qss = f"""
                QPushButton {{ {base}
                    background-color: transparent; color: {brand.MUTED_DARK};
                    border-left: 3px solid transparent; font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {brand.CARBON_SURF}; color: {brand.INK_DARK};
                }}
            """
        self._name_btn.setStyleSheet(qss)


# ── Setting row ───────────────────────────────────────────────────────────────

class SettingRow(QWidget):
    def __init__(self, label_text: str, description: str, control: QWidget):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(label(label_text, "stat_value"))
        if description:
            desc = label(description, "stat_label")
            desc.setWordWrap(True)
            text_col.addWidget(desc)
        layout.addLayout(text_col, stretch=1)
        layout.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)


# ── Judges slide thumbnail ────────────────────────────────────────────────────

class _Thumbnail(QFrame):
    """Clickable slide thumbnail used inside _SlidePicker."""

    _W, _H = 96, 54  # 16:9 display size

    def __init__(self, index: int, path: Path):
        super().__init__()
        self._index = index
        self.setFixedWidth(self._W + 8)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 2)
        layout.setSpacing(2)

        img_lbl = QLabel()
        img_lbl.setFixedSize(self._W, self._H)
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = QPixmap(str(path)).scaled(
            self._W, self._H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img_lbl.setPixmap(pm)
        layout.addWidget(img_lbl)

        num = QLabel(str(index + 1))
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setStyleSheet("font-size: 10px; color: #888888; border: none;")
        layout.addWidget(num)

        self.set_active(False)

    def set_active(self, active: bool):
        accent = config.active_team.primary_color
        border = accent if active else brand.CARBON_LINE
        self.setStyleSheet(
            f"QFrame {{ border: 2px solid {border}; border-radius: 4px;"
            f" background-color: {brand.CARBON_SURF}; }}"
        )

    def mousePressEvent(self, _event):
        judges_slides.go_to(self._index)


# ── Judges slide picker ───────────────────────────────────────────────────────

class _SlidePicker(QWidget):
    """
    Thumbnail strip + nav buttons for judges slides.
    Shown in the settings panel for presentation screens.
    """

    def __init__(self):
        super().__init__()
        self._thumbs: list[_Thumbnail] = []
        self._build_ui()
        judges_slides.slides_reloaded.connect(self._rebuild)
        judges_slides.slide_changed.connect(self._on_slide_changed)
        config.team_changed.connect(lambda _: self._refresh_active())
        self._rebuild()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # Folder path hint so students know where to drop files
        hint = label("Drop images in:  assets/judges_slides/", "stat_label")
        hint.setStyleSheet("font-family: Roboto; font-size: 11px;")
        outer.addWidget(hint)

        # Scrollable thumbnail strip
        self._scroll = QScrollArea()
        self._scroll.setFixedHeight(96)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._strip = QWidget()
        self._strip.setStyleSheet("background: transparent;")
        self._thumb_layout = QHBoxLayout(self._strip)
        self._thumb_layout.setContentsMargins(0, 4, 0, 4)
        self._thumb_layout.setSpacing(8)
        self._scroll.setWidget(self._strip)
        outer.addWidget(self._scroll)

        # Nav row: Prev | counter | Next | Reload
        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(8)

        self._prev_btn = QPushButton("◀  Prev")
        self._prev_btn.setObjectName("btn_primary")
        self._prev_btn.clicked.connect(judges_slides.prev)
        nav_row.addWidget(self._prev_btn)

        self._counter_lbl = label("— / —", "stat_label", Qt.AlignmentFlag.AlignCenter)
        nav_row.addWidget(self._counter_lbl, stretch=1)

        self._next_btn = QPushButton("Next  ▶")
        self._next_btn.setObjectName("btn_primary")
        self._next_btn.clicked.connect(judges_slides.next)
        nav_row.addWidget(self._next_btn)

        reload_btn = QPushButton("↺  Reload")
        reload_btn.clicked.connect(judges_slides.reload)
        nav_row.addWidget(reload_btn)

        outer.addWidget(nav)

    # ── Rebuild from disk ─────────────────────────────────────────────────

    def _rebuild(self):
        clear_layout(self._thumb_layout)
        self._thumbs.clear()

        paths = judges_slides.paths
        if not paths:
            self._thumb_layout.addWidget(
                label("No images found — add files and click Reload", "stat_label")
            )
        else:
            for i, path in enumerate(paths):
                thumb = _Thumbnail(i, path)
                thumb.set_active(i == judges_slides.index)
                self._thumbs.append(thumb)
                self._thumb_layout.addWidget(thumb)
        self._thumb_layout.addStretch()
        self._update_counter()

    # ── Signal handlers ───────────────────────────────────────────────────

    def _on_slide_changed(self, index: int, _pixmap):
        self._refresh_active(index)
        self._update_counter()

    def _refresh_active(self, index: int | None = None):
        active = index if index is not None else judges_slides.index
        for i, thumb in enumerate(self._thumbs):
            thumb.set_active(i == active)

    def _update_counter(self):
        count = judges_slides.count
        has = count > 0
        self._counter_lbl.setText(f"{judges_slides.index + 1} / {count}" if has else "— / —")
        self._prev_btn.setEnabled(has)
        self._next_btn.setEnabled(has)


# ── CAD judges picker ─────────────────────────────────────────────────────────

class _CADJudgesPicker(QWidget):
    """
    Subsystem selector shown in the judges settings for presentation screens.
    Activates the CAD page on the presentation screens and drives subsystem focus.
    """

    def __init__(self):
        super().__init__()
        self._sub_btns: dict[str, QPushButton] = {}
        self._build_ui()
        cad_assets.config_changed.connect(self._rebuild)
        cad_assets.subsystem_focused.connect(self._on_focused)
        config.team_changed.connect(lambda _: self._refresh_colors())
        self._rebuild()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        hint = label("Subsystems map to Onshape node names in assets/cad/subsystems.json.", "stat_label")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._btn_area = QWidget()
        self._btn_layout = QVBoxLayout(self._btn_area)
        self._btn_layout.setContentsMargins(0, 0, 0, 0)
        self._btn_layout.setSpacing(4)
        outer.addWidget(self._btn_area)

        nav = QWidget()
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(8)

        back_btn = QPushButton("◀  Back to Slides")
        back_btn.clicked.connect(lambda: cad_assets.activate_cad(False))
        nav_row.addWidget(back_btn)

        full_btn = QPushButton("↩  Full View")
        full_btn.setObjectName("btn_primary")
        full_btn.clicked.connect(lambda: cad_assets.focus_subsystem(""))
        nav_row.addWidget(full_btn)

        outer.addWidget(nav)

    def _rebuild(self):
        clear_layout(self._btn_layout)
        self._sub_btns.clear()

        cfg = cad_assets.load_config()
        subs = cfg.get("subsystems", [])

        if not subs:
            self._btn_layout.addWidget(
                label("No subsystems configured — add them in the Project panel.", "stat_label")
            )
            return

        color = config.active_team.primary_color
        for sub in subs:
            btn = QPushButton(sub["display_name"])
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            accent = sub.get("accent_color", color)
            btn.setProperty("accent", accent)
            sub_id = sub["id"]
            btn.clicked.connect(lambda checked, sid=sub_id: cad_assets.focus_subsystem(sid))
            self._sub_btns[sub_id] = btn
            self._btn_layout.addWidget(btn)

        self._btn_layout.addStretch()
        self._refresh_colors()

    def _on_focused(self, sub_id: str):
        for sid, btn in self._sub_btns.items():
            accent = btn.property("accent") or config.active_team.primary_color
            if sid == sub_id:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {accent};
                        color: #ffffff;
                        border: none;
                        border-radius: 6px;
                        font-size: 13px;
                        font-weight: 700;
                        padding: 0 14px;
                    }}
                """)
            else:
                btn.setStyleSheet("")

    def _refresh_colors(self):
        self._on_focused(cad_assets.focused_id)


# ── Per-screen settings panel ─────────────────────────────────────────────────

class ScreenSettingsPanel(QWidget):
    def __init__(self, screen_id: str):
        super().__init__()
        self._screen_id = screen_id
        self._team_toggles: list[ToggleSwitch] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(4)

        outer.addWidget(label(
            SCREEN_LABELS.get(screen_id, screen_id).upper(), "section_header"
        ))
        outer.addWidget(label(
            f"Configure settings for the {SCREEN_LABELS.get(screen_id, screen_id)} screen.",
            "stat_label",
        ))
        outer.addSpacing(12)
        outer.addWidget(divider())
        outer.addSpacing(12)

        # ── Appearance ────────────────────────────────────────────────────
        outer.addWidget(label("Appearance", "screen_title"))
        outer.addSpacing(8)

        light = config.screen_theme(screen_id) == "light"
        self._theme_toggle, self._theme_label, theme_row = self._labeled_toggle(
            checked=light, text="Light" if light else "Dark",
        )
        self._theme_toggle.toggled.connect(self._on_theme_toggled)

        outer.addWidget(SettingRow(
            label_text="Dark / Light Mode",
            description="Switch this screen between dark and light display themes.",
            control=theme_row,
        ))

        outer.addWidget(divider())
        outer.addSpacing(12)

        # Judges content — only relevant for presentation screens
        if screen_id in ("presentation_a", "presentation_b"):
            outer.addWidget(label("Judges Slides", "screen_title"))
            outer.addSpacing(8)
            outer.addWidget(_SlidePicker())
            outer.addSpacing(16)

            outer.addWidget(divider())
            outer.addSpacing(12)

            outer.addWidget(label("Judges CAD", "screen_title"))
            outer.addSpacing(8)
            outer.addWidget(_CADJudgesPicker())
            outer.addSpacing(12)

        # Project screen: pick which interactive view is shown, then CAD config.
        if screen_id == "project":
            outer.addWidget(label("Display Content", "screen_title"))
            outer.addSpacing(8)

            board = config.get(screen_id, "content", "cad") == "board"
            self._content_toggle, self._content_label, content_row = self._labeled_toggle(
                checked=board, text="Impact Board" if board else "CAD Viewer",
            )
            self._content_toggle.toggled.connect(self._on_content_toggled)

            outer.addWidget(SettingRow(
                label_text="CAD Viewer / Impact Board",
                description="Choose what the project touchscreen shows.",
                control=content_row,
            ))

            outer.addWidget(divider())
            outer.addSpacing(12)
            outer.addWidget(label("CAD Viewer Config", "screen_title"))
            outer.addSpacing(8)
            outer.addWidget(CADSettingsPanel(), stretch=1)

        outer.addStretch()

        config.team_changed.connect(self._on_team_changed)

    def _labeled_toggle(self, checked: bool, text: str) -> tuple[ToggleSwitch, QLabel, QWidget]:
        """A team-colored ToggleSwitch with a state label beside it."""
        toggle = ToggleSwitch(color_on=config.active_team.primary_color)
        toggle.setChecked(checked)
        self._team_toggles.append(toggle)

        state_lbl = label(text, "stat_value")

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(toggle)
        row_layout.addSpacing(10)
        row_layout.addWidget(state_lbl)
        row_layout.addStretch()
        return toggle, state_lbl, row

    def _on_theme_toggled(self, light: bool):
        self._theme_label.setText("Light" if light else "Dark")
        config.set(self._screen_id, "theme", "light" if light else "dark")

    def _on_content_toggled(self, board: bool):
        self._content_label.setText("Impact Board" if board else "CAD Viewer")
        config.set(self._screen_id, "content", "board" if board else "cad")

    def _on_team_changed(self, team):
        for toggle in self._team_toggles:
            toggle.set_color_on(team.primary_color)


# ── Control screen ────────────────────────────────────────────────────────────

class ControlScreen(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Control")
        self.setMinimumSize(480, 400)
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
        bar.setFixedHeight(76)
        bar.setStyleSheet(
            f"QFrame {{ background-color: {brand.CARBON_SURF}; border: none;"
            f" border-bottom: 1px solid {brand.CARBON_LINE}; }}"
        )

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 20, 0)
        layout.setSpacing(14)

        # Left: brand mark — chamfered red chip with the team number + wordmark
        self._brand_chip = ChamferFrame(
            fill=config.active_team.primary_color,
            border=None,
            cut=brand.CUT_SMALL,
        )
        self._brand_chip.setFixedSize(58, 44)
        chip_l = QVBoxLayout(self._brand_chip)
        chip_l.setContentsMargins(0, 0, 0, 0)
        self._chip_num = label(str(config.active_team.number), "",
                               Qt.AlignmentFlag.AlignCenter)
        self._chip_num.setStyleSheet(
            f'color: #FFFFFF; background: transparent;'
            f' font-family: "{brand.FONT_DISPLAY}"; font-size: 17px; font-weight: 700;'
        )
        chip_l.addWidget(self._chip_num)
        layout.addWidget(self._brand_chip)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._title_label = eyebrow("Pit Display")
        wordmark = label("BREAKAWAY", "screen_title")
        wordmark.setStyleSheet(
            f'color: #FFFFFF; background: transparent;'
            f' font-family: "{brand.FONT_DISPLAY}"; font-size: 20px;'
            f' font-weight: 700; letter-spacing: 1px;'
        )
        title_col.addWidget(self._title_label)
        title_col.addWidget(wordmark)
        layout.addLayout(title_col)

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
        team_col = QVBoxLayout()
        team_col.setSpacing(2)
        team_col.addWidget(eyebrow("Team", brand.MUTED_DARK))
        self._team_combo = QComboBox()
        self._team_combo.setMinimumWidth(190)
        self._populate_team_combo()
        self._team_combo.currentIndexChanged.connect(self._on_team_selected)
        team_col.addWidget(self._team_combo)
        layout.addLayout(team_col)

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
            f"QFrame {{ background-color: {brand.CARBON_SURF}; border: none;"
            f" border-right: 1px solid {brand.CARBON_LINE}; }}"
        )

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 18, 0, 16)
        layout.setSpacing(0)

        hdr = eyebrow("Screens")
        hdr.setContentsMargins(18, 0, 0, 10)
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

    def _right_panel(self) -> QScrollArea:
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

        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._select_screen(SCREENS[0])
        return scroll

    # ── Interaction ───────────────────────────────────────────────────────

    def _select_screen(self, screen_id: str):
        for sid, panel in self._settings_panels.items():
            panel.setVisible(sid == screen_id)
        for sid, card in self._screen_cards.items():
            card.set_active(sid == screen_id)

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
        # Brand chip follows the active team
        self._brand_chip.set_fill(team.primary_color)
        self._chip_num.setText(str(team.number))
        # Mode buttons recolor
        for btn in self._mode_buttons.values():
            btn.set_accent(team.primary_color)
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
        # Managed windows subscribe to this signal themselves; the control
        # window is the only one nobody else re-themes.
        if screen_id == "control" and key == "theme":
            apply_theme(self, value)
