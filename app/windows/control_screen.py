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
from app.admin import admin
from app.cad_assets import cad_assets
from app.config import config, MODES, MODE_LABELS, SCREENS, SCREEN_LABELS
from app.judges_slides import judges_slides
from app.teams import all_teams
from app.theme import apply_theme
from app.widgets.brand_widgets import (
    RoundedButton, RoundedFrame, eyebrow, mono_font,
)
from app.widgets.cad_upload_panel import CADSettingsPanel
from app.widgets.checklist_panel import ChecklistPanel
from app.widgets.admin_bar import AdminBar
from app.widgets.helpers import clear_layout, divider, label
from app.widgets.led_panel import LEDPanel
from app.widgets.music_panel import MusicPanel
from app.widgets.robot_panel import RobotLogPanel
from app.widgets.toggle_switch import ToggleSwitch
from app.leds import leds

# Pit-wide subsystems. Unlike SCREENS these are not windows — they are hardware
# the pit owns, so they get their own sidebar group and their own panels.
SYSTEMS = ["leds", "music", "robot"]
SYSTEM_LABELS = {"leds": "LED Strips", "music": "Music", "robot": "Robot Logs"}


# ── Mode button ──────────────────────────────────────────────────────────────

class ModeButton(RoundedButton):
    """
    One of the three mode selector buttons in the top bar. Rounded shell:
    active = red fill (team accent), inactive = ghost. Recolors with the team.
    """

    def __init__(self, mode: str, team_color: str):
        super().__init__(
            MODE_LABELS.get(mode, mode.title()),
            variant="ghost",
            accent=team_color,
            radius=brand.R_BTN,
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

    def __init__(self, screen_id: str, team_color: str,
                 label_text: str | None = None, show_toggle: bool = True):
        super().__init__()
        self.screen_id = screen_id
        self._active = False
        self._team_color = team_color
        self._label_text = label_text or SCREEN_LABELS.get(screen_id, screen_id)
        self._show_toggle = show_toggle
        self._build(team_color)

    def _build(self, team_color: str):
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(0)

        # Name button (left — fills remaining space)
        self._name_btn = QPushButton(self._label_text)
        self._name_btn.setFlat(True)
        self._name_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_btn.clicked.connect(lambda: self.selected.emit(self.screen_id))
        row.addWidget(self._name_btn)

        # Power toggle (right) — hidden for the control screen itself
        if not self._show_toggle:
            self._toggle = None
        elif self.screen_id != "control":
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

    def set_checked(self, on: bool):
        """Reflect state changed elsewhere, without re-emitting power_toggled."""
        if self._toggle is not None and self._toggle.isChecked() != on:
            self._toggle.blockSignals(True)
            self._toggle.setChecked(on)
            self._toggle.blockSignals(False)

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
        self._down = False
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

    # Fires on release-inside, not on press. On the touch panel a press is also
    # how a drag-scroll of the thumbnail strip begins, and the touch router
    # unlatches that press with a release *outside* the rect (see app/touch.py)
    # — so a swipe along the strip scrolls it instead of jumping the slide.
    def mousePressEvent(self, event):
        self._down = True
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._down and self.rect().contains(event.position().toPoint()):
            judges_slides.go_to(self._index)
        self._down = False
        event.accept()


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


# ── Standard slide picker ─────────────────────────────────────────────────────

class _StandardSlideRow(QFrame):
    """One row in the standard-slide list: number, title, and a preview line."""

    clicked = pyqtSignal(int)

    def __init__(self, index: int, title: str, body: str, is_fact: bool,
                 kind: str = ""):
        super().__init__()
        self.index = index
        self._active = False
        self._down = False
        self._is_fact = is_fact
        self._kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # QLabel subclasses QFrame, so a bare `QFrame { border-left: … }` rule
        # paints that border on every child label too. Scope it by object name.
        self.setObjectName("slide_row")

        row = QVBoxLayout(self)
        row.setContentsMargins(11, 8, 11, 8)
        row.setSpacing(2)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._num = label(f"{index + 1:02d}", "stat_label")
        self._num.setFont(mono_font(11))
        self._num.setFixedWidth(22)
        head.addWidget(self._num)
        self._title = label(title, "stat_value")
        self._title.setWordWrap(True)
        head.addWidget(self._title, stretch=1)
        # Three kinds of entry, and the operator needs to tell them apart:
        # authored slides are edited in code, fact slides regenerate from the
        # log, and the board is a live widget rather than a slide at all.
        tag_text, tag_color = "", ""
        if kind == "board":
            tag_text, tag_color = "LIVE BOARD", brand.STATUS_PENDING
        elif is_fact:
            tag_text, tag_color = "FROM LOG", brand.STATUS_ONLINE
        if tag_text:
            tag = label(tag_text, "stat_label")
            tag.setFont(mono_font(9))
            tag.setStyleSheet(f"color: {tag_color}; background: transparent;")
            head.addWidget(tag)
        row.addLayout(head)

        self._body = label(body, "stat_label")
        self._body.setWordWrap(True)
        self._body.setContentsMargins(30, 0, 0, 0)
        row.addWidget(self._body)

        self._apply_style()

    def set_active(self, active: bool):
        if active != self._active:
            self._active = active
            self._apply_style()

    def apply_team(self, _color: str):
        self._apply_style()

    def _apply_style(self):
        accent = config.active_team.primary_color
        if self._active:
            self.setStyleSheet(
                f"QFrame#slide_row {{ background-color: {brand.CARBON_SURF2};"
                f" border-left: 3px solid {accent}; border-radius: 0; }}")
            self._num.setStyleSheet(
                f"color: {accent}; background: transparent;")
        else:
            self.setStyleSheet(
                "QFrame#slide_row { background-color: transparent;"
                " border-left: 3px solid transparent; border-radius: 0; }")
            self._num.setStyleSheet(
                f"color: {brand.FAINT_DARK}; background: transparent;")

    # Release-inside, for the same reason as _Thumbnail above: this row lives in
    # a scroll list that the operator drags with a finger.
    def mousePressEvent(self, event):
        self._down = True
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._down and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.index)
        self._down = False
        event.accept()


# The four faces of Standard mode, in the order an operator would reach for
# them. Values match `PresentationScreen._CONTENT_PAGES` plus "rotation".
_CONTENT_CHOICES = [
    ("rotation",    "Slide rotation"),
    ("checklist",   "Pit checklist"),
    ("diagnostics", "Robot diagnostics"),
    ("robot_info",  "Robot info"),
]


class _StandardSlidePicker(QWidget):
    """
    Every slide in the Standard rotation for one presentation screen, with the
    live one highlighted and clickable to jump.

    Jumps travel through `config.set(screen, "slide_index", n)` rather than a
    direct call, for the same reason everything else here does: the control
    screen never holds a reference to a presentation window.
    """

    def __init__(self, screen_id: str):
        super().__init__()
        self._screen_id = screen_id
        self._rows: list[_StandardSlideRow] = []
        self._build_ui()
        config.screen_setting_changed.connect(self._on_setting_changed)
        config.mode_changed.connect(lambda _m: self._refresh_enabled())
        config.team_changed.connect(self._on_team_changed)
        config.logs_changed.connect(self._rebuild)
        self._rebuild()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self._count_lbl = label("", "stat_label")
        head.addWidget(self._count_lbl)
        head.addStretch()
        self._mode_hint = label("", "stat_label")
        head.addWidget(self._mode_hint)
        outer.addLayout(head)

        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidget(self._list_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(260)
        outer.addWidget(scroll)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        prev = RoundedButton("‹ Previous", variant="secondary",
                             accent=config.active_team.primary_color)
        prev.clicked.connect(lambda: self._step(-1))
        nxt = RoundedButton("Next ›", variant="secondary",
                            accent=config.active_team.primary_color)
        nxt.clicked.connect(lambda: self._step(+1))
        first = RoundedButton("↩ First", variant="ghost")
        first.clicked.connect(lambda: self._jump(0))
        reload_btn = RoundedButton("↺ Reload", variant="ghost")
        reload_btn.setToolTip(
            "Rebuild the list after importing a robot log. The presentation "
            "screen picks up new slides when it is powered off and on.")
        reload_btn.clicked.connect(self._rebuild)
        for b in (prev, nxt, first, reload_btn):
            nav.addWidget(b)
        self._nav_buttons = [prev, nxt]
        outer.addLayout(nav)

    # ── Build the list ────────────────────────────────────────────────────

    def _screen_cls(self):
        from app.windows.presentation_a import PresentationScreenA
        from app.windows.presentation_b import PresentationScreenB
        return (PresentationScreenA if self._screen_id == "presentation_a"
                else PresentationScreenB)

    def _slides(self) -> list[tuple[str, str]]:
        """
        Every stop in the cycle — slides, then this screen's board.

        Deliberately the same call the presentation screen makes, so an index
        written here means the same stop there. If these two ever disagree, a
        click lands on the wrong slide.
        """
        cls = self._screen_cls()
        try:
            return cls.rotation_entries()
        except Exception:
            return list(cls.SLIDES)

    def _board_index(self) -> int | None:
        cls = self._screen_cls()
        try:
            return (len(cls.rotation_slides())
                    if cls.board_entry() is not None else None)
        except Exception:
            return None

    def _rebuild(self):
        clear_layout(self._list)
        self._rows.clear()
        slides = self._slides()
        authored = len(self._authored())
        board = self._board_index()
        for i, (title, body) in enumerate(slides):
            is_board = board is not None and i == board
            row = _StandardSlideRow(
                i, title, body,
                is_fact=(not is_board) and i >= authored,
                kind="board" if is_board else "")
            row.clicked.connect(self._jump)
            self._rows.append(row)
            self._list.addWidget(row)
        self._list.addStretch()

        n_slides = len(slides) - (1 if board is not None else 0)
        facts = n_slides - authored
        self._count_lbl.setText(
            f"{n_slides} slides — {authored} authored"
            + (f", {facts} from the robot log" if facts else "")
            + (" · + the live board" if board is not None else ""))
        self._refresh_active()
        self._refresh_enabled()

    def _authored(self) -> list:
        from app.windows.presentation_a import PresentationScreenA
        from app.windows.presentation_b import PresentationScreenB
        cls = (PresentationScreenA if self._screen_id == "presentation_a"
               else PresentationScreenB)
        return cls.SLIDES

    # ── Interaction ───────────────────────────────────────────────────────

    def _current(self) -> int:
        return int(config.get(self._screen_id, "slide_index", 0) or 0)

    def _jump(self, index: int):
        if not self._rows:
            return
        config.set(self._screen_id, "slide_index", index % len(self._rows))

    def _step(self, delta: int):
        self._jump(self._current() + delta)

    def _on_setting_changed(self, screen: str, key: str, _value):
        if screen == self._screen_id and key == "slide_index":
            self._refresh_active()

    def _refresh_active(self):
        current = self._current()
        for row in self._rows:
            row.set_active(row.index == current)

    def _refresh_enabled(self):
        standard = config.mode == "standard"
        self._mode_hint.setText(
            "" if standard else f"{MODE_LABELS.get(config.mode, config.mode)} mode is live")
        self._mode_hint.setStyleSheet(
            f"color: {brand.STATUS_PENDING}; background: transparent;")
        for b in self._nav_buttons:
            b.setEnabled(True)   # jumping while in another mode still sets the
                                 # slide the screen returns to

    def _on_team_changed(self, team):
        for row in self._rows:
            row.apply_team(team.primary_color)
        for b in self._nav_buttons:
            b.set_accent(team.primary_color)


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
            # What this screen shows in Standard mode. Per-screen, not a global
            # mode: the useful arrangement is one overhead screen on the
            # checklist while the other keeps rotating for visitors.
            outer.addWidget(label("Standard Content", "screen_title"))
            outer.addSpacing(4)
            outer.addWidget(label(
                "What this overhead screen shows in Standard mode. Rotation "
                "cycles the slides and finishes on the diagnostics board; "
                "anything else pins that page and stops the 45-second timer "
                "touching this screen.",
                "stat_label"))
            outer.addSpacing(8)

            # A combo, not a toggle: there are four faces now, and a two-state
            # switch cannot say which of them you meant.
            self._pres_content = QComboBox()
            for value, text in _CONTENT_CHOICES:
                self._pres_content.addItem(text, value)
            current = config.get(screen_id, "content", "rotation")
            idx = self._pres_content.findData(current)
            self._pres_content.setCurrentIndex(idx if idx >= 0 else 0)
            self._pres_content.setFixedWidth(220)
            self._pres_content.currentIndexChanged.connect(
                self._on_pres_content_changed)

            outer.addWidget(SettingRow(
                label_text="Screen content",
                description="Rotation, the checklist, or a robot board pinned.",
                control=self._pres_content,
            ))
            outer.addSpacing(8)
            outer.addWidget(ChecklistPanel(screen_id))
            outer.addSpacing(16)

            outer.addWidget(divider())
            outer.addSpacing(12)

            outer.addWidget(label("Standard Slides", "screen_title"))
            outer.addSpacing(4)
            outer.addWidget(label(
                "Everything in this screen's Standard rotation. Click one to "
                "put it on screen now; the 45-second timer carries on from there.",
                "stat_label"))
            outer.addSpacing(8)
            outer.addWidget(_StandardSlidePicker(screen_id))
            outer.addSpacing(16)
            outer.addWidget(divider())
            outer.addSpacing(12)

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

    def _on_pres_content_changed(self, _index: int):
        config.set(self._screen_id, "content", self._pres_content.currentData())

    def _on_content_toggled(self, board: bool):
        self._content_label.setText("Impact Board" if board else "CAD Viewer")
        config.set(self._screen_id, "content", "board" if board else "cad")

    def _on_team_changed(self, team):
        for toggle in self._team_toggles:
            toggle.set_color_on(team.primary_color)


# ── Pit subsystem panel ───────────────────────────────────────────────────────

class SystemSettingsPanel(QWidget):
    """
    Wrapper giving the LED and Music panels the same header chrome the
    per-screen panels have, so the right-hand column reads consistently.
    """

    _BLURBS = {
        "leds": "Drive the pit LED strips over USB. The controller keeps "
                "running its animation if this app closes.",
        "music": "Local music for the overhead speakers, with a ten-band "
                 "equaliser for tuning the pit.",
        "robot": "Import telemetry exported off the robot, and name the CAN "
                 "ids so every screen can say “Front-Left Drive” instead of "
                 "“TalonFX 11”.",
    }

    def __init__(self, system_id: str):
        super().__init__()
        self._system_id = system_id

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(4)

        outer.addWidget(label(SYSTEM_LABELS.get(system_id, system_id).upper(),
                              "section_header"))
        outer.addWidget(label(self._BLURBS.get(system_id, ""), "stat_label"))
        outer.addSpacing(12)
        outer.addWidget(divider())
        outer.addSpacing(16)

        self.body = {"leds": LEDPanel, "music": MusicPanel,
                     "robot": RobotLogPanel}[system_id]()
        outer.addWidget(self.body)
        outer.addStretch()


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
        leds.state_changed.connect(self._on_leds_state_changed)

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

        # Admin bar — an inline strip, not a screen and not a modal, so the
        # operator can watch the gated controls appear as they unlock.
        self._admin_bar = AdminBar()
        self._admin_bar.closed.connect(self._close_admin)
        self._admin_bar.setVisible(False)
        main.addWidget(self._admin_bar)

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

        # Left: brand mark — rounded red chip with the team number + wordmark.
        # Also the admin door: clicking it opens the admin bar (see _toggle_admin).
        self._brand_chip = RoundedFrame(
            fill=config.active_team.primary_color,
            border=None,
            radius=brand.R_BTN,
        )
        self._brand_chip.setFixedSize(58, 44)
        self._brand_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brand_chip.setToolTip("Admin — LED tuning and equaliser")
        self._brand_chip.mousePressEvent = self._on_brand_clicked
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

        layout.addSpacing(18)
        sys_hdr = eyebrow("Pit Systems")
        sys_hdr.setContentsMargins(18, 0, 0, 10)
        layout.addWidget(sys_hdr)

        for system_id in SYSTEMS:
            # The LED card's toggle is the strip kill switch; music has no
            # equivalent single on/off, so it gets a plain row.
            card = ScreenCard(system_id, team_color,
                              label_text=SYSTEM_LABELS[system_id],
                              show_toggle=(system_id == "leds"))
            card.selected.connect(self._select_screen)
            card.power_toggled.connect(self._on_power_toggled)
            if system_id == "leds":
                card.set_checked(leds.enabled)
            self._screen_cards[system_id] = card
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

        for system_id in SYSTEMS:
            panel = SystemSettingsPanel(system_id)
            self._settings_panels[system_id] = panel
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

    def _on_brand_clicked(self, _event):
        """The Breakaway mark is the admin door."""
        if self._admin_bar.isVisible():
            self._close_admin()
        else:
            self._admin_bar.setVisible(True)
            self._admin_bar.focus_password()

    def _close_admin(self):
        """Hiding the bar always re-locks — there is no stay-unlocked option."""
        admin.lock()
        self._admin_bar.setVisible(False)

    def _select_screen(self, screen_id: str):
        for sid, panel in self._settings_panels.items():
            panel.setVisible(sid == screen_id)
        for sid, card in self._screen_cards.items():
            card.set_active(sid == screen_id)

    def _on_power_toggled(self, screen_id: str, on: bool):
        if screen_id == "leds":
            leds.set_enabled(on)
            return
        window = self._managed_windows.get(screen_id)
        if window is None:
            return
        if on:
            window.show()
            # `show()` activates the window it shows, and keystrokes go to the
            # active window. The audience screens never need a keyboard, so
            # letting one take it means the operator's next keystroke — a CAN-id
            # name, a checklist item, the admin password — lands on a slide
            # rotation instead. Hand activation straight back.
            self.activateWindow()
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

    def _on_leds_state_changed(self):
        card = self._screen_cards.get("leds")
        if card is not None:
            card.set_checked(leds.enabled)

    def _on_screen_setting_changed(self, screen_id: str, key: str, value):
        # Managed windows subscribe to this signal themselves; the control
        # window is the only one nobody else re-themes.
        if screen_id == "control" and key == "theme":
            apply_theme(self, value)
